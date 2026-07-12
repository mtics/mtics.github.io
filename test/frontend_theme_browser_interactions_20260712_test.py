#!/usr/bin/env python3
"""Browser interaction contract for the local accessibility JavaScript.

The fixture uses the Playwright version locked in the build requirements and
the Chromium executable pinned in the delivery image.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHROME = Path(
    os.environ.get(
        "CHROME_EXECUTABLE",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
)


class FrontendThemeBrowserInteractionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CHROME.is_file() or not os.access(CHROME, os.X_OK):
            raise RuntimeError(f"Required executable Chromium/Chrome not found at {CHROME}")

    def test_local_scripts_operate_native_controls_and_exact_panels(self) -> None:
        bibliography_js = (PROJECT_ROOT / "assets/js/bibliography.js").as_uri()
        back_to_top_js = (PROJECT_ROOT / "assets/js/back-to-top.js").as_uri()
        fixture = f"""<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>Frontend interaction fixture</title></head>
  <body data-result="pending" style="min-height: 5000px">
    <main id="fixture">
      <div class="links">
        <a id="abstract-trigger" href="#fixture-abstract" class="abstract btn" role="button"
           data-bib-disclosure aria-controls="fixture-abstract" aria-expanded="false">Abs</a>
        <button id="video-trigger" type="button" class="video-toggle btn"
                data-bib-disclosure aria-controls="fixture-video" aria-expanded="false">Video</button>
      </div>
      <div id="fixture-abstract" class="abstract hidden">Abstract panel</div>
      <div id="fixture-video" class="abstract video hidden">Video panel</div>
      <button id="authors" type="button" class="more-authors" data-more-authors-toggle
              aria-expanded="false" aria-label="Show 2 more authors">
        <span data-more-authors-collapsed>2 more authors</span>
        <span data-more-authors-expanded hidden>Safe Author, Another Author</span>
      </button>
      <details id="annotation"><summary>Publication note</summary><span role="note">Note</span></details>
    </main>
    <button id="back-to-top" type="button" aria-label="Back to top" hidden>Top</button>
    <script>window.matchMedia = () => ({{ matches: true }});</script>
    <script src="{bibliography_js}"></script>
    <script src="{back_to_top_js}"></script>
    <script>
      window.addEventListener("error", (event) => {{
        document.body.dataset.result = JSON.stringify({{ error: event.message }});
      }}, {{ once: true }});

      document.addEventListener("DOMContentLoaded", async () => {{
        const wait = (milliseconds = 25) => new Promise((resolve) => setTimeout(resolve, milliseconds));
        const waitUntil = async (predicate) => {{
          for (let attempt = 0; attempt < 20; attempt += 1) {{
            if (predicate()) return true;
            await wait();
          }}
          return predicate();
        }};
        const abstract = document.getElementById("abstract-trigger");
        const video = document.getElementById("video-trigger");
        const authors = document.getElementById("authors");
        const backToTop = document.getElementById("back-to-top");

        abstract.focus();
        abstract.dispatchEvent(new KeyboardEvent("keydown", {{
          key: " ", bubbles: true, cancelable: true
        }}));
        const abstractAfterSpace = {{
          expanded: abstract.getAttribute("aria-expanded"),
          panelOpen: document.getElementById("fixture-abstract").classList.contains("open")
        }};

        video.click();
        const afterVideo = {{
          abstractExpanded: abstract.getAttribute("aria-expanded"),
          abstractOpen: document.getElementById("fixture-abstract").classList.contains("open"),
          videoExpanded: video.getAttribute("aria-expanded"),
          videoOpen: document.getElementById("fixture-video").classList.contains("open")
        }};

        authors.click();
        const authorExpansion = {{
          expanded: authors.getAttribute("aria-expanded"),
          collapsedHidden: authors.querySelector("[data-more-authors-collapsed]").hidden,
          expandedHidden: authors.querySelector("[data-more-authors-expanded]").hidden
        }};

        document.querySelector("#annotation summary").click();
        window.scrollTo(0, 800);
        await waitUntil(() => window.scrollY > 0 && !backToTop.hidden);
        const backToTopVisibleAfterScroll = !backToTop.hidden;
        backToTop.click();
        await waitUntil(() => window.scrollY === 0 && backToTop.hidden);

        document.body.dataset.result = JSON.stringify({{
          abstractAfterSpace,
          afterVideo,
          authorExpansion,
          annotationOpen: document.getElementById("annotation").open,
          backToTopVisibleAfterScroll,
          scrollYAfterBackToTop: window.scrollY,
          backToTopHiddenAtTop: backToTop.hidden
        }});
      }});
    </script>
  </body>
</html>
"""

        with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8") as page:
            page.write(fixture)
            page.flush()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    executable_path=str(CHROME),
                    args=["--allow-file-access-from-files"],
                )
                browser_page = browser.new_page(viewport={"width": 1280, "height": 720})
                browser_page.goto(Path(page.name).as_uri(), wait_until="load")
                browser_page.wait_for_function(
                    "document.body.dataset.result !== 'pending'",
                    timeout=5_000,
                )
                result_text = browser_page.locator("body").get_attribute("data-result")
                browser.close()

        self.assertIsNotNone(result_text)
        result = json.loads(result_text)
        self.assertNotIn("error", result)
        self.assertEqual(result["abstractAfterSpace"], {"expanded": "true", "panelOpen": True})
        self.assertEqual(
            result["afterVideo"],
            {
                "abstractExpanded": "false",
                "abstractOpen": False,
                "videoExpanded": "true",
                "videoOpen": True,
            },
        )
        self.assertEqual(
            result["authorExpansion"],
            {"expanded": "true", "collapsedHidden": True, "expandedHidden": False},
        )
        self.assertTrue(result["annotationOpen"])
        self.assertTrue(result["backToTopVisibleAfterScroll"], result)
        self.assertEqual(result["scrollYAfterBackToTop"], 0)
        self.assertTrue(result["backToTopHiddenAtTop"])


if __name__ == "__main__":
    unittest.main()
