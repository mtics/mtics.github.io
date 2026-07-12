#!/usr/bin/env python3
"""Mutation tests for the fail-closed Trivy residual-vulnerability gate."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "bin" / "enforce_trivy_report.py"
PROVENANCE_BUILDER = ROOT / "bin" / "create_trivy_db_provenance.py"
BASELINE_BUILDER = ROOT / "bin" / "create_trivy_baseline.py"
OCI_MANIFEST_VALIDATOR = ROOT / "bin" / "validate_trivy_oci_manifest.py"
FIXTURE_VULNERABILITY_DB = b"fixture vulnerability database"
FIXTURE_JAVA_DB = b"fixture Java database"
ALL_SEVERITIES = ("CRITICAL", "HIGH", "LOW", "MEDIUM", "UNKNOWN")
FINDING_INVENTORY_FIELDS = (
    "Class",
    "Type",
    "PkgID",
    "PkgName",
    "VulnerabilityID",
    "InstalledVersion",
    "Severity",
    "Status",
    "FixedVersion",
)


def oci_manifest_payload(database_name: str) -> str:
    layer = {
        "vulnerability": (
            "application/vnd.aquasec.trivy.db.layer.v1.tar+gzip",
            "db.tar.gz",
            "a",
        ),
        "java": (
            "application/vnd.aquasec.trivy.javadb.layer.v1.tar+gzip",
            "javadb.tar.gz",
            "b",
        ),
    }[database_name]
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "artifactType": "application/vnd.aquasec.trivy.config.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.empty.v1+json",
                "digest": "sha256:"
                "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                "size": 2,
                "data": "e30=",
            },
            "layers": [
                {
                    "mediaType": layer[0],
                    "digest": "sha256:" + layer[2] * 64,
                    "size": 123456,
                    "annotations": {"org.opencontainers.image.title": layer[1]},
                }
            ],
            "annotations": {"org.opencontainers.image.created": "2026-07-12T07:33:36Z"},
        },
        separators=(",", ":"),
    )


def oci_evidence(database_name: str, payload: str) -> dict[str, object]:
    document = json.loads(payload)
    spec = {
        "vulnerability": (
            "ghcr.io/aquasecurity/trivy-db",
            "ghcr.io/aquasecurity/trivy-db:2",
        ),
        "java": (
            "ghcr.io/aquasecurity/trivy-java-db",
            "ghcr.io/aquasecurity/trivy-java-db:1",
        ),
    }[database_name]
    layer = document["layers"][0]
    return {
        "repository": spec[0],
        "resolved_from": spec[1],
        "manifest_digest": "sha256:"
        + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "layer_digest": layer["digest"],
        "layer_media_type": layer["mediaType"],
        "layer_size": layer["size"],
    }


def rfc3339(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

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
        "CreatedAt": rfc3339(utc_now()),
        "Trivy": {"Version": "0.70.0"},
        "ArtifactName": "mtics-al-folio:ci",
        "ArtifactType": "container_image",
        "Metadata": {
            "ImageID": "sha256:" + "e" * 64,
            "ImageConfig": {"architecture": "amd64"},
        },
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


def vulnerability_inventory_coverage(
    findings: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, list[list[str]]] = {severity: [] for severity in ALL_SEVERITIES}
    for finding in findings:
        identity = {
            "Class": finding.get("Class", "os-pkgs"),
            "Type": finding.get("Type", "debian"),
            **finding,
        }
        row = []
        for field in FINDING_INVENTORY_FIELDS:
            value = identity.get(field, "")
            if value is None:
                value = ""
            row.append(str(value))
        grouped[str(identity["Severity"])].append(row)
    return [
        {
            "Severity": severity,
            "FindingCount": len(grouped[severity]),
            "FindingInventorySHA256": hashlib.sha256(
                json.dumps(
                    sorted(grouped[severity]), ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
        }
        for severity in ALL_SEVERITIES
    ]


def baseline(
    entries: list[dict[str, str]],
    *,
    review_before: str | None = None,
    all_findings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    today = dt.date.today()
    minimum = rfc3339(utc_now() - dt.timedelta(hours=4))
    return {
        "schema_version": 4,
        "reviewed_at": today.isoformat(),
        "review_before": review_before or (today + dt.timedelta(days=14)).isoformat(),
        "minimum_db_updated_at": {
            "vulnerability": minimum,
            "java": minimum,
        },
        "images": {"delivery": entries, "development": []},
        "coverage": {
            image: {architecture: fixture_coverage() for architecture in ("amd64", "arm64")}
            for image in ("delivery", "development")
        },
        "vulnerability_coverage": {
            image: {
                architecture: vulnerability_inventory_coverage(
                    (all_findings if image == "delivery" and all_findings is not None else entries)
                    if image == "delivery"
                    else []
                )
                for architecture in ("amd64", "arm64")
            }
            for image in ("delivery", "development")
        },
    }


def valid_provenance(
    report_document: dict[str, object], report_payload: str | None = None
) -> dict[str, object]:
    created_text = report_document.get("CreatedAt", rfc3339(utc_now()))
    try:
        created_at = dt.datetime.fromisoformat(str(created_text).replace("Z", "+00:00"))
    except ValueError:
        created_at = utc_now()
    payload = report_payload if report_payload is not None else json.dumps(report_document)
    development_created_at = rfc3339(created_at + dt.timedelta(seconds=1))
    metadata = report_document.get("Metadata")
    image_config = metadata.get("ImageConfig") if isinstance(metadata, dict) else None
    architecture = (
        image_config.get("architecture") if isinstance(image_config, dict) else "amd64"
    )
    severity_counts = {severity: 0 for severity in ALL_SEVERITIES}
    results = report_document.get("Results")
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, dict):
                continue
            findings = result.get("Vulnerabilities", [])
            if not isinstance(findings, list):
                continue
            for finding in findings:
                if isinstance(finding, dict) and finding.get("Severity") in severity_counts:
                    severity_counts[finding["Severity"]] += 1
    document = {
        "schema_version": 1,
        "captured_at": rfc3339(created_at + dt.timedelta(minutes=1)),
        "trivy_version": "0.70.0",
        "databases": {
            "vulnerability": {
                "schema_version": 2,
                "updated_at": rfc3339(created_at - dt.timedelta(hours=2)),
                "next_update": rfc3339(created_at + dt.timedelta(hours=22)),
                "downloaded_at": rfc3339(created_at - dt.timedelta(hours=1)),
                "sha256": hashlib.sha256(FIXTURE_VULNERABILITY_DB).hexdigest(),
                "metadata_sha256": "pending",
                "oci": oci_evidence(
                    "vulnerability", oci_manifest_payload("vulnerability")
                ),
            },
            "java": {
                "schema_version": 1,
                "updated_at": rfc3339(created_at - dt.timedelta(hours=2)),
                "next_update": rfc3339(created_at + dt.timedelta(hours=22)),
                "downloaded_at": rfc3339(created_at - dt.timedelta(hours=1)),
                "sha256": hashlib.sha256(FIXTURE_JAVA_DB).hexdigest(),
                "metadata_sha256": "pending",
                "oci": oci_evidence("java", oci_manifest_payload("java")),
            },
        },
        "reports": {
            "delivery": {
                "artifact_name": report_document.get("ArtifactName", "mtics-al-folio:ci"),
                "architecture": architecture,
                "created_at": created_text,
                "image_id": (
                    metadata.get("ImageID")
                    if isinstance(metadata, dict)
                    else "sha256:" + "e" * 64
                ),
                "severity_counts": severity_counts,
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            },
            "development": {
                "artifact_name": "mtics-devcontainer:ci",
                "architecture": "amd64",
                "created_at": development_created_at,
                "image_id": "sha256:" + "f" * 64,
                "severity_counts": {
                    "CRITICAL": 0,
                    "HIGH": 0,
                    "LOW": 0,
                    "MEDIUM": 0,
                    "UNKNOWN": 0,
                },
                "sha256": "c" * 64,
            },
        },
    }
    for database_name in ("vulnerability", "java"):
        metadata_payload = json.dumps(
            provenance_metadata_document(document, database_name)
        )
        document["databases"][database_name]["metadata_sha256"] = hashlib.sha256(
            metadata_payload.encode("utf-8")
        ).hexdigest()
    return document


def provenance_metadata_document(
    provenance_document: dict[str, object], database_name: str = "vulnerability"
) -> dict[str, object]:
    database = provenance_document["databases"][database_name]
    return {
        "Version": database["schema_version"],
        "NextUpdate": database["next_update"],
        "UpdatedAt": database["updated_at"],
        "DownloadedAt": database["downloaded_at"],
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
        provenance_document: object | None = None,
        raw_provenance: str | None = None,
        create_report: bool = True,
        create_baseline: bool = True,
        create_provenance: bool = True,
        create_databases: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="trivy-gate-") as directory:
            directory_path = Path(directory)
            report_path = directory_path / "report.json"
            baseline_path = directory_path / "baseline.json"
            provenance_path = directory_path / "provenance.json"
            vulnerability_db_path = directory_path / "trivy.db"
            vulnerability_metadata_path = directory_path / "metadata.json"
            java_db_path = directory_path / "trivy-java.db"
            java_metadata_path = directory_path / "java-metadata.json"
            vulnerability_manifest_path = directory_path / "vulnerability-manifest.json"
            java_manifest_path = directory_path / "java-manifest.json"
            report_payload: str | None = None
            if create_report:
                report_payload = raw_report if raw_report is not None else json.dumps(report_document)
                report_path.write_text(
                    report_payload,
                    encoding="utf-8",
                )
            if create_baseline:
                baseline_path.write_text(
                    raw_baseline if raw_baseline is not None else json.dumps(baseline_document),
                    encoding="utf-8",
                )
            if create_provenance:
                if provenance_document is None:
                    if not isinstance(report_document, dict) or report_payload is None:
                        provenance_document = {}
                    else:
                        provenance_document = valid_provenance(report_document, report_payload)
                provenance_path.write_text(
                    raw_provenance
                    if raw_provenance is not None
                    else json.dumps(provenance_document),
                    encoding="utf-8",
                )
            if create_databases:
                vulnerability_db_path.write_bytes(FIXTURE_VULNERABILITY_DB)
                java_db_path.write_bytes(FIXTURE_JAVA_DB)
                vulnerability_manifest_path.write_text(
                    oci_manifest_payload("vulnerability"), encoding="utf-8"
                )
                java_manifest_path.write_text(
                    oci_manifest_payload("java"), encoding="utf-8"
                )
                if (
                    isinstance(provenance_document, dict)
                    and isinstance(provenance_document.get("databases"), dict)
                    and "vulnerability" in provenance_document["databases"]
                ):
                    vulnerability_metadata_path.write_text(
                        json.dumps(
                            provenance_metadata_document(
                                provenance_document, "vulnerability"
                            )
                        ),
                        encoding="utf-8",
                    )
                    java_metadata_path.write_text(
                        json.dumps(provenance_metadata_document(provenance_document, "java")),
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
                    "--provenance",
                    str(provenance_path),
                    "--vulnerability-db",
                    str(vulnerability_db_path),
                    "--vulnerability-db-metadata",
                    str(vulnerability_metadata_path),
                    "--java-db",
                    str(java_db_path),
                    "--java-db-metadata",
                    str(java_metadata_path),
                    "--vulnerability-db-manifest",
                    str(vulnerability_manifest_path),
                    "--java-db-manifest",
                    str(java_manifest_path),
                    "--expected-architecture",
                    "amd64",
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

    def test_report_architecture_must_match_the_runtime_architecture(self) -> None:
        finding = vulnerability()
        report_document = report([finding])
        report_document["Metadata"]["ImageConfig"]["architecture"] = "arm64"
        result = self.invoke(
            report_document,
            baseline([baseline_entry(finding)]),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("expected architecture amd64", result.stderr)

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

        for disguised_status in ("fixed\u200b", "reviewed-by-hand"):
            with self.subTest(disguised_status=disguised_status):
                disguised = vulnerability(status=disguised_status)
                disguised_result = self.invoke(
                    report([disguised]), baseline([baseline_entry(disguised)])
                )
                self.assertNotEqual(0, disguised_result.returncode)
                self.assertIn("Status is not recognized", disguised_result.stderr)

                valid = vulnerability()
                report_result = self.invoke(
                    report([disguised]),
                    baseline(
                        [baseline_entry(valid)],
                        all_findings=[disguised],
                    ),
                )
                self.assertNotEqual(0, report_result.returncode)
                self.assertIn("invalid Trivy report", report_result.stderr)
                self.assertIn("Status is not recognized", report_result.stderr)

        not_affected = vulnerability(status="not_affected")
        not_affected_result = self.invoke(
            report([not_affected]),
            baseline([], all_findings=[not_affected]),
        )
        self.assertNotEqual(0, not_affected_result.returncode)
        self.assertIn("not_affected must not appear", not_affected_result.stderr)

        sys.path.insert(0, str(GATE.parent))
        try:
            namespace = runpy.run_path(str(GATE))
        finally:
            sys.path.pop(0)
        self.assertEqual(
            {
                "unknown",
                "not_affected",
                "affected",
                "fixed",
                "under_investigation",
                "will_not_fix",
                "fix_deferred",
                "end_of_life",
            },
            namespace["ALLOWED_STATUSES"],
        )

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

    def test_severity_downgrade_cannot_silently_shrink_the_reviewed_set(self) -> None:
        reviewed = vulnerability(severity="HIGH")
        downgraded = vulnerability(severity="LOW")
        result = self.invoke(
            report([downgraded]),
            baseline([baseline_entry(reviewed)]),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("all findings disappeared from a non-empty baseline", result.stderr)

    def test_all_severity_report_keeps_raw_low_medium_and_unknown_findings(self) -> None:
        reviewed = vulnerability()
        lower_risk = [
            vulnerability("CVE-2026-1001", severity="LOW", fixed_version="1.0.1"),
            vulnerability("CVE-2026-1002", severity="MEDIUM"),
            vulnerability("CVE-2026-1003", severity="UNKNOWN"),
        ]
        lower_risk[0]["PkgID"] = None  # Trivy v0.70 emits null for some LOW gem findings.
        result = self.invoke(
            report([reviewed, *lower_risk]),
            baseline([baseline_entry(reviewed)], all_findings=[reviewed, *lower_risk]),
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("reviewed residual vulnerabilities: 1", result.stdout)

        invalid = vulnerability("CVE-2026-1004", severity="NEGLIGIBLE")
        invalid_result = self.invoke(
            report([reviewed, invalid]),
            baseline([baseline_entry(reviewed)]),
        )
        self.assertNotEqual(0, invalid_result.returncode)
        self.assertIn("invalid Trivy report", invalid_result.stderr)

        high_only = self.invoke(
            report([reviewed]),
            baseline([baseline_entry(reviewed)], all_findings=[reviewed, *lower_risk]),
        )
        self.assertNotEqual(0, high_only.returncode)
        self.assertIn("all-severity finding coverage differs", high_only.stderr)

    def test_ecosystem_identity_drift_cannot_collide_with_a_reviewed_finding(self) -> None:
        finding = vulnerability()
        for label, injected in {
            "result context": finding,
            "forged finding context": finding | {"Class": "os-pkgs", "Type": "debian"},
        }.items():
            with self.subTest(label=label):
                drifted_report = report([])
                drifted_report["Results"][1]["Vulnerabilities"] = [injected]
                result = self.invoke(
                    drifted_report,
                    baseline([baseline_entry(finding)]),
                )

                self.assertNotEqual(0, result.returncode)
                if label == "forged finding context":
                    self.assertIn("invalid Trivy report", result.stderr)
                else:
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

    def test_conflicting_core_identity_and_duplicate_json_keys_fail_closed(self) -> None:
        finding = vulnerability()
        conflicting_severity = vulnerability(severity="LOW")
        conflicting_status = vulnerability(status="fix_deferred")
        for label, findings in {
            "severity conflict": [finding, conflicting_severity],
            "status conflict": [finding, conflicting_status],
        }.items():
            with self.subTest(label=label):
                result = self.invoke(
                    report(findings),
                    baseline(
                        [baseline_entry(finding)],
                        all_findings=[finding, *findings[1:]],
                    ),
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("conflicting core finding identity", result.stderr)

        report_document = report([finding])
        raw_report = json.dumps(report_document).replace(
            '"Severity": "HIGH"',
            '"Severity": "LOW", "Severity": "HIGH"',
            1,
        )
        duplicate_key = self.invoke(
            report_document,
            baseline([baseline_entry(finding)]),
            raw_report=raw_report,
        )
        self.assertNotEqual(0, duplicate_key.returncode)
        self.assertIn("duplicate object key", duplicate_key.stderr)

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
        missing_minimum_db = baseline([entry_a])
        del missing_minimum_db["minimum_db_updated_at"]
        malformed_minimum_db = baseline([entry_a])
        malformed_minimum_db["minimum_db_updated_at"]["vulnerability"] = "not-a-timestamp"
        missing_vulnerability_coverage = baseline([entry_a])
        del missing_vulnerability_coverage["vulnerability_coverage"]
        invalid_vulnerability_digest = baseline([entry_a])
        invalid_vulnerability_digest["vulnerability_coverage"]["delivery"]["amd64"][0][
            "FindingInventorySHA256"
        ] = "not-a-sha256"
        conflicting_entry = entry_a | {"Status": "fix_deferred"}
        cases = {
            "missing": {"create_baseline": False},
            "malformed": {"raw_baseline": "not-json"},
            "duplicate": {"baseline_document": baseline([entry_a, entry_a])},
            "conflicting core identity": {
                "baseline_document": baseline([entry_a, conflicting_entry])
            },
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
            "missing minimum database timestamp": {"baseline_document": missing_minimum_db},
            "malformed minimum database timestamp": {
                "baseline_document": malformed_minimum_db
            },
            "missing vulnerability coverage": {
                "baseline_document": missing_vulnerability_coverage
            },
            "invalid vulnerability coverage digest": {
                "baseline_document": invalid_vulnerability_digest
            },
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

    def test_report_and_database_provenance_fail_closed_on_tampering_or_staleness(self) -> None:
        finding = vulnerability()
        report_document = report([finding])
        reviewed = baseline([baseline_entry(finding)])
        valid = valid_provenance(report_document)
        created_at = report_document["CreatedAt"]

        mutations: dict[str, tuple[dict[str, object], str]] = {}

        wrong_version = copy.deepcopy(valid)
        wrong_version["trivy_version"] = "0.69.1"
        mutations["Trivy version mismatch"] = (wrong_version, "invalid Trivy provenance")

        invalid_digest = copy.deepcopy(valid)
        invalid_digest["databases"]["vulnerability"]["sha256"] = "not-a-sha256"
        mutations["invalid database digest"] = (invalid_digest, "invalid Trivy provenance")

        wrong_database_digest = copy.deepcopy(valid)
        wrong_database_digest["databases"]["vulnerability"]["sha256"] = "d" * 64
        mutations["database digest does not match bytes"] = (
            wrong_database_digest,
            "invalid Trivy provenance",
        )

        wrong_oci_layer = copy.deepcopy(valid)
        wrong_oci_layer["databases"]["vulnerability"]["oci"]["layer_digest"] = (
            "sha256:" + "d" * 64
        )
        mutations["OCI layer descriptor does not match manifest bytes"] = (
            wrong_oci_layer,
            "invalid Trivy provenance",
        )

        wrong_database_schema = copy.deepcopy(valid)
        wrong_database_schema["databases"]["vulnerability"]["schema_version"] = True
        mutations["boolean database schema"] = (
            wrong_database_schema,
            "invalid Trivy provenance",
        )

        time_reversal = copy.deepcopy(valid)
        time_reversal["databases"]["vulnerability"]["updated_at"] = created_at
        mutations["database update after download"] = (
            time_reversal,
            "invalid Trivy provenance",
        )

        extra_top_level_key = copy.deepcopy(valid)
        extra_top_level_key["unreviewed"] = True
        mutations["unexpected provenance field"] = (
            extra_top_level_key,
            "invalid Trivy provenance",
        )

        expired_database = copy.deepcopy(valid)
        expired_database["databases"]["vulnerability"]["next_update"] = created_at
        mutations["database expired at scan time"] = (
            expired_database,
            "invalid Trivy provenance",
        )

        postdated_download = copy.deepcopy(valid)
        postdated_download["databases"]["vulnerability"]["downloaded_at"] = rfc3339(
            dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            + dt.timedelta(seconds=1)
        )
        mutations["report predates database download"] = (
            postdated_download,
            "invalid Trivy provenance",
        )

        captured_before_report = copy.deepcopy(valid)
        captured_before_report["captured_at"] = rfc3339(
            dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            - dt.timedelta(seconds=1)
        )
        mutations["provenance captured before report"] = (
            captured_before_report,
            "invalid Trivy provenance",
        )

        wrong_report_digest = copy.deepcopy(valid)
        wrong_report_digest["reports"]["delivery"]["sha256"] = "d" * 64
        mutations["report digest mismatch"] = (
            wrong_report_digest,
            "invalid Trivy provenance",
        )

        wrong_report_timestamp = copy.deepcopy(valid)
        wrong_report_timestamp["reports"]["delivery"]["created_at"] = rfc3339(
            dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            + dt.timedelta(seconds=2)
        )
        mutations["report timestamp mismatch"] = (
            wrong_report_timestamp,
            "invalid Trivy provenance",
        )

        for label, (provenance_document, expected_error) in mutations.items():
            with self.subTest(label=label):
                result = self.invoke(
                    report_document,
                    reviewed,
                    provenance_document=provenance_document,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(expected_error, result.stderr)

        missing = self.invoke(
            report_document,
            reviewed,
            create_provenance=False,
        )
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("invalid Trivy provenance", missing.stderr)

        malformed = self.invoke(
            report_document,
            reviewed,
            raw_provenance="{not-json",
        )
        self.assertNotEqual(0, malformed.returncode)
        self.assertIn("invalid Trivy provenance", malformed.stderr)

        missing_database = self.invoke(
            report_document,
            reviewed,
            create_databases=False,
        )
        self.assertNotEqual(0, missing_database.returncode)
        self.assertIn("invalid Trivy provenance", missing_database.stderr)

        stale_baseline = copy.deepcopy(reviewed)
        future_minimum = rfc3339(utc_now() + dt.timedelta(hours=1))
        stale_baseline["minimum_db_updated_at"]["vulnerability"] = future_minimum
        stale = self.invoke(report_document, stale_baseline)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("older than the reviewed minimum", stale.stderr)

        missing_created_at = copy.deepcopy(report_document)
        del missing_created_at["CreatedAt"]
        bad_report = self.invoke(missing_created_at, reviewed)
        self.assertNotEqual(0, bad_report.returncode)
        self.assertIn("invalid Trivy report", bad_report.stderr)

        nanosecond_stale = copy.deepcopy(reviewed)
        nanosecond_stale["minimum_db_updated_at"][
            "vulnerability"
        ] = "2026-07-12T07:28:36.123456900Z"
        nanosecond_provenance = copy.deepcopy(valid)
        nanosecond_provenance["databases"]["vulnerability"][
            "updated_at"
        ] = "2026-07-12T07:28:36.123456100Z"
        nanosecond_result = self.invoke(
            report_document,
            nanosecond_stale,
            provenance_document=nanosecond_provenance,
        )
        self.assertNotEqual(0, nanosecond_result.returncode)
        self.assertIn("older than the reviewed minimum", nanosecond_result.stderr)

        future_provenance = copy.deepcopy(valid)
        future_provenance["captured_at"] = rfc3339(utc_now() + dt.timedelta(hours=1))
        future_result = self.invoke(
            report_document,
            reviewed,
            provenance_document=future_provenance,
        )
        self.assertNotEqual(0, future_result.returncode)
        self.assertIn("future", future_result.stderr)

        gate_source = GATE.read_text(encoding="utf-8")
        self.assertNotIn("hash_file(report_path", gate_source)


class TrivyOCIManifestContractTest(unittest.TestCase):
    def test_validator_accepts_only_the_expected_content_addressed_db_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-oci-manifest-") as directory:
            root = Path(directory)
            for database_name in ("vulnerability", "java"):
                with self.subTest(database_name=database_name):
                    payload = oci_manifest_payload(database_name)
                    path = root / f"{database_name}.json"
                    path.write_text(payload, encoding="utf-8")
                    result = subprocess.run(
                        [
                            "python3",
                            str(OCI_MANIFEST_VALIDATOR),
                            "--database",
                            database_name,
                            "--manifest",
                            str(path),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(
                        "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                        result.stdout.strip(),
                    )

    def test_validator_rejects_duplicate_keys_and_descriptor_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-oci-negative-") as directory:
            root = Path(directory)
            payload = oci_manifest_payload("vulnerability")
            extra_layer_document = json.loads(payload)
            extra_layer_document["layers"].append(
                copy.deepcopy(extra_layer_document["layers"][0])
            )
            mutations = {
                "duplicate key": payload.replace(
                    '"schemaVersion":2', '"schemaVersion":1,"schemaVersion":2', 1
                ),
                "wrong layer media type": payload.replace(
                    "application/vnd.aquasec.trivy.db.layer.v1.tar+gzip",
                    "application/octet-stream",
                ),
                "zero layer size": payload.replace('"size":123456', '"size":0'),
                "invalid creation date": payload.replace(
                    "2026-07-12T07:33:36Z", "2026-99-99T99:99:99Z"
                ),
                "future creation date": payload.replace(
                    "2026-07-12T07:33:36Z",
                    rfc3339(utc_now() + dt.timedelta(hours=1)),
                ),
                "extra layer": json.dumps(
                    extra_layer_document, separators=(",", ":")
                ),
            }
            for label, mutation in mutations.items():
                with self.subTest(label=label):
                    path = root / f"{label.replace(' ', '-')}.json"
                    path.write_text(mutation, encoding="utf-8")
                    result = subprocess.run(
                        [
                            "python3",
                            str(OCI_MANIFEST_VALIDATOR),
                            "--database",
                            "vulnerability",
                            "--manifest",
                            str(path),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("invalid Trivy OCI manifest", result.stderr)


class TrivyProvenanceBuilderContractTest(unittest.TestCase):
    def test_builder_binds_database_metadata_bytes_and_both_reports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-provenance-") as directory:
            root = Path(directory)
            vulnerability_db = root / "trivy.db"
            vulnerability_metadata = root / "metadata.json"
            java_db = root / "trivy-java.db"
            java_metadata = root / "java-metadata.json"
            vulnerability_manifest = root / "vulnerability-manifest.json"
            java_manifest = root / "java-manifest.json"
            version_path = root / "version.json"
            delivery_path = root / "delivery.json"
            development_path = root / "development.json"
            output_path = root / "provenance.json"

            vulnerability_db.write_bytes(b"vulnerability database fixture")
            java_db.write_bytes(b"Java database fixture")
            created_at = rfc3339(utc_now() - dt.timedelta(minutes=1))
            vulnerability_metadata_document = {
                "Version": 2,
                "UpdatedAt": "2026-07-12T07:28:36Z",
                "NextUpdate": "2026-07-13T07:28:36Z",
                "DownloadedAt": "2026-07-12T08:00:00Z",
            }
            vulnerability_metadata.write_text(
                json.dumps(vulnerability_metadata_document), encoding="utf-8"
            )
            java_metadata_document = vulnerability_metadata_document | {"Version": 1}
            java_metadata.write_text(
                json.dumps(java_metadata_document), encoding="utf-8"
            )
            vulnerability_manifest.write_text(
                oci_manifest_payload("vulnerability"), encoding="utf-8"
            )
            java_manifest.write_text(oci_manifest_payload("java"), encoding="utf-8")
            version_path.write_text(
                json.dumps(
                    {
                        "Version": "0.70.0",
                        "VulnerabilityDB": vulnerability_metadata_document,
                        "JavaDB": java_metadata_document,
                    }
                ),
                encoding="utf-8",
            )

            delivery = report([])
            delivery["CreatedAt"] = created_at
            development = report([])
            development["CreatedAt"] = created_at
            development["ArtifactName"] = "mtics-devcontainer:ci"
            development["Metadata"]["ImageID"] = "sha256:" + "f" * 64
            delivery_path.write_text(json.dumps(delivery), encoding="utf-8")
            development_path.write_text(json.dumps(development), encoding="utf-8")

            command = [
                    "python3",
                    str(PROVENANCE_BUILDER),
                    "--trivy-version-json",
                    str(version_path),
                    "--vulnerability-db",
                    str(vulnerability_db),
                    "--vulnerability-db-metadata",
                    str(vulnerability_metadata),
                    "--java-db",
                    str(java_db),
                    "--java-db-metadata",
                    str(java_metadata),
                    "--vulnerability-db-manifest",
                    str(vulnerability_manifest),
                    "--java-db-manifest",
                    str(java_manifest),
                    "--expected-architecture",
                    "amd64",
                    "--delivery-report",
                    str(delivery_path),
                    "--development-report",
                    str(development_path),
                    "--output",
                    str(output_path),
                ]
            result = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            document = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(1, document["schema_version"])
            self.assertEqual("0.70.0", document["trivy_version"])
            self.assertEqual(
                hashlib.sha256(vulnerability_db.read_bytes()).hexdigest(),
                document["databases"]["vulnerability"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(vulnerability_metadata.read_bytes()).hexdigest(),
                document["databases"]["vulnerability"]["metadata_sha256"],
            )
            self.assertEqual(
                hashlib.sha256(java_db.read_bytes()).hexdigest(),
                document["databases"]["java"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(java_metadata.read_bytes()).hexdigest(),
                document["databases"]["java"]["metadata_sha256"],
            )
            self.assertEqual(
                oci_evidence("vulnerability", vulnerability_manifest.read_text()),
                document["databases"]["vulnerability"]["oci"],
            )
            self.assertEqual(
                oci_evidence("java", java_manifest.read_text()),
                document["databases"]["java"]["oci"],
            )
            self.assertEqual(
                hashlib.sha256(delivery_path.read_bytes()).hexdigest(),
                document["reports"]["delivery"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(development_path.read_bytes()).hexdigest(),
                document["reports"]["development"]["sha256"],
            )
            self.assertEqual(
                {severity: 0 for severity in ALL_SEVERITIES},
                document["reports"]["delivery"]["severity_counts"],
            )

            for label, mutation in {
                "duplicate image identity": {"ImageID": delivery["Metadata"]["ImageID"]},
                "wrong architecture": {"architecture": "arm64"},
            }.items():
                with self.subTest(label=label):
                    mutated = copy.deepcopy(development)
                    if "ImageID" in mutation:
                        mutated["Metadata"]["ImageID"] = mutation["ImageID"]
                    if "architecture" in mutation:
                        mutated["Metadata"]["ImageConfig"]["architecture"] = mutation[
                            "architecture"
                        ]
                    development_path.write_text(json.dumps(mutated), encoding="utf-8")
                    rejected = subprocess.run(
                        command,
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(0, rejected.returncode)
                    self.assertIn("invalid Trivy provenance input", rejected.stderr)
            development_path.write_text(json.dumps(development), encoding="utf-8")

            original_version = version_path.read_bytes()
            alias_command = list(command)
            alias_command[alias_command.index("--output") + 1] = str(version_path)
            alias_result = subprocess.run(
                alias_command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, alias_result.returncode)
            self.assertIn("must not alias an input", alias_result.stderr)
            self.assertEqual(original_version, version_path.read_bytes())

    def test_builder_rejects_metadata_mismatch_and_duplicate_version_keys(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-provenance-negative-") as directory:
            root = Path(directory)
            now = utc_now()
            metadata = {
                "Version": 2,
                "UpdatedAt": rfc3339(now - dt.timedelta(hours=2)),
                "NextUpdate": rfc3339(now + dt.timedelta(hours=22)),
                "DownloadedAt": rfc3339(now - dt.timedelta(hours=1)),
            }
            report_paths = {}
            for image, artifact in {
                "delivery": "mtics-al-folio:ci",
                "development": "mtics-devcontainer:ci",
            }.items():
                document = report([])
                document["CreatedAt"] = rfc3339(now - dt.timedelta(minutes=1))
                document["ArtifactName"] = artifact
                path = root / f"{image}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                report_paths[image] = path
            (root / "trivy.db").write_bytes(FIXTURE_VULNERABILITY_DB)
            (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            java_metadata = metadata | {"Version": 1}
            (root / "trivy-java.db").write_bytes(FIXTURE_JAVA_DB)
            (root / "java-metadata.json").write_text(
                json.dumps(java_metadata), encoding="utf-8"
            )
            (root / "vulnerability-manifest.json").write_text(
                oci_manifest_payload("vulnerability"), encoding="utf-8"
            )
            (root / "java-manifest.json").write_text(
                oci_manifest_payload("java"), encoding="utf-8"
            )

            common = [
                "python3",
                str(PROVENANCE_BUILDER),
                "--vulnerability-db",
                str(root / "trivy.db"),
                "--vulnerability-db-metadata",
                str(root / "metadata.json"),
                "--java-db",
                str(root / "trivy-java.db"),
                "--java-db-metadata",
                str(root / "java-metadata.json"),
                "--vulnerability-db-manifest",
                str(root / "vulnerability-manifest.json"),
                "--java-db-manifest",
                str(root / "java-manifest.json"),
                "--expected-architecture",
                "amd64",
                "--delivery-report",
                str(report_paths["delivery"]),
                "--development-report",
                str(report_paths["development"]),
                "--output",
                str(root / "provenance.json"),
            ]

            mismatched = metadata | {"DownloadedAt": rfc3339(now - dt.timedelta(minutes=30))}
            mismatch_version = root / "mismatch-version.json"
            mismatch_version.write_text(
                json.dumps(
                    {
                        "Version": "0.70.0",
                        "VulnerabilityDB": mismatched,
                        "JavaDB": java_metadata,
                    }
                ),
                encoding="utf-8",
            )
            mismatch = subprocess.run(
                [*common, "--trivy-version-json", str(mismatch_version)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, mismatch.returncode)
            self.assertIn("does not match the frozen metadata", mismatch.stderr)

            duplicate_version = root / "duplicate-version.json"
            duplicate_version.write_text(
                '{"Version":"0.69.0","Version":"0.70.0","VulnerabilityDB":'
                + json.dumps(metadata)
                + ',"JavaDB":'
                + json.dumps(java_metadata)
                + "}",
                encoding="utf-8",
            )
            duplicate = subprocess.run(
                [*common, "--trivy-version-json", str(duplicate_version)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, duplicate.returncode)
            self.assertIn("duplicate object key", duplicate.stderr)


class TrivyBaselineBuilderContractTest(unittest.TestCase):
    def create_inputs(
        self,
        root: Path,
        *,
        arm64_delivery_findings: list[dict[str, str]] | None = None,
    ) -> dict[str, Path]:
        now = utc_now()
        metadata = {
            "Version": 2,
            "UpdatedAt": rfc3339(now - dt.timedelta(hours=2)),
            "NextUpdate": rfc3339(now + dt.timedelta(hours=22)),
            "DownloadedAt": rfc3339(now - dt.timedelta(hours=1)),
        }
        java_metadata = metadata | {"Version": 1}
        paths = {
            "vulnerability_db": root / "trivy.db",
            "vulnerability_metadata": root / "metadata.json",
            "java_db": root / "trivy-java.db",
            "java_metadata": root / "java-metadata.json",
            "vulnerability_manifest": root / "vulnerability-manifest.json",
            "java_manifest": root / "java-manifest.json",
            "version": root / "version.json",
            "baseline": root / "baseline.json",
            "manifest": root / "manifest.json",
        }
        paths["vulnerability_db"].write_bytes(FIXTURE_VULNERABILITY_DB)
        paths["vulnerability_metadata"].write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        paths["java_db"].write_bytes(FIXTURE_JAVA_DB)
        paths["java_metadata"].write_text(
            json.dumps(java_metadata), encoding="utf-8"
        )
        paths["vulnerability_manifest"].write_text(
            oci_manifest_payload("vulnerability"), encoding="utf-8"
        )
        paths["java_manifest"].write_text(
            oci_manifest_payload("java"), encoding="utf-8"
        )
        paths["version"].write_text(
            json.dumps(
                {
                    "Version": "0.70.0",
                    "VulnerabilityDB": metadata,
                    "JavaDB": java_metadata,
                }
            ),
            encoding="utf-8",
        )

        delivery_findings = [vulnerability()]
        development_findings = [vulnerability("CVE-2026-0002", package="dev-package")]
        report_specs = {
            "delivery_amd64": (
                "mtics-al-folio:ci",
                "amd64",
                delivery_findings,
                "a",
            ),
            "delivery_arm64": (
                "mtics-al-folio:release-arm64",
                "arm64",
                arm64_delivery_findings or delivery_findings,
                "b",
            ),
            "development_amd64": (
                "mtics-devcontainer:ci",
                "amd64",
                development_findings,
                "c",
            ),
            "development_arm64": (
                "mtics-devcontainer:release-arm64",
                "arm64",
                development_findings,
                "d",
            ),
        }
        for name, (artifact, architecture, findings, image_id_seed) in report_specs.items():
            document = report(copy.deepcopy(findings))
            document["CreatedAt"] = rfc3339(now - dt.timedelta(minutes=1))
            document["ArtifactName"] = artifact
            document["Metadata"]["ImageID"] = "sha256:" + image_id_seed * 64
            document["Metadata"]["ImageConfig"]["architecture"] = architecture
            path = root / f"{name}.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            paths[name] = path
        return paths

    def invoke(self, paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
        today = dt.date.today()
        return subprocess.run(
            [
                "python3",
                str(BASELINE_BUILDER),
                "--trivy-version-json",
                str(paths["version"]),
                "--vulnerability-db",
                str(paths["vulnerability_db"]),
                "--vulnerability-db-metadata",
                str(paths["vulnerability_metadata"]),
                "--java-db",
                str(paths["java_db"]),
                "--java-db-metadata",
                str(paths["java_metadata"]),
                "--vulnerability-db-manifest",
                str(paths["vulnerability_manifest"]),
                "--java-db-manifest",
                str(paths["java_manifest"]),
                "--delivery-amd64-report",
                str(paths["delivery_amd64"]),
                "--delivery-arm64-report",
                str(paths["delivery_arm64"]),
                "--development-amd64-report",
                str(paths["development_amd64"]),
                "--development-arm64-report",
                str(paths["development_arm64"]),
                "--reviewed-at",
                today.isoformat(),
                "--review-before",
                (today + dt.timedelta(days=14)).isoformat(),
                "--baseline-output",
                str(paths["baseline"]),
                "--manifest-output",
                str(paths["manifest"]),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_builder_generates_schema_four_baseline_and_four_report_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-baseline-") as directory:
            paths = self.create_inputs(Path(directory))
            result = self.invoke(paths)

            self.assertEqual(0, result.returncode, result.stderr)
            baseline_document = json.loads(paths["baseline"].read_text(encoding="utf-8"))
            manifest_document = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            self.assertEqual(4, baseline_document["schema_version"])
            self.assertEqual(
                {
                    "vulnerability": json.loads(
                        paths["version"].read_text(encoding="utf-8")
                    )["VulnerabilityDB"]["UpdatedAt"],
                    "java": json.loads(paths["version"].read_text(encoding="utf-8"))[
                        "JavaDB"
                    ]["UpdatedAt"],
                },
                baseline_document["minimum_db_updated_at"],
            )
            self.assertEqual(1, len(baseline_document["images"]["delivery"]))
            self.assertEqual(1, len(baseline_document["images"]["development"]))
            self.assertEqual(
                {"amd64", "arm64"}, baseline_document["coverage"]["delivery"].keys()
            )
            self.assertEqual(
                ALL_SEVERITIES,
                tuple(
                    item["Severity"]
                    for item in baseline_document["vulnerability_coverage"]["delivery"][
                        "amd64"
                    ]
                ),
            )

            self.assertEqual(1, manifest_document["schema_version"])
            self.assertEqual(
                hashlib.sha256(paths["vulnerability_db"].read_bytes()).hexdigest(),
                manifest_document["databases"]["vulnerability"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(paths["java_db"].read_bytes()).hexdigest(),
                manifest_document["databases"]["java"]["sha256"],
            )
            self.assertEqual(
                oci_evidence(
                    "vulnerability", paths["vulnerability_manifest"].read_text()
                ),
                manifest_document["databases"]["vulnerability"]["oci"],
            )
            self.assertEqual(
                hashlib.sha256(paths["delivery_arm64"].read_bytes()).hexdigest(),
                manifest_document["reports"]["delivery"]["arm64"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(paths["baseline"].read_bytes()).hexdigest(),
                manifest_document["baseline"]["sha256"],
            )
            self.assertEqual(
                0,
                manifest_document["reports"]["development"]["arm64"][
                    "fixable_high_critical_count"
                ],
            )

    def test_builder_rejects_cross_architecture_drift_and_fixable_findings(self) -> None:
        mutations = {
            "cross-architecture drift": [
                vulnerability(),
                vulnerability("CVE-2026-9999", package="new-package"),
            ],
            "fixable finding": [vulnerability(fixed_version="1.0.1")],
        }
        for label, arm64_findings in mutations.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory(prefix="trivy-baseline-negative-") as directory:
                    paths = self.create_inputs(
                        Path(directory), arm64_delivery_findings=arm64_findings
                    )
                    result = self.invoke(paths)
                    self.assertNotEqual(0, result.returncode)
                    self.assertFalse(paths["baseline"].exists())
                    self.assertFalse(paths["manifest"].exists())

    def test_builder_rejects_duplicate_image_ids_and_output_input_aliases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-baseline-identity-") as directory:
            root = Path(directory)
            paths = self.create_inputs(root)
            delivery = json.loads(paths["delivery_amd64"].read_text(encoding="utf-8"))
            development = json.loads(
                paths["development_amd64"].read_text(encoding="utf-8")
            )
            development["Metadata"]["ImageID"] = delivery["Metadata"]["ImageID"]
            paths["development_amd64"].write_text(
                json.dumps(development), encoding="utf-8"
            )
            duplicate = self.invoke(paths)
            self.assertNotEqual(0, duplicate.returncode)
            self.assertIn("ImageID", duplicate.stderr)
            self.assertFalse(paths["baseline"].exists())
            self.assertFalse(paths["manifest"].exists())

        with tempfile.TemporaryDirectory(prefix="trivy-baseline-alias-") as directory:
            paths = self.create_inputs(Path(directory))
            original_version = paths["version"].read_bytes()
            paths["manifest"] = paths["version"]
            aliased = self.invoke(paths)
            self.assertNotEqual(0, aliased.returncode)
            self.assertIn("must not alias an input", aliased.stderr)
            self.assertEqual(original_version, paths["version"].read_bytes())
            self.assertFalse(paths["baseline"].exists())

    def test_builder_rolls_back_when_the_output_pair_cannot_be_published(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-baseline-publish-") as directory:
            paths = self.create_inputs(Path(directory))
            paths["baseline"].write_text("preexisting baseline\n", encoding="utf-8")
            paths["manifest"] = Path("/dev/null/manifest.json")

            result = self.invoke(paths)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("invalid Trivy baseline review input", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(
                "preexisting baseline\n",
                paths["baseline"].read_text(encoding="utf-8"),
            )

    def test_builder_rolls_back_when_interrupted_between_pair_replacements(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-baseline-interrupt-") as directory:
            root = Path(directory)
            baseline_path = root / "baseline.json"
            manifest_path = root / "manifest.json"
            baseline_path.write_bytes(b"old baseline\n")
            manifest_path.write_bytes(b"old manifest\n")

            sys.path.insert(0, str(BASELINE_BUILDER.parent))
            try:
                namespace = runpy.run_path(str(BASELINE_BUILDER))
            finally:
                sys.path.pop(0)

            original_replace = Path.replace
            interrupted = False

            def interrupt_before_manifest_replace(
                source: Path, target: Path
            ) -> Path:
                nonlocal interrupted
                if Path(target) == manifest_path and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt
                return original_replace(source, target)

            with mock.patch.object(Path, "replace", interrupt_before_manifest_replace):
                with self.assertRaises(KeyboardInterrupt):
                    namespace["write_atomic_pair"](
                        (
                            (baseline_path, b"new baseline\n"),
                            (manifest_path, b"new manifest\n"),
                        )
                    )

            self.assertTrue(interrupted)
            self.assertEqual(b"old baseline\n", baseline_path.read_bytes())
            self.assertEqual(b"old manifest\n", manifest_path.read_bytes())

            interrupted = False

            def interrupt_after_baseline_replace(
                source: Path, target: Path
            ) -> Path:
                nonlocal interrupted
                result = original_replace(source, target)
                if Path(target) == baseline_path and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt
                return result

            with mock.patch.object(Path, "replace", interrupt_after_baseline_replace):
                with self.assertRaises(KeyboardInterrupt):
                    namespace["write_atomic_pair"](
                        (
                            (baseline_path, b"new baseline\n"),
                            (manifest_path, b"new manifest\n"),
                        )
                    )

            self.assertTrue(interrupted)
            self.assertEqual(b"old baseline\n", baseline_path.read_bytes())
            self.assertEqual(b"old manifest\n", manifest_path.read_bytes())

    def test_builder_preserves_legitimate_lower_severity_architecture_differences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trivy-baseline-lower-severity-") as directory:
            paths = self.create_inputs(Path(directory))
            arm64_report = json.loads(
                paths["development_arm64"].read_text(encoding="utf-8")
            )
            arm64_report["Results"][0]["Vulnerabilities"].append(
                vulnerability(
                    "CVE-2026-7777",
                    package="arm64-only-package",
                    severity="LOW",
                )
            )
            paths["development_arm64"].write_text(
                json.dumps(arm64_report), encoding="utf-8"
            )

            result = self.invoke(paths)
            self.assertEqual(0, result.returncode, result.stderr)
            baseline_document = json.loads(paths["baseline"].read_text(encoding="utf-8"))
            amd64_low = next(
                entry
                for entry in baseline_document["vulnerability_coverage"]["development"][
                    "amd64"
                ]
                if entry["Severity"] == "LOW"
            )
            arm64_low = next(
                entry
                for entry in baseline_document["vulnerability_coverage"]["development"][
                    "arm64"
                ]
                if entry["Severity"] == "LOW"
            )
            self.assertEqual(amd64_low["FindingCount"] + 1, arm64_low["FindingCount"])
            self.assertNotEqual(
                amd64_low["FindingInventorySHA256"],
                arm64_low["FindingInventorySHA256"],
            )


if __name__ == "__main__":
    unittest.main()
