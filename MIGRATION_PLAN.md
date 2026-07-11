# al-folio v0.x → v1.x Migration Plan (zhw.li)

Branch: `al-folio-v1-migration` (worktree at `/tmp/alfolio-v1-mig`, based on origin/main @ 78c6960).
Live `main` is untouched and stays the deployed site until this branch is verified and approved.
Reference starter clone: `/tmp/al-folio-v1-starter` (alshedivat/al-folio main = v1 contract + `al-folio upgrade` tooling).

## Goal & constraints
- Move zhw.li onto al-folio v1.x (gem-owned runtime) so future upgrades are conflict-free via the override-drift system.
- HARD constraint: job-market season — the live site must not break. Nothing merges to `main` until the branch is byte/visually verified against the current live baseline and the user approves.
- Fold the 22 confirmed audit findings into the v1 build so they are not reintroduced.

## Verified v1 facts (from the pinned starter, 2026-07-11)
- `al_folio.api_version: 1`; `style_engine: tailwind` (Tailwind v4.1.18, preflight:false, entry `assets/tailwind/app.css`).
- `theme: al_folio_core` (= 1.0.11). Runtime lives in gems; the starter ships NO `_layouts/_includes/_sass/_plugins`.
- Bundled gems: al_folio_core, al_icons, al_folio_cv, al_folio_distill, al_folio_upgrade(1.0.3), al_folio_bootstrap_compat, al_cookie, al_analytics, al_citations, al_ext_posts, al_img_tools, al_search, al_charts, al_math, al_comments, al_newsletter.
- **Bootstrap compat crutch**: `al_folio_bootstrap_compat` + `al_folio.compat.bootstrap` (default enabled:false). Support window v1.0–v1.2, deprecated v1.3, removed v2.0. Lets existing Bootstrap/MDB SCSS run during transition → we reach parity WITHOUT an up-front Tailwind rewrite, then modernize later.
- `Gemfile.lock` is committed with sha256 checksums + multi-platform sass-embedded → reproducible (also resolves the audit's unpinned-deps finding).
- Toolchain: `Dockerfile FROM ruby:slim` + `docker compose` (ports 8080/35729, mounts `.:/srv/jekyll`). Official upgrade CLI: `bundle exec al-folio upgrade {audit --no-fail, overrides audit, overrides diff PATH, overrides accept PATH, report}`.
- Ownership routing (docs/BOUNDARIES.md) — used to place each customization below.

## Toolchain status (BLOCKER to resolve first)
- Local Ruby is system 2.6 only (can't run v1). No rbenv/rvm/asdf/colima/podman.
- Docker Desktop app exists but did not start headlessly on first attempts (needs GUI/first-run, retry pending).
- Fallbacks: (a) user starts Docker Desktop once; (b) `brew install ruby@3.3` + `imagemagick` for a local toolchain. Docker is preferred (matches CI `ruby:slim`).
- Phase 1 gate = `bundle exec al-folio upgrade audit --no-fail` runs on the pristine starter.

## Customization → v1 mapping (the crux)
Fork surface: 13 custom `_layouts`, 15 `_sass` files (+font-awesome), custom `_includes/head.liquid` & `footer.liquid`, `bin/update_scholar_citations.py` + workflow, `_data/*`, `_bibliography/papers.bib`.

| Fork customization | v1 disposition |
|---|---|
| Burgundy theme (`_sass/_variables.scss` `$burgundy-*`, `_themes.scss`) | Keep as local `_sass` override under bootstrap-compat first; later re-express as Tailwind theme tokens / CSS vars. |
| `_sass/_components.scss` `.profile-social`, `_publications.scss` `.scholar-citations` | Local `_sass` overrides (site-specific). Fold in audit fixes (specificity, `transition` shorthand). |
| `_layouts/about.liquid` (social icons in sidebar) | Local layout override of `al_folio_core`; re-diff against v1 core about layout. |
| `_layouts/cv.liquid` (Experience-first removal, Service case) | Now owned by `al_folio_cv`. Check whether v1 CV plugin already renders cv.yml order + fixes the `cv.location` bug; keep override only if still needed. |
| `_layouts/bib.liquid` (Scholar badge redesign) | Citation logic owned by `al_citations`; badge styling stays a local override. Verify citations.yml data contract vs `al_citations`. |
| `_includes/head.liquid` (`?v=site.time` cache-bust workaround) | Re-evaluate: v1 core head may fix the `bust_css_cache` issue → workaround may be droppable. |
| `_includes/footer.liquid` (minimal copyright) | Local include override; re-diff vs v1 core footer. |
| `bin/update_scholar_citations.py` + `.github/workflows/update_scholar_citations.yml` | Site-owned, carry over. BUT confirm `al_citations` still reads `_data/citations.yml` in the same shape. |
| `.github/workflows/deploy.yml` | Rebuild from v1 starter's workflow (Tailwind build step, Gemfile.lock cache). Fold audit deploy fixes. |
| Content: `_bibliography`, `_news`, `_pages`, `_data`, `assets` | Port verbatim; then apply content audit fixes (TKDE entry, 2nd AAAI 2026, cv.location). |

## Phase plan
- **P1 Toolchain**: bring up Docker (or brew ruby@3.3); `bundle install` starter; `al-folio upgrade audit --no-fail` green on pristine starter. Snapshot live baseline (Playwright: home/pubs/cv/news + mobile) for pixel diff later.
- **P2 Scaffold+content**: copy starter's v1 wiring (`_config.yml` al_folio block + plugin list, Gemfile, Gemfile.lock, Dockerfile, assets/tailwind, bin/, .github) into the branch; port site content/data/bibliography/assets; merge v0 `_config.yml` VALUES into the v1 config contract (identity, socials, scholar, enable_* → v1 feature flags). Remove stale copied runtime files now gem-owned.
- **P3 Overrides**: enable bootstrap-compat; re-add only genuine overrides (burgundy `_sass`, about/footer/bib layout bits) as local files; `overrides audit` → `diff` → `accept`; commit `.al-folio-overrides.yml`.
- **P4 Audit fixes**: apply the 22 confirmed fixes in the v1 context (many now free: Gemfile.lock committed, cv.location via al_folio_cv, figure.liquid jQuery gone in core, SRI/purgecss obsolete under Tailwind). Track which are auto-resolved vs still-manual.
- **P5 Verify**: `docker compose up`, drive home/CV/publications/news + mobile; Playwright pixelmatch vs P1 baseline; 0 console errors; feed title, search index, dark mode, citation badges, MathJax. `al-folio upgrade report`.
- **P6 Land**: push branch, open PR, present visual diff + change summary for approval; only then merge to main. Rollback = tag `pre-al-folio-cutover` already exists; main is unchanged so revert = ignore branch.

## Open decisions (surface to user)
1. Bootstrap-compat lifespan: reach parity on compat (fast, but deprecated by v1.3) vs. invest now in the Tailwind re-theme (slower, future-proof). Recommend: parity on compat first, Tailwind as a follow-up.
2. Toolchain: OK to `brew install ruby@3.3` + imagemagick if Docker won't start? (reversible)
3. Adopt v1's richer defaults (repo trophies, distill, newsletter) or keep the current minimal site? Recommend: keep minimal, enable nothing new during job season.

## STATUS — 2026-07-11 (end of session 1)
DONE (committed on branch):
- P0 isolation: worktree `/tmp/alfolio-v1-mig`, branch `al-folio-v1-migration` (base origin/main @78c6960). Live `main` untouched.
- P1 toolchain PROVEN: `docker compose build` in `/tmp/al-folio-v1-starter` → image `amirpourmand/al-folio:latest` (Ruby 4.0/bundler 4.0.6, all al_folio_* gems baked). `al-folio upgrade` CLI runs; pristine starter builds green.
  - Reusable run: `docker run --rm -v /tmp/alfolio-v1-mig:/srv/jekyll -w /srv/jekyll amirpourmand/al-folio:latest bash -lc "JEKYLL_ENV=production bundle exec jekyll build"`
- P2 skeleton (commit ecaf1b8): removed gem-owned `_layouts/_includes/_sass/_plugins`; brought v1 Gemfile/lock+Dockerfile+entry_point; `_config.yml` rebased on v1 contract with site identity + scholar [Li]/[Zhiwei,Z.] + google_scholar-only badges + external_sources removed; dropped v0 theme assets (css/js/fonts/webfonts) + `_scripts`. **Content builds & renders**: home "Zhiwei Li", publications (FedDAE/FedVLR + scholar badges), CV (Education/Experience), news (AAAI/ICLR/PRICAI). `al-folio upgrade audit` = 0 blocking / 0 non-blocking; overrides clean.

CAVEAT: site currently renders in **STOCK v1 look** — burgundy theme, sidebar-social, custom cv/bib/about/footer NOT yet re-applied (that is P3).

NEXT (P3, start here):
1. Diff live baseline vs current stock-v1 (Playwright screenshots of both).
2. Burgundy theme: find v1's theme-color mechanism (Tailwind token / CSS var in al_folio_core) — prefer a config/token override over resurrecting the 15 `_sass` files. If needed, enable `al_folio.compat.bootstrap.enabled: true` + re-add minimal `_sass` overrides.
3. Re-add genuine overrides only: sidebar social icons (about layout), minimal footer, scholar-badge styling, profile-social. After each, `al-folio upgrade overrides audit` → `accept` → commit `.al-folio-overrides.yml`.
4. CV data: fork uses `_data/cv.yml` (rendercv) + `cv.md cv_format: rendercv`; v1 `al_folio_cv` + starter config expects `assets/json/resume.json` (jekyll_get_json/jsonresume block still in config). RECONCILE: either keep rendercv path (confirm al_folio_cv supports it) or convert cv.yml→resume.json. Verify `al_citations` reads `_data/citations.yml` in the same shape (SerpApi script output).
Then P4 audit fixes (feed title "blank" still reproduces; exclude MIGRATION_PLAN.md+requirements.txt from _site; TKDE + 2nd AAAI 2026 bib entries; v1 deploy.yml with Tailwind build), P5 verify, P6 PR.

## P3 progress — burgundy DONE (commit d356c60)
Approach A confirmed: v1 theme color = CSS var `--global-theme-color`, sourced from `$purple-color`(light)/`$cyan-color`(dark) in gem `_sass/_variables.scss`. Override needs BOTH `_sass/_variables.scss` (burgundy #a51c30/#e5495d) AND `_sass/_themes.scss` (gem-verbatim bridge) — Dart Sass `@use "variables"` resolves relative to the importing file's dir, so shadowing variables alone is ignored until themes.scss is also local. Acknowledged in `.al-folio-overrides.yml`.
Preview server (KEEP UP for user): `docker run -d --name alfolio-v1-serve -p 8091:8000 -v /tmp/alfolio-v1-mig/_site:/site -w /site amirpourmand/al-folio:latest python3 -m http.server 8000` → http://localhost:8091. Rebuild _site then it auto-serves latest.

### Live-vs-v1 deltas remaining (screenshot compare @1280):
MATCHES: burgundy accents everywhere, solid-burgundy venue badges, pub buttons (ABS/ARXIV/BIB/CODE), scholar citation badges, job-market callout w/ burgundy left border, nav, dark toggle, name two-tone, all content.
DELTAS to close (v0 customizations not in v1 default):
1. Social icons (CV/email/GitHub/LinkedIn/ORCID/Scholar) + "Patiently hoping…" note: live has them in the PROFILE SIDEBAR under the photo; v1 default puts them at PAGE BOTTOM. → override v1 about layout (fork's `.profile-social` in about.liquid). MOST visible.
2. Publication preview thumbnails: live caps ~110px (small); v1 shows full-size figures. → PREFERENCE (ask user) — `.preview` max-height override if they want the small caps back.
3. Footer: live "© 2026 Zhiwei Li · Sydney, Australia · Last updated <date>"; v1 default "© Copyright 2026 … Last updated:". → footer override or footer_text config.
4. Section heading case: v1 renders "news"/"selected publications" lowercase vs live Title Case. → investigate about layout heading (text-transform or content).
Each delta: find gem file → local override → build → `overrides audit`→`accept` → commit.

## P3 COMPLETE (commit 6f1755b) — v1 visually matches live
All 4 deltas closed and screenshot-verified (home + CV) against live:
- Burgundy (P3 earlier): `_sass/_variables.scss` + `_sass/_themes.scss` overrides.
- Social icons in profile sidebar + Title-case headings: `_layouts/about.liquid` override.
- Minimal footer: `_includes/footer.liquid` override.
- Preview 110px cap + `.profile-social` styling (with audit `transition:` fix): new `_sass/_brand.scss`, wired via `@use "brand"` in `_themes.scss`.
- 4 overrides acknowledged in `.al-folio-overrides.yml`; `_brand.scss` is a site-only partial (not a gem shadow).
CV page: `al_folio_cv` reads `_data/cv.yml` (rendercv) NATIVELY — no resume.json conversion needed. Renders Contact/Summary/Experience/Education/Awards/Service/Languages with burgundy TOC. RECONCILIATION RESOLVED.
Citation badges: `al_citations` reads `_data/citations.yml` fine (badges show 1/11/39/92 — the July data, fresher than stale live 10/31/88).

### Remaining micro-deltas noted (Phase 4/5):
- Publication button labels: v1 "Abs/arXiv/Bib/Code" vs live uppercase "ABS/ARXIV/BIB/CODE" (cosmetic; `text-transform: uppercase` if wanted).
- CV Contact block omits Location row (audit finding — al_folio_cv renders address-shape, not cv.location; add location or address).

## Phase 4 remaining (audit fixes into v1)
- feed.xml still titled "blank" (jekyll-feed reads site.title raw) → set a real feed title / override.
- exclude MIGRATION_PLAN.md (+ requirements.txt) from _site (add to _config exclude).
- Content: add IEEE TKDE @article + 2nd AAAI 2026 entry to papers.bib (google_scholar_id from citations.yml).
- requirements.txt: scholarly→google-search-results.
- deploy.yml: rebuild for v1 — Tailwind build step, Gemfile.lock cache, and DECISION (audit #1): schedule a daily/periodic build so citation updates actually publish (GITHUB_TOKEN citation commits don't trigger deploy). NEEDS USER on cadence.
- CV Location row.
## Phase 5: publications page, dark mode, ninja-keys search, mobile, feed, functional verification vs live (Playwright). Preview server: docker container `alfolio-v1-serve` on :8091 (restart cmd above).
## Phase 6: push branch → PR → user approves → merge to main.
