# Container vulnerability review

Both release images are scanned once with Trivy v0.70.0 for all HIGH and
CRITICAL OS and library vulnerabilities. The unfiltered JSON reports are
uploaded from every CI run, including failed runs. Scanner or database errors
remain fatal.

`bin/enforce_trivy_report.py` then applies the release policy:

- any finding with `Status=fixed` or a non-empty `FixedVersion` blocks release;
- the unfixed findings must exactly equal the short-lived
  `.trivy-unfixed-baseline.json` set for that image;
- a new CVE, package/version, ecosystem, severity, or status blocks release;
- a reviewed finding missing from the report also blocks release, preventing a
  truncated report or stale vulnerability database from masquerading as a fix;
- malformed, missing, expired, duplicated, or suspiciously empty evidence
  blocks release.

The gate also binds each report to Trivy v0.70.0 and to its CI image name
(`mtics-al-folio:ci` or `mtics-devcontainer:ci`), so scanner-version drift,
swapped reports, or accidentally scanning one image twice cannot satisfy the
reviewed baseline. Every report must contain exactly the Debian, Node, Python,
and Ruby scanner result identities. For each identity, the baseline stores an
architecture-specific package count and SHA-256 commitment; dropping an empty
language result or truncating its package inventory therefore fails closed.

The normalized identity intentionally uses `Class`, `Type`, `PkgID`,
`PkgName`, `VulnerabilityID`, `InstalledVersion`, `Severity`, and `Status`.
Raw `Target` is excluded because it contains the local image tag, and PURL is
excluded because its architecture qualifier would make one reviewed baseline
incompatible across arm64 development and amd64 CI.

Package-inventory commitments canonicalize `Class`, `Type`, package `ID`,
`Name`, `Version`, `FilePath`, and PURL. Layer digests and Trivy's internal UID
are excluded because they change across byte-identical rebuilds. The complete,
human-reviewable package rows remain in the uploaded raw JSON reports; the
baseline digest is a compact integrity commitment, not a replacement for that
evidence.

Native Ruby gems are compiled in a separate digest-pinned, snapshot-upgraded
builder stage. Only the locked `/usr/local/bundle` is copied into the delivery
image, so compilers and development headers are absent from production. The
development container intentionally retains its toolchain for interactive use.

## Reviewing a baseline update

The baseline is not an ignore file. Before its `review_before` date, rebuild
both pinned images, retain the two raw reports, and review every new residual.
Only findings with no available fix may enter the baseline. Regenerate entries
and architecture-specific coverage commitments from the raw reports, keep them
sorted and deduplicated, set a review window no
longer than 30 days, and run the checks below. A genuine package fix or finding
removal therefore requires an explicit baseline update and review; the gate
never edits or silently shrinks the baseline.

```sh
python3 test/trivy_report_contract_test.py
ruby test/release_contract_test.rb
```

CI runs on amd64. A finding seen there but absent from an arm64-generated
baseline is expected to fail closed and must be reviewed from the uploaded raw
artifact; do not broaden the baseline speculatively.

## Known Chromium scanner gap

Trivy v0.70.0 currently reports no Chromium finding for the delivery image,
while the [Debian security tracker](https://security-tracker.debian.org/tracker/source-package/chromium)
lists CVE-2026-15107 through CVE-2026-15133 for releases before upstream
150.0.7871.115. The image uses the newest package available in the pinned
Debian snapshot, 150.0.7871.114-1~deb12u1. This discrepancy is reviewed
explicitly in `.security-scanner-gaps.yml`, not hidden in the Trivy baseline.

Until Debian publishes 150.0.7871.115 or later, browser checks receive a
read-only site workspace and write evidence only to runner-temporary storage.
The scanner-gap record expires within 30 days so this containment cannot become
an indefinite exception.
