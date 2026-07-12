#!/usr/bin/env python3
"""Fail-closed contracts for the SerpApi citation updater."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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


class CitationUpdaterContractTest(unittest.TestCase):
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
        with self.assertRaisesRegex(SystemExit, "object payload"):
            UPDATER.fetch_author_articles("scholar-id", client)

    def test_missing_articles_key_fails_closed(self) -> None:
        client = _SearchClient([{}])
        with self.assertRaisesRegex(SystemExit, "articles"):
            UPDATER.fetch_author_articles("scholar-id", client)

    def test_articles_with_the_wrong_type_fails_closed(self) -> None:
        payload = {"articles": "not-a-list"}
        client = _SearchClient([payload])
        with self.assertRaisesRegex(SystemExit, "list"):
            UPDATER.fetch_author_articles("scholar-id", client)

    def test_non_object_article_fails_closed(self) -> None:
        payload = {"articles": ["not-an-object"]}
        client = _SearchClient([payload])
        with self.assertRaisesRegex(SystemExit, "article.*object"):
            UPDATER.fetch_author_articles("scholar-id", client)

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
