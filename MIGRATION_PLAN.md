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
