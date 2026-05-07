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

Reads scholar_userid from _data/socials.yml.
Writes _data/citations.yml.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import yaml
from serpapi import GoogleSearch


SOCIALS_FILE = Path("_data/socials.yml")
OUTPUT_FILE = Path("_data/citations.yml")


def load_scholar_user_id() -> str:
    if not SOCIALS_FILE.exists():
        sys.exit(f"Error: {SOCIALS_FILE} not found.")
    try:
        with SOCIALS_FILE.open() as f:
            cfg = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        sys.exit(f"Error parsing {SOCIALS_FILE}: {e}")
    sid = cfg.get("scholar_userid")
    if not sid:
        sys.exit(f"Error: 'scholar_userid' missing in {SOCIALS_FILE}.")
    return sid


def fetch_author_articles(scholar_id: str, api_key: str) -> list:
    """Page through SerpApi google_scholar_author and return all articles."""
    articles = []
    start = 0
    page_size = 100
    while True:
        params = {
            "engine": "google_scholar_author",
            "author_id": scholar_id,
            "api_key": api_key,
            "num": page_size,
            "start": start,
        }
        result = GoogleSearch(params).get_dict()
        if "error" in result:
            sys.exit(f"SerpApi error: {result['error']}")
        page = result.get("articles", [])
        articles.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return articles


def main() -> None:
    scholar_id = load_scholar_user_id()
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        sys.exit("Error: SERPAPI_API_KEY environment variable is not set.")

    today = datetime.now().strftime("%Y-%m-%d")

    # Skip if already updated today (the schema's metadata.last_updated)
    existing = {}
    if OUTPUT_FILE.exists():
        try:
            with OUTPUT_FILE.open() as f:
                existing = yaml.safe_load(f) or {}
            if existing.get("metadata", {}).get("last_updated") == today:
                print("Citations already up-to-date for today; skipping.")
                return
        except Exception as e:
            print(f"Warning: could not read existing {OUTPUT_FILE}: {e}")

    print(f"Fetching SerpApi google_scholar_author for ID: {scholar_id}")
    articles = fetch_author_articles(scholar_id, api_key)
    print(f"Got {len(articles)} articles.")

    citation_data = {"metadata": {"last_updated": today}, "papers": {}}
    for art in articles:
        cid = art.get("citation_id")  # format: "USER_ID:PUB_ID"
        if not cid:
            continue
        title = art.get("title") or ""
        year = art.get("year") or ""
        citations = (art.get("cited_by") or {}).get("value") or 0
        try:
            citations = int(citations)
        except (TypeError, ValueError):
            citations = 0
        citation_data["papers"][cid] = {
            "title": title,
            "year": year,
            "citations": citations,
        }
        print(f"  {citations:>5}  {title[:80]}")

    # Skip write if nothing changed in papers content
    if existing.get("papers") == citation_data["papers"]:
        print("No changes in citation counts; not rewriting file.")
        return

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w") as f:
        yaml.dump(citation_data, f, width=1000, sort_keys=True, allow_unicode=True)
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"Unexpected error: {e}")
