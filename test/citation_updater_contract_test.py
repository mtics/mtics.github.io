#!/usr/bin/env python3
"""Fail-closed contracts for the SerpApi citation updater."""

from __future__ import annotations

from collections import UserDict
from contextlib import redirect_stderr
import importlib.util
import io
import os
from pathlib import Path
import runpy
import stat
import tempfile
import unittest
from unittest import mock

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "bin" / "update_scholar_citations.py"
SPEC = importlib.util.spec_from_file_location("citation_updater_under_test", UPDATER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {UPDATER_PATH}")
UPDATER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATER)


class _SearchClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.params: list[dict[str, object]] = []

    def search(self, params: dict[str, object]) -> object:
        self.params.append(dict(params))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _MutatingSearchClient:
    def __init__(self) -> None:
        self.params_at_entry: list[dict[str, object]] = []

    def search(self, params: dict[str, object]) -> object:
        self.params_at_entry.append(dict(params))
        params["api_key"] = "injected-by-client"
        if len(self.params_at_entry) == 1:
            raise UPDATER.serpapi.TimeoutError("transient timeout")
        return {"articles": []}


def _http_error(status_code: int, detail: str = "request failed") -> BaseException:
    error = UPDATER.serpapi.HTTPError(Exception(detail))
    error.status_code = status_code
    error.error = detail
    return error


class CitationUpdaterContractTest(unittest.TestCase):
    def test_unexpected_error_is_redacted_without_retry_or_file_changes(
        self,
    ) -> None:
        secret = "sentinel-unexpected-api-key"
        request_url = f"https://serpapi.com/search?api_key={secret}"
        remote_body = "sentinel-unexpected-remote-body"
        client = _SearchClient([RuntimeError(f"{request_url} {remote_body}")])
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_directory = root / "_data"
            data_directory.mkdir()
            (data_directory / "socials.yml").write_text(
                "scholar_userid: scholar-id\n",
                encoding="utf-8",
            )
            output = data_directory / "citations.yml"
            output.write_text(
                yaml.safe_dump(existing, sort_keys=True),
                encoding="utf-8",
            )
            before = output.read_bytes()
            previous_directory = Path.cwd()

            try:
                os.chdir(root)
                with (
                    mock.patch.dict(
                        os.environ,
                        {"SERPAPI_API_KEY": secret},
                        clear=True,
                    ),
                    mock.patch.object(
                        UPDATER.serpapi,
                        "Client",
                        return_value=client,
                    ),
                    self.assertRaises(SystemExit) as raised,
                ):
                    runpy.run_path(str(UPDATER_PATH), run_name="__main__")
            finally:
                os.chdir(previous_directory)

            message = str(raised.exception)
            self.assertEqual(
                "Unexpected error while updating citations.",
                message,
            )
            for sentinel in (request_url, secret, remote_body):
                self.assertNotIn(sentinel, message)
            self.assertEqual(1, len(client.params))
            self.assertEqual(before, output.read_bytes())
            self.assertEqual([], self._citation_temp_files(output))

    def test_socials_root_must_be_an_object(self) -> None:
        for name, root in (("list", []), ("scalar", "not-an-object")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                self._prepare_files(Path(directory), {})
                UPDATER.SOCIALS_FILE.write_text(
                    yaml.safe_dump(root),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(SystemExit, "socials.*root.*object"):
                    UPDATER.load_scholar_user_id()

    def test_scholar_user_id_must_be_a_nonempty_string_and_is_trimmed(self) -> None:
        invalid_values = (None, "", "   ", 7, True, [], {})
        for value in invalid_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                self._prepare_files(Path(directory), {})
                UPDATER.SOCIALS_FILE.write_text(
                    yaml.safe_dump({"scholar_userid": value}),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(SystemExit, "scholar_userid.*non-empty string"):
                    UPDATER.load_scholar_user_id()

        with tempfile.TemporaryDirectory() as directory:
            self._prepare_files(Path(directory), {})
            UPDATER.SOCIALS_FILE.write_text(
                yaml.safe_dump({"scholar_userid": "  scholar-id  "}),
                encoding="utf-8",
            )
            self.assertEqual("scholar-id", UPDATER.load_scholar_user_id())

    def test_non_object_payload_fails_closed(self) -> None:
        client = _SearchClient([[]])
        sleep = mock.Mock()

        with self.assertRaisesRegex(SystemExit, "object payload"):
            UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        sleep.assert_not_called()
        self.assertEqual(1, len(client.params))

    def test_timeout_retries_once_then_returns_the_page(self) -> None:
        secret = "sentinel-timeout-api-key"
        client = _SearchClient(
            [
                UPDATER.serpapi.TimeoutError(f"request URL contained {secret}"),
                {"articles": []},
            ]
        )
        sleep = mock.Mock()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            articles = UPDATER.fetch_author_articles(
                "scholar-id",
                client,
                sleep=sleep,
            )

        self.assertEqual([], articles)
        self.assertEqual([mock.call(1)], sleep.call_args_list)
        self.assertEqual(2, len(client.params))
        self.assertNotIn(secret, stderr.getvalue())

    def test_server_errors_use_one_and_two_second_backoff(self) -> None:
        secret = "sentinel-http-api-key"
        client = _SearchClient(
            [
                _http_error(500, f"request URL contained {secret}"),
                _http_error(503, f"remote response contained {secret}"),
                {"articles": []},
            ]
        )
        sleep = mock.Mock()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            articles = UPDATER.fetch_author_articles(
                "scholar-id",
                client,
                sleep=sleep,
            )

        self.assertEqual([], articles)
        self.assertEqual([mock.call(1), mock.call(2)], sleep.call_args_list)
        self.assertEqual(3, len(client.params))
        self.assertNotIn(secret, stderr.getvalue())

    def test_real_client_retries_wrapped_connect_timeouts(self) -> None:
        secret = "sentinel-connect-timeout-api-key"
        request_url = f"https://serpapi.example/search?api_key={secret}"
        wrapping_client = UPDATER.serpapi.Client(api_key=secret, timeout=15)
        wrapping_client.session.request = mock.Mock(
            side_effect=requests.ConnectTimeout(request_url)
        )

        with self.assertRaises(UPDATER.serpapi.HTTPConnectionError) as wrapped:
            wrapping_client.search({"engine": "google_scholar_author"})

        self.assertIsInstance(wrapped.exception.__context__, requests.ConnectTimeout)

        response = requests.Response()
        response.status_code = 200
        response._content = b'{"articles": []}'
        client = UPDATER.serpapi.Client(api_key=secret, timeout=15)
        client.session.request = mock.Mock(
            side_effect=[
                requests.ConnectTimeout(request_url),
                requests.ConnectTimeout(request_url),
                response,
            ]
        )
        sleep = mock.Mock()
        stderr = io.StringIO()

        try:
            with redirect_stderr(stderr):
                articles = UPDATER.fetch_author_articles(
                    "scholar-id",
                    client,
                    sleep=sleep,
                )
        except SystemExit as exc:
            self.fail(f"wrapped connect timeout was not retried: {exc}")

        self.assertEqual([], articles)
        self.assertEqual([mock.call(1), mock.call(2)], sleep.call_args_list)
        self.assertEqual(3, client.session.request.call_count)
        self.assertEqual(
            "Transient SerpApi timeout; retrying in 1s (1/3).\n"
            "Transient SerpApi timeout; retrying in 2s (2/3).\n",
            stderr.getvalue(),
        )
        self.assertNotIn(secret, stderr.getvalue())
        self.assertNotIn(request_url, stderr.getvalue())

    def test_real_client_connection_error_fails_without_retry(self) -> None:
        secret = "sentinel-connection-error-api-key"
        request_url = f"https://serpapi.example/search?api_key={secret}"
        client = UPDATER.serpapi.Client(api_key=secret, timeout=15)
        client.session.request = mock.Mock(
            side_effect=requests.ConnectionError(request_url)
        )
        sleep = mock.Mock()
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        message = str(raised.exception)
        self.assertEqual(
            "SerpApi request failed permanently with HTTP -1 at start=0.",
            message,
        )
        self.assertNotIn(secret, message)
        self.assertNotIn(request_url, message)
        self.assertNotIn(secret, stderr.getvalue())
        sleep.assert_not_called()
        self.assertEqual(1, client.session.request.call_count)

    def test_three_timeouts_exhaust_the_page_budget(self) -> None:
        secret = "sentinel-timeout-api-key"
        client = _SearchClient(
            [
                UPDATER.serpapi.TimeoutError(f"request URL contained {secret}"),
                UPDATER.serpapi.TimeoutError(f"remote response contained {secret}"),
                UPDATER.serpapi.TimeoutError(f"request detail contained {secret}"),
            ]
        )
        sleep = mock.Mock()
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        message = str(raised.exception)
        self.assertEqual(
            "SerpApi request failed after 3 attempts at start=0 (timeout).",
            message,
        )
        self.assertNotIn(secret, message)
        self.assertNotIn(secret, stderr.getvalue())
        self.assertEqual([mock.call(1), mock.call(2)], sleep.call_args_list)
        self.assertEqual(3, len(client.params))

    def test_permanent_http_errors_fail_without_retry(self) -> None:
        secret = "sentinel-permanent-http-api-key"
        for status_code in (400, 401, 429, -1):
            with self.subTest(status_code=status_code):
                client = _SearchClient(
                    [_http_error(status_code, f"request URL contained {secret}")]
                )
                sleep = mock.Mock()

                with self.assertRaises(SystemExit) as raised:
                    UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

                message = str(raised.exception)
                self.assertEqual(
                    "SerpApi request failed permanently "
                    f"with HTTP {status_code} at start=0.",
                    message,
                )
                self.assertNotIn(secret, message)
                sleep.assert_not_called()
                self.assertEqual(1, len(client.params))

    def test_error_payload_redacts_remote_text_without_retry(self) -> None:
        secret = "sentinel-remote-api-key"
        client = _SearchClient([{"error": f"remote error with {secret}"}])
        sleep = mock.Mock()

        with self.assertRaises(SystemExit) as raised:
            UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        message = str(raised.exception)
        self.assertEqual("SerpApi returned an error payload at start=0.", message)
        self.assertNotIn(secret, message)
        sleep.assert_not_called()
        self.assertEqual(1, len(client.params))

    def test_missing_articles_key_fails_closed(self) -> None:
        client = _SearchClient([{}])
        sleep = mock.Mock()

        with self.assertRaisesRegex(SystemExit, "articles"):
            UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        sleep.assert_not_called()
        self.assertEqual(1, len(client.params))

    def test_articles_with_the_wrong_type_fails_closed(self) -> None:
        payload = {"articles": "not-a-list"}
        client = _SearchClient([payload])
        sleep = mock.Mock()

        with self.assertRaisesRegex(SystemExit, "list"):
            UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        sleep.assert_not_called()
        self.assertEqual(1, len(client.params))

    def test_non_object_article_fails_closed(self) -> None:
        payload = {"articles": ["not-an-object"]}
        client = _SearchClient([payload])
        sleep = mock.Mock()

        with self.assertRaisesRegex(SystemExit, "article.*object"):
            UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        sleep.assert_not_called()
        self.assertEqual(1, len(client.params))

    def test_search_uses_a_copy_without_an_api_key(self) -> None:
        params: dict[str, object] = {
            "engine": "google_scholar_author",
            "author_id": "scholar-id",
            "num": 100,
            "start": 0,
        }
        original = dict(params)
        client = _MutatingSearchClient()
        sleep = mock.Mock()

        UPDATER.search_page_with_retry(client, params, sleep=sleep)

        self.assertEqual(original, params)
        self.assertEqual(2, len(client.params_at_entry))
        for received in client.params_at_entry:
            self.assertNotIn("api_key", received)
        self.assertEqual([mock.call(1)], sleep.call_args_list)

    def test_non_dict_mapping_response_and_article_are_accepted(self) -> None:
        article = UserDict({"citation_id": "scholar-id:paper"})
        client = _SearchClient([UserDict({"articles": [article]})])
        sleep = mock.Mock()

        articles = UPDATER.fetch_author_articles("scholar-id", client, sleep=sleep)

        self.assertEqual([article], articles)
        sleep.assert_not_called()
        self.assertEqual(1, len(client.params))

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
        self.assertEqual(
            [0, 0, 0, 100, 100, 100],
            [item["start"] for item in client.params],
        )

    def test_main_constructs_one_client_and_passes_it_to_pagination(self) -> None:
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Paper",
            "year": "2025",
            "cited_by": {"value": 1},
        }
        client = mock.sentinel.search_client
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.unlink()
            with (
                mock.patch.dict(
                    os.environ,
                    {"SERPAPI_API_KEY": "api-key"},
                    clear=True,
                ),
                mock.patch.object(
                    UPDATER.serpapi,
                    "Client",
                    return_value=client,
                ) as client_constructor,
                mock.patch.object(
                    UPDATER,
                    "fetch_author_articles",
                    return_value=[article],
                ) as fetch_author_articles,
            ):
                UPDATER.main()

            client_constructor.assert_called_once_with(api_key="api-key", timeout=15)
            fetch_author_articles.assert_called_once_with("scholar-id", client)
            self.assertTrue(output.exists())

    def test_citation_id_must_be_a_nonempty_string(self) -> None:
        for name, citation_id in (
            ("missing", None),
            ("empty", ""),
            ("number", 7),
            ("container", []),
        ):
            article = {
                "citation_id": citation_id,
                "title": "Paper",
                "year": "2025",
                "cited_by": {"value": 1},
            }
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                output = self._prepare_files(Path(directory), {})
                output.unlink()

                with self.assertRaisesRegex(SystemExit, "citation_id.*non-empty string"):
                    self._run_main(output, [article])

                self.assertFalse(output.exists())

    def test_citation_id_must_belong_to_the_current_scholar(self) -> None:
        for name, citation_id in (
            ("other-scholar", "other-scholar:paper"),
            ("missing-separator", "scholar-id-paper"),
            ("missing-publication", "scholar-id:"),
        ):
            article = {
                "citation_id": citation_id,
                "title": "Paper",
                "year": "2025",
                "cited_by": {"value": 1},
            }
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                output = self._prepare_files(Path(directory), {})
                output.unlink()

                with self.assertRaisesRegex(SystemExit, "citation_id.*scholar prefix"):
                    self._run_main(output, [article])

                self.assertFalse(output.exists())

    def test_title_and_year_reject_non_string_values(self) -> None:
        invalid_values = (7, True, ["not", "scalar"], {"not": "scalar"})
        for field in ("title", "year"):
            for value in invalid_values:
                article = {
                    "citation_id": "scholar-id:paper",
                    "title": "Paper",
                    "year": "2025",
                    "cited_by": {"value": 1},
                    field: value,
                }
                with (
                    self.subTest(field=field, value=value),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    output = self._prepare_files(Path(directory), {})
                    output.unlink()

                    with self.assertRaisesRegex(SystemExit, f"{field}.*string"):
                        self._run_main(output, [article])

                    self.assertFalse(output.exists())

    def test_missing_null_and_blank_title_year_are_serialized_as_blank_strings(self) -> None:
        articles = [
            {
                "citation_id": "scholar-id:missing",
                "cited_by": {"value": 1},
            },
            {
                "citation_id": "scholar-id:null",
                "title": None,
                "year": None,
                "cited_by": {"value": 1},
            },
            {
                "citation_id": "scholar-id:blank",
                "title": "",
                "year": "",
                "cited_by": {"value": 1},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.unlink()
            self._run_main(output, articles)

            papers = yaml.safe_load(output.read_text(encoding="utf-8"))["papers"]
            for paper in papers.values():
                self.assertEqual("", paper["title"])
                self.assertEqual("", paper["year"])

    def test_cited_by_must_be_a_mapping(self) -> None:
        for name, cited_by in (
            ("string", "1"),
            ("number", 1),
            ("sequence", []),
        ):
            article = {
                "citation_id": "scholar-id:paper",
                "title": "Paper",
                "year": "2025",
                "cited_by": cited_by,
            }
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                output = self._prepare_files(Path(directory), {})
                output.unlink()

                with self.assertRaisesRegex(SystemExit, "cited_by.*mapping"):
                    self._run_main(output, [article])

                self.assertFalse(output.exists())

    def test_missing_and_null_cited_by_are_valid_zero_citations(self) -> None:
        articles = [
            {
                "citation_id": "scholar-id:missing-cited-by",
                "title": "Missing cited-by",
                "year": "2025",
            },
            {
                "citation_id": "scholar-id:null-cited-by",
                "title": "Null cited-by",
                "year": "2025",
                "cited_by": None,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.unlink()
            self._run_main(output, articles)

            papers = yaml.safe_load(output.read_text(encoding="utf-8"))["papers"]
            self.assertEqual(0, papers["scholar-id:missing-cited-by"]["citations"])
            self.assertEqual(0, papers["scholar-id:null-cited-by"]["citations"])

    def test_first_run_empty_result_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.unlink()

            with self.assertRaisesRegex(SystemExit, "empty"):
                self._run_main(output, [])

            self.assertFalse(output.exists())

    def test_first_run_with_no_usable_records_fails_closed(self) -> None:
        articles = [
            {
                "title": "Missing citation identifier",
                "year": "2025",
                "cited_by": {"value": 1},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.unlink()

            with self.assertRaisesRegex(SystemExit, "usable|citation_id"):
                self._run_main(output, articles)

            self.assertFalse(output.exists())

    def test_duplicate_citation_ids_fail_closed(self) -> None:
        articles = [
            {
                "citation_id": "scholar-id:duplicate",
                "title": "First record",
                "year": "2024",
                "cited_by": {"value": 1},
            },
            {
                "citation_id": "scholar-id:duplicate",
                "title": "Second record",
                "year": "2025",
                "cited_by": {"value": 2},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.unlink()

            with self.assertRaisesRegex(SystemExit, "[Dd]uplicate citation_id"):
                self._run_main(output, articles)

            self.assertFalse(output.exists())

    def test_empty_result_cannot_erase_existing_papers(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper-id": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            with self.assertRaisesRegex(SystemExit, "empty"):
                self._run_main(output, [])

            self.assertEqual(existing, yaml.safe_load(output.read_text(encoding="utf-8")))

    def test_missing_remote_keys_cannot_delete_papers_without_explicit_authorization(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:keep": {"title": "Keep", "year": "2024", "citations": 7},
                "scholar-id:missing": {"title": "Missing", "year": "2023", "citations": 3},
            },
        }
        articles = [
            {
                "citation_id": "scholar-id:keep",
                "title": "Keep",
                "year": "2024",
                "cited_by": {"value": 8},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            with self.assertRaisesRegex(SystemExit, "delet.*scholar-id:missing"):
                self._run_main(output, articles)

            self.assertEqual(existing, yaml.safe_load(output.read_text(encoding="utf-8")))

    def test_invalid_citation_count_fails_closed_instead_of_becoming_zero(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:keep": {"title": "Keep", "year": "2024", "citations": 7}
            },
        }
        articles = [
            {
                "citation_id": "scholar-id:keep",
                "title": "Keep",
                "year": "2024",
                "cited_by": {"value": "not-a-number"},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            with self.assertRaisesRegex(SystemExit, "citation.*integer"):
                self._run_main(output, articles)

            self.assertEqual(existing, yaml.safe_load(output.read_text(encoding="utf-8")))

    def test_negative_citation_counts_fail_closed(self) -> None:
        for citations in (-1, "-2", -0.5):
            article = {
                "citation_id": "scholar-id:paper",
                "title": "Paper",
                "year": "2025",
                "cited_by": {"value": citations},
            }
            with (
                self.subTest(citations=citations),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = self._prepare_files(Path(directory), {})
                output.unlink()

                with self.assertRaisesRegex(SystemExit, "citation.*non-negative"):
                    self._run_main(output, [article])

                self.assertFalse(output.exists())

    def test_zero_citations_remains_a_valid_integer_zero(self) -> None:
        article = {
            "citation_id": "scholar-id:uncited",
            "title": "Uncited paper",
            "year": "2025",
            "cited_by": {"value": 0},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.unlink()
            self._run_main(output, [article])

            papers = yaml.safe_load(output.read_text(encoding="utf-8"))["papers"]
            self.assertEqual(0, papers["scholar-id:uncited"]["citations"])
            self.assertIsInstance(papers["scholar-id:uncited"]["citations"], int)

    def test_explicit_authorization_allows_a_missing_key_to_be_deleted(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:keep": {"title": "Keep", "year": "2024", "citations": 7},
                "scholar-id:remove": {"title": "Remove", "year": "2023", "citations": 3},
            },
        }
        articles = [
            {
                "citation_id": "scholar-id:keep",
                "title": "Keep",
                "year": "2024",
                "cited_by": {"value": 8},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            self._run_main(
                output,
                articles,
                **{UPDATER.ALLOW_KEY_DELETION_ENV: "1"},
            )

            updated = yaml.safe_load(output.read_text(encoding="utf-8"))
            self.assertEqual(["scholar-id:keep"], list(updated["papers"]))
            self.assertEqual(8, updated["papers"]["scholar-id:keep"]["citations"])

    def test_malformed_existing_yaml_fails_closed_without_overwrite(self) -> None:
        malformed = "metadata: [unterminated\n"
        articles = [
            {
                "citation_id": "scholar-id:new",
                "title": "New",
                "year": "2025",
                "cited_by": {"value": 1},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), {})
            output.write_text(malformed, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "parsing.*citations"):
                self._run_main(output, articles)

            self.assertEqual(malformed, output.read_text(encoding="utf-8"))

    def test_replace_failure_preserves_existing_bytes_and_cleans_temp_file(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Existing paper",
            "year": "2024",
            "cited_by": {"value": 8},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            before = output.read_bytes()

            with (
                mock.patch.object(
                    UPDATER.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
                self.assertRaisesRegex(
                    SystemExit,
                    "Error writing citations atomically: replace failed",
                ),
            ):
                self._run_main(output, [article])

            self.assertEqual(before, output.read_bytes())
            self.assertEqual([], self._citation_temp_files(output))

    def test_temp_cleanup_failure_reports_primary_and_cleanup_errors(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Existing paper",
            "year": "2024",
            "cited_by": {"value": 8},
        }
        real_unlink = Path.unlink
        captured_temp_paths: list[Path] = []

        def capture_and_fail_unlink(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> None:
            captured_temp_paths.append(path)
            raise OSError("unlink failed")

        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            before = output.read_bytes()

            try:
                with (
                    mock.patch.object(
                        UPDATER.os,
                        "replace",
                        side_effect=OSError("replace failed"),
                    ),
                    mock.patch.object(
                        UPDATER.Path,
                        "unlink",
                        autospec=True,
                        side_effect=capture_and_fail_unlink,
                    ) as unlink,
                    self.assertRaises(SystemExit) as raised,
                ):
                    self._run_main(output, [article])

                self.assertEqual(
                    "Error writing citations atomically: replace failed; "
                    "additionally failed to clean up temporary citation file: "
                    "unlink failed",
                    str(raised.exception),
                )
                unlink.assert_called_once()
                self.assertEqual(before, output.read_bytes())
                self.assertEqual(1, len(captured_temp_paths))
                self.assertTrue(captured_temp_paths[0].exists())
                self.assertEqual(
                    captured_temp_paths,
                    self._citation_temp_files(output),
                )
            finally:
                for temp_path in captured_temp_paths:
                    try:
                        real_unlink(temp_path)
                    except FileNotFoundError:
                        pass
                self.assertEqual([], self._citation_temp_files(output))

    def test_yaml_serialization_failure_preserves_existing_bytes(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Existing paper",
            "year": "2024",
            "cited_by": {"value": 8},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            before = output.read_bytes()

            with (
                mock.patch.object(
                    UPDATER.yaml,
                    "safe_dump",
                    side_effect=yaml.YAMLError("serialization failed"),
                ),
                self.assertRaisesRegex(
                    SystemExit,
                    "Error writing citations atomically: serialization failed",
                ),
            ):
                self._run_main(output, [article])

            self.assertEqual(before, output.read_bytes())
            self.assertEqual([], self._citation_temp_files(output))

    def test_fsync_failure_preserves_existing_bytes_and_cleans_temp_file(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Existing paper",
            "year": "2024",
            "cited_by": {"value": 8},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            before = output.read_bytes()

            with (
                mock.patch.object(
                    UPDATER.os,
                    "fsync",
                    side_effect=OSError("fsync failed"),
                ),
                self.assertRaisesRegex(
                    SystemExit,
                    "Error writing citations atomically: fsync failed",
                ),
            ):
                self._run_main(output, [article])

            self.assertEqual(before, output.read_bytes())
            self.assertEqual([], self._citation_temp_files(output))

    def test_write_failure_preserves_existing_bytes_and_cleans_real_temp_file(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Existing paper",
            "year": "2024",
            "cited_by": {"value": 8},
        }
        real_named_temporary_file = tempfile.NamedTemporaryFile
        created_temp_paths: list[Path] = []

        def create_write_failing_temp_file(*args: object, **kwargs: object) -> object:
            actual_temp_file = real_named_temporary_file(*args, **kwargs)
            created_temp_paths.append(Path(actual_temp_file.name))
            wrapper = mock.MagicMock(wraps=actual_temp_file)
            wrapper.name = actual_temp_file.name
            wrapper.__enter__.return_value = wrapper
            wrapper.__exit__.side_effect = actual_temp_file.__exit__
            wrapper.write.side_effect = OSError("write failed")
            return wrapper

        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            before = output.read_bytes()

            with (
                mock.patch.object(
                    UPDATER.tempfile,
                    "NamedTemporaryFile",
                    side_effect=create_write_failing_temp_file,
                ) as named_temporary_file,
                self.assertRaisesRegex(
                    SystemExit,
                    "Error writing citations atomically: write failed",
                ),
            ):
                self._run_main(output, [article])

            named_temporary_file.assert_called_once()
            self.assertEqual(1, len(created_temp_paths))
            self.assertFalse(created_temp_paths[0].exists())
            self.assertEqual(before, output.read_bytes())
            self.assertEqual([], self._citation_temp_files(output))

    def test_first_run_replace_failure_leaves_output_absent_and_cleans_temp_file(self) -> None:
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
                mock.patch.object(
                    UPDATER.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
                self.assertRaisesRegex(
                    SystemExit,
                    "Error writing citations atomically: replace failed",
                ),
            ):
                self._run_main(output, [article])

            self.assertFalse(output.exists())
            self.assertEqual([], self._citation_temp_files(output))

    def test_changed_data_is_fsynced_and_atomically_replaced_as_sorted_yaml(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:z-paper": {
                    "title": "Zed paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        articles = [
            {
                "citation_id": "scholar-id:z-paper",
                "title": "Zed paper",
                "year": "2024",
                "cited_by": {"value": 8},
            },
            {
                "citation_id": "scholar-id:a-paper",
                "title": "Alpha paper",
                "year": "2025",
                "cited_by": {"value": 1},
            },
        ]
        events: list[str] = []
        real_fchmod = os.fchmod
        real_fsync = os.fsync
        real_replace = os.replace

        def recording_fchmod(file_descriptor: int, mode: int) -> None:
            events.append("fchmod")
            real_fchmod(file_descriptor, mode)

        def recording_fsync(file_descriptor: int) -> None:
            descriptor_mode = os.fstat(file_descriptor).st_mode
            events.append(
                "directory fsync" if stat.S_ISDIR(descriptor_mode) else "file fsync"
            )
            real_fsync(file_descriptor)

        def recording_replace(source: object, destination: object) -> None:
            events.append("replace")
            real_replace(source, destination)

        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)

            with (
                mock.patch.object(
                    UPDATER.os,
                    "fchmod",
                    side_effect=recording_fchmod,
                ) as fchmod,
                mock.patch.object(
                    UPDATER.os,
                    "fsync",
                    side_effect=recording_fsync,
                ) as fsync,
                mock.patch.object(
                    UPDATER.os,
                    "replace",
                    side_effect=recording_replace,
                ) as replace,
            ):
                self._run_main(output, articles)

            fchmod.assert_called_once()
            self.assertEqual(0o644, fchmod.call_args.args[1])
            self.assertEqual(2, fsync.call_count)
            replace.assert_called_once()
            self.assertEqual(output, Path(replace.call_args.args[1]))
            self.assertEqual(
                ["fchmod", "file fsync", "replace", "directory fsync"],
                events,
            )
            serialized = output.read_text(encoding="utf-8")
            parsed = yaml.safe_load(serialized)
            self.assertEqual(
                yaml.safe_dump(
                    parsed,
                    width=1000,
                    sort_keys=True,
                    allow_unicode=True,
                ),
                serialized,
            )
            self.assertLess(
                serialized.index("scholar-id:a-paper"),
                serialized.index("scholar-id:z-paper"),
            )
            self.assertEqual(0o644, stat.S_IMODE(output.stat().st_mode))
            self.assertEqual([], self._citation_temp_files(output))

    def test_directory_fsync_failure_reports_uncertain_durability_after_replace(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Existing paper",
            "year": "2024",
            "cited_by": {"value": 8},
        }
        real_fsync = os.fsync
        real_close = os.close

        def fail_directory_fsync(file_descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(file_descriptor).st_mode):
                raise OSError("directory fsync failed")
            real_fsync(file_descriptor)

        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)

            with (
                mock.patch.object(
                    UPDATER.os,
                    "fsync",
                    side_effect=fail_directory_fsync,
                ),
                mock.patch.object(UPDATER.os, "close", wraps=real_close) as close,
                self.assertRaisesRegex(
                    SystemExit,
                    "Citation file was replaced, but directory durability could not "
                    "be confirmed: directory fsync failed",
                ),
            ):
                self._run_main(output, [article])

            close.assert_called_once()
            updated = yaml.safe_load(output.read_text(encoding="utf-8"))
            self.assertEqual(8, updated["papers"]["scholar-id:paper"]["citations"])
            self.assertEqual([], self._citation_temp_files(output))

    def test_unchanged_papers_do_not_replace_or_change_existing_bytes(self) -> None:
        existing = {
            "metadata": {"last_updated": "2000-01-01"},
            "papers": {
                "scholar-id:paper": {
                    "title": "Existing paper",
                    "year": "2024",
                    "citations": 7,
                }
            },
        }
        article = {
            "citation_id": "scholar-id:paper",
            "title": "Existing paper",
            "year": "2024",
            "cited_by": {"value": 7},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = self._prepare_files(Path(directory), existing)
            before = output.read_bytes()

            with mock.patch.object(UPDATER.os, "replace") as replace:
                self._run_main(output, [article])

            replace.assert_not_called()
            self.assertEqual(before, output.read_bytes())
            self.assertEqual(
                "2000-01-01",
                yaml.safe_load(output.read_text(encoding="utf-8"))["metadata"][
                    "last_updated"
                ],
            )
            self.assertEqual([], self._citation_temp_files(output))

    def test_existing_schema_is_validated_before_the_daily_skip(self) -> None:
        today = UPDATER.datetime.now(UPDATER.UTC).date().isoformat()
        existing_paper = {"paper": {"title": "Paper", "year": "2024", "citations": 1}}
        cases = [
            ("root", [], "root.*object"),
            ("metadata", {"metadata": [], "papers": existing_paper}, "metadata.*object"),
            ("papers", {"metadata": {"last_updated": today}, "papers": []}, "papers.*object"),
            ("today-empty", {"metadata": {"last_updated": today}, "papers": {}}, "empty.*today"),
        ]
        for name, existing, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                output = self._prepare_files(Path(directory), existing)
                before = output.read_text(encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, message):
                    self._run_main(output, [])
                self.assertEqual(before, output.read_text(encoding="utf-8"))

    def test_existing_paper_records_are_validated_before_the_daily_skip(self) -> None:
        today = UPDATER.datetime.now(UPDATER.UTC).date().isoformat()
        cases = [
            ("non-string-key", {7: {"citations": 1}}, "paper key.*non-empty string"),
            ("blank-key", {"   ": {"citations": 1}}, "paper key.*non-empty string"),
            ("non-object-record", {"paper": []}, "paper.*object"),
            ("missing-citations", {"paper": {"title": "Paper"}}, "citations.*non-negative integer"),
            ("negative-citations", {"paper": {"citations": -1}}, "citations.*non-negative integer"),
            ("string-citations", {"paper": {"citations": "1"}}, "citations.*non-negative integer"),
            ("boolean-citations", {"paper": {"citations": True}}, "citations.*non-negative integer"),
        ]
        for name, papers, message in cases:
            existing = {"metadata": {"last_updated": today}, "papers": papers}
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                output = self._prepare_files(Path(directory), existing)
                before = output.read_text(encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, message):
                    self._run_main(output, [])
                self.assertEqual(before, output.read_text(encoding="utf-8"))

    def _prepare_files(self, directory: Path, existing: dict) -> Path:
        socials = directory / "socials.yml"
        output = directory / "citations.yml"
        socials.write_text("scholar_userid: scholar-id\n", encoding="utf-8")
        output.write_text(yaml.safe_dump(existing, sort_keys=True), encoding="utf-8")
        self.addCleanup(setattr, UPDATER, "SOCIALS_FILE", UPDATER.SOCIALS_FILE)
        self.addCleanup(setattr, UPDATER, "OUTPUT_FILE", UPDATER.OUTPUT_FILE)
        UPDATER.SOCIALS_FILE = socials
        UPDATER.OUTPUT_FILE = output
        return output

    def _citation_temp_files(self, output: Path) -> list[Path]:
        return sorted(output.parent.glob(f".{output.name}.*.tmp"))

    def _run_main(self, output: Path, articles: list[dict], **environment: str) -> None:
        env = {"SERPAPI_API_KEY": "api-key", **environment}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(UPDATER.serpapi, "Client"),
            mock.patch.object(UPDATER, "fetch_author_articles", return_value=articles),
        ):
            UPDATER.main()
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
