#!/usr/bin/env python3
"""Fail closed on fixable or newly-unreviewed HIGH/CRITICAL image findings.

Trivy's raw JSON remains the audit record.  This gate deliberately does not
hide unfixed findings: it normalizes the stable security identity of every
finding and requires it to have been reviewed in the short-lived baseline.
"""

from __future__ import annotations

import argparse
import calendar
import collections
import datetime as dt
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import validate_trivy_oci_manifest as oci_manifest


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
CORE_IDENTITY_FIELDS = BASELINE_FIELDS[:6]
FINDING_INVENTORY_FIELDS = BASELINE_FIELDS + ("FixedVersion",)
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
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
RFC3339_UTC_PATTERN = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,9})?Z"
)
ALL_SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
ORDERED_SEVERITIES = ("CRITICAL", "HIGH", "LOW", "MEDIUM", "UNKNOWN")
HIGH_RISK_SEVERITIES = {"HIGH", "CRITICAL"}
ALLOWED_STATUSES = {
    "unknown",
    "not_affected",
    "affected",
    "fixed",
    "under_investigation",
    "will_not_fix",
    "fix_deferred",
    "end_of_life",
}
MAX_REVIEW_WINDOW_DAYS = 30
MAX_CLOCK_SKEW_SECONDS = 300
EXPECTED_TRIVY_VERSION = "0.70.0"
PROVENANCE_DATABASE_FIELDS = (
    "schema_version",
    "updated_at",
    "next_update",
    "downloaded_at",
    "sha256",
    "metadata_sha256",
    "oci",
)
PROVENANCE_REPORT_FIELDS = (
    "artifact_name",
    "architecture",
    "created_at",
    "image_id",
    "severity_counts",
    "sha256",
)
VULNERABILITY_COVERAGE_FIELDS = (
    "Severity",
    "FindingCount",
    "FindingInventorySHA256",
)


class DuplicateJSONKeyError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class UtcTimestamp:
    epoch_seconds: int
    nanosecond: int
    text: str = field(compare=False)


def reject(kind: str, message: str) -> NoReturn:
    print(f"invalid {kind}: {message}", file=sys.stderr)
    raise SystemExit(2)


def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJSONKeyError(f"duplicate object key {key!r}")
        document[key] = value
    return document


def reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant {value}")


def load_document_with_digest(path: Path, kind: str) -> tuple[object, str]:
    try:
        if not path.is_file():
            reject(kind, f"missing file: {path}")
        payload = path.read_bytes()
        if not payload:
            reject(kind, f"empty file: {path}")
        document = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_nonstandard_json_constant,
        )
        return document, hashlib.sha256(payload).hexdigest()
    except (OSError, UnicodeError) as error:
        reject(kind, f"cannot read {path}: {error}")
    except DuplicateJSONKeyError as error:
        reject(kind, str(error))
    except json.JSONDecodeError as error:
        reject(kind, f"malformed JSON in {path}: {error}")
    except ValueError as error:
        reject(kind, f"malformed JSON in {path}: {error}")


def load_document(path: Path, kind: str) -> object:
    return load_document_with_digest(path, kind)[0]


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


def parse_timestamp(value: object, field: str, kind: str) -> tuple[str, UtcTimestamp]:
    text = require_nonempty_string(value, field, kind)
    match = RFC3339_UTC_PATTERN.fullmatch(text)
    if match is None:
        reject(kind, f"{field} must be an RFC3339 UTC timestamp ending in Z")
    fraction = (match.group("fraction") or "")[1:]
    nanosecond = int((fraction + "000000000")[:9]) if fraction else 0
    try:
        parsed = dt.datetime.strptime(
            f"{match.group('date')}T{match.group('time')}", "%Y-%m-%dT%H:%M:%S"
        )
    except ValueError:
        reject(kind, f"{field} is not a valid calendar timestamp")
    return text, UtcTimestamp(calendar.timegm(parsed.timetuple()), nanosecond, text)


def reject_future_timestamp(timestamp: UtcTimestamp, field: str, kind: str) -> None:
    timestamp_nanoseconds = timestamp.epoch_seconds * 1_000_000_000 + timestamp.nanosecond
    if timestamp_nanoseconds > time.time_ns() + MAX_CLOCK_SKEW_SECONDS * 1_000_000_000:
        reject(kind, f"{field} must not be in the future")


def hash_file(path: Path, field: str, kind: str) -> str:
    try:
        if not path.is_file():
            reject(kind, f"missing {field}: {path}")
        if path.stat().st_size <= 0:
            reject(kind, f"empty {field}: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        reject(kind, f"cannot hash {field} {path}: {error}")


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
    if status not in ALLOWED_STATUSES:
        reject("Trivy baseline", f"{location}.Status is not recognized")
    if status in {"fixed", "not_affected"}:
        reject(
            "Trivy baseline",
            f"{location} must not approve a {status} finding",
        )
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


def normalize_vulnerability_coverage_entry(
    entry: object, location: str
) -> tuple[str, int, str]:
    if not isinstance(entry, dict) or set(entry) != set(VULNERABILITY_COVERAGE_FIELDS):
        reject(
            "Trivy baseline",
            f"{location} must contain exactly {', '.join(VULNERABILITY_COVERAGE_FIELDS)}",
        )
    severity = require_nonempty_string(
        entry["Severity"], f"{location}.Severity", "Trivy baseline"
    )
    if severity not in ALL_SEVERITIES:
        reject("Trivy baseline", f"{location}.Severity is not recognized")
    count = entry["FindingCount"]
    if type(count) is not int or count < 0:
        reject("Trivy baseline", f"{location}.FindingCount must be a non-negative integer")
    digest = require_nonempty_string(
        entry["FindingInventorySHA256"],
        f"{location}.FindingInventorySHA256",
        "Trivy baseline",
    )
    if SHA256_PATTERN.fullmatch(digest) is None:
        reject(
            "Trivy baseline",
            f"{location}.FindingInventorySHA256 must be a lowercase SHA-256 digest",
        )
    return severity, count, digest


def load_baseline(
    path: Path, image: str
) -> tuple[
    set[tuple[str, ...]],
    dict[str, tuple[tuple[str, str, int, str], ...]],
    dict[str, UtcTimestamp],
    dict[str, tuple[tuple[str, int, str], ...]],
]:
    document = load_document(path, "Trivy baseline")
    if not isinstance(document, dict):
        reject("Trivy baseline", "top level must be an object")
    expected_keys = {
        "schema_version",
        "reviewed_at",
        "review_before",
        "minimum_db_updated_at",
        "images",
        "coverage",
        "vulnerability_coverage",
    }
    if set(document) != expected_keys:
        reject("Trivy baseline", f"top level must contain exactly {sorted(expected_keys)}")
    if type(document["schema_version"]) is not int or document["schema_version"] != 4:
        reject("Trivy baseline", "schema_version must be integer 4")

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

    minimum_db_document = document["minimum_db_updated_at"]
    if not isinstance(minimum_db_document, dict) or set(minimum_db_document) != {
        "vulnerability",
        "java",
    }:
        reject(
            "Trivy baseline",
            "minimum_db_updated_at must contain exactly vulnerability and java",
        )
    minimum_db_updated_at = {
        database_name: parse_timestamp(
            minimum_db_document[database_name],
            f"minimum_db_updated_at.{database_name}",
            "Trivy baseline",
        )[1]
        for database_name in ("vulnerability", "java")
    }

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
        core_identities = [entry[: len(CORE_IDENTITY_FIELDS)] for entry in normalized]
        if len(core_identities) != len(set(core_identities)):
            reject(
                "Trivy baseline",
                f"images.{image_name} contains conflicting core finding identity",
            )
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

    vulnerability_coverage = document["vulnerability_coverage"]
    if not isinstance(vulnerability_coverage, dict) or set(vulnerability_coverage) != set(
        EXPECTED_IMAGES
    ):
        reject(
            "Trivy baseline",
            f"vulnerability_coverage must contain exactly {', '.join(EXPECTED_IMAGES)}",
        )
    normalized_vulnerability_coverage: dict[
        str, dict[str, tuple[tuple[str, int, str], ...]]
    ] = {}
    for image_name in EXPECTED_IMAGES:
        architectures = vulnerability_coverage[image_name]
        if not isinstance(architectures, dict) or set(architectures) != set(
            EXPECTED_ARCHITECTURES
        ):
            reject(
                "Trivy baseline",
                f"vulnerability_coverage.{image_name} must contain exactly "
                f"{', '.join(EXPECTED_ARCHITECTURES)}",
            )
        normalized_vulnerability_coverage[image_name] = {}
        for architecture in EXPECTED_ARCHITECTURES:
            entries = architectures[architecture]
            location = f"vulnerability_coverage.{image_name}.{architecture}"
            if not isinstance(entries, list):
                reject("Trivy baseline", f"{location} must be an array")
            normalized_entries = tuple(
                normalize_vulnerability_coverage_entry(entry, f"{location}[{index}]")
                for index, entry in enumerate(entries)
            )
            if tuple(entry[0] for entry in normalized_entries) != ORDERED_SEVERITIES:
                reject(
                    "Trivy baseline",
                    f"{location} must contain every severity in canonical order",
                )
            normalized_vulnerability_coverage[image_name][architecture] = normalized_entries

    return (
        normalized_images[image],
        normalized_coverage[image],
        minimum_db_updated_at,
        normalized_vulnerability_coverage[image],
    )


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
    str,
    UtcTimestamp,
    str,
    str,
    str,
    dict[str, int],
    tuple[tuple[str, int, str], ...],
]:
    document, report_digest = load_document_with_digest(path, "Trivy report")
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
    created_at_text, created_at = parse_timestamp(
        document.get("CreatedAt"), "CreatedAt", "Trivy report"
    )
    reject_future_timestamp(created_at, "CreatedAt", "Trivy report")
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
    image_id = require_nonempty_string(
        metadata.get("ImageID"), "Metadata.ImageID", "Trivy report"
    )
    if IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        reject("Trivy report", "Metadata.ImageID must be a lowercase sha256 digest")
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
    seen_core_identities: set[tuple[str, ...]] = set()
    finding_inventory: dict[str, list[tuple[str, ...]]] = {
        severity: [] for severity in ORDERED_SEVERITIES
    }
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
            if "Class" in finding or "Type" in finding:
                reject(
                    "Trivy report",
                    f"{finding_location} must not override its result Class or Type",
                )
            identity = {
                **finding,
                "Class": result_class,
                "Type": result_type,
            }
            severity = require_nonempty_string(
                identity.get("Severity"),
                f"{finding_location}.Severity",
                "Trivy report",
            )
            if severity not in ALL_SEVERITIES:
                reject(
                    "Trivy report",
                    f"{finding_location}.Severity must be one of {sorted(ALL_SEVERITIES)}",
                )
            normalized_values: list[str] = []
            for field in BASELINE_FIELDS:
                if field == "PkgID" and severity not in HIGH_RISK_SEVERITIES:
                    normalized_values.append(
                        optional_canonical_string(
                            identity.get(field), f"{finding_location}.{field}"
                        )
                    )
                else:
                    normalized_values.append(
                        require_nonempty_string(
                            identity.get(field),
                            f"{finding_location}.{field}",
                            "Trivy report",
                        )
                    )
            normalized = tuple(normalized_values)
            core_identity = normalized[: len(CORE_IDENTITY_FIELDS)]
            if core_identity in seen_core_identities:
                reject(
                    "Trivy report",
                    f"conflicting core finding identity at {finding_location}",
                )
            seen_core_identities.add(core_identity)
            status = normalized[BASELINE_FIELDS.index("Status")]
            if status not in ALLOWED_STATUSES:
                reject(
                    "Trivy report",
                    f"{finding_location}.Status is not recognized",
                )
            if status == "not_affected":
                reject(
                    "Trivy report",
                    f"{finding_location}.Status=not_affected must not appear in active findings",
                )
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
            finding_inventory[severity].append(normalized + (fixed_version,))
            if severity in HIGH_RISK_SEVERITIES:
                if status == "fixed" or fixed_version:
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
    severity_counts = {
        severity: len(finding_inventory[severity]) for severity in ORDERED_SEVERITIES
    }
    vulnerability_coverage = tuple(
        (
            severity,
            severity_counts[severity],
            hashlib.sha256(
                json.dumps(
                    sorted(finding_inventory[severity]),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )
        for severity in ORDERED_SEVERITIES
    )
    return (
        residuals,
        fixable,
        architecture,
        tuple(sorted(observed_coverage)),
        created_at_text,
        created_at,
        trivy_version,
        image_id,
        report_digest,
        severity_counts,
        vulnerability_coverage,
    )


def validate_provenance_database(
    entry: object,
    *,
    database_name: str,
    expected_schema_version: int,
    database_path: Path,
    metadata_path: Path,
    manifest_path: Path,
    minimum_updated_at: UtcTimestamp,
    captured_at: UtcTimestamp,
) -> dict[str, UtcTimestamp]:
    location = f"databases.{database_name}"
    if not isinstance(entry, dict) or set(entry) != set(PROVENANCE_DATABASE_FIELDS):
        reject(
            "Trivy provenance",
            f"{location} must contain exactly {list(PROVENANCE_DATABASE_FIELDS)}",
        )
    if (
        type(entry["schema_version"]) is not int
        or entry["schema_version"] != expected_schema_version
    ):
        reject(
            "Trivy provenance",
            f"{location}.schema_version must be integer {expected_schema_version}",
        )
    updated_text, updated_at = parse_timestamp(
        entry["updated_at"], f"{location}.updated_at", "Trivy provenance"
    )
    next_text, next_update = parse_timestamp(
        entry["next_update"], f"{location}.next_update", "Trivy provenance"
    )
    downloaded_text, downloaded_at = parse_timestamp(
        entry["downloaded_at"], f"{location}.downloaded_at", "Trivy provenance"
    )
    if updated_at > downloaded_at:
        reject(
            "Trivy provenance",
            f"{location}.updated_at must not be later than downloaded_at",
        )
    if next_update <= updated_at:
        reject(
            "Trivy provenance",
            f"{location}.next_update must be later than updated_at",
        )
    if updated_at < minimum_updated_at:
        reject(
            "Trivy provenance",
            f"{location}.updated_at is older than the reviewed minimum",
        )
    if downloaded_at > captured_at:
        reject(
            "Trivy provenance",
            f"{location}.downloaded_at must not be later than captured_at",
        )

    digest = require_nonempty_string(
        entry["sha256"], f"{location}.sha256", "Trivy provenance"
    )
    if SHA256_PATTERN.fullmatch(digest) is None:
        reject(
            "Trivy provenance",
            f"{location}.sha256 must be a lowercase SHA-256 digest",
        )
    if digest != hash_file(database_path, database_name, "Trivy provenance"):
        reject(
            "Trivy provenance",
            f"{location}.sha256 does not match the frozen database bytes",
        )

    metadata_digest = require_nonempty_string(
        entry["metadata_sha256"], f"{location}.metadata_sha256", "Trivy provenance"
    )
    if SHA256_PATTERN.fullmatch(metadata_digest) is None:
        reject(
            "Trivy provenance",
            f"{location}.metadata_sha256 must be a lowercase SHA-256 digest",
        )
    metadata_document, actual_metadata_digest = load_document_with_digest(
        metadata_path, "Trivy provenance"
    )
    if metadata_digest != actual_metadata_digest:
        reject(
            "Trivy provenance",
            f"{location}.metadata_sha256 does not match the metadata bytes",
        )
    expected_metadata = {
        "Version": expected_schema_version,
        "NextUpdate": next_text,
        "UpdatedAt": updated_text,
        "DownloadedAt": downloaded_text,
    }
    if metadata_document != expected_metadata:
        reject(
            "Trivy provenance",
            f"{location} timestamps do not match the frozen metadata",
        )
    try:
        actual_oci_evidence = oci_manifest.load_and_validate(
            manifest_path, database_name
        )
    except oci_manifest.ManifestError as error:
        reject(
            "Trivy provenance",
            f"{location}.oci manifest is invalid: {error}",
        )
    if entry["oci"] != actual_oci_evidence:
        reject(
            "Trivy provenance",
            f"{location}.oci does not match the frozen manifest bytes",
        )
    return {
        "updated_at": updated_at,
        "next_update": next_update,
        "downloaded_at": downloaded_at,
    }


def load_provenance(
    path: Path,
    *,
    image: str,
    vulnerability_db_path: Path,
    vulnerability_metadata_path: Path,
    java_db_path: Path,
    java_metadata_path: Path,
    vulnerability_manifest_path: Path,
    java_manifest_path: Path,
    expected_architecture: str,
    minimum_db_updated_at: dict[str, UtcTimestamp],
    report_created_at_text: str,
    report_created_at: UtcTimestamp,
    report_architecture: str,
    report_trivy_version: str,
    report_image_id: str,
    report_digest: str,
    report_severity_counts: dict[str, int],
) -> None:
    document = load_document(path, "Trivy provenance")
    if not isinstance(document, dict):
        reject("Trivy provenance", "top level must be an object")
    expected_keys = {
        "schema_version",
        "captured_at",
        "trivy_version",
        "databases",
        "reports",
    }
    if set(document) != expected_keys:
        reject(
            "Trivy provenance",
            f"top level must contain exactly {sorted(expected_keys)}",
        )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        reject("Trivy provenance", "schema_version must be integer 1")
    captured_at_text, captured_at = parse_timestamp(
        document["captured_at"], "captured_at", "Trivy provenance"
    )
    reject_future_timestamp(captured_at, "captured_at", "Trivy provenance")
    trivy_version = require_nonempty_string(
        document["trivy_version"], "trivy_version", "Trivy provenance"
    )
    if trivy_version != EXPECTED_TRIVY_VERSION or trivy_version != report_trivy_version:
        reject(
            "Trivy provenance",
            f"trivy_version must match report Trivy {EXPECTED_TRIVY_VERSION}",
        )

    databases_document = document["databases"]
    if not isinstance(databases_document, dict) or set(databases_document) != {
        "vulnerability",
        "java",
    }:
        reject(
            "Trivy provenance",
            "databases must contain exactly vulnerability and java",
        )
    database_specs = {
        "vulnerability": (
            2,
            vulnerability_db_path,
            vulnerability_metadata_path,
            vulnerability_manifest_path,
        ),
        "java": (1, java_db_path, java_metadata_path, java_manifest_path),
    }
    database_times = {
        database_name: validate_provenance_database(
            databases_document[database_name],
            database_name=database_name,
            expected_schema_version=expected_schema_version,
            database_path=database_path,
            metadata_path=metadata_path,
            manifest_path=manifest_path,
            minimum_updated_at=minimum_db_updated_at[database_name],
            captured_at=captured_at,
        )
        for database_name, (
            expected_schema_version,
            database_path,
            metadata_path,
            manifest_path,
        ) in database_specs.items()
    }

    reports_document = document["reports"]
    if not isinstance(reports_document, dict) or set(reports_document) != set(
        EXPECTED_IMAGES
    ):
        reject(
            "Trivy provenance",
            f"reports must contain exactly {', '.join(EXPECTED_IMAGES)}",
        )
    normalized_reports: dict[str, dict[str, object]] = {}
    for report_image in EXPECTED_IMAGES:
        entry = reports_document[report_image]
        location = f"reports.{report_image}"
        if not isinstance(entry, dict) or set(entry) != set(PROVENANCE_REPORT_FIELDS):
            reject(
                "Trivy provenance",
                f"{location} must contain exactly {list(PROVENANCE_REPORT_FIELDS)}",
            )
        artifact_name = require_nonempty_string(
            entry["artifact_name"], f"{location}.artifact_name", "Trivy provenance"
        )
        if artifact_name != EXPECTED_ARTIFACTS[report_image]:
            reject(
                "Trivy provenance",
                f"{location}.artifact_name must be {EXPECTED_ARTIFACTS[report_image]!r}",
            )
        architecture = require_nonempty_string(
            entry["architecture"], f"{location}.architecture", "Trivy provenance"
        )
        if architecture not in EXPECTED_ARCHITECTURES:
            reject(
                "Trivy provenance",
                f"{location}.architecture must be amd64 or arm64",
            )
        if architecture != expected_architecture:
            reject(
                "Trivy provenance",
                f"{location}.architecture must match expected architecture "
                f"{expected_architecture}",
            )
        created_text, created_at = parse_timestamp(
            entry["created_at"], f"{location}.created_at", "Trivy provenance"
        )
        reject_future_timestamp(created_at, f"{location}.created_at", "Trivy provenance")
        image_id = require_nonempty_string(
            entry["image_id"], f"{location}.image_id", "Trivy provenance"
        )
        if IMAGE_ID_PATTERN.fullmatch(image_id) is None:
            reject(
                "Trivy provenance",
                f"{location}.image_id must be a lowercase sha256 digest",
            )
        digest = require_nonempty_string(
            entry["sha256"], f"{location}.sha256", "Trivy provenance"
        )
        if SHA256_PATTERN.fullmatch(digest) is None:
            reject(
                "Trivy provenance",
                f"{location}.sha256 must be a lowercase SHA-256 digest",
            )
        severity_counts = entry["severity_counts"]
        if not isinstance(severity_counts, dict) or tuple(severity_counts) != ORDERED_SEVERITIES:
            reject(
                "Trivy provenance",
                f"{location}.severity_counts must contain every severity in canonical order",
            )
        for severity, count in severity_counts.items():
            if type(count) is not int or count < 0:
                reject(
                    "Trivy provenance",
                    f"{location}.severity_counts.{severity} must be a non-negative integer",
                )
        if created_at > captured_at:
            reject(
                "Trivy provenance",
                f"{location}.created_at must not be later than captured_at {captured_at_text}",
            )
        for database_name, times in database_times.items():
            if created_at < times["downloaded_at"]:
                reject(
                    "Trivy provenance",
                    f"{location}.created_at predates the {database_name} database download",
                )
            if created_at >= times["next_update"]:
                reject(
                    "Trivy provenance",
                    f"{location}.created_at used an expired {database_name} database",
                )
        normalized_reports[report_image] = {
            "artifact_name": artifact_name,
            "architecture": architecture,
            "created_at_text": created_text,
            "created_at": created_at,
            "image_id": image_id,
            "severity_counts": severity_counts,
            "sha256": digest,
        }

    image_ids = [
        normalized_reports[report_image]["image_id"] for report_image in EXPECTED_IMAGES
    ]
    if len(image_ids) != len(set(image_ids)):
        reject(
            "Trivy provenance",
            "delivery and development reports must identify different ImageIDs",
        )

    current = normalized_reports[image]
    if current["created_at_text"] != report_created_at_text:
        reject("Trivy provenance", f"reports.{image}.created_at does not match the report")
    if current["created_at"] != report_created_at:
        reject("Trivy provenance", f"reports.{image}.created_at timestamp mismatch")
    if current["architecture"] != report_architecture:
        reject("Trivy provenance", f"reports.{image}.architecture does not match the report")
    if current["image_id"] != report_image_id:
        reject("Trivy provenance", f"reports.{image}.image_id does not match the report")
    if current["severity_counts"] != report_severity_counts:
        reject(
            "Trivy provenance",
            f"reports.{image}.severity_counts does not match the report",
        )
    if current["sha256"] != report_digest:
        reject("Trivy provenance", f"reports.{image}.sha256 does not match the report bytes")


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
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--vulnerability-db", required=True, type=Path)
    parser.add_argument("--vulnerability-db-metadata", required=True, type=Path)
    parser.add_argument("--java-db", required=True, type=Path)
    parser.add_argument("--java-db-metadata", required=True, type=Path)
    parser.add_argument("--vulnerability-db-manifest", required=True, type=Path)
    parser.add_argument("--java-db-manifest", required=True, type=Path)
    parser.add_argument(
        "--expected-architecture", required=True, choices=EXPECTED_ARCHITECTURES
    )
    parser.add_argument("--image", required=True, choices=EXPECTED_IMAGES)
    arguments = parser.parse_args()

    (
        reviewed,
        reviewed_coverage,
        minimum_db_updated_at,
        reviewed_vulnerability_coverage,
    ) = load_baseline(arguments.baseline, arguments.image)
    (
        residuals,
        fixable,
        architecture,
        observed_coverage,
        report_created_at_text,
        report_created_at,
        report_trivy_version,
        report_image_id,
        report_digest,
        report_severity_counts,
        observed_vulnerability_coverage,
    ) = load_report(arguments.report, expected_artifact=EXPECTED_ARTIFACTS[arguments.image])
    if architecture != arguments.expected_architecture:
        reject(
            "Trivy report",
            f"expected architecture {arguments.expected_architecture}, got {architecture}",
        )
    load_provenance(
        arguments.provenance,
        image=arguments.image,
        vulnerability_db_path=arguments.vulnerability_db,
        vulnerability_metadata_path=arguments.vulnerability_db_metadata,
        java_db_path=arguments.java_db,
        java_metadata_path=arguments.java_db_metadata,
        vulnerability_manifest_path=arguments.vulnerability_db_manifest,
        java_manifest_path=arguments.java_db_manifest,
        expected_architecture=arguments.expected_architecture,
        minimum_db_updated_at=minimum_db_updated_at,
        report_created_at_text=report_created_at_text,
        report_created_at=report_created_at,
        report_architecture=architecture,
        report_trivy_version=report_trivy_version,
        report_image_id=report_image_id,
        report_digest=report_digest,
        report_severity_counts=report_severity_counts,
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

    expected_vulnerability_coverage = reviewed_vulnerability_coverage[architecture]
    if observed_vulnerability_coverage != expected_vulnerability_coverage:
        expected_by_severity = {
            entry[0]: entry for entry in expected_vulnerability_coverage
        }
        observed_by_severity = {
            entry[0]: entry for entry in observed_vulnerability_coverage
        }
        differences = []
        for severity in ORDERED_SEVERITIES:
            expected = expected_by_severity[severity]
            observed = observed_by_severity[severity]
            if expected != observed:
                differences.append(
                    f"{severity} expected count={expected[1]} sha256={expected[2]}, "
                    f"got count={observed[1]} sha256={observed[2]}"
                )
        reject(
            "Trivy report",
            f"all-severity finding coverage differs from reviewed {architecture} evidence: "
            + "; ".join(differences),
        )

    blocking_severity_counts = collections.Counter(
        finding[BASELINE_FIELDS.index("Severity")] for finding in residuals
    )
    package_counts = collections.Counter(
        finding[BASELINE_FIELDS.index("PkgName")] for finding in residuals
    )
    top_packages = ", ".join(
        f"{package}={count}"
        for package, count in sorted(
            package_counts.items(), key=lambda item: (-item[1], item[0])
        )[:5]
    ) or "none"
    print(
        f"{arguments.image}: reviewed residual vulnerabilities: {len(residuals)} "
        f"(CRITICAL={blocking_severity_counts['CRITICAL']}, "
        f"HIGH={blocking_severity_counts['HIGH']}); "
        "exact reviewed-baseline match; "
        f"top packages: {top_packages}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
