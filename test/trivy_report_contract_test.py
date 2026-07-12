#!/usr/bin/env python3
"""Mutation tests for the fail-closed Trivy residual-vulnerability gate."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "bin" / "enforce_trivy_report.py"

FIXTURE_PACKAGES = {
    ("os-pkgs", "debian"): [
        {
            "ID": "fixture-os-a@1.0",
            "Name": "fixture-os-a",
            "Version": "1.0",
            "Identifier": {"PURL": "pkg:deb/debian/fixture-os-a@1.0?arch=amd64"},
        },
        {
            "ID": "fixture-os-b@2.0",
            "Name": "fixture-os-b",
            "Version": "2.0",
            "Identifier": {"PURL": "pkg:deb/debian/fixture-os-b@2.0?arch=amd64"},
        },
    ],
    ("lang-pkgs", "node-pkg"): [
        {
            "ID": "fixture-node-a@1.0",
            "Name": "fixture-node-a",
            "Version": "1.0",
            "FilePath": "node_modules/fixture-node-a/package.json",
            "Identifier": {"PURL": "pkg:npm/fixture-node-a@1.0"},
        },
        {
            "ID": "fixture-node-b@2.0",
            "Name": "fixture-node-b",
            "Version": "2.0",
            "FilePath": "node_modules/fixture-node-b/package.json",
            "Identifier": {"PURL": "pkg:npm/fixture-node-b@2.0"},
        },
    ],
    ("lang-pkgs", "python-pkg"): [
        {
            "Name": "fixture-python",
            "Version": "1.0",
            "FilePath": "site-packages/fixture_python-1.0.dist-info/METADATA",
            "Identifier": {"PURL": "pkg:pypi/fixture-python@1.0"},
        }
    ],
    ("lang-pkgs", "gemspec"): [
        {
            "Name": "fixture-ruby",
            "Version": "1.0",
            "FilePath": "specifications/fixture-ruby-1.0.gemspec",
            "Identifier": {"PURL": "pkg:gem/fixture-ruby@1.0"},
        }
    ],
}


def package_inventory_sha256(
    package_class: str, package_type: str, packages: list[dict[str, object]]
) -> str:
    inventory = sorted(
        [
            package_class,
            package_type,
            package.get("ID", ""),
            package["Name"],
            package["Version"],
            package.get("FilePath", ""),
            package["Identifier"]["PURL"],
        ]
        for package in packages
    )
    payload = json.dumps(inventory, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fixture_coverage() -> list[dict[str, object]]:
    return [
        {
            "Class": package_class,
            "Type": package_type,
            "PackageCount": len(packages),
            "PackageInventorySHA256": package_inventory_sha256(
                package_class, package_type, packages
            ),
        }
        for (package_class, package_type), packages in sorted(FIXTURE_PACKAGES.items())
    ]


def vulnerability(
    vulnerability_id: str = "CVE-2026-0001",
    *,
    package: str = "fixture-package",
    installed_version: str = "1.0.0",
    severity: str = "HIGH",
    status: str = "affected",
    fixed_version: str = "",
) -> dict[str, str]:
    finding = {
        "VulnerabilityID": vulnerability_id,
        "PkgID": f"{package}@{installed_version}",
        "PkgName": package,
        "InstalledVersion": installed_version,
        "Severity": severity,
        "Status": status,
    }
    if fixed_version:
        finding["FixedVersion"] = fixed_version
    return finding


def report(
    findings: list[dict[str, str]],
    *,
    target: str = "Debian GNU/Linux 12 (bookworm)",
    package_class: str = "os-pkgs",
    package_type: str = "debian",
) -> dict[str, object]:
    return {
        "SchemaVersion": 2,
        "Trivy": {"Version": "0.70.0"},
        "ArtifactName": "mtics-al-folio:ci",
        "ArtifactType": "container_image",
        "Metadata": {"ImageConfig": {"architecture": "amd64"}},
        "Results": [
            {
                "Target": target,
                "Class": package_class,
                "Type": package_type,
                "Packages": copy.deepcopy(FIXTURE_PACKAGES[("os-pkgs", "debian")]),
                "Vulnerabilities": findings,
            },
            {
                "Target": "Node.js",
                "Class": "lang-pkgs",
                "Type": "node-pkg",
                "Packages": copy.deepcopy(FIXTURE_PACKAGES[("lang-pkgs", "node-pkg")]),
                "Vulnerabilities": [],
            },
            {
                "Target": "Python",
                "Class": "lang-pkgs",
                "Type": "python-pkg",
                "Packages": copy.deepcopy(FIXTURE_PACKAGES[("lang-pkgs", "python-pkg")]),
                "Vulnerabilities": [],
            },
            {
                "Target": "Ruby",
                "Class": "lang-pkgs",
                "Type": "gemspec",
                "Packages": copy.deepcopy(FIXTURE_PACKAGES[("lang-pkgs", "gemspec")]),
                "Vulnerabilities": [],
            },
        ],
    }


def baseline_entry(
    finding: dict[str, str],
    *,
    package_class: str = "os-pkgs",
    package_type: str = "debian",
) -> dict[str, str]:
    return {
        "Class": package_class,
        "Type": package_type,
        **{
            key: finding[key]
            for key in (
                "PkgID",
                "PkgName",
                "VulnerabilityID",
                "InstalledVersion",
                "Severity",
                "Status",
            )
        },
    }


def baseline(entries: list[dict[str, str]], *, review_before: str | None = None) -> dict[str, object]:
    today = dt.date.today()
    return {
        "schema_version": 2,
        "reviewed_at": today.isoformat(),
        "review_before": review_before or (today + dt.timedelta(days=14)).isoformat(),
        "images": {"delivery": entries, "development": []},
        "coverage": {
            image: {architecture: fixture_coverage() for architecture in ("amd64", "arm64")}
            for image in ("delivery", "development")
        },
    }


class TrivyReportContractTest(unittest.TestCase):
    maxDiff = None

    def invoke(
        self,
        report_document: object | None,
        baseline_document: object | None,
        *,
        raw_report: str | None = None,
        raw_baseline: str | None = None,
        create_report: bool = True,
        create_baseline: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="trivy-gate-") as directory:
            directory_path = Path(directory)
            report_path = directory_path / "report.json"
            baseline_path = directory_path / "baseline.json"
            if create_report:
                report_path.write_text(
                    raw_report if raw_report is not None else json.dumps(report_document),
                    encoding="utf-8",
                )
            if create_baseline:
                baseline_path.write_text(
                    raw_baseline if raw_baseline is not None else json.dumps(baseline_document),
                    encoding="utf-8",
                )
            return subprocess.run(
                [
                    "python3",
                    str(GATE),
                    "--report",
                    str(report_path),
                    "--baseline",
                    str(baseline_path),
                    "--image",
                    "delivery",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

    def test_reviewed_unfixed_residuals_pass(self) -> None:
        finding = vulnerability()
        finding["FixedVersion"] = None  # Trivy v0.70 emits null for many unfixed OS findings.
        result = self.invoke(report([finding]), baseline([baseline_entry(finding)]))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("reviewed residual vulnerabilities: 1", result.stdout)

    def test_fixable_findings_fail_for_either_trivy_signal(self) -> None:
        mutations = {
            "fixed status": vulnerability(status="fixed"),
            "nonempty fixed version": vulnerability(fixed_version="1.0.1"),
        }
        for label, finding in mutations.items():
            with self.subTest(label=label):
                result = self.invoke(report([finding]), baseline([baseline_entry(vulnerability())]))
                self.assertNotEqual(0, result.returncode)
                self.assertIn("fixable HIGH/CRITICAL", result.stderr)

    def test_whitespace_cannot_disguise_fixed_status_or_fixed_version(self) -> None:
        whitespace_status = vulnerability(status="fixed ")
        status_result = self.invoke(
            report([whitespace_status]), baseline([baseline_entry(whitespace_status)])
        )
        self.assertNotEqual(0, status_result.returncode)
        self.assertIn("invalid Trivy baseline", status_result.stderr)

        whitespace_version = vulnerability()
        whitespace_version["FixedVersion"] = " "
        version_result = self.invoke(
            report([whitespace_version]), baseline([baseline_entry(whitespace_version)])
        )
        self.assertNotEqual(0, version_result.returncode)
        self.assertIn("invalid Trivy report", version_result.stderr)

    def test_new_or_drifted_unfixed_findings_fail(self) -> None:
        reviewed = vulnerability()
        mutations = {
            "new CVE": vulnerability("CVE-2026-9999"),
            "package version drift": vulnerability(installed_version="1.0.1"),
            "severity drift": vulnerability(severity="CRITICAL"),
            "status drift": vulnerability(status="will_not_fix"),
        }
        for label, finding in mutations.items():
            with self.subTest(label=label):
                result = self.invoke(report([finding]), baseline([baseline_entry(reviewed)]))
                self.assertNotEqual(0, result.returncode)
                self.assertIn("not present in the reviewed baseline", result.stderr)

    def test_ecosystem_identity_drift_cannot_collide_with_a_reviewed_finding(self) -> None:
        finding = vulnerability()
        drifted_report = report([])
        drifted_report["Results"][1]["Vulnerabilities"] = [finding]
        result = self.invoke(
            drifted_report,
            baseline([baseline_entry(finding)]),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("not present in the reviewed baseline", result.stderr)

    def test_partially_disappeared_residuals_fail_closed(self) -> None:
        remaining = vulnerability("CVE-2026-0001")
        disappeared = vulnerability("CVE-2026-0002")
        entries = sorted(
            [baseline_entry(remaining), baseline_entry(disappeared)],
            key=lambda item: tuple(item.values()),
        )
        result = self.invoke(report([remaining]), baseline(entries))

        self.assertNotEqual(0, result.returncode)
        self.assertIn("reviewed baseline findings missing from the report", result.stderr)
        self.assertIn("CVE-2026-0002", result.stderr)

    def test_missing_malformed_or_structurally_empty_reports_fail_closed(self) -> None:
        finding = vulnerability()
        reviewed = baseline([baseline_entry(finding)])
        truncated_packages = report([finding])
        truncated_packages["Results"][1]["Packages"] = truncated_packages["Results"][1][
            "Packages"
        ][:1]
        duplicate_package = report([finding])
        duplicate_package["Results"][1]["Packages"].append(
            duplicate_package["Results"][1]["Packages"][0]
        )
        missing_package_purl = report([finding])
        missing_package_purl["Results"][1]["Packages"][0] = {
            key: value
            for key, value in missing_package_purl["Results"][1]["Packages"][0].items()
            if key != "Identifier"
        }
        cases = {
            "missing": {"create_report": False},
            "malformed": {"raw_report": "{not-json"},
            "missing Results": {"report_document": {"SchemaVersion": 2}},
            "empty Results": {"report_document": report([]) | {"Results": []}},
            "unknown schema version": {
                "report_document": report([finding]) | {"SchemaVersion": 999}
            },
            "missing Trivy identity": {
                "report_document": {
                    key: value for key, value in report([finding]).items() if key != "Trivy"
                }
            },
            "wrong Trivy identity type": {
                "report_document": report([finding]) | {"Trivy": "0.70.0"}
            },
            "missing Trivy version": {
                "report_document": report([finding]) | {"Trivy": {}}
            },
            "unreviewed Trivy version": {
                "report_document": report([finding]) | {"Trivy": {"Version": "0.69.1"}}
            },
            "missing language result coverage": {
                "report_document": report([finding])
                | {"Results": report([finding])["Results"][:1]}
            },
            "duplicate result identity": {
                "report_document": report([finding])
                | {"Results": report([finding])["Results"] + [report([])["Results"][1]]}
            },
            "unexpected result identity": {
                "report_document": report([finding])
                | {
                    "Results": report([finding])["Results"]
                    + [
                        {
                            "Target": "Rust",
                            "Class": "lang-pkgs",
                            "Type": "cargo",
                            "Vulnerabilities": [],
                        }
                    ]
                }
            },
            "missing metadata": {
                "report_document": {
                    key: value for key, value in report([finding]).items() if key != "Metadata"
                }
            },
            "unsupported architecture": {
                "report_document": report([finding])
                | {"Metadata": {"ImageConfig": {"architecture": "s390x"}}}
            },
            "truncated package inventory": {"report_document": truncated_packages},
            "duplicate package row": {"report_document": duplicate_package},
            "missing package PURL": {"report_document": missing_package_purl},
            "empty package inventory": {
                "report_document": report([finding])
                | {
                    "Results": [
                        result | ({"Packages": []} if result["Type"] == "node-pkg" else {})
                        for result in report([finding])["Results"]
                    ]
                }
            },
            "wrong Results type": {
                "report_document": report([finding]) | {"Results": {}}
            },
            "wrong artifact type": {
                "report_document": report([finding]) | {"ArtifactType": "filesystem"}
            },
            "swapped development artifact": {
                "report_document": report([finding])
                | {"ArtifactName": "mtics-devcontainer:ci"}
            },
            "wrong vulnerability type": {
                "report_document": report([]) | {
                    "Results": [
                        {
                            "Target": "fixture",
                            "Class": "os-pkgs",
                            "Type": "debian",
                            "Vulnerabilities": {},
                        }
                    ]
                }
            },
        }
        for label, options in cases.items():
            with self.subTest(label=label):
                result = self.invoke(
                    options.get("report_document", report([finding])),
                    reviewed,
                    raw_report=options.get("raw_report"),
                    create_report=options.get("create_report", True),
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("invalid Trivy report", result.stderr)

    def test_empty_truncated_findings_and_duplicate_or_bad_identity_fail_closed(self) -> None:
        finding = vulnerability()
        reviewed = baseline([baseline_entry(finding)])
        cases = {
            "empty vulnerabilities": report([]),
            "null vulnerabilities": report([]) | {
                "Results": [
                    {
                        "Target": "fixture",
                        "Class": "os-pkgs",
                        "Type": "debian",
                        "Vulnerabilities": None,
                    }
                ]
            },
            "duplicate normalized finding": report([finding, finding]),
            "missing PkgID": report([{key: value for key, value in finding.items() if key != "PkgID"}]),
            "non-string status": report([finding | {"Status": 1}]),
        }
        for label, report_document in cases.items():
            with self.subTest(label=label):
                result = self.invoke(report_document, reviewed)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("invalid Trivy report", result.stderr)

    def test_missing_malformed_duplicate_unsorted_or_expired_baselines_fail_closed(self) -> None:
        finding_a = vulnerability("CVE-2026-0001", package="a-package")
        finding_b = vulnerability("CVE-2026-0002", package="b-package")
        entry_a = baseline_entry(finding_a)
        entry_b = baseline_entry(finding_b)
        missing_coverage = baseline([entry_a])
        del missing_coverage["coverage"]
        missing_architecture = baseline([entry_a])
        del missing_architecture["coverage"]["delivery"]["arm64"]
        unsorted_coverage = baseline([entry_a])
        unsorted_coverage["coverage"]["delivery"]["amd64"].reverse()
        duplicate_coverage = baseline([entry_a])
        duplicate_coverage["coverage"]["delivery"]["amd64"].append(
            duplicate_coverage["coverage"]["delivery"]["amd64"][0]
        )
        missing_coverage_result = baseline([entry_a])
        missing_coverage_result["coverage"]["delivery"]["amd64"].pop()
        invalid_coverage_digest = baseline([entry_a])
        invalid_coverage_digest["coverage"]["delivery"]["amd64"][0][
            "PackageInventorySHA256"
        ] = "not-a-sha256"
        invalid_package_count = baseline([entry_a])
        invalid_package_count["coverage"]["delivery"]["amd64"][0]["PackageCount"] = True
        cases = {
            "missing": {"create_baseline": False},
            "malformed": {"raw_baseline": "not-json"},
            "duplicate": {"baseline_document": baseline([entry_a, entry_a])},
            "unsorted": {"baseline_document": baseline([entry_b, entry_a])},
            "expired": {
                "baseline_document": baseline(
                    [entry_a], review_before=dt.date.today().isoformat()
                )
            },
            "missing coverage": {"baseline_document": missing_coverage},
            "missing architecture coverage": {"baseline_document": missing_architecture},
            "unsorted coverage": {"baseline_document": unsorted_coverage},
            "duplicate coverage": {"baseline_document": duplicate_coverage},
            "missing coverage result": {"baseline_document": missing_coverage_result},
            "invalid coverage digest": {"baseline_document": invalid_coverage_digest},
            "invalid package count": {"baseline_document": invalid_package_count},
        }
        for label, options in cases.items():
            with self.subTest(label=label):
                result = self.invoke(
                    report([finding_a]),
                    options.get("baseline_document", baseline([entry_a])),
                    raw_baseline=options.get("raw_baseline"),
                    create_baseline=options.get("create_baseline", True),
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("invalid Trivy baseline", result.stderr)


if __name__ == "__main__":
    unittest.main()
