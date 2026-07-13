#!/usr/bin/env python
"""Fetch Google Scholar citations via SerpApi and write _data/citations.yml
in the schema al-folio's bib.liquid expects.

Originally al-folio bundles a `scholarly`-based version of this script.
We swap to SerpApi because GitHub Actions IPs are routinely blocked /
rate-limited by Google Scholar when called via `scholarly`, while
SerpApi is paid-but-reliable. The output schema (_data/citations.yml)
is identical, so no template changes are needed.

Required environment variable:
  SERPAPI_API_KEY -- the SerpApi key (set as a GitHub Actions secret)

Optional destructive override:
  ALLOW_CITATION_KEY_DELETION=1 -- explicitly allow remote results to remove
  keys already present in _data/citations.yml. Scheduled CI does not set it.

Reads scholar_userid from _data/socials.yml.
Writes _data/citations.yml.
"""

import os
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import requests
import serpapi
import yaml


SOCIALS_FILE = Path("_data/socials.yml")
OUTPUT_FILE = Path("_data/citations.yml")
ALLOW_KEY_DELETION_ENV = "ALLOW_CITATION_KEY_DELETION"
REQUEST_TIMEOUT_SECONDS = 15
MAX_SEARCH_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)


def load_scholar_user_id() -> str:
    if not SOCIALS_FILE.exists():
        sys.exit(f"Error: {SOCIALS_FILE} not found.")
    try:
        with SOCIALS_FILE.open() as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        sys.exit(f"Error parsing {SOCIALS_FILE}: {e}")
    if not isinstance(cfg, dict):
        sys.exit("Error: socials root must be an object.")
    sid = cfg.get("scholar_userid")
    if not isinstance(sid, str) or not sid.strip():
        sys.exit(
            f"Error: 'scholar_userid' must be a non-empty string in {SOCIALS_FILE}."
        )
    return sid.strip()


def validate_existing_papers(papers: Mapping) -> None:
    for citation_id, record in papers.items():
        if not isinstance(citation_id, str) or not citation_id.strip():
            sys.exit("Existing citation paper key must be a non-empty string.")
        if not isinstance(record, Mapping):
            sys.exit(f"Existing citation paper {citation_id!r} must be an object.")

        citations = record.get("citations")
        if (
            isinstance(citations, bool)
            or not isinstance(citations, int)
            or citations < 0
        ):
            sys.exit(
                "Existing citation citations must be a non-negative integer "
                f"for {citation_id!r}."
            )


def fsync_directory(directory: Path) -> None:
    directory_fd = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    fsync_error: OSError | None = None
    try:
        os.fsync(directory_fd)
    except OSError as e:
        fsync_error = e

    try:
        os.close(directory_fd)
    except OSError as close_error:
        if fsync_error is not None:
            raise OSError(
                f"{fsync_error}; additionally failed to close directory: {close_error}"
            ) from fsync_error
        raise

    if fsync_error is not None:
        raise fsync_error


def write_citations_atomically(citation_data: Mapping) -> None:
    temp_path: Path | None = None
    replaced = False
    primary_error: OSError | yaml.YAMLError | None = None
    cleanup_error: OSError | None = None
    try:
        serialized = yaml.safe_dump(
            citation_data,
            width=1000,
            sort_keys=True,
            allow_unicode=True,
        )
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=OUTPUT_FILE.parent,
            prefix=f".{OUTPUT_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(serialized)
            temp_file.flush()
            os.fchmod(temp_file.fileno(), 0o644)
            os.fsync(temp_file.fileno())
        os.replace(temp_path, OUTPUT_FILE)
        replaced = True
        fsync_directory(OUTPUT_FILE.parent)
    except (OSError, yaml.YAMLError) as e:
        primary_error = e
    finally:
        if temp_path is not None and not replaced:
            try:
                with suppress(FileNotFoundError):
                    temp_path.unlink()
            except OSError as e:
                cleanup_error = e

    if primary_error is None:
        return
    if replaced:
        sys.exit(
            "Citation file was replaced, but directory durability could not be "
            f"confirmed: {primary_error}"
        )

    message = f"Error writing citations atomically: {primary_error}"
    if cleanup_error is not None:
        message += (
            "; additionally failed to clean up temporary citation file: "
            f"{cleanup_error}"
        )
    sys.exit(message)


def is_wrapped_connect_timeout(exc: serpapi.HTTPError) -> bool:
    original = exc.__cause__ or exc.__context__
    return isinstance(exc, serpapi.HTTPConnectionError) and isinstance(
        original, requests.ConnectTimeout
    )


def search_page_with_retry(
    client: serpapi.Client,
    params: dict[str, object],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Mapping:
    for attempt in range(1, MAX_SEARCH_ATTEMPTS + 1):
        try:
            result = client.search(dict(params))
        except serpapi.TimeoutError:
            reason = "timeout"
        except serpapi.HTTPError as exc:
            if is_wrapped_connect_timeout(exc):
                reason = "timeout"
            elif 500 <= exc.status_code <= 599:
                reason = f"HTTP {exc.status_code}"
            else:
                sys.exit(
                    "SerpApi request failed permanently "
                    f"with HTTP {exc.status_code} at start={params['start']}."
                )
        else:
            if not isinstance(result, Mapping):
                sys.exit(
                    "SerpApi error: expected an object payload "
                    f"at start={params['start']}."
                )
            return result

        if attempt == MAX_SEARCH_ATTEMPTS:
            sys.exit(
                f"SerpApi request failed after {MAX_SEARCH_ATTEMPTS} attempts "
                f"at start={params['start']} ({reason})."
            )

        delay = RETRY_DELAYS_SECONDS[attempt - 1]
        print(
            f"Transient SerpApi {reason}; retrying in {delay}s "
            f"({attempt}/{MAX_SEARCH_ATTEMPTS}).",
            file=sys.stderr,
        )
        sleep(delay)

    raise AssertionError("unreachable")


def fetch_author_articles(
    scholar_id: str,
    client: serpapi.Client,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Mapping]:
    """Page through SerpApi google_scholar_author and return all articles."""
    articles: list[Mapping] = []
    start = 0
    page_size = 100
    while True:
        result = search_page_with_retry(
            client,
            {
                "engine": "google_scholar_author",
                "author_id": scholar_id,
                "num": page_size,
                "start": start,
            },
            sleep=sleep,
        )
        if "error" in result:
            sys.exit(f"SerpApi returned an error payload at start={start}.")
        if "articles" not in result:
            sys.exit(f"SerpApi error: payload missing 'articles' at start={start}.")
        page = result["articles"]
        if not isinstance(page, list):
            sys.exit(f"SerpApi error: 'articles' must be a list at start={start}.")
        for index, article in enumerate(page):
            if not isinstance(article, Mapping):
                sys.exit(
                    f"SerpApi error: article {start + index} must be an object."
                )
        articles.extend(page)
        if len(page) < page_size:
            return articles
        start += page_size


def main() -> None:
    scholar_id = load_scholar_user_id()
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        sys.exit("Error: SERPAPI_API_KEY environment variable is not set.")

    today = datetime.now(UTC).date().isoformat()

    # Skip if already updated today (the schema's metadata.last_updated)
    existing = {}
    if OUTPUT_FILE.exists():
        try:
            with OUTPUT_FILE.open() as f:
                existing = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            sys.exit(f"Error parsing existing citations in {OUTPUT_FILE}: {e}")
        if not isinstance(existing, dict):
            sys.exit("Existing citations root must be an object.")
        metadata = existing.get("metadata")
        existing_papers = existing.get("papers")
        if not isinstance(metadata, dict):
            sys.exit("Existing citations metadata must be an object.")
        if not isinstance(existing_papers, dict):
            sys.exit("Existing citations papers must be an object.")
        validate_existing_papers(existing_papers)
        if metadata.get("last_updated") == today:
            if not existing_papers:
                sys.exit("Existing citations papers are empty despite being updated today.")
            print("Citations already up-to-date for today; skipping.")
            return

    client = serpapi.Client(
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    print(f"Fetching SerpApi google_scholar_author for ID: {scholar_id}")
    articles = fetch_author_articles(scholar_id, client)
    print(f"Got {len(articles)} articles.")
    existing_papers = existing.get("papers") or {}
    if not articles:
        sys.exit("Refusing to use an empty citation result.")

    citation_data = {"metadata": {"last_updated": today}, "papers": {}}
    for art in articles:
        cid = art.get("citation_id")  # format: "USER_ID:PUB_ID"
        if not isinstance(cid, str) or not cid.strip():
            sys.exit("SerpApi article citation_id must be a non-empty string.")
        scholar_prefix = f"{scholar_id}:"
        if (
            not cid.startswith(scholar_prefix)
            or not cid[len(scholar_prefix) :].strip()
        ):
            sys.exit(
                "SerpApi article citation_id must match the current scholar prefix "
                f"'{scholar_prefix}'."
            )
        if cid in citation_data["papers"]:
            sys.exit(f"Duplicate citation_id in SerpApi result: {cid}")
        title = art.get("title")
        year = art.get("year")
        if title is None:
            title = ""
        if year is None:
            year = ""
        if not isinstance(title, str):
            sys.exit(f"SerpApi article title must be a string for {cid}.")
        if not isinstance(year, str):
            sys.exit(f"SerpApi article year must be a string for {cid}.")
        cited_by = art.get("cited_by")
        if cited_by is None:
            citations = 0
        elif isinstance(cited_by, Mapping):
            citations = cited_by.get("value", 0)
        else:
            sys.exit(f"SerpApi article cited_by must be a mapping for {cid}.")
        if citations is None:
            citations = 0
        if isinstance(citations, bool) or (
            isinstance(citations, float) and not citations.is_integer()
        ):
            sys.exit(
                f"Invalid citation count for {cid}: expected a non-negative integer."
            )
        try:
            citations = int(citations)
        except (TypeError, ValueError):
            sys.exit(
                f"Invalid citation count for {cid}: expected a non-negative integer."
            )
        if citations < 0:
            sys.exit(
                f"Invalid citation count for {cid}: expected a non-negative integer."
            )
        citation_data["papers"][cid] = {
            "title": title,
            "year": year,
            "citations": citations,
        }
        print(f"  {citations:>5}  {title[:80]}")

    if not citation_data["papers"]:
        sys.exit("SerpApi result contained no usable citation records.")

    missing_keys = sorted(set(existing_papers) - set(citation_data["papers"]))
    if missing_keys and os.environ.get(ALLOW_KEY_DELETION_ENV) != "1":
        sys.exit(
            "Refusing to delete citation keys without "
            f"{ALLOW_KEY_DELETION_ENV}=1: {', '.join(missing_keys)}"
        )

    # Skip write if nothing changed in papers content
    if existing_papers == citation_data["papers"]:
        print("No changes in citation counts; not rewriting file.")
        return

    write_citations_atomically(citation_data)
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit("Unexpected error while updating citations.")
