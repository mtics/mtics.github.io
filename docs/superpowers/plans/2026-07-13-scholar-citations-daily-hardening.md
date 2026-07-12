# Daily Google Scholar Citation Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the existing daily Google Scholar citation pipeline so it uses the maintained SerpApi client, retries only transient failures, preserves the last known-good data atomically, and proves that every rendered badge uses the exact committed citation record.

**Architecture:** Keep the current least-privilege two-job citation workflow and the existing `workflow_run` deployment handoff. Strengthen the workflow's schedule and time bounds, isolate request retry logic from response validation, publish citation YAML with an atomic replace, and add source-to-built-site contracts that connect BibTeX IDs, committed YAML keys, and rendered badge metadata.

**Tech Stack:** GitHub Actions, Python 3.13, `serpapi` 1.0.2, PyYAML, Python `unittest`, Jekyll/Liquid, Ruby Minitest, bibtex-ruby, Nokogiri, Bundler, GitHub CLI.

---

## Preconditions

- Work only on `codex/scholar-citations-daily-hardening` until the pull request is approved.
- Do not change publication content, badge styling, page layout, or `deploy.yml` unless a failing contract proves the existing handoff is incorrect.
- Never place `SERPAPI_API_KEY` in command output, retry diagnostics, test fixtures, commit messages, or the commit job.
- Keep the existing deletion guard and no-op behavior: `metadata.last_updated` changes only when paper data changes.
- Use small red-green-refactor commits and run `git diff --check` before each commit.

## Task 1: Bound the scheduled workflow

**Files:**

- Modify: `.github/workflows/update_scholar_citations.yml`
- Modify: `test/release_contract_test.rb`

- [ ] **Step 1: Add failing workflow contracts**

Add these methods immediately before `test_citation_refresh_isolates_secreted_updates_from_write_credentials`:

```ruby
def test_citation_refresh_uses_off_hour_daily_schedule_and_fixed_concurrency
  workflow = load_workflow(".github/workflows/update_scholar_citations.yml")
  triggers = workflow.fetch("on")

  assert_equal ["17 8 * * *"], Array(triggers.fetch("schedule")).map { |entry| entry.fetch("cron") }
  assert triggers.key?("workflow_dispatch"), "citation refresh must remain manually dispatchable"
  assert_equal({ "group" => "scholar-citations", "cancel-in-progress" => false },
               workflow.fetch("concurrency"))
end

def test_citation_refresh_jobs_have_bounded_timeouts
  jobs = load_workflow(".github/workflows/update_scholar_citations.yml").fetch("jobs")
  expected = { "update" => 15, "commit" => 5 }
  actual = expected.keys.to_h { |name| [name, jobs.fetch(name)["timeout-minutes"]] }

  assert_equal expected, actual
end
```

In `test_citation_refresh_isolates_secreted_updates_from_write_credentials`, also bind both artifact actions to the same fixed artifact name:

```ruby
assert_equal "scholar-citations", upload.dig("with", "name")
assert_equal "scholar-citations", download.dig("with", "name")
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
ruby test/release_contract_test.rb --name '/citation_refresh_(uses_off_hour_daily_schedule_and_fixed_concurrency|jobs_have_bounded_timeouts)/'
```

Expected: two failures. The cron is still `0 8 * * *`, and neither job has a timeout.

- [ ] **Step 3: Make the smallest workflow change**

In `.github/workflows/update_scholar_citations.yml`:

```yaml
on:
  schedule:
    - cron: "17 8 * * *" # daily at 08:17 UTC
  workflow_dispatch:
```

Add `timeout-minutes: 15` to `jobs.update` and `timeout-minutes: 5` to `jobs.commit`, each at job scope beside `runs-on`.

- [ ] **Step 4: Verify GREEN and preserve the existing security contracts**

Run:

```bash
ruby test/release_contract_test.rb --name '/citation_(refresh|publish|updater|dependency)/'
git diff --check
```

Expected: all selected tests pass; the update job remains read-only and secret-bearing, while the commit job remains secret-free and write-enabled.

- [ ] **Step 5: Commit the workflow boundary**

```bash
git add .github/workflows/update_scholar_citations.yml test/release_contract_test.rb
git commit -m "fix: bound Scholar citation workflow"
```

## Task 2: Replace the legacy SerpApi package and client

**Files:**

- Modify: `requirements-citations.in`
- Regenerate: `requirements-citations.txt`
- Modify: `bin/update_scholar_citations.py`
- Modify: `test/citation_updater_contract_test.py`
- Modify: `test/release_contract_test.rb`

- [ ] **Step 1: Change the dependency expectations first**

In both dependency expectation hashes in `test/release_contract_test.rb`, replace `google-search-results==2.4.2` with `serpapi==1.0.2`. Keep `PyYAML==6.0.3` unchanged.

Add an explicit absence contract so the old package cannot remain as an accidental direct or transitive dependency:

```ruby
def test_legacy_scholar_client_is_absent
  refute_includes read("requirements-citations.in"), "google-search-results"
  refute_includes read("requirements-citations.txt"), "google-search-results"
  refute_includes read("bin/update_scholar_citations.py"), "GoogleSearch"
end
```

- [ ] **Step 2: Verify the dependency contract fails against the legacy lock**

Run:

```bash
ruby test/release_contract_test.rb --name '/python_automation_dependencies_are_fully_hashed|compiled_requirement_inputs_are_minimal_and_present|legacy_scholar_client_is_absent/'
```

Expected: failures report that `serpapi==1.0.2` is absent and the old direct dependency is still present.

- [ ] **Step 3: Regenerate and install the reviewed citation lock**

Change `requirements-citations.in` to:

```text
serpapi==1.0.2
PyYAML==6.0.3
```

Regenerate and install with the repository's pinned interpreter contract:

```bash
uv pip compile requirements-citations.in --python-version 3.13.14 --universal --generate-hashes --exclude-newer 2026-07-12T00:00:00Z --output-file requirements-citations.txt
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c 'python -m pip install --require-hashes -r requirements-citations.txt && python -m pip check'
```

Verify that the direct package block contains the reviewed hashes:

```text
serpapi==1.0.2
sha256:06ff981129a1cb7c3706469a67f8d43e77ab295bcbdbfcb7c118d39e8efb0783
sha256:4edb67318918c0ff460aae118d66f76ad83ab75fbf901a77a9722b0cfe6c70aa
```

- [ ] **Step 4: Add a failing maintained-client construction test**

Replace `_SearchResult` with this fake client:

```python
class _SearchClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.params: list[dict[str, object]] = []

    def search(self, params: dict[str, object]) -> object:
        self.params.append(dict(params))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
```

Add this test:

```python
def test_main_builds_one_maintained_client_with_a_bounded_timeout(self) -> None:
    article = {
        "citation_id": "scholar-id:paper",
        "title": "Paper",
        "year": "2025",
        "cited_by": {"value": 1},
    }
    with tempfile.TemporaryDirectory() as directory:
        output = self._prepare_files(Path(directory), {})
        output.unlink()
        with (
            mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "api-key"}, clear=True),
            mock.patch.object(UPDATER.serpapi, "Client") as client_class,
            mock.patch.object(
                UPDATER,
                "fetch_author_articles",
                return_value=[article],
            ) as fetch,
        ):
            UPDATER.main()

        client_class.assert_called_once_with(api_key="api-key", timeout=15)
        fetch.assert_called_once_with("scholar-id", client_class.return_value)
```

Run:

```bash
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c 'python -m pip install --require-hashes -r requirements-citations.txt >/tmp/pip-install.log && python test/citation_updater_contract_test.py -k maintained_client'
```

Expected: failure because the updater still imports `GoogleSearch` and passes the API key into the pagination function.

- [ ] **Step 5: Migrate the updater to one maintained client**

Use these imports and constants:

```python
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import serpapi
import yaml

REQUEST_TIMEOUT_SECONDS = 15
```

Change the pagination signature to accept a client:

```python
def fetch_author_articles(
    scholar_id: str,
    client: serpapi.Client,
) -> list[Mapping]:
```

For each page, call the maintained client without putting the API key in the request mapping:

```python
params: dict[str, object] = {
    "engine": "google_scholar_author",
    "author_id": scholar_id,
    "num": page_size,
    "start": start,
}
result = client.search(dict(params))
```

In `main`, make the date explicitly UTC and create exactly one client:

```python
today = datetime.now(UTC).date().isoformat()
client = serpapi.Client(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
articles = fetch_author_articles(scholar_id, client)
```

Update the two existing daily-skip tests to use `datetime.now(UPDATER.UTC).date().isoformat()`.

Update the four existing pagination/schema tests to pass `_SearchClient` directly instead of patching `GoogleSearch`.

- [ ] **Step 6: Verify the client and lock migration**

Run:

```bash
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c 'python -m pip install --require-hashes -r requirements-citations.txt >/tmp/pip-install.log && python test/citation_updater_contract_test.py && python -m pip check'
ruby test/release_contract_test.rb --name '/python_automation_dependencies_are_fully_hashed|compiled_requirement_inputs_are_minimal_and_present|python_locks_record_the_supported_interpreter_and_cutoff|legacy_scholar_client_is_absent/'
rg -n 'google-search-results|from serpapi import GoogleSearch' requirements-citations.in requirements-citations.txt bin test .github || true
git diff --check
```

Expected: all tests pass, `pip check` reports no broken requirements, and the scoped `rg` command prints nothing.

- [ ] **Step 7: Commit the client migration**

```bash
git add requirements-citations.in requirements-citations.txt bin/update_scholar_citations.py test/citation_updater_contract_test.py test/release_contract_test.rb
git commit -m "refactor: use maintained SerpApi client"
```

## Task 3: Retry only transient page requests

**Files:**

- Modify: `bin/update_scholar_citations.py`
- Modify: `test/citation_updater_contract_test.py`

- [ ] **Step 1: Add retry test helpers**

Add:

```python
def _http_error(status_code: int):
    error = UPDATER.serpapi.HTTPError(Exception("request failed"))
    error.status_code = status_code
    error.error = f"HTTP {status_code}"
    return error
```

- [ ] **Step 2: Add failing retry-classification tests**

Add tests covering all of these exact cases:

```python
def test_timeout_retries_once_then_returns_the_page(self) -> None:
    client = _SearchClient([UPDATER.serpapi.TimeoutError(), {"articles": []}])
    sleep = mock.Mock()

    result = UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

    self.assertEqual([], result)
    self.assertEqual([mock.call(1)], sleep.call_args_list)
    self.assertEqual(2, len(client.params))

def test_server_errors_use_one_and_two_second_backoff(self) -> None:
    client = _SearchClient(
        [_http_error(500), _http_error(503), {"articles": []}]
    )
    sleep = mock.Mock()

    UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

    self.assertEqual([mock.call(1), mock.call(2)], sleep.call_args_list)
    self.assertEqual(3, len(client.params))

def test_three_timeouts_exhaust_the_page_budget(self) -> None:
    client = _SearchClient([UPDATER.serpapi.TimeoutError()] * 3)
    sleep = mock.Mock()

    with self.assertRaisesRegex(SystemExit, "after 3 attempts"):
        UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

    self.assertEqual([mock.call(1), mock.call(2)], sleep.call_args_list)
    self.assertEqual(3, len(client.params))

def test_permanent_http_errors_fail_without_retry(self) -> None:
    for status_code in (400, 401, 429, -1):
        with self.subTest(status_code=status_code):
            client = _SearchClient([_http_error(status_code)])
            sleep = mock.Mock()
            with self.assertRaisesRegex(SystemExit, "failed permanently"):
                UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)
            sleep.assert_not_called()
            self.assertEqual(1, len(client.params))
```

Also add tests asserting:

- the caller's parameter mapping is unchanged and no captured request contains `api_key`;
- a mapping with an API-level `error`, a missing `articles`, a non-list `articles`, or a non-mapping article fails after one request with no sleep;
- a successful 100-record first page and a transiently failing second page use a fresh three-attempt budget for the second page.

Use this mutation-aware fake to make the parameter-secrecy assertion observable:

```python
class _MutatingSearchClient:
    def __init__(self) -> None:
        self.params_at_entry: dict[str, object] | None = None

    def search(self, params: dict[str, object]) -> object:
        self.params_at_entry = dict(params)
        params["api_key"] = "injected-by-client"
        return {"articles": []}


def test_search_uses_a_copy_without_an_api_key(self) -> None:
    params: dict[str, object] = {
        "engine": "google_scholar_author",
        "author_id": "scholar-id",
        "num": 100,
        "start": 0,
    }
    original = dict(params)
    client = _MutatingSearchClient()

    UPDATER.search_page_with_retry(client, params, sleep=mock.Mock())

    self.assertEqual(original, params)
    self.assertNotIn("api_key", client.params_at_entry or {})
```

Strengthen each existing malformed-page test by passing `sleep=mock.Mock()`, then assert that sleep was not called and `len(client.params) == 1`. Add the missing API-level error case:

```python
def test_api_error_payload_fails_without_retry(self) -> None:
    client = _SearchClient([{"error": "remote failure"}])
    sleep = mock.Mock()

    with self.assertRaisesRegex(SystemExit, "error payload"):
        UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

    sleep.assert_not_called()
    self.assertEqual(1, len(client.params))
```

Use exactly 100 valid first-page articles to prove the retry budget resets on page two:

```python
def test_each_page_has_an_independent_retry_budget(self) -> None:
    first_page = [
        {
            "citation_id": f"scholar-id:paper-{index}",
            "title": f"Paper {index}",
            "year": "2025",
            "cited_by": {"value": index},
        }
        for index in range(100)
    ]
    client = _SearchClient(
        [
            _http_error(500),
            _http_error(503),
            {"articles": first_page},
            _http_error(500),
            _http_error(503),
            {"articles": []},
        ]
    )
    sleep = mock.Mock()

    articles = UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

    self.assertEqual(first_page, articles)
    self.assertEqual(
        [mock.call(1), mock.call(2), mock.call(1), mock.call(2)],
        sleep.call_args_list,
    )
    self.assertEqual([0, 0, 0, 100, 100, 100], [item["start"] for item in client.params])
```

- [ ] **Step 3: Verify RED**

Run:

```bash
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c 'python -m pip install --require-hashes -r requirements-citations.txt >/tmp/pip-install.log && python test/citation_updater_contract_test.py'
```

Expected: failures because `fetch_author_articles` has no injectable sleep or bounded retry helper.

- [ ] **Step 4: Implement the isolated retry helper**

Add imports and constants:

```python
import time
from collections.abc import Callable, Mapping

MAX_SEARCH_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
```

Add the complete helper:

```python
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
            if not 500 <= exc.status_code <= 599:
                sys.exit(
                    "SerpApi request failed permanently "
                    f"with HTTP {exc.status_code} at start={params['start']}."
                )
            reason = f"HTTP {exc.status_code}"
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
```

Make pagination accept the injected sleep, call this helper once per page, and keep schema failures outside the retry loop:

```python
def fetch_author_articles(
    scholar_id: str,
    client: serpapi.Client,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Mapping]:
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
```

Replace the old response-error message with the fixed page-scoped message shown above; do not echo `result["error"]`, because remote text is not trusted diagnostic output.

The helper must pass `dict(params)` into `client.search`; the maintained client mutates its argument by injecting the API key.

- [ ] **Step 5: Verify GREEN and secret-safe diagnostics**

Run:

```bash
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c 'python -m pip install --require-hashes -r requirements-citations.txt >/tmp/pip-install.log && python test/citation_updater_contract_test.py && python -m py_compile bin/update_scholar_citations.py test/citation_updater_contract_test.py'
git diff --check
```

Expected: all updater tests pass; no diagnostic contains the API key or the underlying exception string.

- [ ] **Step 6: Commit retry behavior**

```bash
git add bin/update_scholar_citations.py test/citation_updater_contract_test.py
git commit -m "fix: retry transient Scholar requests"
```

## Task 4: Publish citation YAML atomically

**Files:**

- Modify: `bin/update_scholar_citations.py`
- Modify: `test/citation_updater_contract_test.py`

- [ ] **Step 1: Add a failing rollback contract**

Add a test that creates an old valid citation file, returns changed citation data, forces `os.replace` to raise `OSError`, and then asserts byte-for-byte preservation plus removal of temporary files:

```python
def test_atomic_replace_failure_preserves_the_last_known_good_file(self) -> None:
    existing = {
        "metadata": {"last_updated": "2000-01-01"},
        "papers": {
            "scholar-id:paper": {
                "title": "Paper",
                "year": "2025",
                "citations": 1,
            }
        },
    }
    changed = {
        "citation_id": "scholar-id:paper",
        "title": "Paper",
        "year": "2025",
        "cited_by": {"value": 2},
    }
    with tempfile.TemporaryDirectory() as directory:
        output = self._prepare_files(Path(directory), existing)
        before = output.read_bytes()
        with (
            mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "api-key"}, clear=True),
            mock.patch.object(UPDATER, "fetch_author_articles", return_value=[changed]),
            mock.patch.object(UPDATER.os, "replace", side_effect=OSError("replace failed")),
        ):
            with self.assertRaisesRegex(SystemExit, "writing citations atomically"):
                UPDATER.main()

        self.assertEqual(before, output.read_bytes())
        self.assertEqual([], list(output.parent.glob(f".{output.name}.*.tmp")))
```

Add the serialization rollback and no-op preservation contracts explicitly:

```python
def test_atomic_serialization_failure_preserves_the_last_known_good_file(self) -> None:
    existing = {
        "metadata": {"last_updated": "2000-01-01"},
        "papers": {
            "scholar-id:paper": {
                "title": "Paper",
                "year": "2025",
                "citations": 1,
            }
        },
    }
    changed = {
        "citation_id": "scholar-id:paper",
        "title": "Paper",
        "year": "2025",
        "cited_by": {"value": 2},
    }
    with tempfile.TemporaryDirectory() as directory:
        output = self._prepare_files(Path(directory), existing)
        before = output.read_bytes()
        with (
            mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "api-key"}, clear=True),
            mock.patch.object(UPDATER, "fetch_author_articles", return_value=[changed]),
            mock.patch.object(
                UPDATER.yaml,
                "safe_dump",
                side_effect=yaml.YAMLError("serialization failed"),
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "writing citations atomically"):
                UPDATER.main()

        self.assertEqual(before, output.read_bytes())
        self.assertEqual([], list(output.parent.glob(f".{output.name}.*.tmp")))

def test_unchanged_papers_do_not_replace_the_file_or_advance_the_date(self) -> None:
    existing = {
        "metadata": {"last_updated": "2000-01-01"},
        "papers": {
            "scholar-id:paper": {
                "title": "Paper",
                "year": "2025",
                "citations": 1,
            }
        },
    }
    unchanged = {
        "citation_id": "scholar-id:paper",
        "title": "Paper",
        "year": "2025",
        "cited_by": {"value": 1},
    }
    with tempfile.TemporaryDirectory() as directory:
        output = self._prepare_files(Path(directory), existing)
        before = output.read_bytes()
        with (
            mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "api-key"}, clear=True),
            mock.patch.object(UPDATER, "fetch_author_articles", return_value=[unchanged]),
            mock.patch.object(UPDATER.os, "replace") as replace,
        ):
            UPDATER.main()

        replace.assert_not_called()
        self.assertEqual(before, output.read_bytes())
```

- [ ] **Step 2: Verify RED**

Run:

```bash
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c 'python -m pip install --require-hashes -r requirements-citations.txt >/tmp/pip-install.log && python test/citation_updater_contract_test.py -k atomic'
```

Expected: failure because the updater writes directly to the tracked output path.

- [ ] **Step 3: Implement atomic serialization and replace**

Add imports:

```python
import tempfile
from contextlib import suppress
```

Add:

```python
def write_citations_atomically(citation_data: Mapping) -> None:
    temporary_path: Path | None = None
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
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())

        os.replace(temporary_path, OUTPUT_FILE)
        temporary_path = None
    except (OSError, yaml.YAMLError) as exc:
        sys.exit(f"Error writing citations atomically: {exc}")
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()
```

Replace the direct `OUTPUT_FILE.open("w")` block with `write_citations_atomically(citation_data)`. Call it only after all schema, deletion, and no-op comparisons succeed.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c 'python -m pip install --require-hashes -r requirements-citations.txt >/tmp/pip-install.log && python test/citation_updater_contract_test.py && python -m py_compile bin/update_scholar_citations.py test/citation_updater_contract_test.py'
git diff --check
```

Expected: every failure path preserves the old file, and successful writes still produce safe, sorted YAML.

- [ ] **Step 5: Commit atomic publication**

```bash
git add bin/update_scholar_citations.py test/citation_updater_contract_test.py
git commit -m "fix: write Scholar citations atomically"
```

## Task 5: Connect BibTeX IDs to freshly built badges

**Files:**

- Modify: `test/frontend_theme_accessibility_contract_20260712_test.rb`

- [ ] **Step 1: Add source-data and URL helpers**

Add these requires:

```ruby
require "bibtex"
require "uri"
require "yaml"
```

Add these helpers after `document`:

```ruby
def scholar_contract_data
  user = YAML.safe_load_file(File.join(ROOT, "_data/socials.yml")).fetch("scholar_userid")
  papers = YAML.safe_load_file(File.join(ROOT, "_data/citations.yml")).fetch("papers")
  entries = BibTeX.open(File.join(ROOT, "_bibliography/papers.bib")).entries.values.select do |entry|
    !entry[:google_scholar_id].to_s.strip.empty?
  end
  [user, papers, entries]
end

def rendered_scholar_badges(path, user)
  document(path).css("a.scholar-citations").map do |anchor|
    refute_empty anchor["href"].to_s
    uri = URI.parse(anchor["href"].to_s)
    params = URI.decode_www_form(uri.query.to_s).group_by(&:first).transform_values do |pairs|
      pairs.map(&:last)
    end
    assert_equal "https", uri.scheme
    assert_equal "scholar.google.com", uri.host
    assert_equal "/citations", uri.path
    assert_equal ["view_citation"], params.fetch("view_op")
    assert_equal [user], params.fetch("user")
    assert_equal 1, params.fetch("citation_for_view").length
    key = params.fetch("citation_for_view").first
    assert key.start_with?("#{user}:")
    {
      key: key,
      publication_id: key.delete_prefix("#{user}:"),
      text: anchor.at_css(".scholar-citation-count")&.text&.strip,
      aria: anchor["aria-label"],
    }
  end
end
```

- [ ] **Step 2: Add the exact source mapping contract**

```ruby
def test_every_bibtex_scholar_id_has_an_exact_committed_citation_key
  user, papers, entries = scholar_contract_data
  publication_ids = entries.map { |entry| entry[:google_scholar_id].to_s.strip }

  assert_equal 5, publication_ids.length
  assert_equal publication_ids.length, publication_ids.uniq.length
  assert_equal 4, entries.count { |entry| entry[:selected].to_s == "true" }

  publication_ids.each do |publication_id|
    key = "#{user}:#{publication_id}"
    assert papers.key?(key), "missing exact citation key #{key}"
    count = papers.fetch(key).fetch("citations")
    assert_instance_of Integer, count
    assert_operator count, :>=, 0
  end
end
```

- [ ] **Step 3: Add the fresh-build rendering contract**

```ruby
def test_fresh_pages_render_exact_scholar_counts_links_and_labels
  user, papers, entries = scholar_contract_data
  expected = {
    "index.html" => entries.select { |entry| entry[:selected].to_s == "true" },
    "publications/index.html" => entries,
  }
  all_badges = []

  expected.each do |path, page_entries|
    badges = rendered_scholar_badges(path, user)
    expected_ids = page_entries.map { |entry| entry[:google_scholar_id].to_s.strip }.sort
    assert_equal expected_ids.length, badges.length
    assert_equal expected_ids, badges.map { |badge| badge.fetch(:publication_id) }.sort

    badges.each do |badge|
      count = papers.fetch(badge.fetch(:key)).fetch("citations")
      assert_equal count.to_s, badge.fetch(:text)
      assert_equal "#{count} Google Scholar citations", badge.fetch(:aria)
    end
    all_badges.concat(badges)
  end

  assert_equal 9, all_badges.length
  assert_equal 5, all_badges.map { |badge| badge.fetch(:publication_id) }.uniq.length
end
```

- [ ] **Step 4: Prove the oracle rejects a controlled stale build**

First make a fresh build, copy it to a temporary site, and corrupt one rendered count. This is deterministic in a clean checkout and does not depend on whatever happens to be in the local `_site` directory:

```bash
docker build --tag mtics-al-folio:ci .
printf 'time: "%s"\n' "$(git show -s --format=%cI HEAD)" > .jekyll-reproducible.yml
docker run --rm --user "$(id -u):$(id -g)" --env HOME=/tmp --volume "$PWD:/srv/jekyll" --workdir /srv/jekyll mtics-al-folio:ci bundle exec jekyll build --config _config.yml,.jekyll-reproducible.yml
stale_site="$(mktemp -d)"
trap 'rm -rf "$stale_site"' EXIT
cp -R _site/. "$stale_site/"
perl -0pi -e 's/(scholar-citation-count">)[^<]+/${1}999999/' "$stale_site/index.html"
set +e
stale_output="$(docker run --rm --user "$(id -u):$(id -g)" --env HOME=/tmp --env SITE_DIR=/stale-site --volume "$PWD:/srv/jekyll:ro" --volume "$stale_site:/stale-site:ro" --workdir /srv/jekyll mtics-al-folio:ci bundle exec ruby test/frontend_theme_accessibility_contract_20260712_test.rb --name '/fresh_pages_render_exact_scholar_counts_links_and_labels/' 2>&1)"
stale_status=$?
set -e
if [[ "$stale_status" -eq 0 ]]; then
  rm -rf "$stale_site"
  echo "The Scholar badge contract accepted a controlled stale count." >&2
  exit 1
fi
if ! printf '%s\n' "$stale_output" | rg -q 'Actual: "999999"'; then
  printf '%s\n' "$stale_output" >&2
  echo "The negative control failed for a reason other than the stale count." >&2
  exit 1
fi
rm -rf "$stale_site"
trap - EXIT
```

Expected: the focused test fails specifically with `Actual: "999999"`, proving the exact-count oracle rejected the controlled stale badge; unrelated runtime failures do not count as success.

- [ ] **Step 5: Verify GREEN against the fresh production build**

Run:

```bash
docker run --rm --user "$(id -u):$(id -g)" --env HOME=/tmp --volume "$PWD:/srv/jekyll:ro" --workdir /srv/jekyll mtics-al-folio:ci bundle exec ruby test/frontend_theme_accessibility_contract_20260712_test.rb
git diff --check
```

Expected: the full frontend contract passes against the freshly generated `_site`, proving four home badges, five publication badges, nine instances, five unique IDs, and exact numbers, links, and labels.

- [ ] **Step 6: Commit the cross-layer contract**

```bash
git add test/frontend_theme_accessibility_contract_20260712_test.rb
git commit -m "test: verify rendered Scholar citation badges"
```

## Task 6: Run affected local checks and require the complete PR gates

**Files:**

- Verify only; do not add generated `_site`, browser artifacts, PDFs, or local caches to the commit.

- [ ] **Step 1: Verify the updater in the pinned Python environment**

The delivery image intentionally omits citation-fetch dependencies, and the local `uv` catalog does not provide Python 3.13.14. Run the secret-facing updater in an ephemeral, digest-pinned Python 3.13.14 container instead of depending on the host interpreter:

```bash
citation_python_image="python:3.13.14-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280"
docker run --rm --env PYTHONPYCACHEPREFIX=/tmp/pycache --volume "$PWD:/workspace:ro" --workdir /workspace "$citation_python_image" sh -c '
  set -eu
  python -m pip install --require-hashes -r requirements-citations.txt
  python test/citation_updater_contract_test.py
  python -m py_compile bin/update_scholar_citations.py test/citation_updater_contract_test.py
  python -m pip check
'
```

Expected: the updater suite passes under the same Python version used by Actions, and the hash-locked environment is internally consistent.

- [ ] **Step 2: Run the delivery image's actual non-browser gates**

Do not add `requirements-citations.txt` to the delivery image merely to make updater tests import there. Build the existing image and run only gates whose dependencies it intentionally contains:

```bash
docker build --tag mtics-al-folio:ci .
docker run --rm --user "$(id -u):$(id -g)" --env HOME=/tmp --volume "$PWD:/srv/jekyll" --workdir /srv/jekyll mtics-al-folio:ci bash -lc '
  set -euo pipefail
  ./bin/dependency_audit
  ruby test/release_contract_test.rb
  python3 test/trivy_report_contract_test.py
  bundle exec ruby test/cache_bust_contract_test.rb
  bundle exec ruby test/cv_schema_rendering_contract_test.rb
  for rendercv_input in _data/cv.yml test/fixtures/cv_rendercv_*.yml; do
    rendercv_output="$(mktemp -d)"
    rendercv render "$rendercv_input" --output-folder "$rendercv_output" --dont-generate-markdown --dont-generate-png --quiet
    find "$rendercv_output" -maxdepth 1 -type f -name "*.pdf" -size +0c -print -quit | grep -q .
    rm -rf "$rendercv_output"
  done
  bundle exec ruby test/accessibility_social_contract_test.rb
  python3 test/pdf_contract_test.py
  bundle exec al-folio upgrade overrides audit --fail-on-stale
  bundle exec jekyll build --config _config.yml,.jekyll-reproducible.yml
  node --test test/search_contract_test.mjs
  python3 test/accessibility_contract_test.py
  python3 test/minimal_runtime_contract_test.py
  bundle exec ruby test/frontend_theme_accessibility_contract_20260712_test.rb
'
```

Expected: dependency audit, release/workflow YAML contracts, Trivy report contracts, RenderCV/PDF, override audit, Jekyll, accessibility, runtime, search, and fresh-page contracts all pass.

- [ ] **Step 3: Run both browser suites against the fresh build**

```bash
browser_artifacts="$(mktemp -d)"
docker run --rm --user "$(id -u):$(id -g)" --env HOME=/tmp --env CHROME_EXECUTABLE=/usr/bin/chromium --env A11Y_ARTIFACT_DIR=/a11y-artifacts --volume "$browser_artifacts:/a11y-artifacts" --volume "$PWD:/srv/jekyll:ro" --workdir /srv/jekyll mtics-al-folio:ci bash -lc '
  set -euo pipefail
  python3 -m http.server 8091 --directory _site >/tmp/site-server.log 2>&1 &
  site_pid=$!
  cleanup() { kill "$site_pid" 2>/dev/null || true; wait "$site_pid" 2>/dev/null || true; }
  trap cleanup EXIT
  ready=false
  for _attempt in {1..50}; do
    if ! kill -0 "$site_pid" 2>/dev/null; then cat /tmp/site-server.log >&2; exit 1; fi
    if (exec 3<>/dev/tcp/127.0.0.1/8091) 2>/dev/null; then ready=true; break; fi
    sleep 0.1
  done
  if [[ "$ready" != true ]]; then cat /tmp/site-server.log >&2; exit 1; fi
  python3 test/accessibility_browser_test.py
  python3 test/frontend_theme_browser_interactions_20260712_test.py
'
rm -rf "$browser_artifacts"
```

Expected: keyboard, interaction, responsive, same-origin resource, and Axe checks pass with pinned Chromium.

- [ ] **Step 4: Request adversarial review before publishing**

Use `superpowers:requesting-code-review` against the complete branch diff. Resolve every confirmed P1, P2, or P3 issue with a new red-green commit, then repeat Steps 1 through 3.

- [ ] **Step 5: Confirm repository hygiene and workflow syntax evidence**

```bash
git diff --check
git log --oneline main..HEAD
git diff --stat main...HEAD
git status --short
rg -n 'google-search-results|from serpapi import GoogleSearch' requirements-citations.in requirements-citations.txt bin test .github || true
```

Expected: focused commits, no legacy client references, no untracked scratch files, and a clean worktree. `test/release_contract_test.rb` must have parsed both workflow YAML files and passed the static schedule, permissions, expression, and handoff contracts.

- [ ] **Step 6: Require the complete pull-request validate job**

The local checks above intentionally do not recreate the frozen Trivy database/provenance pipeline or the devcontainer smoke environment. The GitHub pull-request `validate` job is the authoritative full gate and must pass its delivery/development image builds, frozen Trivy scans and baseline enforcement, devcontainer smoke, dependency audit, RenderCV/PDF, override audit, Jekyll, accessibility, browser, and artifact checks before merge.

## Task 7: Publish, merge with approval, and verify production

**Files:**

- No source changes expected unless production evidence reveals a defect.

- [ ] **Step 1: Push the feature branch and open a pull request**

```bash
git push -u origin codex/scholar-citations-daily-hardening
gh pr create --base main --head codex/scholar-citations-daily-hardening --title "Harden daily Google Scholar citation updates" --body-file docs/superpowers/specs/2026-07-13-scholar-citations-daily-hardening-design.md
```

- [ ] **Step 2: Wait for all pull-request checks**

```bash
gh pr checks codex/scholar-citations-daily-hardening --watch
```

Expected: all required checks pass.

- [ ] **Step 3: Stop for explicit merge approval**

Report the pull-request URL, local verification evidence, and any operational caveats. Do not merge or delete the branch until the user explicitly approves those state changes.

- [ ] **Step 4: Merge after approval and confirm the default branch**

After approval, merge using the repository's accepted strategy, update local `main`, and verify the workflow on the merged revision:

```bash
git switch main
git pull --ff-only origin main
git log -1 --oneline
gh workflow view update_scholar_citations.yml --yaml | rg '17 8 \* \* \*|timeout-minutes: (15|5)'
```

- [ ] **Step 5: Dispatch the production citation workflow**

```bash
rollout_state="${TMPDIR:-/tmp}/mtics-scholar-rollout.env"
previous_manual_id="$(gh run list --workflow update_scholar_citations.yml --branch main --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId // 0')"
previous_deploy_id="$(gh run list --workflow deploy.yml --branch main --event workflow_run --limit 1 --json databaseId --jq '.[0].databaseId // 0')"
gh workflow run update_scholar_citations.yml --ref main
manual_run_id=""
for attempt in {1..30}; do
  manual_run_id="$(gh run list --workflow update_scholar_citations.yml --branch main --event workflow_dispatch --limit 10 --json databaseId --jq "map(select(.databaseId > $previous_manual_id)) | .[0].databaseId // empty")"
  [[ -n "$manual_run_id" ]] && break
  sleep 2
done
if [[ -z "$manual_run_id" ]]; then
  echo "The newly dispatched Scholar workflow did not appear in the run list." >&2
  exit 1
fi
gh run watch "$manual_run_id" --exit-status
gh run view "$manual_run_id" --json conclusion,jobs,url
manual_completed_at="$(gh run view "$manual_run_id" --json updatedAt --jq '.updatedAt')"
printf 'manual_run_id=%s\nmanual_completed_at=%s\nprevious_deploy_id=%s\n' "$manual_run_id" "$manual_completed_at" "$previous_deploy_id" > "$rollout_state"
```

Expected: the selected run ID is strictly newer than the pre-dispatch run, and both `update` and `commit` complete successfully. A no-op commit is valid when counts are unchanged.

- [ ] **Step 6: Verify the downstream deployment handoff**

```bash
rollout_state="${TMPDIR:-/tmp}/mtics-scholar-rollout.env"
source "$rollout_state"
deploy_run_id=""
for attempt in {1..60}; do
  deploy_run_id="$(gh run list --workflow deploy.yml --branch main --event workflow_run --limit 10 --json databaseId,createdAt --jq "map(select(.databaseId > $previous_deploy_id and .createdAt >= \"$manual_completed_at\")) | .[0].databaseId // empty")"
  [[ -n "$deploy_run_id" ]] && break
  sleep 5
done
if [[ -z "$deploy_run_id" ]]; then
  echo "No new Deploy site workflow_run appeared after the manual Scholar run completed." >&2
  exit 1
fi
gh run watch "$deploy_run_id" --exit-status
gh run view "$deploy_run_id" --json conclusion,createdAt,event,jobs,url
test "$(gh run view "$deploy_run_id" --json event --jq '.event')" = "workflow_run"
deploy_created_at="$(gh run view "$deploy_run_id" --json createdAt --jq '.createdAt')"
[[ "$deploy_created_at" > "$manual_completed_at" || "$deploy_created_at" == "$manual_completed_at" ]]
```

Expected: a strictly newer `Deploy site` run has event `workflow_run`, was created at or after the manual Scholar run completed, checks out current `main`, passes all gates, and deploys Pages. The static workflow contract proves that `Update Scholar Citations` is the only configured `workflow_run` source; the ID and timestamp bounds prevent an older successful deployment from being mistaken for rollout evidence without assuming that run-level head SHAs remain equal after the commit job creates a citation commit.

- [ ] **Step 7: Verify the next real scheduled fetch**

Before the next `08:17 UTC` trigger, record the newest existing scheduled run. After the trigger, wait for a strictly newer run and inspect its updater log:

```bash
previous_scheduled_id="$(gh run list --workflow update_scholar_citations.yml --branch main --event schedule --limit 1 --json databaseId --jq '.[0].databaseId // 0')"
scheduled_run_id=""
for attempt in {1..120}; do
  scheduled_run_id="$(gh run list --workflow update_scholar_citations.yml --branch main --event schedule --limit 10 --json databaseId --jq "map(select(.databaseId > $previous_scheduled_id)) | .[0].databaseId // empty")"
  [[ -n "$scheduled_run_id" ]] && break
  sleep 30
done
if [[ -z "$scheduled_run_id" ]]; then
  echo "No new scheduled Scholar run appeared in the polling window." >&2
  exit 1
fi
gh run watch "$scheduled_run_id" --exit-status
gh run view "$scheduled_run_id" --log | rg 'Fetching SerpApi google_scholar_author|Got [0-9]+ articles|No changes in citation counts|Wrote _data/citations.yml'
```

Expected: the scheduled run exits successfully and contains real fetch evidence rather than only the same-UTC-day skip message.

- [ ] **Step 8: Compare live badges with committed data**

Fetch the live home and publications pages after deployment and verify the same four/five badge cardinality and exact counts asserted by the fresh-build test. If live HTML differs from `main`, treat it as a deployment failure and preserve the feature branch until diagnosed.

- [ ] **Step 9: Delete the feature branch only after production verification**

After the manual run, downstream deploy, live badge comparison, and next scheduled fetch all pass, delete the merged local and remote feature branch.

## Completion Evidence

The implementation is complete only when all of the following evidence exists:

- the legacy dependency and client are absent;
- the updater tests prove timeout and HTTP classification, bounded attempts, per-page budgets, parameter secrecy, UTC dates, deletion refusal, no-op preservation, and atomic rollback;
- the release contracts prove `08:17 UTC`, manual dispatch, fixed concurrency, job timeouts, least privilege, artifact isolation, conflict-safe pushes, and the guarded deployment handoff;
- the fresh-build contracts prove five exact source keys and four/home plus five/publications rendered badges with matching counts, links, and accessible labels;
- local and pull-request gates pass;
- the manually dispatched production run and downstream Pages deployment pass;
- the next scheduled run performs a real SerpApi fetch successfully.
