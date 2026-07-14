# Container vulnerability review

Both release images are scanned with the digest-pinned Trivy v0.70.0 container
for `UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL` OS and library vulnerabilities. The JSON
reports retain every package (`--list-all-pkgs`) and are uploaded even when the
policy gate fails.

The images contain JAR files, so Trivy requires both its vulnerability DB and
Java artifact-identification DB even though the current reports do not contain
a Java result. Each CI job therefore downloads both DBs exactly once, mounts
the resulting cache read-only for both scans, and disables DB updates, remote
dependency lookup, telemetry, version checks, and non-Docker image fallbacks.

## Evidence and trust boundary

The DB tags are first resolved from the official GHCR namespaces to raw OCI
manifests. `bin/validate_trivy_oci_manifest.py` rejects malformed manifests,
unexpected artifact/config/layer media types, extra layers, invalid digests,
and non-positive sizes. Trivy then receives only the resulting manifest-digest
repository reference, so a tag move cannot change the downloaded bytes and
there is no mirror or repository fallback.

After both scans, `bin/create_trivy_db_provenance.py` binds:

- both raw OCI manifests and their layer descriptors;
- both extracted DB files and runtime metadata files;
- Trivy's version and DB timestamps;
- both raw reports, image IDs, architectures, timestamps, severity counts, and
  artifact names.

`bin/enforce_trivy_report.py` re-hashes and re-parses those mounted files rather
than accepting recorded digests at face value. The reports are read once, and
the same bytes are used for parsing and hashing. The raw manifests and
provenance are retained with the reports for 30 days.

This is a content-addressed workflow chain, not an independent vendor
signature. Its publisher trust root is TLS plus the reviewed
`ghcr.io/aquasecurity` namespaces; the scanner itself is separately pinned by
container digest. Manifest and layer digests make the selected artifacts
immutable, but do not by themselves prove that a publisher credential was
never compromised.

## Release policy

The gate applies these rules:

- any HIGH/CRITICAL finding with `Status=fixed` or a non-empty `FixedVersion`
  blocks release;
- unfixed HIGH/CRITICAL findings must exactly equal the short-lived reviewed
  set for that image;
- every severity has an architecture-specific finding count and canonical
  inventory SHA-256; LOW/MEDIUM/UNKNOWN do not decide fixability, but any drift
  still blocks release and requires review;
- every expected Debian, Node, Python, and Ruby result has an
  architecture-specific package count and inventory SHA-256;
- a new, missing, duplicated, contradictory, or truncated logical finding or
  package inventory blocks release;
- malformed, missing, swapped, or tampered report, DB, metadata, provenance,
  or manifest evidence blocks release;
- status values must be one of Trivy v0.70.0's eight canonical lowercase
  values; `not_affected` is rejected if it appears among active findings;
- DBs older than the reviewed minimum, downloaded after a report, or expired
  when a report was created cannot satisfy the gate.

Baseline schema v4 stores minimum timestamps for both DBs, the shared unfixed
HIGH/CRITICAL sets, four architecture-specific package inventories, and four
all-severity finding inventories. A severity downgrade or disappearance is
therefore a review event, not a silent reduction in risk.

The normalized HIGH/CRITICAL identity uses `Class`, `Type`, `PkgID`, `PkgName`,
`VulnerabilityID`, `InstalledVersion`, `Severity`, and `Status`. Package
coverage additionally commits package ID, name, version, file path, and PURL.
Raw target names, layer-local UIDs, and layer digests are excluded from those
logical inventories because they are rebuild-local rather than vulnerability
identity.

## Reviewing a baseline update

`.trivy-unfixed-baseline.json` is not an ignore file. Its companion
`.trivy-baseline-review.json` binds the final baseline SHA-256, both DBs, and
all four amd64/arm64 reports. `bin/create_trivy_baseline.py` generates both
files together and refuses output when a report is fixable, malformed, outside
the frozen DB validity window, assigned to the wrong slot, or has a different
HIGH/CRITICAL set across architectures.

The review manifest's scanner image, scan profile, and OCI `resolved_from`
fields record the required reviewed configuration. They are enforced by the CI
workflow and supported by the retained evidence, but the report JSON format
does not independently attest the historical command-line arguments.

Before `review_before`:

1. Build both images for amd64 and arm64.
2. Resolve, validate, and freeze one fresh vulnerability DB and Java DB.
3. Scan all four image/architecture combinations with the documented profile.
4. Review every addition, removal, package change, and severity/status change
   against an official vendor source.
5. Run the generator with the two DBs, their metadata/manifests, and all four
   reports; never hand-edit generated commitments.
6. Keep the review window at 30 days or less, then run:

```sh
python3 test/trivy_report_contract_test.py
ruby test/release_contract_test.rb
```

CI currently rebuilds and scans amd64. The committed arm64 evidence comes from
the four-report review manifest; it is not presented as a per-run arm64 CI
scan.

### 2026-07-14 four-architecture review

The review rebuilt both images for amd64 and arm64 from Debian snapshot
`20260714T000000Z`, froze one Trivy vulnerability/Java DB pair, and compared
every all-severity row with the previous retained reports. The delivery
all-severity findings and both images' HIGH/CRITICAL findings are identical
across architectures. Development LOW inventories retain expected
architecture-specific binutils rows. None of the remaining HIGH/CRITICAL
findings has `Status=fixed` or a `FixedVersion`.

| Image | Previous counts (C/H/M/L/U) | Current counts (C/H/M/L/U) | Fixable C/H |
| --- | --- | --- | --- |
| delivery | 17 / 89 / 260 / 353 / 79 | 17 / 84 / 199 / 324 / 31 | 0 |
| development | 27 / 324 / 1111 / 1109 / 33 | 26 / 319 / 1039 / 1083 / 43 | 0 |

The snapshot upgrade installs ImageMagick
`8:6.9.11.60+dfsg-1.6+deb12u12`. Debian
[DLA-4680-1](https://lists.debian.org/debian-lts-announce/2026/07/msg00023.html)
fixes 19 CVEs in that version. This removes the five previously reviewed
HIGH/CRITICAL ImageMagick CVEs (CVE-2026-56361, CVE-2026-56367,
CVE-2026-56368, CVE-2026-56370, and CVE-2026-56378) across all five installed
ImageMagick binary packages.

The same current DB classifies CVE-2026-56372 as CRITICAL and CVE-2026-61857,
CVE-2026-61861, and CVE-2026-61870 as HIGH for those packages. The Bookworm
tracker entries still have no fixed version, so these 20 rows remain explicit
unfixed baseline entries. ImageMagick is used only during the build against
repository-controlled inputs; that limits exposure but is not treated as a
substitute for a vendor fix.

The current DB also changes the following HIGH/CRITICAL rows from `affected`
to Debian's `fix_deferred` classification, without adding a `FixedVersion`:

- gzip CVE-2026-41992 in both images;
- wget CVE-2026-58471 and CVE-2026-58472 in the development image;
- openssh-client CVE-2026-59999, CVE-2026-60000, and CVE-2026-60002 in the
  development image;
- python3-jwt CVE-2026-48526 in the development image.

CVE-2026-56123 disappears from socat because Debian Bookworm is not affected.
The fresh DB also removes rows now resolved as fixed or not affected for XZ,
FreeType, Python, and PyJWT. The 27 Chromium CVEs previously reported as
`UNKNOWN` disappear because Debian's
installed `150.0.7871.114-1~deb12u1` package contains the fixes recorded in
[DLA-4677-1](https://lists.debian.org/debian-lts-announce/2026/07/msg00019.html).
These removals were reviewed as vendor-data corrections, not silently accepted
as risk reductions.

At lower severities, the refresh adds current Perl and gawk `UNKNOWN` records
and reintroduces curl CVE-2026-9547 as `LOW`. All additions, removals, severity
changes, and status changes are committed by the architecture-specific counts
and inventory hashes, so later drift still fails closed.

## Known curl build-configuration scanner gap

Trivy reports CVE-2026-9547 for one delivery package row and four development
package rows on each architecture. The
[Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-9547)
currently labels Bookworm vulnerable but also records that Debian builds curl
with `--without-libssh --with-libssh2`. The
[curl advisory](https://curl.se/docs/CVE-2026-9547.html) states that the flaw
requires the libssh backend, does not affect libssh2, and does not affect the
curl command-line tool. Debian's pinned
[Bookworm build rules](https://sources.debian.org/src/curl/7.88.1-10%2Bdeb12u15/debian/rules/#L19)
confirm that configuration.

This is therefore a conservative package-level false positive for the exact
installed build, not a claim that the source-package tracker is wrong. The
evidence, affected package rows, and verification conditions are recorded in
`.security-scanner-gaps.yml` and bound to the same DB timestamp and review
window as the generated baseline. The record expires within 14 days and must be
removed if Trivy stops reporting it or if the package build configuration
changes.
