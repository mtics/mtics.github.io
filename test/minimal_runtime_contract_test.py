#!/usr/bin/env python3
"""Assert that generated pages do not reference disabled global runtimes.

Theme/plugin packages may still publish inert helper files; this contract is
about the browser's executable dependency graph, not static-file inventory.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"


class MinimalRuntimeContractTest(unittest.TestCase):
    def test_unused_math_and_masonry_runtimes_are_not_referenced(self) -> None:
        pages = sorted(SITE.rglob("*.html"))
        self.assertTrue(pages, "build _site before running the minimal-runtime contract")
        combined = "\n".join(path.read_text(encoding="utf-8") for path in pages).lower()

        for forbidden in (
            "polyfill.min.js?features=es6",
            "mathjax-script",
            "masonry-layout",
            "imagesloaded",
            "/assets/js/masonry.js",
        ):
            self.assertNotIn(forbidden, combined)

        publications = (SITE / "publications" / "index.html").read_text(encoding="utf-8").lower()
        self.assertIn("medium-zoom", publications)


if __name__ == "__main__":
    unittest.main()
