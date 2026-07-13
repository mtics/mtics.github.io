# Daily Google Scholar Citation Hardening Design

## Status

Approved for planning on 2026-07-13.

## Context

The site already updates Google Scholar citation counts through
`.github/workflows/update_scholar_citations.yml`. A scheduled job queries the
SerpApi Google Scholar Author API, writes `_data/citations.yml`, commits a
changed file to `main`, and lets `deploy.yml` rebuild the static site through a
`workflow_run` event.

The existing automation has a strong operational record: 67 scheduled runs
completed successfully before the al-folio migration. The migration introduced
a safer two-job workflow that separates the SerpApi secret from repository
write permission, but that exact version has not yet completed a real scheduled
or manually dispatched run on `main`.

Five BibTeX publications have `google_scholar_id` values. Four appear on the
home page and all five appear on the publications page. Citation values are
rendered into static HTML during the Jekyll build; browser JavaScript does not
fetch or update them.

## Goals

- Run the citation refresh once per UTC day with lower schedule-delay risk.
- Continue using Google Scholar citation counts through SerpApi.
- Replace the legacy Python integration with SerpApi's maintained client.
- Bound network and job execution time.
- Retry only transient failures, preserve the last known-good bytes before
  replacement, and surface any uncertainty after replacement.
- Prove that every displayed publication resolves to the exact citation record
  for the configured Scholar user.
- Prove that the built home and publications pages contain the expected badge
  count, number, link, and accessible label.
- Validate the newly separated update/commit workflow and the downstream Pages
  deployment with a real dispatch after merge.

## Non-goals

- Changing publication content, citation badge styling, or page layout.
- Replacing Google Scholar counts with OpenAlex or another citation source.
- Scraping Google Scholar directly from a GitHub-hosted runner.
- Treating titles as identifiers; Scholar, arXiv, and final publication titles
  may legitimately differ.
- Requiring citation counts to increase monotonically; Scholar can merge or
  deduplicate records.
- Creating a lightweight deployment path that bypasses the existing release,
  dependency, browser, or Trivy gates.
- Deploying only when counts change. A successful daily citation workflow will
  continue to trigger a full fail-closed deployment, even when the data file is
  unchanged.

## Architecture

### 1. Scheduled update job

The workflow remains scheduled on the default branch and manually dispatchable.
Its cron changes from `0 8 * * *` to `17 8 * * *`. GitHub documents the start
of each hour as a high-load period where scheduled jobs may be delayed or
dropped, so a non-zero minute is a reliability control rather than a timing
promise.

The `update` job retains:

- `contents: read` only;
- `persist-credentials: false`;
- access to `SERPAPI_API_KEY` only for the updater step;
- dependency installation from hash-locked requirements;
- contract tests before the live request;
- artifact upload of the generated `_data/citations.yml`.

The job receives `timeout-minutes: 15`.

### 2. Secret-free commit job

The `commit` job remains dependent on a successful `update` job. It receives
`contents: write`, but no SerpApi secret. It downloads the generated artifact,
installs it at `_data/citations.yml`, and commits only when the tracked file
changes.

The existing three-attempt fetch/rebase/push loop remains. It never force-pushes
and fails on a real rebase conflict. The job receives `timeout-minutes: 5`.

### 3. Deployment handoff

The updater's `GITHUB_TOKEN` push does not itself trigger a new push workflow.
The existing `workflow_run` handoff in `deploy.yml` therefore remains the sole
citation deployment path. It accepts only a successful run from the same
repository and default branch, then explicitly checks out the latest default
branch so it includes a citation commit created during the upstream run.

The deployment continues through the complete build, dependency, rendering,
browser, accessibility, PDF, and container-vulnerability gates. A citation
update may be committed but remain unpublished if a release gate fails; this is
intentional fail-closed behavior.

## Updater Design

### Maintained SerpApi client

Replace the legacy `google-search-results` dependency with the maintained
`serpapi` package, pinned to the reviewed version and fully hash-locked with its
transitive dependencies. The updater creates one client with the API key and a
15-second request timeout.

The Google Scholar Author pagination and output schema remain unchanged:

```text
metadata:
  last_updated: YYYY-MM-DD
papers:
  SCHOLAR_USER_ID:PUBLICATION_ID:
    title: string
    year: string
    citations: non-negative integer
```

`metadata.last_updated` continues to mean the last UTC date on which citation
content changed. Daily execution health is represented by GitHub Actions run
history, not by forcing a no-op data commit each day.

### Retry policy

Each page request receives at most three total attempts: the initial request and
two retries with one- and two-second delays.

Retry:

- client timeouts;
- HTTP 5xx responses.

Fail immediately:

- authentication failures;
- quota or throughput failures such as HTTP 429;
- malformed or incomplete response data;
- an unexpected author ID, duplicate citation ID, empty result, invalid count,
  or deletion of a previously recorded key without the existing explicit
  destructive override.

The retry helper is isolated from response normalization so retry behavior can
be tested without weakening schema validation.

### Atomic output

The complete YAML payload is serialized before publication. It is then written
as UTF-8 to a same-directory temporary file in `_data`, flushed, changed to mode
`0644`, and file-synced before it is atomically replaced over
`_data/citations.yml`. The parent `_data` directory is synced after the replace
so the rename is crash durable.

Request, validation, serialization, and filesystem failures before the replace
preserve the previous output bytes. Pre-replace temporary-file cleanup is
best-effort: cleanup failures are reported together with the primary failure and
may leave the temporary file for diagnosis. A parent-directory sync failure is
different because replacement has already occurred: the new, fully written YAML
may be visible, rollback is not attempted, and the updater reports that its
durability across a crash is uncertain.

## Publication Mapping Contract

The production contract derives the configured Scholar user from
`_data/socials.yml` and the five publication IDs from
`_bibliography/papers.bib`. For every BibTeX entry that has a
`google_scholar_id`, `_data/citations.yml` must contain the exact key:

```text
<scholar_userid>:<google_scholar_id>
```

The contract does not compare titles and does not accept a publication-only or
arbitrary-user suffix match for production data. Compatibility fallbacks may
remain in the Liquid template, but committed site data must never rely on them.

The updater may continue to retain all records returned for the Scholar profile.
The existing deletion guard remains fail-closed for that complete profile; this
design does not add a BibTeX parser to the updater or silently discard
non-displayed Scholar records.

## Built-site Contract

After a fresh Jekyll build, tests must verify:

- four Scholar citation badges on the home page;
- five Scholar citation badges on the publications page;
- nine rendered badge instances but five unique publication IDs;
- badge text equals the exact committed citation count;
- `aria-label` contains the same count;
- the Scholar URL contains the configured user and publication identifiers;
- no production badge uses a compatibility fallback or silently displays zero
  because of a missing exact key.

## Workflow Contract

Static workflow tests must lock down:

- cron `17 8 * * *` and manual dispatch support;
- the fixed concurrency group and non-cancelling behavior;
- `timeout-minutes: 15` for `update` and `5` for `commit`;
- read-only permissions and disabled checkout credentials in the secret-bearing
  job;
- write permission only in the secret-free commit job;
- artifact transfer between jobs;
- absence of force-push behavior;
- the three-attempt rebase/push conflict loop;
- the downstream `workflow_run` success, branch, and same-repository guards;
- explicit checkout of the latest default branch for a citation-triggered
  deployment.

## Test Strategy

1. Extend Python updater contracts for the maintained client, timeout, retry
   classification, retry limits, pre-replace rollback, cleanup reporting, and
   post-replace durability uncertainty.
2. Extend release contracts for dependency replacement, workflow permissions,
   cron, timeouts, concurrency, and deployment handoff.
3. Add a cross-layer source-data contract for exact BibTeX-to-citation keys.
4. Add fresh-build HTML assertions for home and publications citation badges.
5. Run the existing citation, release, Jekyll, accessibility, browser, PDF,
   dependency-audit, and workflow syntax suites.

## Rollout and Verification

1. Implement on a feature branch and pass the complete local and pull-request
   checks.
2. Merge the reviewed change to `main`.
3. Manually dispatch `Update Scholar Citations` on `main`.
4. Confirm both the `update` and `commit` jobs complete successfully, including
   artifact transfer and least-privilege permissions.
5. Confirm the resulting `workflow_run` starts `Deploy site`, checks out the
   latest `main`, and completes both build and Pages deployment.
6. Because a same-UTC-day dispatch may skip the live request, inspect the next
   `08:17 UTC` scheduled run and confirm that it executes the real SerpApi fetch.
7. Verify the live home and publications pages against the committed citation
   counts.

## Acceptance Criteria

- The daily workflow is active on `main` with the non-hourly schedule.
- The maintained SerpApi client is hash-locked and the legacy client is absent.
- Transient failures retry within strict time limits; permanent, malformed, and
  pre-replace publication failures preserve the previous citation bytes.
- Successful publication sets mode `0644` and syncs both the file and parent
  directory; a post-replace directory-sync failure surfaces uncertain durability
  even though the new valid file may already be visible.
- All five displayed publications have exact citation keys.
- Freshly built pages render the correct four/home and five/publications badges.
- A manual production dispatch and its downstream Pages deployment succeed.
- The first subsequent scheduled run performs a real API fetch successfully.

## References

- [GitHub Actions workflow scheduling](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)
- [GitHub workflow triggering with `GITHUB_TOKEN`](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [SerpApi maintained Python integration](https://serpapi.com/integrations/python)
