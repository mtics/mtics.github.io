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

### Reviewed Debian classification changes

The 2026-07-12 DB changed 12 distinct Bookworm CVEs from `affected` to
`fix_deferred` (12 normalized rows in the delivery image and 52 in the
development image). The corresponding Debian source-package trackers classify
them as postponed, and those report rows contain no `FixedVersion`, so this is
a reviewed status change rather than evidence of a package fix:

- `acl`: CVE-2026-54369
  ([Debian tracker](https://security-tracker.debian.org/tracker/source-package/acl));
- `curl`: CVE-2026-12064, CVE-2026-8286, CVE-2026-8927, and CVE-2026-8932
  ([Debian tracker](https://security-tracker.debian.org/tracker/source-package/curl));
- `glib2.0`: CVE-2026-58010 through CVE-2026-58016
  ([Debian tracker](https://security-tracker.debian.org/tracker/source-package/glib2.0)).

`CVE-2026-9547` was removed after the fresh DB stopped reporting it. The
[Debian security tracker](https://security-tracker.debian.org/tracker/CVE-2026-9547)
marks Bookworm not affected because Debian builds curl with `libssh2` and
without the affected `libssh` backend. The
[tracker change](https://salsa.debian.org/security-tracker-team/security-tracker/-/commit/78e637f1bc72f7e6f59c35fa4a89ac61f675f96b),
[curl advisory](https://curl.se/docs/CVE-2026-9547.html), and Debian
[Bookworm build rules](https://sources.debian.org/src/curl/7.88.1-10%2Bdeb12u15/debian/rules/#L19)
agree with that classification.

## Known Chromium severity-classification gap

The all-severity reports contain CVE-2026-15107 through CVE-2026-15133 for both
`chromium` and `chromium-common`: 27 unique CVEs and 54 package rows on each
architecture. Trivy classifies every row as `UNKNOWN`, while the
[Debian tracker](https://security-tracker.debian.org/tracker/source-package/chromium)
lists the issues for releases before 150.0.7871.115. The image uses the newest
package available in the pinned Debian snapshot, 150.0.7871.114-1~deb12u1.

This unresolved severity classification is recorded in
`.security-scanner-gaps.yml`; it is not hidden in the HIGH/CRITICAL baseline.
Until Debian publishes 150.0.7871.115 or later, browser checks receive a
read-only site workspace and write evidence only to runner-temporary storage.
The record expires within 30 days so the containment cannot become indefinite.
