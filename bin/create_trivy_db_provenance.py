#!/usr/bin/env python3
"""Create a strict, content-addressed attestation for one frozen Trivy scan set."""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import validate_trivy_oci_manifest as oci_manifest


EXPECTED_TRIVY_VERSION = "0.70.0"
EXPECTED_REPORTS = {
    "delivery": "mtics-al-folio:ci",
    "development": "mtics-devcontainer:ci",
}
ORDERED_SEVERITIES = ("CRITICAL", "HIGH", "LOW", "MEDIUM", "UNKNOWN")
MAX_CLOCK_SKEW_SECONDS = 300
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
RFC3339_UTC_PATTERN = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,9})?Z"
)


class DuplicateJSONKeyError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class UtcTimestamp:
    epoch_seconds: int
    nanosecond: int
    text: str = field(compare=False)


def reject(message: str) -> NoReturn:
    print(f"invalid Trivy provenance input: {message}", file=sys.stderr)
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


def load_json(path: Path, label: str) -> tuple[object, bytes]:
    try:
        if not path.is_file():
            reject(f"missing {label}: {path}")
        payload = path.read_bytes()
        if not payload:
            reject(f"empty {label}: {path}")
        document = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_nonstandard_json_constant,
        )
        return document, payload
    except (OSError, UnicodeError) as error:
        reject(f"cannot read {label} {path}: {error}")
    except DuplicateJSONKeyError as error:
        reject(str(error))
    except json.JSONDecodeError as error:
        reject(f"malformed JSON in {label} {path}: {error}")
    except ValueError as error:
        reject(f"malformed JSON in {label} {path}: {error}")


def require_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        reject(f"{location} must be a canonical non-empty string")
    return value


def parse_timestamp(value: object, location: str) -> tuple[str, UtcTimestamp]:
    text = require_string(value, location)
    match = RFC3339_UTC_PATTERN.fullmatch(text)
    if match is None:
        reject(f"{location} must be an RFC3339 UTC timestamp ending in Z")
    fraction = (match.group("fraction") or "")[1:]
    nanosecond = int((fraction + "000000000")[:9]) if fraction else 0
    try:
        parsed = dt.datetime.strptime(
            f"{match.group('date')}T{match.group('time')}", "%Y-%m-%dT%H:%M:%S"
        )
    except ValueError:
        reject(f"{location} is not a valid calendar timestamp")
    return text, UtcTimestamp(calendar.timegm(parsed.timetuple()), nanosecond, text)


def reject_future_timestamp(timestamp: UtcTimestamp, location: str) -> None:
    timestamp_nanoseconds = timestamp.epoch_seconds * 1_000_000_000 + timestamp.nanosecond
    if timestamp_nanoseconds > time.time_ns() + MAX_CLOCK_SKEW_SECONDS * 1_000_000_000:
        reject(f"{location} must not be in the future")


def hash_file(path: Path, label: str) -> str:
    try:
        if not path.is_file():
            reject(f"missing {label}: {path}")
        if path.stat().st_size <= 0:
            reject(f"empty {label}: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        reject(f"cannot hash {label} {path}: {error}")


def paths_alias(left: Path, right: Path) -> bool:
    """Return whether two paths resolve to the same existing or future file."""
    try:
        if left.resolve() == right.resolve():
            return True
        if left.exists() and right.exists():
            return os.path.samefile(left, right)
    except OSError as error:
        reject(f"cannot compare paths {left} and {right}: {error}")
    return False


def write_atomic(path: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
        temporary_path = None
    except OSError as error:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        reject(f"cannot write provenance {path}: {error}")


def normalize_database(
    version_document: dict[str, object],
    *,
    version_field: str,
    database_label: str,
    expected_schema_version: int,
    database_path: Path,
    metadata_path: Path,
    manifest_path: Path,
) -> tuple[dict[str, object], dict[str, UtcTimestamp]]:
    raw = version_document.get(version_field)
    if not isinstance(raw, dict):
        reject(f"Trivy version JSON {version_field} must be an object")
    expected_keys = {"Version", "UpdatedAt", "NextUpdate", "DownloadedAt"}
    if set(raw) != expected_keys:
        reject(
            f"Trivy version JSON {version_field} must contain exactly {sorted(expected_keys)}"
        )
    if type(raw["Version"]) is not int or raw["Version"] != expected_schema_version:
        reject(
            f"Trivy version JSON {version_field}.Version must be integer "
            f"{expected_schema_version}"
        )

    metadata_document, metadata_payload = load_json(
        metadata_path, f"{database_label} database metadata"
    )
    if metadata_document != raw:
        reject(f"Trivy version JSON {version_field} does not match the frozen metadata")

    updated_text, updated = parse_timestamp(raw["UpdatedAt"], f"{version_field}.UpdatedAt")
    next_text, next_update = parse_timestamp(raw["NextUpdate"], f"{version_field}.NextUpdate")
    downloaded_text, downloaded = parse_timestamp(
        raw["DownloadedAt"], f"{version_field}.DownloadedAt"
    )
    if updated > downloaded:
        reject(f"{version_field}.UpdatedAt must not be later than DownloadedAt")
    if next_update <= updated:
        reject(f"{version_field}.NextUpdate must be later than UpdatedAt")

    try:
        oci_evidence = oci_manifest.load_and_validate(manifest_path, database_label)
    except oci_manifest.ManifestError as error:
        reject(f"invalid {database_label} OCI manifest: {error}")

    return (
        {
            "schema_version": expected_schema_version,
            "updated_at": updated_text,
            "next_update": next_text,
            "downloaded_at": downloaded_text,
            "sha256": hash_file(database_path, f"{database_label} database"),
            "metadata_sha256": hashlib.sha256(metadata_payload).hexdigest(),
            "oci": oci_evidence,
        },
        {
            "updated_at": updated,
            "next_update": next_update,
            "downloaded_at": downloaded,
        },
    )


def normalize_report(
    path: Path,
    image: str,
    captured_at: UtcTimestamp,
    database_times: dict[str, dict[str, UtcTimestamp]],
) -> dict[str, object]:
    document, payload = load_json(path, f"{image} report")
    if not isinstance(document, dict):
        reject(f"{image} report top level must be an object")
    if document.get("SchemaVersion") != 2 or type(document.get("SchemaVersion")) is not int:
        reject(f"{image} report SchemaVersion must be integer 2")
    trivy = document.get("Trivy")
    if not isinstance(trivy, dict) or trivy.get("Version") != EXPECTED_TRIVY_VERSION:
        reject(f"{image} report must identify Trivy {EXPECTED_TRIVY_VERSION}")
    artifact_name = require_string(document.get("ArtifactName"), f"{image}.ArtifactName")
    if artifact_name != EXPECTED_REPORTS[image]:
        reject(f"{image} report ArtifactName must be {EXPECTED_REPORTS[image]!r}")
    if document.get("ArtifactType") != "container_image":
        reject(f"{image} report ArtifactType must be container_image")

    created_text, created_at = parse_timestamp(document.get("CreatedAt"), f"{image}.CreatedAt")
    reject_future_timestamp(created_at, f"{image}.CreatedAt")
    metadata = document.get("Metadata")
    if not isinstance(metadata, dict):
        reject(f"{image} report Metadata must be an object")
    image_id = require_string(metadata.get("ImageID"), f"{image}.Metadata.ImageID")
    if IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        reject(f"{image}.Metadata.ImageID must be a lowercase sha256 digest")
    image_config = metadata.get("ImageConfig")
    if not isinstance(image_config, dict):
        reject(f"{image} report Metadata.ImageConfig must be an object")
    architecture = require_string(
        image_config.get("architecture"), f"{image}.Metadata.ImageConfig.architecture"
    )
    if architecture not in {"amd64", "arm64"}:
        reject(f"{image} report architecture must be amd64 or arm64")

    if created_at > captured_at:
        reject(f"{image} report CreatedAt must not be later than provenance capture")
    for database_name, times in database_times.items():
        if created_at < times["downloaded_at"]:
            reject(f"{image} report predates the {database_name} database download")
        if created_at >= times["next_update"]:
            reject(f"{image} report used an expired {database_name} database")

    severity_counts = {severity: 0 for severity in ORDERED_SEVERITIES}
    results = document.get("Results")
    if not isinstance(results, list) or not results:
        reject(f"{image} report Results must be a non-empty array")
    for result_index, result in enumerate(results):
        if not isinstance(result, dict):
            reject(f"{image}.Results[{result_index}] must be an object")
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            reject(f"{image}.Results[{result_index}].Vulnerabilities must be an array")
        for finding_index, finding in enumerate(vulnerabilities):
            if not isinstance(finding, dict):
                reject(
                    f"{image}.Results[{result_index}].Vulnerabilities[{finding_index}] "
                    "must be an object"
                )
            severity = finding.get("Severity")
            if severity not in severity_counts:
                reject(
                    f"{image}.Results[{result_index}].Vulnerabilities[{finding_index}].Severity "
                    "is not recognized"
                )
            severity_counts[severity] += 1

    return {
        "artifact_name": artifact_name,
        "architecture": architecture,
        "created_at": created_text,
        "image_id": image_id,
        "severity_counts": severity_counts,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trivy-version-json", required=True, type=Path)
    parser.add_argument("--vulnerability-db", required=True, type=Path)
    parser.add_argument("--vulnerability-db-metadata", required=True, type=Path)
    parser.add_argument("--java-db", required=True, type=Path)
    parser.add_argument("--java-db-metadata", required=True, type=Path)
    parser.add_argument("--vulnerability-db-manifest", required=True, type=Path)
    parser.add_argument("--java-db-manifest", required=True, type=Path)
    parser.add_argument(
        "--expected-architecture", required=True, choices=("amd64", "arm64")
    )
    parser.add_argument("--delivery-report", required=True, type=Path)
    parser.add_argument("--development-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    input_paths = (
        arguments.trivy_version_json,
        arguments.vulnerability_db,
        arguments.vulnerability_db_metadata,
        arguments.java_db,
        arguments.java_db_metadata,
        arguments.vulnerability_db_manifest,
        arguments.java_db_manifest,
        arguments.delivery_report,
        arguments.development_report,
    )
    for input_path in input_paths:
        if paths_alias(arguments.output, input_path):
            reject(f"output {arguments.output} must not alias an input {input_path}")

    version_document, _payload = load_json(arguments.trivy_version_json, "Trivy version JSON")
    if not isinstance(version_document, dict):
        reject("Trivy version JSON top level must be an object")
    if set(version_document) != {"Version", "VulnerabilityDB", "JavaDB"}:
        reject(
            "Trivy version JSON must contain exactly Version, VulnerabilityDB, and JavaDB"
        )
    if version_document["Version"] != EXPECTED_TRIVY_VERSION:
        reject(f"Trivy version must be {EXPECTED_TRIVY_VERSION}")

    database_specs = {
        "vulnerability": (
            "VulnerabilityDB",
            2,
            arguments.vulnerability_db,
            arguments.vulnerability_db_metadata,
            arguments.vulnerability_db_manifest,
        ),
        "java": (
            "JavaDB",
            1,
            arguments.java_db,
            arguments.java_db_metadata,
            arguments.java_db_manifest,
        ),
    }
    databases: dict[str, dict[str, object]] = {}
    database_times: dict[str, dict[str, UtcTimestamp]] = {}
    for database_name, (
        version_field,
        expected_schema_version,
        database_path,
        metadata_path,
        manifest_path,
    ) in database_specs.items():
        databases[database_name], database_times[database_name] = normalize_database(
            version_document,
            version_field=version_field,
            database_label=database_name,
            expected_schema_version=expected_schema_version,
            database_path=database_path,
            metadata_path=metadata_path,
            manifest_path=manifest_path,
        )
    now = dt.datetime.now(dt.timezone.utc)
    captured_at = UtcTimestamp(
        calendar.timegm(now.utctimetuple()), now.microsecond * 1_000, ""
    )
    reports = {
        "delivery": normalize_report(
            arguments.delivery_report, "delivery", captured_at, database_times
        ),
        "development": normalize_report(
            arguments.development_report, "development", captured_at, database_times
        ),
    }
    for image, report in reports.items():
        if report["architecture"] != arguments.expected_architecture:
            reject(
                f"{image} report architecture must be "
                f"{arguments.expected_architecture}"
            )
    if reports["delivery"]["image_id"] == reports["development"]["image_id"]:
        reject("delivery and development reports must identify different ImageIDs")
    for database_name, times in database_times.items():
        if times["downloaded_at"] > captured_at:
            reject(
                f"{database_name} database DownloadedAt must not be later than provenance capture"
            )

    output = {
        "schema_version": 1,
        "captured_at": now.isoformat().replace("+00:00", "Z"),
        "trivy_version": EXPECTED_TRIVY_VERSION,
        "databases": databases,
        "reports": reports,
    }
    payload = (json.dumps(output, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    write_atomic(arguments.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
