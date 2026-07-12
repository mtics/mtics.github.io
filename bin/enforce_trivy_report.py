#!/usr/bin/env python3
"""Fail closed on fixable or newly-unreviewed HIGH/CRITICAL image findings.

Trivy's raw JSON remains the audit record.  This gate deliberately does not
hide unfixed findings: it normalizes the stable security identity of every
finding and requires it to have been reviewed in the short-lived baseline.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NoReturn


BASELINE_FIELDS = (
    "Class",
    "Type",
    "PkgID",
    "PkgName",
    "VulnerabilityID",
    "InstalledVersion",
    "Severity",
    "Status",
)
EXPECTED_IMAGES = ("delivery", "development")
EXPECTED_ARCHITECTURES = ("amd64", "arm64")
EXPECTED_ARTIFACTS = {
    "delivery": "mtics-al-folio:ci",
    "development": "mtics-devcontainer:ci",
}
EXPECTED_RESULT_IDENTITIES = frozenset(
    {
        ("os-pkgs", "debian"),
        ("lang-pkgs", "node-pkg"),
        ("lang-pkgs", "python-pkg"),
        ("lang-pkgs", "gemspec"),
    }
)
COVERAGE_FIELDS = ("Class", "Type", "PackageCount", "PackageInventorySHA256")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
HIGH_RISK_SEVERITIES = {"HIGH", "CRITICAL"}
MAX_REVIEW_WINDOW_DAYS = 30
EXPECTED_TRIVY_VERSION = "0.70.0"


def reject(kind: str, message: str) -> NoReturn:
    print(f"invalid {kind}: {message}", file=sys.stderr)
    raise SystemExit(2)


def load_document(path: Path, kind: str) -> object:
    try:
        if not path.is_file():
            reject(kind, f"missing file: {path}")
        if path.stat().st_size == 0:
            reject(kind, f"empty file: {path}")
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError) as error:
        reject(kind, f"cannot read {path}: {error}")
    except json.JSONDecodeError as error:
        reject(kind, f"malformed JSON in {path}: {error}")


def require_nonempty_string(value: object, location: str, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        reject(kind, f"{location} must be a non-empty string")
    if value != value.strip():
        reject(kind, f"{location} must not contain leading or trailing whitespace")
    return value


def parse_date(value: object, field: str) -> dt.date:
    text = require_nonempty_string(value, field, "Trivy baseline")
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        reject("Trivy baseline", f"{field} must be an ISO-8601 calendar date")


def normalize_baseline_entry(entry: object, location: str) -> tuple[str, ...]:
    if not isinstance(entry, dict):
        reject("Trivy baseline", f"{location} must be an object")
    if set(entry) != set(BASELINE_FIELDS):
        reject(
            "Trivy baseline",
            f"{location} must contain exactly {', '.join(BASELINE_FIELDS)}",
        )
    normalized = tuple(
        require_nonempty_string(entry[field], f"{location}.{field}", "Trivy baseline")
        for field in BASELINE_FIELDS
    )
    severity = normalized[BASELINE_FIELDS.index("Severity")]
    status = normalized[BASELINE_FIELDS.index("Status")]
    if severity not in HIGH_RISK_SEVERITIES:
        reject("Trivy baseline", f"{location}.Severity must be HIGH or CRITICAL")
    if status.strip().lower() == "fixed":
        reject("Trivy baseline", f"{location} must not approve a fixed finding")
    return normalized


def normalize_coverage_entry(entry: object, location: str) -> tuple[str, str, int, str]:
    if not isinstance(entry, dict):
        reject("Trivy baseline", f"{location} must be an object")
    if set(entry) != set(COVERAGE_FIELDS):
        reject(
            "Trivy baseline",
            f"{location} must contain exactly {', '.join(COVERAGE_FIELDS)}",
        )
    package_class = require_nonempty_string(
        entry["Class"], f"{location}.Class", "Trivy baseline"
    )
    package_type = require_nonempty_string(
        entry["Type"], f"{location}.Type", "Trivy baseline"
    )
    if (package_class, package_type) not in EXPECTED_RESULT_IDENTITIES:
        reject(
            "Trivy baseline",
            f"{location} contains unexpected result identity {package_class}/{package_type}",
        )
    package_count = entry["PackageCount"]
    if type(package_count) is not int or package_count <= 0:
        reject("Trivy baseline", f"{location}.PackageCount must be a positive integer")
    digest = require_nonempty_string(
        entry["PackageInventorySHA256"],
        f"{location}.PackageInventorySHA256",
        "Trivy baseline",
    )
    if SHA256_PATTERN.fullmatch(digest) is None:
        reject(
            "Trivy baseline",
            f"{location}.PackageInventorySHA256 must be a lowercase SHA-256 digest",
        )
    return package_class, package_type, package_count, digest


def load_baseline(
    path: Path, image: str
) -> tuple[
    set[tuple[str, ...]],
    dict[str, tuple[tuple[str, str, int, str], ...]],
]:
    document = load_document(path, "Trivy baseline")
    if not isinstance(document, dict):
        reject("Trivy baseline", "top level must be an object")
    expected_keys = {"schema_version", "reviewed_at", "review_before", "images", "coverage"}
    if set(document) != expected_keys:
        reject("Trivy baseline", f"top level must contain exactly {sorted(expected_keys)}")
    if type(document["schema_version"]) is not int or document["schema_version"] != 2:
        reject("Trivy baseline", "schema_version must be integer 2")

    reviewed_at = parse_date(document["reviewed_at"], "reviewed_at")
    review_before = parse_date(document["review_before"], "review_before")
    today = dt.date.today()
    if reviewed_at > today:
        reject("Trivy baseline", "reviewed_at cannot be in the future")
    if review_before <= today:
        reject("Trivy baseline", f"review_before expired on {review_before.isoformat()}")
    if review_before <= reviewed_at:
        reject("Trivy baseline", "review_before must be later than reviewed_at")
    if (review_before - reviewed_at).days > MAX_REVIEW_WINDOW_DAYS:
        reject(
            "Trivy baseline",
            f"review window must not exceed {MAX_REVIEW_WINDOW_DAYS} days",
        )

    images = document["images"]
    if not isinstance(images, dict) or set(images) != set(EXPECTED_IMAGES):
        reject(
            "Trivy baseline",
            f"images must contain exactly {', '.join(EXPECTED_IMAGES)}",
        )

    normalized_images: dict[str, set[tuple[str, ...]]] = {}
    for image_name in EXPECTED_IMAGES:
        entries = images[image_name]
        if not isinstance(entries, list):
            reject("Trivy baseline", f"images.{image_name} must be an array")
        normalized = [
            normalize_baseline_entry(entry, f"images.{image_name}[{index}]")
            for index, entry in enumerate(entries)
        ]
        if normalized != sorted(normalized):
            reject("Trivy baseline", f"images.{image_name} must be canonically sorted")
        if len(normalized) != len(set(normalized)):
            reject("Trivy baseline", f"images.{image_name} contains duplicate entries")
        normalized_images[image_name] = set(normalized)

    coverage = document["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != set(EXPECTED_IMAGES):
        reject(
            "Trivy baseline",
            f"coverage must contain exactly {', '.join(EXPECTED_IMAGES)}",
        )
    normalized_coverage: dict[
        str, dict[str, tuple[tuple[str, str, int, str], ...]]
    ] = {}
    for image_name in EXPECTED_IMAGES:
        architectures = coverage[image_name]
        if not isinstance(architectures, dict) or set(architectures) != set(
            EXPECTED_ARCHITECTURES
        ):
            reject(
                "Trivy baseline",
                f"coverage.{image_name} must contain exactly "
                f"{', '.join(EXPECTED_ARCHITECTURES)}",
            )
        normalized_coverage[image_name] = {}
        for architecture in EXPECTED_ARCHITECTURES:
            entries = architectures[architecture]
            location = f"coverage.{image_name}.{architecture}"
            if not isinstance(entries, list):
                reject("Trivy baseline", f"{location} must be an array")
            normalized_entries = [
                normalize_coverage_entry(entry, f"{location}[{index}]")
                for index, entry in enumerate(entries)
            ]
            if normalized_entries != sorted(normalized_entries):
                reject("Trivy baseline", f"{location} must be canonically sorted")
            identities = [(entry[0], entry[1]) for entry in normalized_entries]
            if len(identities) != len(set(identities)):
                reject("Trivy baseline", f"{location} contains duplicate result identities")
            if set(identities) != EXPECTED_RESULT_IDENTITIES:
                reject(
                    "Trivy baseline",
                    f"{location} must cover every expected scanner result identity",
                )
            normalized_coverage[image_name][architecture] = tuple(normalized_entries)

    return normalized_images[image], normalized_coverage[image]


def optional_canonical_string(value: object, location: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        reject("Trivy report", f"{location} must be a string when present")
    if value != value.strip():
        reject("Trivy report", f"{location} must not contain surrounding whitespace")
    return value


def package_inventory_coverage(
    packages: object, result_class: str, result_type: str, location: str
) -> tuple[str, str, int, str]:
    if not isinstance(packages, list) or not packages:
        reject("Trivy report", f"{location}.Packages must be a non-empty array")
    rows: list[tuple[str, ...]] = []
    for package_index, package in enumerate(packages):
        package_location = f"{location}.Packages[{package_index}]"
        if not isinstance(package, dict):
            reject("Trivy report", f"{package_location} must be an object")
        identifier = package.get("Identifier")
        if not isinstance(identifier, dict):
            reject("Trivy report", f"{package_location}.Identifier must be an object")
        row = (
            result_class,
            result_type,
            optional_canonical_string(package.get("ID"), f"{package_location}.ID"),
            require_nonempty_string(
                package.get("Name"), f"{package_location}.Name", "Trivy report"
            ),
            require_nonempty_string(
                package.get("Version"), f"{package_location}.Version", "Trivy report"
            ),
            optional_canonical_string(
                package.get("FilePath"), f"{package_location}.FilePath"
            ),
            require_nonempty_string(
                identifier.get("PURL"),
                f"{package_location}.Identifier.PURL",
                "Trivy report",
            ),
        )
        rows.append(row)
    if len(rows) != len(set(rows)):
        reject("Trivy report", f"{location}.Packages contains duplicate canonical rows")
    payload = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return result_class, result_type, len(rows), digest


def load_report(
    path: Path, expected_artifact: str | None = None
) -> tuple[
    set[tuple[str, ...]],
    list[tuple[str, ...]],
    str,
    tuple[tuple[str, str, int, str], ...],
]:
    document = load_document(path, "Trivy report")
    if not isinstance(document, dict):
        reject("Trivy report", "top level must be an object")
    if type(document.get("SchemaVersion")) is not int or document["SchemaVersion"] != 2:
        reject("Trivy report", "SchemaVersion must be integer 2")
    trivy = document.get("Trivy")
    if not isinstance(trivy, dict):
        reject("Trivy report", "Trivy must be an object")
    trivy_version = require_nonempty_string(
        trivy.get("Version"), "Trivy.Version", "Trivy report"
    )
    if trivy_version != EXPECTED_TRIVY_VERSION:
        reject(
            "Trivy report",
            f"Trivy.Version must be {EXPECTED_TRIVY_VERSION!r}, got {trivy_version!r}",
        )
    artifact_name = require_nonempty_string(
        document.get("ArtifactName"), "ArtifactName", "Trivy report"
    )
    if expected_artifact is not None and artifact_name != expected_artifact:
        reject(
            "Trivy report",
            f"ArtifactName must be {expected_artifact!r}, got {artifact_name!r}",
        )
    artifact_type = require_nonempty_string(
        document.get("ArtifactType"), "ArtifactType", "Trivy report"
    )
    if artifact_type != "container_image":
        reject("Trivy report", "ArtifactType must be container_image")
    metadata = document.get("Metadata")
    if not isinstance(metadata, dict):
        reject("Trivy report", "Metadata must be an object")
    image_config = metadata.get("ImageConfig")
    if not isinstance(image_config, dict):
        reject("Trivy report", "Metadata.ImageConfig must be an object")
    architecture = require_nonempty_string(
        image_config.get("architecture"),
        "Metadata.ImageConfig.architecture",
        "Trivy report",
    )
    if architecture not in EXPECTED_ARCHITECTURES:
        reject(
            "Trivy report",
            f"Metadata.ImageConfig.architecture must be one of "
            f"{', '.join(EXPECTED_ARCHITECTURES)}",
        )
    results = document.get("Results")
    if not isinstance(results, list) or not results:
        reject("Trivy report", "Results must be a non-empty array")

    residuals: set[tuple[str, ...]] = set()
    fixable: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    observed_result_identities: set[tuple[str, str]] = set()
    observed_coverage: list[tuple[str, str, int, str]] = []
    for result_index, result in enumerate(results):
        location = f"Results[{result_index}]"
        if not isinstance(result, dict):
            reject("Trivy report", f"{location} must be an object")
        require_nonempty_string(result.get("Target"), f"{location}.Target", "Trivy report")
        result_class = require_nonempty_string(
            result.get("Class"), f"{location}.Class", "Trivy report"
        )
        result_type = require_nonempty_string(
            result.get("Type"), f"{location}.Type", "Trivy report"
        )
        result_identity = (result_class, result_type)
        if result_identity in observed_result_identities:
            reject(
                "Trivy report",
                f"duplicate result identity {result_class}/{result_type} at {location}",
            )
        observed_result_identities.add(result_identity)
        observed_coverage.append(
            package_inventory_coverage(
                result.get("Packages"), result_class, result_type, location
            )
        )
        if "Vulnerabilities" not in result:
            continue
        vulnerabilities = result["Vulnerabilities"]
        if not isinstance(vulnerabilities, list):
            reject("Trivy report", f"{location}.Vulnerabilities must be an array")
        for finding_index, finding in enumerate(vulnerabilities):
            finding_location = f"{location}.Vulnerabilities[{finding_index}]"
            if not isinstance(finding, dict):
                reject("Trivy report", f"{finding_location} must be an object")
            identity = {
                "Class": result["Class"],
                "Type": result["Type"],
                **finding,
            }
            normalized = tuple(
                require_nonempty_string(
                    identity.get(field), f"{finding_location}.{field}", "Trivy report"
                )
                for field in BASELINE_FIELDS
            )
            if normalized in seen:
                reject("Trivy report", f"duplicate normalized finding at {finding_location}")
            seen.add(normalized)
            severity = normalized[BASELINE_FIELDS.index("Severity")]
            status = normalized[BASELINE_FIELDS.index("Status")]
            if severity not in HIGH_RISK_SEVERITIES:
                reject("Trivy report", f"{finding_location}.Severity must be HIGH or CRITICAL")
            fixed_version = finding.get("FixedVersion", "")
            if fixed_version is None:
                fixed_version = ""
            if not isinstance(fixed_version, str):
                reject("Trivy report", f"{finding_location}.FixedVersion must be a string")
            if fixed_version != fixed_version.strip():
                reject(
                    "Trivy report",
                    f"{finding_location}.FixedVersion must not contain surrounding whitespace",
                )
            if status.strip().lower() == "fixed" or fixed_version.strip():
                fixable.append(normalized + (fixed_version,))
            else:
                residuals.add(normalized)

    if observed_result_identities != EXPECTED_RESULT_IDENTITIES:
        missing = sorted(EXPECTED_RESULT_IDENTITIES - observed_result_identities)
        unexpected = sorted(observed_result_identities - EXPECTED_RESULT_IDENTITIES)
        details: list[str] = []
        if missing:
            details.append(
                "missing " + ", ".join(f"{item_class}/{item_type}" for item_class, item_type in missing)
            )
        if unexpected:
            details.append(
                "unexpected "
                + ", ".join(
                    f"{item_class}/{item_type}" for item_class, item_type in unexpected
                )
            )
        reject("Trivy report", "result coverage mismatch: " + "; ".join(details))
    return residuals, fixable, architecture, tuple(sorted(observed_coverage))


def display_finding(finding: tuple[str, ...]) -> str:
    values = dict(zip(BASELINE_FIELDS, finding[: len(BASELINE_FIELDS)]))
    return (
        f"{values['Severity']} {values['VulnerabilityID']} "
        f"{values['PkgName']}@{values['InstalledVersion']} status={values['Status']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--image", required=True, choices=EXPECTED_IMAGES)
    arguments = parser.parse_args()

    reviewed, reviewed_coverage = load_baseline(arguments.baseline, arguments.image)
    residuals, fixable, architecture, observed_coverage = load_report(
        arguments.report, expected_artifact=EXPECTED_ARTIFACTS[arguments.image]
    )
    expected_coverage = reviewed_coverage[architecture]
    if observed_coverage != expected_coverage:
        expected_by_identity = {(entry[0], entry[1]): entry for entry in expected_coverage}
        observed_by_identity = {(entry[0], entry[1]): entry for entry in observed_coverage}
        differences = []
        for identity in sorted(EXPECTED_RESULT_IDENTITIES):
            expected = expected_by_identity[identity]
            observed = observed_by_identity[identity]
            if expected != observed:
                differences.append(
                    f"{identity[0]}/{identity[1]} expected count={expected[2]} "
                    f"sha256={expected[3]}, got count={observed[2]} sha256={observed[3]}"
                )
        reject(
            "Trivy report",
            f"package coverage differs from reviewed {architecture} evidence: "
            + "; ".join(differences),
        )
    if fixable:
        print("fixable HIGH/CRITICAL vulnerabilities detected:", file=sys.stderr)
        for finding in sorted(fixable):
            fixed_version = finding[-1]
            suffix = f" fixed_version={fixed_version}" if fixed_version else ""
            print(f"  - {display_finding(finding)}{suffix}", file=sys.stderr)
        return 1

    if reviewed and not residuals:
        reject(
            "Trivy report",
            "all findings disappeared from a non-empty baseline; refusing a suspiciously empty scan",
        )

    unreviewed = residuals - reviewed
    if unreviewed:
        print(
            "unfixed HIGH/CRITICAL vulnerabilities not present in the reviewed baseline:",
            file=sys.stderr,
        )
        for finding in sorted(unreviewed):
            print(f"  - {display_finding(finding)}", file=sys.stderr)
        return 1

    missing = reviewed - residuals
    if missing:
        print(
            "reviewed baseline findings missing from the report; "
            "refusing truncated or stale scanner evidence:",
            file=sys.stderr,
        )
        for finding in sorted(missing):
            print(f"  - {display_finding(finding)}", file=sys.stderr)
        return 1

    severity_counts = collections.Counter(
        finding[BASELINE_FIELDS.index("Severity")] for finding in residuals
    )
    package_counts = collections.Counter(
        finding[BASELINE_FIELDS.index("PkgName")] for finding in residuals
    )
    top_packages = ", ".join(
        f"{package}={count}" for package, count in package_counts.most_common(5)
    ) or "none"
    print(
        f"{arguments.image}: reviewed residual vulnerabilities: {len(residuals)} "
        f"(CRITICAL={severity_counts['CRITICAL']}, HIGH={severity_counts['HIGH']}); "
        "exact reviewed-baseline match; "
        f"top packages: {top_packages}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
