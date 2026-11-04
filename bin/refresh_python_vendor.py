#!/usr/bin/env python3
"""Refresh pip's stale vendored dependency metadata and msgpack implementation."""

from pathlib import Path
import shutil
import sysconfig


# pip 26.3 already vendors msgpack 1.2.4; its pkg_resources metadata still
# reports setuptools 70.3.0.
PIP_VENDORED_MSGPACK_VERSION = "1.2.4"
PIP_VENDORED_SETUPTOOLS_VERSION = "70.3.0"
PINNED_MSGPACK_VERSION = "1.2.4"
PINNED_SETUPTOOLS_VERSION = "84.1.0"
site_packages = Path(sysconfig.get_paths()["purelib"])
source = site_packages / "msgpack"
vendor = site_packages / "pip" / "_vendor" / "msgpack"
vendor_manifest = site_packages / "pip" / "_vendor" / "vendor.txt"

if not source.is_dir():
    raise SystemExit(f"missing installed msgpack package: {source}")

if not vendor.is_dir():
    raise SystemExit(f"missing pip vendored msgpack package: {vendor}")

shutil.rmtree(vendor)
shutil.copytree(source, vendor)

manifest = vendor_manifest.read_text()
for old_entry, new_entry in (
    (f"msgpack=={PIP_VENDORED_MSGPACK_VERSION}", f"msgpack=={PINNED_MSGPACK_VERSION}"),
    (f"setuptools=={PIP_VENDORED_SETUPTOOLS_VERSION}", f"setuptools=={PINNED_SETUPTOOLS_VERSION}"),
):
    if old_entry not in manifest:
        raise SystemExit(f"missing {old_entry!r} in {vendor_manifest}")
    manifest = manifest.replace(old_entry, new_entry)
vendor_manifest.write_text(manifest)

bom_manifest = site_packages / "pip" / "_vendor" / "bom.cdx.json"
bom = bom_manifest.read_text()
for old_entry, new_entry in (
    (f"pkg:pypi/msgpack@{PIP_VENDORED_MSGPACK_VERSION}", f"pkg:pypi/msgpack@{PINNED_MSGPACK_VERSION}"),
    (f"pkg:pypi/setuptools@{PIP_VENDORED_SETUPTOOLS_VERSION}", f"pkg:pypi/setuptools@{PINNED_SETUPTOOLS_VERSION}"),
):
    if old_entry not in bom:
        raise SystemExit(f"missing {old_entry!r} in {bom_manifest}")
    bom = bom.replace(old_entry, new_entry)
bom_manifest.write_text(bom)

print(
    "updated pip vendored msgpack to "
    f"{PINNED_MSGPACK_VERSION} and setuptools metadata to {PINNED_SETUPTOOLS_VERSION}"
)
