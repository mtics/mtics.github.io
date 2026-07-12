#!/usr/bin/env python3
"""Strictly validate the OCI manifests used to fetch Trivy databases."""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import NoReturn


SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
CREATED_PATTERN = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,9})?Z"
)
MAX_CLOCK_SKEW_SECONDS = 300
EMPTY_CONFIG_DIGEST = (
    "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
)
DATABASE_SPECS = {
    "vulnerability": {
        "repository": "ghcr.io/aquasecurity/trivy-db",
        "resolved_from": "ghcr.io/aquasecurity/trivy-db:2",
        "layer_media_type": "application/vnd.aquasec.trivy.db.layer.v1.tar+gzip",
        "layer_title": "db.tar.gz",
    },
    "java": {
        "repository": "ghcr.io/aquasecurity/trivy-java-db",
        "resolved_from": "ghcr.io/aquasecurity/trivy-java-db:1",
        "layer_media_type": "application/vnd.aquasec.trivy.javadb.layer.v1.tar+gzip",
        "layer_title": "javadb.tar.gz",
    },
}


class ManifestError(ValueError):
    pass


def reject(message: str) -> NoReturn:
    raise ManifestError(message)


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            reject(f"duplicate object key {key!r}")
        document[key] = value
    return document


def reject_nonstandard_constant(value: str) -> NoReturn:
    reject(f"non-standard JSON constant {value}")


def load_and_validate(path: Path, database_name: str) -> dict[str, object]:
    if database_name not in DATABASE_SPECS:
        reject(f"unsupported database {database_name!r}")
    try:
        if not path.is_file():
            reject(f"missing manifest: {path}")
        payload = path.read_bytes()
    except OSError as error:
        reject(f"cannot read manifest {path}: {error}")
    if not payload:
        reject(f"empty manifest: {path}")
    try:
        document = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_constant,
        )
    except (json.JSONDecodeError, UnicodeError, ManifestError) as error:
        reject(f"malformed JSON in {path}: {error}")
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion",
        "mediaType",
        "artifactType",
        "config",
        "layers",
        "annotations",
    }:
        reject(
            "top level must contain exactly schemaVersion, mediaType, artifactType, "
            "config, layers, and annotations"
        )
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 2:
        reject("schemaVersion must be integer 2")
    if document["mediaType"] != "application/vnd.oci.image.manifest.v1+json":
        reject("mediaType must be the OCI image manifest media type")
    if document["artifactType"] != "application/vnd.aquasec.trivy.config.v1+json":
        reject("artifactType must be the Aqua Trivy database config media type")
    annotations = document["annotations"]
    if not isinstance(annotations, dict) or set(annotations) != {
        "org.opencontainers.image.created"
    }:
        reject("manifest annotations must contain only the OCI creation timestamp")
    created = annotations["org.opencontainers.image.created"]
    match = CREATED_PATTERN.fullmatch(created) if isinstance(created, str) else None
    if match is None:
        reject("OCI creation annotation must be an RFC3339 UTC timestamp")
    try:
        parsed = dt.datetime.strptime(
            f"{match.group('date')}T{match.group('time')}", "%Y-%m-%dT%H:%M:%S"
        )
    except ValueError:
        reject("OCI creation annotation is not a valid calendar timestamp")
    fraction = (match.group("fraction") or "")[1:]
    nanosecond = int((fraction + "000000000")[:9]) if fraction else 0
    created_nanoseconds = calendar.timegm(parsed.timetuple()) * 1_000_000_000 + nanosecond
    if created_nanoseconds > time.time_ns() + MAX_CLOCK_SKEW_SECONDS * 1_000_000_000:
        reject("OCI creation annotation must not be in the future")

    config = document["config"]
    expected_config = {
        "mediaType": "application/vnd.oci.empty.v1+json",
        "digest": EMPTY_CONFIG_DIGEST,
        "size": 2,
        "data": "e30=",
    }
    if config != expected_config:
        reject("config must be the canonical empty OCI descriptor")

    layers = document["layers"]
    if not isinstance(layers, list) or len(layers) != 1:
        reject("layers must contain exactly one database layer")
    layer = layers[0]
    if not isinstance(layer, dict) or set(layer) != {
        "mediaType",
        "digest",
        "size",
        "annotations",
    }:
        reject("database layer descriptor has unexpected fields")
    spec = DATABASE_SPECS[database_name]
    if layer["mediaType"] != spec["layer_media_type"]:
        reject(f"unexpected {database_name} database layer media type")
    digest = layer["digest"]
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        reject("database layer digest must be a lowercase sha256 digest")
    size = layer["size"]
    if type(size) is not int or size <= 0:
        reject("database layer size must be a positive integer")
    expected_annotations = {"org.opencontainers.image.title": spec["layer_title"]}
    if layer["annotations"] != expected_annotations:
        reject("database layer title annotation is not canonical")

    manifest_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    return {
        "repository": spec["repository"],
        "resolved_from": spec["resolved_from"],
        "manifest_digest": manifest_digest,
        "layer_digest": digest,
        "layer_media_type": layer["mediaType"],
        "layer_size": size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, choices=tuple(DATABASE_SPECS))
    parser.add_argument("--manifest", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        evidence = load_and_validate(arguments.manifest, arguments.database)
    except ManifestError as error:
        print(f"invalid Trivy OCI manifest: {error}", file=sys.stderr)
        return 2
    print(evidence["manifest_digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
