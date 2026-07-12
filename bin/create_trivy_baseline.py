#!/usr/bin/env python3
"""Generate a reviewed Trivy baseline and four-report evidence manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

import create_trivy_db_provenance as provenance
import enforce_trivy_report as gate


TRIVY_IMAGE = (
    "aquasec/trivy@sha256:"
    "be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e"
)
REPORT_SPECS = {
    ("delivery", "amd64"): "mtics-al-folio:ci",
    ("delivery", "arm64"): "mtics-al-folio:release-arm64",
    ("development", "amd64"): "mtics-devcontainer:ci",
    ("development", "arm64"): "mtics-devcontainer:release-arm64",
}


def reject(message: str) -> NoReturn:
    print(f"invalid Trivy baseline review input: {message}", file=sys.stderr)
    raise SystemExit(2)


def parse_review_dates(reviewed_text: str, review_before_text: str) -> tuple[dt.date, dt.date]:
    try:
        reviewed_at = dt.date.fromisoformat(reviewed_text)
        review_before = dt.date.fromisoformat(review_before_text)
    except ValueError:
        reject("reviewed-at and review-before must be ISO-8601 calendar dates")
    today = dt.date.today()
    if reviewed_at > today:
        reject("reviewed-at cannot be in the future")
    if review_before <= today:
        reject("review-before must be in the future")
    if review_before <= reviewed_at:
        reject("review-before must be later than reviewed-at")
    if (review_before - reviewed_at).days > gate.MAX_REVIEW_WINDOW_DAYS:
        reject(f"review window must not exceed {gate.MAX_REVIEW_WINDOW_DAYS} days")
    return reviewed_at, review_before


def database_snapshot(
    version_document: dict[str, object],
    *,
    database_name: str,
    version_field: str,
    schema_version: int,
    database_path: Path,
    metadata_path: Path,
    manifest_path: Path,
) -> tuple[dict[str, object], dict[str, gate.UtcTimestamp]]:
    entry, times = provenance.normalize_database(
        version_document,
        version_field=version_field,
        database_label=database_name,
        expected_schema_version=schema_version,
        database_path=database_path,
        metadata_path=metadata_path,
        manifest_path=manifest_path,
    )
    return entry, times


def coverage_entries(
    rows: tuple[tuple[str, str, int, str], ...]
) -> list[dict[str, object]]:
    return [dict(zip(gate.COVERAGE_FIELDS, row)) for row in rows]


def vulnerability_coverage_entries(
    rows: tuple[tuple[str, int, str], ...]
) -> list[dict[str, object]]:
    return [dict(zip(gate.VULNERABILITY_COVERAGE_FIELDS, row)) for row in rows]


def baseline_entries(rows: set[tuple[str, ...]]) -> list[dict[str, str]]:
    return [dict(zip(gate.BASELINE_FIELDS, row)) for row in sorted(rows)]


def normalized_inventory_sha256(rows: set[tuple[str, ...]]) -> str:
    payload = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def timestamp_key(timestamp: object) -> tuple[int, int]:
    return int(getattr(timestamp, "epoch_seconds")), int(getattr(timestamp, "nanosecond"))


def paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve() == right.resolve():
            return True
        if left.exists() and right.exists():
            return os.path.samefile(left, right)
    except OSError as error:
        reject(f"cannot compare paths {left} and {right}: {error}")
    return False


def write_atomic_pair(outputs: tuple[tuple[Path, bytes], ...]) -> None:
    """Stage both review files before publishing either, with ordinary-error rollback."""
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    failure: str | None = None
    try:
        for path, payload in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as handle:
                staged[path] = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

        # Preserve both existing public files before replacing either one.
        for path, _payload in outputs:
            if path.exists():
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=path.parent, prefix=f".{path.name}.backup.", delete=False
                ) as handle:
                    backup_path = Path(handle.name)
                backups[path] = backup_path
                shutil.copyfile(path, backup_path)
                with backup_path.open("rb") as handle:
                    os.fsync(handle.fileno())

        for path, _payload in outputs:
            # Record intent before replace so an interrupt immediately after the
            # filesystem operation still restores the previous public pair.
            published.append(path)
            staged[path].replace(path)
            del staged[path]
    except BaseException as error:
        rollback_errors: list[str] = []
        for path in reversed(published):
            try:
                if path in backups:
                    backups[path].replace(path)
                    del backups[path]
                else:
                    path.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        if isinstance(error, OSError):
            failure = f"cannot publish baseline review outputs: {error}"
            if rollback_errors:
                failure += "; rollback failed for " + "; ".join(rollback_errors)
        else:
            raise
    finally:
        for temporary_path in (*staged.values(), *backups.values()):
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    if failure is not None:
        reject(failure)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trivy-version-json", required=True, type=Path)
    parser.add_argument("--vulnerability-db", required=True, type=Path)
    parser.add_argument("--vulnerability-db-metadata", required=True, type=Path)
    parser.add_argument("--java-db", required=True, type=Path)
    parser.add_argument("--java-db-metadata", required=True, type=Path)
    parser.add_argument("--vulnerability-db-manifest", required=True, type=Path)
    parser.add_argument("--java-db-manifest", required=True, type=Path)
    for image in gate.EXPECTED_IMAGES:
        for architecture in gate.EXPECTED_ARCHITECTURES:
            parser.add_argument(
                f"--{image}-{architecture}-report", required=True, type=Path
            )
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument("--review-before", required=True)
    parser.add_argument("--baseline-output", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    arguments = parser.parse_args()

    if paths_alias(arguments.baseline_output, arguments.manifest_output):
        reject("baseline-output and manifest-output must be different files")
    input_paths = (
        arguments.trivy_version_json,
        arguments.vulnerability_db,
        arguments.vulnerability_db_metadata,
        arguments.java_db,
        arguments.java_db_metadata,
        arguments.vulnerability_db_manifest,
        arguments.java_db_manifest,
        *(
            getattr(arguments, f"{image}_{architecture}_report")
            for image in gate.EXPECTED_IMAGES
            for architecture in gate.EXPECTED_ARCHITECTURES
        ),
    )
    for output_path in (arguments.baseline_output, arguments.manifest_output):
        for input_path in input_paths:
            if paths_alias(output_path, input_path):
                reject(f"output {output_path} must not alias an input {input_path}")
    reviewed_at, review_before = parse_review_dates(
        arguments.reviewed_at, arguments.review_before
    )

    version_document, version_payload = provenance.load_json(
        arguments.trivy_version_json, "Trivy version JSON"
    )
    if not isinstance(version_document, dict):
        reject("Trivy version JSON top level must be an object")
    if set(version_document) != {"Version", "VulnerabilityDB", "JavaDB"}:
        reject("Trivy version JSON must contain Version, VulnerabilityDB, and JavaDB")
    if version_document["Version"] != gate.EXPECTED_TRIVY_VERSION:
        reject(f"Trivy version must be {gate.EXPECTED_TRIVY_VERSION}")

    database_arguments = {
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
    database_times: dict[str, dict[str, gate.UtcTimestamp]] = {}
    for database_name, (
        version_field,
        schema_version,
        database_path,
        metadata_path,
        manifest_path,
    ) in database_arguments.items():
        databases[database_name], database_times[database_name] = database_snapshot(
            version_document,
            database_name=database_name,
            version_field=version_field,
            schema_version=schema_version,
            database_path=database_path,
            metadata_path=metadata_path,
            manifest_path=manifest_path,
        )

    observations: dict[tuple[str, str], dict[str, object]] = {}
    for (image, architecture), expected_artifact in REPORT_SPECS.items():
        report_path = getattr(arguments, f"{image}_{architecture}_report")
        (
            residuals,
            fixable,
            observed_architecture,
            package_coverage,
            created_at_text,
            created_at,
            trivy_version,
            image_id,
            report_digest,
            severity_counts,
            vulnerability_coverage,
        ) = gate.load_report(report_path, expected_artifact=expected_artifact)
        if observed_architecture != architecture:
            reject(
                f"{image}/{architecture} report identifies architecture "
                f"{observed_architecture!r}"
            )
        if trivy_version != gate.EXPECTED_TRIVY_VERSION:
            reject(f"{image}/{architecture} report uses the wrong Trivy version")
        if fixable:
            reject(f"{image}/{architecture} contains fixable HIGH/CRITICAL findings")
        for database_name, times in database_times.items():
            if timestamp_key(created_at) < timestamp_key(times["downloaded_at"]):
                reject(f"{image}/{architecture} predates the {database_name} DB download")
            if timestamp_key(created_at) >= timestamp_key(times["next_update"]):
                reject(f"{image}/{architecture} used an expired {database_name} DB")
        observations[(image, architecture)] = {
            "residuals": residuals,
            "package_coverage": package_coverage,
            "vulnerability_coverage": vulnerability_coverage,
            "report": {
                "artifact_name": expected_artifact,
                "architecture": architecture,
                "created_at": created_at_text,
                "image_id": image_id,
                "sha256": report_digest,
                "severity_counts": severity_counts,
                "fixable_high_critical_count": 0,
            },
        }

    image_ids = [
        observations[(image, architecture)]["report"]["image_id"]
        for image in gate.EXPECTED_IMAGES
        for architecture in gate.EXPECTED_ARCHITECTURES
    ]
    if len(set(image_ids)) != len(image_ids):
        reject("all four reviewed reports must identify different ImageIDs")

    for image in gate.EXPECTED_IMAGES:
        amd64 = observations[(image, "amd64")]["residuals"]
        arm64 = observations[(image, "arm64")]["residuals"]
        if amd64 != arm64:
            added = len(arm64 - amd64)
            missing = len(amd64 - arm64)
            reject(
                f"{image} HIGH/CRITICAL findings differ across architectures "
                f"(arm64 added={added}, missing={missing})"
            )

    baseline_document = {
        "schema_version": 4,
        "reviewed_at": reviewed_at.isoformat(),
        "review_before": review_before.isoformat(),
        "minimum_db_updated_at": {
            database_name: databases[database_name]["updated_at"]
            for database_name in ("vulnerability", "java")
        },
        "images": {
            image: baseline_entries(observations[(image, "amd64")]["residuals"])
            for image in gate.EXPECTED_IMAGES
        },
        "coverage": {
            image: {
                architecture: coverage_entries(
                    observations[(image, architecture)]["package_coverage"]
                )
                for architecture in gate.EXPECTED_ARCHITECTURES
            }
            for image in gate.EXPECTED_IMAGES
        },
        "vulnerability_coverage": {
            image: {
                architecture: vulnerability_coverage_entries(
                    observations[(image, architecture)]["vulnerability_coverage"]
                )
                for architecture in gate.EXPECTED_ARCHITECTURES
            }
            for image in gate.EXPECTED_IMAGES
        },
    }
    baseline_payload = (
        json.dumps(baseline_document, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")

    manifest_document = {
        "schema_version": 1,
        "baseline": {
            "path": arguments.baseline_output.name,
            "schema_version": 4,
            "sha256": hashlib.sha256(baseline_payload).hexdigest(),
        },
        "scanner": {
            "name": "Trivy",
            "version": gate.EXPECTED_TRIVY_VERSION,
            "container_image": TRIVY_IMAGE,
            "scan_profile": {
                "scanners": ["vuln"],
                "pkg_types": ["os", "library"],
                "severities": ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
                "list_all_packages": True,
                "offline_scan": True,
                "skip_db_update": True,
                "skip_java_db_update": True,
            },
        },
        "trivy_version_json_sha256": hashlib.sha256(version_payload).hexdigest(),
        "databases": databases,
        "reports": {
            image: {
                architecture: observations[(image, architecture)]["report"]
                for architecture in gate.EXPECTED_ARCHITECTURES
            }
            for image in gate.EXPECTED_IMAGES
        },
        "high_critical_inventory_sha256": {
            image: normalized_inventory_sha256(
                observations[(image, "amd64")]["residuals"]
            )
            for image in gate.EXPECTED_IMAGES
        },
    }
    manifest_payload = (
        json.dumps(manifest_document, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")

    write_atomic_pair(
        (
            (arguments.baseline_output, baseline_payload),
            (arguments.manifest_output, manifest_payload),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
