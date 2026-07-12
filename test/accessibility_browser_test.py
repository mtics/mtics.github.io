#!/usr/bin/env python3
"""Keyboard, responsive-layout, theme, and Axe regression against the built site.

PNG files emitted by this suite are diagnostic browser artifacts only. They
have no approved pixel baseline and are not visual/pixel-regression evidence.

The browser dependency is pinned in requirements-build.in/requirements-build.txt.
Run in that locked Python environment with a local Chromium/Chrome:

    python test/accessibility_browser_test.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright


BASE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8091").rstrip("/")
CHROME = os.environ.get(
    "CHROME_EXECUTABLE",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
ARTIFACT_DIR = Path(os.environ.get("A11Y_ARTIFACT_DIR", "/tmp/mtics-a11y"))
SITE_DIR = Path(os.environ.get("SITE_DIR", "_site"))
AXE_URL = "https://unpkg.com/axe-core@4.10.3/axe.min.js"
AXE_SHA256 = "880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb"
DIAGNOSTIC_SCREENSHOT_NOTICE = (
    "Diagnostic screenshot only; no pixel baseline or pixel-diff assertion is applied."
)


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        if not scheme or not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, hostname, port


BASE_ORIGIN = _origin(BASE_URL)


def rendered_html_routes(site_dir: Path) -> list[str]:
    """Map every rendered HTML file to the route a static server exposes."""

    site_root = site_dir.resolve()
    html_files = sorted(path for path in site_root.rglob("*.html") if path.is_file())
    if not html_files:
        raise RuntimeError(f"No rendered HTML files found below {site_root}")

    routes: set[str] = set()
    for html_file in html_files:
        relative = html_file.relative_to(site_root).as_posix()
        if relative == "index.html":
            route = "/"
        elif relative.endswith("/index.html"):
            route = f"/{relative.removesuffix('index.html')}"
        else:
            route = f"/{relative}"
        routes.add(route)
    return sorted(routes)


def navigate(page: Page, path: str) -> dict[str, object]:
    target_url = f"{BASE_URL}{path}"
    local_failures: set[str] = set()

    def is_same_origin(url: str) -> bool:
        return BASE_ORIGIN is not None and _origin(url) == BASE_ORIGIN

    def record_failed_request(request) -> None:
        if is_same_origin(request.url):
            reason = request.failure or "unknown browser failure"
            local_failures.add(f"request failed: {request.url} ({reason})")

    def record_error_response(response) -> None:
        if is_same_origin(response.url) and response.status >= 400:
            local_failures.add(f"HTTP {response.status}: {response.url}")

    page.on("requestfailed", record_failed_request)
    page.on("response", record_error_response)
    main_response = None
    navigation_error: Exception | None = None
    try:
        try:
            main_response = page.goto(target_url, wait_until="domcontentloaded")
        except Exception as error:
            navigation_error = error
        if main_response is not None:
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                # Third-party fonts/images may remain active; DOMContentLoaded has
                # fired and only same-origin failures are release-blocking.
                page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("requestfailed", record_failed_request)
        page.remove_listener("response", record_error_response)

    main_status = main_response.status if main_response is not None else None
    if main_response is None:
        detail = f": {navigation_error}" if navigation_error else ""
        local_failures.add(f"main response unavailable: {target_url}{detail}")
    elif main_status >= 400:
        local_failures.add(f"main response HTTP {main_status}: {main_response.url}")

    failures = sorted(local_failures)
    assert failures == [], f"{path}: same-origin navigation/resource failures: {failures}"
    return {"main_status": main_status, "local_failures": failures}


def assert_navigation_resource_gate(page: Page) -> dict[str, object]:
    error_page_navigation = navigate(page, "/404.html")
    assert error_page_navigation == {"main_status": 200, "local_failures": []}, error_page_navigation

    resource_fixture_path = "/__resource-gate-fixture__/"
    resource_fixture_url = f"{BASE_URL}{resource_fixture_path}"
    broken_resource_url = f"{resource_fixture_url}broken.js"
    aborted_resource_url = f"{resource_fixture_url}aborted.js"
    external_resource_url = "https://third-party.invalid/flaky.png"

    page.route(
        resource_fixture_url,
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=(
                f'<script src="{broken_resource_url}"></script>'
                f'<script src="{aborted_resource_url}"></script>'
                f'<img src="{external_resource_url}" alt="External fixture">'
            ),
        ),
    )
    page.route(broken_resource_url, lambda route: route.fulfill(status=404, body="missing"))
    page.route(aborted_resource_url, lambda route: route.abort(error_code="connectionfailed"))
    page.route(external_resource_url, lambda route: route.abort(error_code="connectionfailed"))
    resource_failure = ""
    try:
        try:
            navigate(page, resource_fixture_path)
        except AssertionError as error:
            resource_failure = str(error)
    finally:
        page.unroute(resource_fixture_url)
        page.unroute(broken_resource_url)
        page.unroute(aborted_resource_url)
        page.unroute(external_resource_url)

    assert "broken.js" in resource_failure and "HTTP 404" in resource_failure, resource_failure
    assert "aborted.js" in resource_failure and "request failed" in resource_failure, resource_failure
    assert "third-party.invalid" not in resource_failure, resource_failure

    missing_page_path = "/__resource-gate-main-404__/"
    missing_page_url = f"{BASE_URL}{missing_page_path}"
    page.route(missing_page_url, lambda route: route.fulfill(status=404, body="missing page"))
    main_failure = ""
    try:
        try:
            navigate(page, missing_page_path)
        except AssertionError as error:
            main_failure = str(error)
    finally:
        page.unroute(missing_page_url)
    assert missing_page_url in main_failure and "HTTP 404" in main_failure, main_failure

    return {
        "404_page_status": error_page_navigation["main_status"],
        "missing_local_resource_mutation": "rejected",
        "failed_local_request_mutation": "rejected",
        "external_request_failure": "ignored",
        "unexpected_main_404_mutation": "rejected",
    }


def contrast_ratio(page: Page, foreground_selector: str, background_selector: str) -> float:
    return page.evaluate(
        r"""
        ([foregroundSelector, backgroundSelector]) => {
          const parse = (value) => {
            const channels = value.match(/[\d.]+/g).slice(0, 3).map(Number).map((channel) => channel / 255);
            return channels.map((channel) =>
              channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4)
            );
          };
          const luminance = (value) => {
            const [red, green, blue] = parse(value);
            return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
          };
          const foreground = getComputedStyle(document.querySelector(foregroundSelector)).color;
          const background = getComputedStyle(document.querySelector(backgroundSelector)).backgroundColor;
          const first = luminance(foreground);
          const second = luminance(background);
          return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
        }
        """,
        [foreground_selector, background_selector],
    )


def assert_keyboard_disclosures(page: Page) -> dict[str, object]:
    navigate(page, "/publications/")

    bibtex = page.locator("a.bibtex.btn").first
    assert bibtex.count() == 1, "expected a BibTeX disclosure"
    abstract = page.locator("a.abstract.btn").first
    assert abstract.count() == 1, "expected an abstract disclosure"

    def state(trigger) -> dict[str, object]:
        return trigger.evaluate(
            """(element) => ({
              expanded: element.getAttribute('aria-expanded'),
              panelOpen: document.getElementById(element.getAttribute('aria-controls')).classList.contains('open')
            })"""
        )

    abstract.click()
    page.wait_for_timeout(100)
    abstract_mouse_open = state(abstract)
    assert abstract_mouse_open == {"expanded": "true", "panelOpen": True}, abstract_mouse_open

    bibtex.click()
    page.wait_for_timeout(100)
    same_entry_switch = {"abstract": state(abstract), "bibtex": state(bibtex)}
    assert same_entry_switch == {
        "abstract": {"expanded": "false", "panelOpen": False},
        "bibtex": {"expanded": "true", "panelOpen": True},
    }, same_entry_switch
    bibtex.click()

    abstract.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    abstract_enter_open = state(abstract)
    assert abstract_enter_open == {"expanded": "true", "panelOpen": True}, abstract_enter_open
    page.keyboard.press("Enter")

    abstract.focus()
    page.keyboard.press("Space")
    page.wait_for_timeout(100)
    abstract_space_open = state(abstract)
    assert abstract_space_open == {"expanded": "true", "panelOpen": True}, abstract_space_open
    page.keyboard.press("Space")

    bibtex.focus()
    page.keyboard.press("Space")
    page.wait_for_timeout(100)
    panel_id = bibtex.get_attribute("aria-controls")
    bibtex_open = page.evaluate(
        """(panelId) => ({
          expanded: document.activeElement.getAttribute('aria-expanded'),
          panelOpen: document.getElementById(panelId).classList.contains('open')
        })""",
        panel_id,
    )
    assert bibtex_open == {"expanded": "true", "panelOpen": True}, bibtex_open

    page.keyboard.press("Space")
    page.wait_for_timeout(100)
    bibtex_closed = page.evaluate(
        """(panelId) => ({
          expanded: document.activeElement.getAttribute('aria-expanded'),
          panelOpen: document.getElementById(panelId).classList.contains('open')
        })""",
        panel_id,
    )
    assert bibtex_closed == {"expanded": "false", "panelOpen": False}, bibtex_closed

    more_authors = page.locator("button.more-authors").first
    assert more_authors.count() == 1, "expected a more-authors button"
    collapsed_authors_text = more_authors.text_content().strip()
    more_authors.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    more_authors_expanded = more_authors.get_attribute("aria-expanded")
    assert more_authors_expanded == "true", more_authors_expanded
    page.keyboard.press("Enter")
    page.wait_for_timeout(2_000)
    more_authors_collapsed = {
        "expanded": more_authors.get_attribute("aria-expanded"),
        "text": more_authors.text_content().strip(),
    }
    assert more_authors_collapsed == {
        "expanded": "false",
        "text": collapsed_authors_text,
    }, more_authors_collapsed

    duplicate_ids = page.evaluate(
        """() => {
          const counts = {};
          document.querySelectorAll('[id]').forEach((element) => {
            counts[element.id] = (counts[element.id] || 0) + 1;
          });
          return Object.entries(counts).filter(([, count]) => count > 1);
        }"""
    )
    assert duplicate_ids == [], duplicate_ids

    navigate(page, "/cv/")
    cv_duplicate_ids = page.evaluate(
        """() => {
          const counts = {};
          document.querySelectorAll('[id]').forEach((element) => {
            counts[element.id] = (counts[element.id] || 0) + 1;
          });
          return Object.entries(counts).filter(([, count]) => count > 1);
        }"""
    )
    assert cv_duplicate_ids == [], cv_duplicate_ids
    return {
        "abstract_mouse_open": abstract_mouse_open,
        "abstract_enter_open": abstract_enter_open,
        "abstract_space_open": abstract_space_open,
        "same_entry_abstract_to_bibtex": same_entry_switch,
        "bibtex_space_open": bibtex_open,
        "bibtex_space_closed": bibtex_closed,
        "more_authors_enter_expanded": more_authors_expanded,
        "more_authors_rapid_repeat_collapsed": more_authors_collapsed,
        "duplicate_ids_after_dom_ready": duplicate_ids,
        "cv_duplicate_ids_after_dom_ready": cv_duplicate_ids,
    }


def assert_dropdown_space_activation(page: Page) -> dict[str, object]:
    header_source = (Path(__file__).resolve().parents[1] / "_includes/header.liquid").read_text(encoding="utf-8")
    marker_index = header_source.index('data-nav-dropdown-toggle="true"')
    anchor_index = header_source.rfind("<a", 0, marker_index)
    button_index = header_source.rfind("<button", 0, marker_index)
    toggle_tag = "button" if button_index > anchor_index else "a"
    toggle_attributes = 'type="button"' if toggle_tag == "button" else 'href="#" role="button"'

    fixture = f"""
      <nav aria-label="Fixture navigation">
        <div class="dropdown">
          <{toggle_tag} id="fixture-dropdown" class="dropdown-toggle" {toggle_attributes}
             data-nav-dropdown-toggle="true" aria-haspopup="true" aria-expanded="false">Resources</{toggle_tag}>
          <div class="dropdown-menu" aria-labelledby="fixture-dropdown"><a href="/child/">Child</a></div>
        </div>
      </nav>
      <script src="{BASE_URL}/assets/js/nav-toggle.js"></script>
    """
    page.set_content(fixture, wait_until="networkidle")
    toggle = page.locator("#fixture-dropdown")
    toggle.focus()
    page.keyboard.press("Space")
    page.wait_for_timeout(100)
    state = {
        "tag": toggle.evaluate("element => element.tagName.toLowerCase()"),
        "expanded": toggle.get_attribute("aria-expanded"),
        "menu_open": page.locator(".dropdown-menu").evaluate("element => element.classList.contains('show')"),
    }
    assert state == {"tag": "button", "expanded": "true", "menu_open": True}, state
    return state


def assert_embedded_video_targets_exact_panel(page: Page) -> dict[str, object]:
    template_source = (Path(__file__).resolve().parents[1] / "_layouts/bib.liquid").read_text(encoding="utf-8")
    abstract_match = re.search(
        r"\{% if entry\.abstract %\}(.*?)\{% if doi_id and doi_id != empty %\}",
        template_source,
        re.DOTALL,
    )
    video_match = re.search(
        r"\{% if video_url and video_url != empty and site\.enable_video_embedding %\}(.*?)\{% elsif video_url and video_url != empty %\}",
        template_source,
        re.DOTALL,
    )
    assert abstract_match and video_match
    abstract_trigger = abstract_match.group(1).replace("{{ entry_dom_id | escape }}", "fixture")
    video_trigger = video_match.group(1).replace("{{ entry_dom_id | escape }}", "fixture")
    fixture = f"""
      <div class="publications"><ol class="bibliography"><li><div id="fixture">
        <div class="links">{abstract_trigger}{video_trigger}</div>
        <div id="fixture-abstract" class="abstract hidden">Abstract panel</div>
        <div id="fixture-video" class="abstract video hidden">Video panel</div>
      </div></li></ol></div>
      <script src="{BASE_URL}/assets/js/bibliography.js"></script>
    """
    page.set_content(fixture, wait_until="networkidle")

    def fixture_state() -> dict[str, object]:
        return page.evaluate(
            """() => ({
              abstractExpanded: document.querySelector('a.abstract').getAttribute('aria-expanded'),
              abstractOpen: document.getElementById('fixture-abstract').classList.contains('open'),
              videoExpanded: document.querySelector('button.video-toggle').getAttribute('aria-expanded'),
              videoOpen: document.getElementById('fixture-video').classList.contains('open')
            })"""
        )

    page.locator("a.abstract").click()
    page.locator("button.video-toggle").click()
    video_open = fixture_state()
    assert video_open == {
        "abstractExpanded": "false",
        "abstractOpen": False,
        "videoExpanded": "true",
        "videoOpen": True,
    }, video_open

    page.locator("a.abstract").click()
    abstract_reopened = fixture_state()
    assert abstract_reopened == {
        "abstractExpanded": "true",
        "abstractOpen": True,
        "videoExpanded": "false",
        "videoOpen": False,
    }, abstract_reopened
    return {"video_open": video_open, "abstract_reopened": abstract_reopened}


def assert_themes_and_capture(page: Page) -> dict[str, object]:
    navigate(page, "/publications/")
    results: dict[str, object] = {}
    for theme in ("light", "dark"):
        page.evaluate(
            """(theme) => {
              document.documentElement.dataset.theme = theme;
              document.documentElement.dataset.themeSetting = theme;
            }""",
            theme,
        )
        page.wait_for_timeout(900)
        ratios = {
            "publication_year": contrast_ratio(page, ".publications h2.bibliography", "body"),
            "venue_badge": contrast_ratio(page, ".publications .abbr abbr", ".publications .abbr abbr"),
            "more_authors": contrast_ratio(page, "button.more-authors", "body"),
        }
        for name, ratio in ratios.items():
            assert ratio >= 4.5, f"{theme} {name}: {ratio:.2f}:1"

        feddae_background = page.locator('img.preview[src*="publication_preview/FedDAE"]').evaluate(
            "element => getComputedStyle(element).backgroundColor"
        )
        assert feddae_background == "rgb(255, 255, 255)", feddae_background
        screenshot = ARTIFACT_DIR / f"publications-{theme}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        results[theme] = {
            "ratios": {name: round(ratio, 3) for name, ratio in ratios.items()},
            "feddae_background": feddae_background,
            "diagnostic_screenshot": str(screenshot),
            "diagnostic_screenshot_notice": DIAGNOSTIC_SCREENSHOT_NOTICE,
        }
    return results


def assert_mobile_site_integrity(page: Page) -> dict[str, object]:
    page.set_viewport_size({"width": 390, "height": 844})
    results: dict[str, object] = {}

    def geometry() -> dict[str, int]:
        return page.evaluate(
            """() => ({
              viewportWidth: document.documentElement.clientWidth,
              documentScrollWidth: document.documentElement.scrollWidth,
              bodyScrollWidth: document.body.scrollWidth
            })"""
        )

    def assert_no_horizontal_overflow(label: str) -> dict[str, int]:
        dimensions = geometry()
        widest_content = max(dimensions["documentScrollWidth"], dimensions["bodyScrollWidth"])
        assert widest_content <= dimensions["viewportWidth"] + 1, (
            f"{label} horizontally overflows the 390px mobile viewport: {dimensions}"
        )
        return dimensions

    for path, label in (("/", "about"), ("/publications/", "publications"), ("/cv/", "cv")):
        navigate(page, path)
        toggle = page.locator("button.navbar-toggler-main[data-nav-toggle]").first
        assert toggle.count() == 1 and toggle.is_visible(), f"{label}: missing visible mobile navigation toggle"
        panel_id = toggle.get_attribute("aria-controls")
        assert panel_id, f"{label}: mobile navigation toggle must identify its panel"
        panel = page.locator(f"#{panel_id}")
        assert panel.count() == 1, f"{label}: missing mobile navigation panel #{panel_id}"

        initial = {
            "expanded": toggle.get_attribute("aria-expanded"),
            "panel_open": panel.evaluate("element => element.classList.contains('show')"),
            "geometry": assert_no_horizontal_overflow(f"{label} with navigation closed"),
        }
        assert initial["expanded"] == "false" and initial["panel_open"] is False, initial

        toggle.click()
        page.wait_for_timeout(100)
        opened = {
            "expanded": toggle.get_attribute("aria-expanded"),
            "panel_open": panel.evaluate("element => element.classList.contains('show')"),
            "geometry": assert_no_horizontal_overflow(f"{label} with navigation open"),
        }
        assert opened["expanded"] == "true" and opened["panel_open"] is True, opened

        toggle.click()
        page.wait_for_timeout(100)
        closed = {
            "expanded": toggle.get_attribute("aria-expanded"),
            "panel_open": panel.evaluate("element => element.classList.contains('show')"),
            "geometry": assert_no_horizontal_overflow(f"{label} after closing navigation"),
        }
        assert closed["expanded"] == "false" and closed["panel_open"] is False, closed
        results[label] = {"path": path, "initial": initial, "opened": opened, "closed": closed}

    return results


def assert_cv_responsive_layout(page: Page) -> dict[str, object]:
    results: dict[str, object] = {}
    for label, viewport in (
        ("desktop", {"width": 1440, "height": 1100}),
        ("mobile", {"width": 390, "height": 844}),
    ):
        page.set_viewport_size(viewport)
        navigate(page, "/cv/")
        row = page.locator(".cv .list-group-item .row").first
        metadata = row.locator(".cv-entry-meta")
        content = row.locator(".cv-entry-content")
        metadata_box = metadata.bounding_box()
        content_box = content.bounding_box()
        assert metadata_box and content_box, f"missing CV entry geometry at {label}"

        styles = content.evaluate(
            """element => ({
              fontSize: getComputedStyle(element).fontSize,
              cardBorderTopWidth: getComputedStyle(element.closest('.card')).borderTopWidth
            })"""
        )
        assert styles == {"fontSize": "16px", "cardBorderTopWidth": "0px"}, styles
        if label == "desktop":
            assert abs(metadata_box["y"] - content_box["y"]) <= 1, (metadata_box, content_box)
            assert metadata_box["width"] < content_box["width"], (metadata_box, content_box)
            assert abs(metadata_box["x"] + metadata_box["width"] - content_box["x"]) <= 1, (
                metadata_box,
                content_box,
            )
        else:
            assert abs(metadata_box["width"] - content_box["width"]) <= 1, (metadata_box, content_box)
            assert content_box["y"] >= metadata_box["y"] + metadata_box["height"], (
                metadata_box,
                content_box,
            )

        screenshot = ARTIFACT_DIR / f"cv-{label}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        results[label] = {
            "viewport": viewport,
            "metadata": metadata_box,
            "content": content_box,
            "styles": styles,
            "diagnostic_screenshot": str(screenshot),
            "diagnostic_screenshot_notice": DIAGNOSTIC_SCREENSHOT_NOTICE,
        }
    return results


def run_axe(page: Page, axe_source: str) -> list[dict[str, object]]:
    page.add_script_tag(content=axe_source)
    result = page.evaluate(
        """async () => await axe.run(document, {
          resultTypes: ['violations']
        })"""
    )
    return [
        {
            "id": violation["id"],
            "impact": violation["impact"],
            "description": violation["description"],
            "nodes": [
                {
                    "target": node["target"],
                    "failureSummary": node.get("failureSummary"),
                }
                for node in violation["nodes"]
            ],
        }
        for violation in result["violations"]
    ]


def load_verified_axe_source() -> str:
    with urllib.request.urlopen(AXE_URL, timeout=30) as response:
        axe_payload = response.read()

    axe_digest = hashlib.sha256(axe_payload).hexdigest()
    if axe_digest != AXE_SHA256:
        raise RuntimeError(
            f"Downloaded Axe payload digest mismatch: expected {AXE_SHA256}, got {axe_digest}"
        )
    return axe_payload.decode("utf-8")


def main() -> None:
    chrome_path = Path(CHROME)
    if not chrome_path.is_file() or not os.access(chrome_path, os.X_OK):
        raise RuntimeError(f"Required executable Chromium/Chrome not found at {chrome_path}")

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    axe_source = load_verified_axe_source()

    report: dict[str, object] = {
        "base_url": BASE_URL,
        "site_dir": str(SITE_DIR),
        "diagnostic_screenshot_notice": DIAGNOSTIC_SCREENSHOT_NOTICE,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=CHROME)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()

        report["navigation_resource_gate"] = assert_navigation_resource_gate(page)
        report["keyboard"] = assert_keyboard_disclosures(page)
        report["dropdown_keyboard"] = assert_dropdown_space_activation(page)
        report["video_fixture"] = assert_embedded_video_targets_exact_panel(page)
        report["themes"] = assert_themes_and_capture(page)
        report["mobile_site_integrity"] = assert_mobile_site_integrity(page)
        report["cv_responsive"] = assert_cv_responsive_layout(page)
        page.set_viewport_size({"width": 1440, "height": 1100})

        axe_results: dict[str, object] = {}
        all_violations: list[str] = []
        serious_or_critical: list[str] = []
        axe_routes = rendered_html_routes(SITE_DIR)
        report["axe_routes"] = axe_routes
        for path in axe_routes:
            navigate(page, path)
            for theme in ("light", "dark"):
                page.evaluate(
                    """(theme) => {
                      document.documentElement.dataset.theme = theme;
                      document.documentElement.dataset.themeSetting = theme;
                    }""",
                    theme,
                )
                page.wait_for_timeout(900)
                violations = run_axe(page, axe_source)
                key = f"{path}::{theme}"
                axe_results[key] = violations
                all_violations.extend(
                    f"{key}:{violation['id']}:{violation.get('impact')}"
                    for violation in violations
                )
                serious_or_critical.extend(
                    f"{key}:{violation['id']}" for violation in violations if violation["impact"] in {"serious", "critical"}
                )

        report["axe"] = axe_results
        report["all_violations"] = all_violations
        report["serious_or_critical"] = serious_or_critical
        browser.close()

    report_path = ARTIFACT_DIR / "browser-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    assert all_violations == [], f"Axe violations: {all_violations}"


if __name__ == "__main__":
    main()
