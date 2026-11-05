# Container vulnerability review

Both release images are scanned with the digest-pinned Trivy v0.70.0 container
for `UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL` OS and library vulnerabilities. The JSON
reports retain every package (`--list-all-pkgs`) and are uploaded even when the
policy gate fails.

The final images do not contain JAR files: their build steps remove the
non-runtime JRuby and gettext integration JARs and fail if any other JAR is
introduced. Trivy therefore needs only its vulnerability DB, not the unrelated
Java artifact-identification DB. Each CI job downloads that DB exactly once,
mounts the resulting cache read-only for both scans, and disables DB updates,
remote dependency lookup, telemetry, version checks, and non-Docker image
fallbacks. If a future dependency adds a JAR, the image build fails and the
scanner scope must be reviewed before release.

Trivy analyzes image layers, including layers that originally contained a JAR
later deleted from the final filesystem. The scan therefore excludes only the
two reviewed JRuby JAR paths in the delivery image, and the two gettext plus
two removed RVM/JRuby paths in the development image with exact `--skip-files` flags.
These paths are recorded in the review manifest and asserted by the release
contract test. Any other JAR still requires a Java DB and makes this
vulnerability-only scan fail closed.

## Debian package sources

Every APT stage resolves packages from the pinned `DEBIAN_SNAPSHOT` through
`mirror+file:` lists:

- index files (`InRelease`, `Packages`) are fetched only from
  `snapshot.debian.org/archive/{debian,debian-security}/${DEBIAN_SNAPSHOT}`; the
  live-mirror entries carry `type:deb`, so APT never reads an index from them,
  and the build fails if `debian.sources` still names `deb.debian.org`;
- a `.deb` is downloaded from the snapshot first and falls back to
  `deb.debian.org` only when the snapshot cannot serve it. APT verifies every
  file against the SHA256 in the snapshot index, so a fallback download is
  byte-identical to the snapshot copy and the pinned package set is unchanged;
- `Acquire::Retries "8"` and `apt-get update --error-on=any` make a failed
  index download stop the build instead of continuing with a partial index.

On 2026-11-05 the snapshot returned persistent HTTP 504 for
`libsm6_1.2.3-1_arm64.deb` while serving neighbouring files, which blocked every
arm64 build until the verified fallback was added. A package version that has
already left `deb.debian.org` still depends on the snapshot alone.

## Evidence and trust boundary

The DB tag is first resolved from the official GHCR namespace to a raw OCI
manifest. `bin/validate_trivy_oci_manifest.py` rejects malformed manifests,
unexpected artifact/config/layer media types, extra layers, invalid digests,
and non-positive sizes. Trivy then receives only the resulting manifest-digest
repository reference, so a tag move cannot change the downloaded bytes and
there is no mirror or repository fallback.

After both scans, `bin/create_trivy_db_provenance.py` binds:

- the raw OCI manifest and its layer descriptor;
- the extracted DB file and runtime metadata file;
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

### Where the gate blocks

CI downloads the current vulnerability DB on every run, while the baseline
freezes the DB from the last review. Debian publishes new CVE records almost
daily, so an exact-match gate starts failing within a day of each review: after
the 2026-09-22 review, every deploy from 2026-09-23 onwards was rejected for a
new unfixed HIGH finding, long before `review_before`.

The scanned images are build tools. The published artifact is the static site
on GitHub Pages, and visitors never execute these packages. The gate therefore
runs in two modes:

| Trigger | Mode | Effect of a gate failure or expired review |
| --- | --- | --- |
| `pull_request` (`validate` job) | strict | blocks the job |
| `workflow_dispatch` (`build` job) | strict | blocks the deploy |
| `push`, citation `workflow_run` (`build` job) | report | records a warning and the step summary; the deploy continues |

Report mode keeps every scan, report upload, and provenance binding. Only the
two `Enforce reviewed … vulnerability baseline` steps use
`continue-on-error`, and `ruby test/release_contract_test.rb` skips just the
`review_before` expiry assertion when `TRIVY_GATE_MODE=report`. A
`Trivy baseline drift` warning in a deploy run is the signal to start a new
review; a manual `workflow_dispatch` run checks a fresh review under the strict
gate.

### Gate rules

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
- a DB older than the reviewed minimum, downloaded after a report, or expired
  when a report was created cannot satisfy the gate.

Baseline schema v4 stores the minimum timestamp for the vulnerability DB, the shared unfixed
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
`.trivy-baseline-review.json` binds the final baseline SHA-256, the vulnerability DB, and
all four amd64/arm64 reports. `bin/create_trivy_baseline.py` generates both
files together and refuses output when a report is fixable, malformed, outside
the frozen vulnerability DB validity window, assigned to the wrong slot, or has a different
HIGH/CRITICAL set across architectures.

The review manifest's scanner image, scan profile, and OCI `resolved_from`
fields record the required reviewed configuration. They are enforced by the CI
workflow and supported by the retained evidence, but the report JSON format
does not independently attest the historical command-line arguments.

The manual **Trivy baseline review** workflow
(`.github/workflows/trivy_baseline_review.yml`) collects the evidence on
GitHub-hosted runners: it freezes one vulnerability DB, builds both images
natively on `ubuntu-24.04` and `ubuntu-24.04-arm`, scans the four slots with
`bin/trivy_review_scan.sh` (the deploy scan profile), runs the generator, and
uploads `trivy-baseline-candidate` with a step summary of added and removed
HIGH/CRITICAL rows. It never commits; the reviewer checks the changes, copies
the two generated files into the repository, updates the curl scanner gap, and
records the review below.

Before `review_before`:

1. Build both images for amd64 and arm64.
2. Resolve, validate, and freeze one fresh vulnerability DB.
3. Scan all four image/architecture combinations with the documented profile.
4. Review every addition, removal, package change, and severity/status change
   against an official vendor source.
5. Run the generator with the DB, its metadata/manifest, and all four
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

Review dates are UTC calendar dates, matching the DB timestamps and GitHub
runners even when the reviewer is in a different time zone. The review rebuilt
both images for amd64 and arm64 from Debian snapshot
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

### 2026-09-22 UTC four-architecture review

The review rebuilt both images for amd64 and arm64 from Debian snapshot
`20260922T200000Z` and froze the vulnerability DB updated at
`2026-09-22T19:06:27.093918464Z`. The images contain no JARs; six exact
non-runtime JAR paths in their earlier layers are excluded from Java artifact
analysis. The official Java DB had expired before this review, so the current
scan no longer depends on it. New or unreviewed JAR paths fail the scan.

| Image | Previous counts (C/H/M/L/U) | Current counts (C/H/M/L/U) | Fixable C/H |
| --- | --- | --- | --- |
| delivery | 17 / 76 / 265 / 336 / 671 | 17 / 128 / 280 / 349 / 66 | 0 |
| development | 29 / 446 / 1698 / 1238 / 90 | 29 / 475 / 2107 / 1313 / 74 | 0 |

Each image has the same HIGH/CRITICAL set and all-severity counts on both
architectures. The new DB adds current [libxml2](https://security-tracker.debian.org/tracker/source-package/libxml2)
and [util-linux](https://security-tracker.debian.org/tracker/source-package/util-linux)
findings; the development image also has substantial
[Linux header](https://security-tracker.debian.org/tracker/source-package/linux)
inventory churn after `linux-libc-dev` moved from `6.1.180-1` to `6.1.187-1`.
The Debian snapshot updates `libaom3` to `3.6.0-1+deb12u3` and
[ImageMagick](https://security-tracker.debian.org/tracker/source-package/imagemagick)
to `8:6.9.11.60+dfsg-1.6+deb12u13`. The Python lock now includes fixed
`soupsieve` and `tornado` releases, and the images install Ruby `resolv 0.7.2`.
The generated baseline retains the exact unfixed findings and hashes the full
package and all-severity inventories; additions, removals, and status changes
therefore remain visible and block later unreviewed drift.

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

## Accepted dependency advisories

`bin/dependency_audit` fails on every locked Ruby, Python, and npm advisory
except the single exception below. The release contract test pins the ignore
list to exactly this ID and fails once activesupport leaves the 7.2 line, so the
exception has to be removed as soon as a fixed version can be locked.

| Advisory | Package | Reviewed | Review before |
| --- | --- | --- | --- |
| [GHSA-8474-xrp6-wh69](https://github.com/rails/rails/security/advisories/GHSA-8474-xrp6-wh69) | activesupport 7.2.3.1 | 2026-11-05 | 2027-02-03 |

The advisory is a denial of service in `ActiveSupport::Inflector#safe_constantize`.
It is fixed only in activesupport 8.0.5.2 and 8.1.4.1; the 7.2 line has no fix.
The latest `al_citations` (1.0.1) requires `activesupport (>= 6.0, < 8.0)` and
uses only `ActiveSupport::NumberHelper`. No other gem in the bundle calls
`safe_constantize`, and the build runs on repository-controlled input only, so
the vulnerable code path is not reachable from the published static site. After
the review date the audit fails with an explicit expiry message.
