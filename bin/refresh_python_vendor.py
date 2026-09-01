#!/usr/bin/env python3
"""Replace pip's vendored msgpack with the pinned, security-fixed package."""

from pathlib import Path
import shutil
import sysconfig


PINNED_VERSION = "1.2.1"
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
old_entry = "msgpack==1.1.2"
new_entry = f"msgpack=={PINNED_VERSION}"
if old_entry not in manifest:
    raise SystemExit(f"missing {old_entry!r} in {vendor_manifest}")
vendor_manifest.write_text(manifest.replace(old_entry, new_entry))

print(f"updated pip vendored msgpack to {PINNED_VERSION}")
