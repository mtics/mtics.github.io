#!/usr/bin/env python3
"""Refresh pip's stale vendored dependency metadata and msgpack implementation."""

from pathlib import Path
import shutil
import sysconfig


PINNED_MSGPACK_VERSION = "1.2.1"
PINNED_SETUPTOOLS_VERSION = "83.0.0"
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
    ("msgpack==1.1.2", f"msgpack=={PINNED_MSGPACK_VERSION}"),
    ("setuptools==70.3.0", f"setuptools=={PINNED_SETUPTOOLS_VERSION}"),
):
    if old_entry not in manifest:
        raise SystemExit(f"missing {old_entry!r} in {vendor_manifest}")
    manifest = manifest.replace(old_entry, new_entry)
vendor_manifest.write_text(manifest)

bom_manifest = site_packages / "pip" / "_vendor" / "bom.cdx.json"
bom = bom_manifest.read_text()
for old_entry, new_entry in (
    ("pkg:pypi/msgpack@1.1.2", f"pkg:pypi/msgpack@{PINNED_MSGPACK_VERSION}"),
    ("pkg:pypi/setuptools@70.3.0", f"pkg:pypi/setuptools@{PINNED_SETUPTOOLS_VERSION}"),
):
    if old_entry not in bom:
        raise SystemExit(f"missing {old_entry!r} in {bom_manifest}")
    bom = bom.replace(old_entry, new_entry)
bom_manifest.write_text(bom)

print(
    "updated pip vendored msgpack to "
    f"{PINNED_MSGPACK_VERSION} and setuptools metadata to {PINNED_SETUPTOOLS_VERSION}"
)
