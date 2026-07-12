# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` and follow test-driven development for every behavioral fix.

**Goal:** Fix every confirmed P1, P2, and P3 issue from the 2026-07-12 framework/template audit while preserving the current site content and visual design.

**Architecture:** Keep `al_folio_core` and `al_folio_cv` pinned, but place narrowly scoped local overrides at the theme boundary. Treat current RenderCV v2.8-compatible YAML as the canonical CV schema while retaining tested JSONResume compatibility through explicit normalization. Make delivery reproducible from a clean Git snapshot and enforce the contracts in CI.

**Tech Stack:** Jekyll 4.4, Liquid, Ruby 3.4.10/Bundler 2.6.9, Tailwind/Sass, JavaScript, GitHub Actions, Docker, Python/Node contract tests, Playwright/Axe.

**Workspace rule:** This is an already-dirty feature branch containing user-approved migration work. Do not reset, discard, commit, or push. Agents must edit only their assigned files; after final clean-snapshot verification, stage the complete reviewed change set as explicitly requested by the user.

---

### Task 1: Reproducible release and supply-chain boundary

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/update_scholar_citations.yml`
- Modify: `Dockerfile`, `.devcontainer/Dockerfile`, `.devcontainer/devcontainer.json`
- Modify: `requirements.txt`, `_config.yml`
- Test: `test/release_contract_test.rb`

- [x] Add failing release-contract assertions for tracked lock/test/runtime inputs, executable entry points, full-SHA Action pins, pinned Python requirements, exclusion of `requirements.txt`, and override-audit execution.
- [x] Run `ruby test/release_contract_test.rb` and confirm failures identify the existing delivery gaps.
- [x] Pin Actions to immutable full commit SHAs with version comments, pin Python packages and transitive build tooling with hashes/constraints where practical, and remove unnecessary credential persistence.
- [x] Pin devcontainer and CI runtime inputs sufficiently to make rebuild drift explicit; keep main Docker base digests.
- [x] Exclude `requirements.txt` from Pages and make feed output deterministic or disable the empty feed if the site has no posts.
- [x] Run release tests, workflow YAML parsing, `bash -n`, `docker compose config`, and two isolated builds; confirm only explicitly documented nondeterminism remains.

### Task 2: Canonical RenderCV and complete JSONResume rendering

**Files:**
- Modify: `_data/cv.yml`
- Modify: `_includes/cv/render.liquid`
- Modify: `_includes/cv/awards.liquid`, `education.liquid`, `experience.liquid`, `languages.liquid`, `social_networks.liquid`
- Create as needed: focused CV entry partials under `_includes/cv/`
- Test: `test/cv_schema_rendering_contract_test.rb` and fixtures under `test/fixtures/`

- [x] Add failing fixture builds covering lowercase/arbitrary RenderCV sections, NormalEntry, current header arrays, empty date/location, slug collision, JSONResume profiles/publication name/project description, and heading hierarchy.
- [x] Verify the new tests fail for the audited reasons, not fixture/setup errors.
- [x] Convert `_data/cv.yml` to a RenderCV v2.8-valid schema without changing visible factual content.
- [x] Normalize RenderCV fields and dispatch by entry shape rather than display title; generate collision-safe heading IDs.
- [x] Normalize JSONResume standard fields and produce an `h1 → h2 → h3` outline.
- [x] Remove whitespace-sensitive capture checks and omit absent date/location nodes.
- [x] Run schema validation, fixture builds, CV contract tests, and current-site snapshot assertions.

### Task 3: Accessible theme boundary and robust bibliography

**Files:**
- Create/modify local core overrides: `_includes/header.liquid`, `_includes/scripts.liquid`, `_layouts/page.liquid`, `_includes/figure.liquid` only where required
- Modify: `_layouts/about.liquid`, `_layouts/bib.liquid`
- Modify/create: `assets/js/back-to-top.js`, `assets/js/nav-toggle.js` only where required
- Test: `test/accessibility_contract_test.py`, `test/accessibility_browser_test.py`, focused fixture tests

- [x] Add failing assertions for keyboard-operable back-to-top, top-level page header semantics, uniquely named navigation landmarks/dropdowns, valid progress/picture markup, `social: true` without profile, and quoted BibTeX author names.
- [x] Confirm each assertion fails against the current generated site/fixture.
- [x] Replace the third-party mouse-only back-to-top node with a native labelled button while preserving behavior.
- [x] Override page/header markup to remove nested banner landmarks, give each dropdown a stable unique ID, label primary navigation, and use valid progress/picture markup.
- [x] Render social links independently of profile presence.
- [x] Move bibliography disclosure behavior out of inline JS attributes, store author text safely, escape HTML/JS contexts, and replace legacy Bootstrap `data-toggle` annotation behavior.
- [x] Run static HTML validation, Axe/keyboard tests, bibliography interaction tests, and responsive browser checks.

### Task 4: Correct cache fingerprints and artifact references

**Files:**
- Create/modify: `_plugins/cache_bust_local_assets.rb` or the smallest equivalent local plugin override
- Modify: `_includes/cv/render.liquid`, `_data/socials.yml` or the local social-link override
- Test: add cache assertions to `test/release_contract_test.rb`

- [x] Add failing assertions proving local `_sass` changes alter the `main.css` query fingerprint and CV PDF content changes alter every public PDF URL.
- [x] Verify both assertions fail against the existing implementation.
- [x] Make the CSS digest include local and theme Sass sources in deterministic order.
- [x] Route CV PDF links through `bust_file_cache` in both CV header and social/search surfaces.
- [x] Build before/after controlled fixture changes and confirm fingerprints change exactly when their underlying assets change.

### Task 5: Override manifest, clean snapshot, and full adversarial verification

**Files:**
- Modify: `.al-folio-overrides.yml`
- Modify/add tests only for uncovered regression cases

- [x] Register every intentional local theme override and refresh local hashes only after code review.
- [x] Run `al-folio upgrade audit` and `al-folio upgrade overrides audit --fail-on-stale`; require exit 0.
- [x] Build from a temporary clean snapshot containing all intended current files; run the same commands as GitHub Actions.
- [x] Run release, CV, search, PDF, accessibility, browser, dependency-audit, link/anchor/ID, HTML-validation, Docker/Compose, and reproducibility checks.
- [x] Perform a final spec-compliance review followed by an adversarial code-quality review; fix and re-run until both approve.
- [x] Leave all reviewed changes staged but uncommitted and unpushed, as explicitly requested by the user.
