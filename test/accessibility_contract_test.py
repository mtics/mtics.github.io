#!/usr/bin/env python3
"""Accessibility contracts for the rendered public site.

Run after a Jekyll build. Set SITE_DIR when the destination is not ``_site``.
The parser intentionally uses only the Python standard library so the contract
can run in the same minimal environment as the static-site build.
"""

from __future__ import annotations

from collections import Counter
import os
import posixpath
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote, urljoin, urlsplit


SITE_DIR = Path(os.environ.get("SITE_DIR", "_site"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    parent: "Element | None" = None
    children: list["Element"] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text_content(self) -> str:
        parts = list(self.text)
        for child in self.children:
            parts.append(child.text_content())
        return " ".join(" ".join(parts).split())

    def descendants(self) -> list["Element"]:
        result: list[Element] = []
        for child in self.children:
            result.append(child)
            result.extend(child.descendants())
        return result


class DocumentParser(HTMLParser):
    VOID_ELEMENTS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {key: value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(element)
        if tag not in self.VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].text.append(data)


def load_document(relative_path: str) -> Element:
    page = SITE_DIR / relative_path
    if not page.exists():
        raise AssertionError(f"Expected rendered page at {page}; run the Jekyll build first")
    parser = DocumentParser()
    parser.feed(page.read_text(encoding="utf-8"))
    return parser.root


def _ancestors(element: Element) -> list[Element]:
    result: list[Element] = []
    parent = element.parent
    while parent is not None:
        result.append(parent)
        parent = parent.parent
    return result


def _accessible_name(element: Element) -> str:
    return " ".join(
        (
            element.attrs.get("aria-label")
            or element.attrs.get("title")
            or element.text_content()
            or ""
        ).split()
    )


def _css_variables(css: str, selector: str) -> dict[str, str]:
    match = re.search(re.escape(selector) + r"\{([^}]+)\}", css)
    if not match:
        raise AssertionError(f"Missing compiled CSS selector: {selector}")
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+)", match.group(1)))


def _relative_luminance(color: str) -> float:
    value = color.strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise AssertionError(f"Expected a six-digit hex color, got {color!r}")
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter, darker = sorted((first_luminance, second_luminance), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _srcset_urls(value: str) -> list[str]:
    """Extract URL tokens using the delimiter rules from the HTML srcset parser."""

    urls: list[str] = []
    position = 0
    while position < len(value):
        while position < len(value) and (value[position].isspace() or value[position] == ","):
            position += 1
        if position >= len(value):
            break

        url_start = position
        while position < len(value) and not value[position].isspace():
            position += 1
        url = value[url_start:position]
        trailing_commas = len(url) - len(url.rstrip(","))
        url = url.rstrip(",")
        if url:
            urls.append(url)
        if trailing_commas:
            continue

        parentheses = 0
        while position < len(value):
            character = value[position]
            if character == "(":
                parentheses += 1
            elif character == ")" and parentheses:
                parentheses -= 1
            elif character == "," and not parentheses:
                position += 1
                break
            position += 1
    return urls


def audit_site_navigation(site_dir: Path) -> list[str]:
    """Return deterministic navigation-integrity errors for a rendered site."""

    site_root = site_dir.resolve()
    html_files = sorted(path for path in site_root.rglob("*.html") if path.is_file())
    if not html_files:
        return [f"no rendered HTML files found below {site_root}"]

    documents: dict[Path, Element] = {}
    id_counts: dict[Path, Counter[str]] = {}
    fragment_targets: dict[Path, set[str]] = {}
    for html_file in html_files:
        parser = DocumentParser()
        parser.feed(html_file.read_text(encoding="utf-8"))
        documents[html_file] = parser.root

        elements = parser.root.descendants()
        identifiers = [element.attrs["id"] for element in elements if element.attrs.get("id")]
        id_counts[html_file] = Counter(identifiers)
        fragment_targets[html_file] = set(identifiers) | {
            element.attrs["name"]
            for element in elements
            if element.tag == "a" and element.attrs.get("name")
        }

    root_document = documents.get(site_root / "index.html")
    canonical_url = None
    if root_document is not None:
        canonical_url = next(
            (
                element.attrs.get("href")
                for element in root_document.descendants()
                if element.tag == "link"
                and "canonical" in element.attrs.get("rel", "").lower().split()
                and element.attrs.get("href")
            ),
            None,
        )

    canonical = urlsplit(canonical_url) if canonical_url else None
    internal_hostname = canonical.hostname.lower() if canonical and canonical.hostname else None
    configured_baseurl = os.environ.get("SITE_BASEURL")
    if configured_baseurl is None:
        configured_baseurl = unquote(canonical.path).rstrip("/") if canonical else ""
    baseurl = "/" + configured_baseurl.strip("/") if configured_baseurl.strip("/") else ""

    errors: list[str] = []
    if re.search(r"(?:^|/)\.\.(?:/|$)", baseurl) or "\\" in baseurl:
        errors.append(f"invalid SITE_BASEURL/canonical base path: {baseurl!r}")
        return errors

    def display_path(path: Path) -> str:
        return path.relative_to(site_root).as_posix()

    def public_path(path: Path) -> str:
        relative = path.relative_to(site_root).as_posix()
        if relative == "index.html":
            route = "/"
        elif relative.endswith("/index.html"):
            route = f"/{relative.removesuffix('index.html')}"
        else:
            route = f"/{relative}"
        return f"{baseurl}{route}" if baseurl else route

    def target_file(url_path: str, *, allow_html_routes: bool = True) -> tuple[Path | None, str | None]:
        if baseurl:
            if url_path == baseurl:
                relative_url_path = ""
            elif url_path.startswith(f"{baseurl}/"):
                relative_url_path = url_path[len(baseurl) :].lstrip("/")
            else:
                return None, f"escapes baseurl {baseurl!r}"
        else:
            relative_url_path = url_path.lstrip("/")

        candidate_paths: list[Path]
        raw_candidate = site_root / relative_url_path
        if not allow_html_routes:
            candidate_paths = [raw_candidate]
        elif not relative_url_path or url_path.endswith("/"):
            candidate_paths = [raw_candidate / "index.html"]
        else:
            candidate_paths = [raw_candidate]
            if not raw_candidate.suffix:
                candidate_paths.extend((raw_candidate / "index.html", raw_candidate.with_suffix(".html")))

        for candidate in candidate_paths:
            resolved_candidate = candidate.resolve(strict=False)
            if not resolved_candidate.is_relative_to(site_root):
                return None, "resolves outside the rendered site directory"
            if candidate.is_file():
                return resolved_candidate, None
        return None, "missing internal target"

    for html_file in html_files:
        relative_source = display_path(html_file)
        for identifier, count in sorted(id_counts[html_file].items()):
            if count > 1:
                errors.append(f"{relative_source}: duplicate id {identifier!r} ({count} occurrences)")

        source_url_path = public_path(html_file)
        for element in documents[html_file].descendants():
            if element.tag not in {"a", "area"} or "href" not in element.attrs:
                continue
            href = element.attrs["href"].strip()
            if not href:
                continue

            try:
                parsed_href = urlsplit(href)
            except ValueError as error:
                errors.append(f"{relative_source}: malformed href {href!r}: {error}")
                continue

            if parsed_href.scheme and parsed_href.scheme.lower() not in {"http", "https"}:
                # mailto:, tel:, sms:, and other non-HTTP links do not target this static site.
                continue
            if parsed_href.hostname:
                if internal_hostname is None or parsed_href.hostname.lower() != internal_hostname:
                    continue
            if re.search(r"%(?![0-9A-Fa-f]{2})", href):
                errors.append(f"{relative_source}: malformed percent escape in href {href!r}")
                continue

            decoded_href_path = unquote(parsed_href.path)
            if "\x00" in decoded_href_path or "\\" in decoded_href_path:
                errors.append(f"{relative_source}: unsafe path syntax in href {href!r}")
                continue

            resolved = urlsplit(urljoin(f"https://local.invalid{source_url_path}", href))
            resolved_path = unquote(resolved.path)
            had_trailing_slash = resolved_path.endswith("/")
            resolved_path = "/" + posixpath.normpath(resolved_path).lstrip("/")
            if had_trailing_slash and resolved_path != "/":
                resolved_path += "/"

            target, target_error = target_file(resolved_path)
            if target_error:
                errors.append(f"{relative_source}: {target_error}: href {href!r}")
                continue
            if target is None:
                continue

            fragment = unquote(parsed_href.fragment)
            if fragment:
                if target.suffix.lower() != ".html":
                    errors.append(
                        f"{relative_source}: fragment {fragment!r} targets non-HTML file: href {href!r}"
                    )
                elif fragment not in fragment_targets.get(target, set()):
                    errors.append(f"{relative_source}: missing fragment {fragment!r}: href {href!r}")

        resource_attributes = {
            "script": ("src",),
            "img": ("src", "srcset"),
            "source": ("src", "srcset"),
            "iframe": ("src",),
            "video": ("src", "poster"),
            "audio": ("src",),
            "track": ("src",),
            "embed": ("src",),
            "object": ("data",),
            "input": ("src",),
        }
        resource_link_relations = {
            "stylesheet",
            "icon",
            "apple-touch-icon",
            "mask-icon",
            "manifest",
            "preload",
            "modulepreload",
            "prefetch",
        }
        for element in documents[html_file].descendants():
            attributes: tuple[str, ...]
            if element.tag == "link":
                relations = set(element.attrs.get("rel", "").lower().split())
                attributes = ("href",) if relations & resource_link_relations else ()
            else:
                attributes = resource_attributes.get(element.tag, ())

            for attribute in attributes:
                if attribute not in element.attrs:
                    continue
                raw_value = element.attrs[attribute].strip()
                references = _srcset_urls(raw_value) if attribute == "srcset" else [raw_value]
                if not references:
                    errors.append(f"{relative_source}: empty local resource: {element.tag}[{attribute}]")
                    continue

                for reference in references:
                    if not reference:
                        errors.append(f"{relative_source}: empty local resource: {element.tag}[{attribute}]")
                        continue
                    try:
                        parsed_reference = urlsplit(reference)
                    except ValueError as error:
                        errors.append(
                            f"{relative_source}: malformed resource URL {reference!r}: "
                            f"{element.tag}[{attribute}]: {error}"
                        )
                        continue

                    if parsed_reference.scheme and parsed_reference.scheme.lower() not in {"http", "https"}:
                        # data:, blob:, about:, and other runtime/non-HTTP resources are not files in this site.
                        continue
                    if parsed_reference.hostname:
                        if internal_hostname is None or parsed_reference.hostname.lower() != internal_hostname:
                            continue
                    if re.search(r"%(?![0-9A-Fa-f]{2})", reference):
                        errors.append(
                            f"{relative_source}: malformed percent escape in resource {reference!r}: "
                            f"{element.tag}[{attribute}]"
                        )
                        continue

                    decoded_path = unquote(parsed_reference.path)
                    if "\x00" in decoded_path or "\\" in decoded_path:
                        errors.append(
                            f"{relative_source}: unsafe path syntax in resource {reference!r}: "
                            f"{element.tag}[{attribute}]"
                        )
                        continue

                    resolved = urlsplit(urljoin(f"https://local.invalid{source_url_path}", reference))
                    resolved_path = unquote(resolved.path)
                    had_trailing_slash = resolved_path.endswith("/")
                    resolved_path = "/" + posixpath.normpath(resolved_path).lstrip("/")
                    if had_trailing_slash and resolved_path != "/":
                        resolved_path += "/"

                    _, target_error = target_file(
                        resolved_path,
                        allow_html_routes=element.tag == "iframe",
                    )
                    if target_error:
                        errors.append(
                            f"{relative_source}: {target_error}: "
                            f"{element.tag}[{attribute}] {reference!r}"
                        )

    return sorted(errors)


class AccessibilityContractTest(unittest.TestCase):
    def test_all_rendered_html_has_unique_ids_and_valid_internal_navigation(self) -> None:
        self.assertEqual(audit_site_navigation(SITE_DIR), [])

    def test_navigation_audit_handles_baseurls_fragments_and_external_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            site_dir = Path(temporary_directory)
            (site_dir / "guide").mkdir()
            (site_dir / "index.html").write_text(
                """<!doctype html>
<html><head><link rel="canonical" href="https://example.test/project/"></head>
<body id="home"><a href="/project/guide/#topic">Guide</a>
<a href="https://outside.example/%not-a-local-path#fragment">External</a>
<a href="mailto:person@example.test">Mail</a></body></html>""",
                encoding="utf-8",
            )
            (site_dir / "guide" / "index.html").write_text(
                """<!doctype html><html><body id="topic">
<a href="../#home">Home</a><a href="#topic">Topic</a></body></html>""",
                encoding="utf-8",
            )

            self.assertEqual(audit_site_navigation(site_dir), [])

            (site_dir / "guide" / "index.html").write_text(
                """<!doctype html><html><body id="duplicate"><div id="duplicate"></div>
<a href="/outside-baseurl/">Escape</a><a href="#missing">Missing fragment</a>
<a href="%2e%2e/%2e%2e/secret/">Traversal</a>
<a href="../missing/">Missing page</a></body></html>""",
                encoding="utf-8",
            )
            errors = audit_site_navigation(site_dir)
            self.assertTrue(any("duplicate id 'duplicate'" in error for error in errors), errors)
            self.assertTrue(any("escapes baseurl '/project'" in error for error in errors), errors)
            self.assertTrue(
                any("%2e%2e/%2e%2e/secret/" in error and "escapes baseurl" in error for error in errors),
                errors,
            )
            self.assertTrue(any("missing fragment 'missing'" in error for error in errors), errors)
            self.assertTrue(any("missing internal target" in error for error in errors), errors)

    def test_navigation_audit_detects_missing_local_resource_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            site_dir = Path(temporary_directory)
            assets_dir = site_dir / "assets"
            frame_dir = site_dir / "frame"
            assets_dir.mkdir()
            frame_dir.mkdir()

            resource_names = (
                "site.css",
                "app.js",
                "portrait.png",
                "portrait@2x.png",
                "wide.webp",
                "wide@2x.webp",
                "clip.mp4",
                "poster.jpg",
                "sound.mp3",
                "captions.vtt",
                "widget.svg",
                "document.pdf",
                "button.png",
                "encoded asset.svg",
            )
            for resource_name in resource_names:
                (assets_dir / resource_name).write_bytes(b"fixture")
            (frame_dir / "index.html").write_text("<!doctype html><title>Frame</title>", encoding="utf-8")

            (site_dir / "index.html").write_text(
                """<!doctype html>
<html><head>
<link rel="canonical" href="https://example.test/project/">
<link rel="alternate" href="/project/intentionally-absent-feed.xml">
<link rel="stylesheet" href="/project/assets/site.css?v=1#theme">
<script src="assets/app.js?cache=1"></script>
</head><body>
<img src="/project/assets/portrait.png" srcset="/project/assets/portrait.png 1x, /project/assets/portrait@2x.png 2x" alt="Portrait">
<picture><source src="/project/assets/wide.webp" srcset="/project/assets/wide.webp 1x, /project/assets/wide@2x.webp 2x"></picture>
<iframe src="/project/frame/?embedded=1#content" title="Fixture"></iframe>
<video src="/project/assets/clip.mp4" poster="/project/assets/poster.jpg"><track src="/project/assets/captions.vtt"></video>
<audio src="/project/assets/sound.mp3"></audio>
<embed src="/project/assets/widget.svg"><object data="/project/assets/document.pdf"></object>
<input type="image" src="/project/assets/button.png" alt="Submit">
<img src="/project/assets/encoded%20asset.svg" alt="Encoded path">
<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" alt="Inline">
<script src="https://cdn.example.test/external.js"></script>
<img src="//cdn.example.test/external.png" alt="External">
<iframe src="about:blank" title="Blank"></iframe>
<video poster="blob:https://example.test/runtime-object"></video>
</body></html>""",
                encoding="utf-8",
            )

            self.assertEqual(audit_site_navigation(site_dir), [])

            for resource_name in resource_names:
                (assets_dir / resource_name).unlink()
            (frame_dir / "index.html").unlink()

            errors = audit_site_navigation(site_dir)
            for expected_reference in (
                "link[href]",
                "script[src]",
                "img[src]",
                "img[srcset]",
                "source[src]",
                "source[srcset]",
                "iframe[src]",
                "video[src]",
                "video[poster]",
                "track[src]",
                "audio[src]",
                "embed[src]",
                "object[data]",
                "input[src]",
            ):
                self.assertTrue(
                    any(expected_reference in error for error in errors),
                    f"missing mutation failure for {expected_reference}: {errors}",
                )
            self.assertFalse(
                any("intentionally-absent-feed.xml" in error for error in errors),
                f"alternate document discovery links are not fetchable page resources: {errors}",
            )

    def test_embedded_video_disclosure_targets_its_own_panel(self) -> None:
        source = (PROJECT_ROOT / "_layouts/bib.liquid").read_text(encoding="utf-8")
        scripts = (PROJECT_ROOT / "_includes/scripts.liquid").read_text(encoding="utf-8")
        interactions = (PROJECT_ROOT / "assets/js/bibliography.js").read_text(encoding="utf-8")
        trigger_match = re.search(
            r"\{% if video_url and video_url != empty and site\.enable_video_embedding %\}(.*?)\{% elsif video_url and video_url != empty %\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(trigger_match)
        trigger = trigger_match.group(1)
        self.assertIn('<button', trigger)
        self.assertIn('class="video-toggle btn btn-sm z-depth-0"', trigger)
        self.assertIn("data-bib-disclosure", trigger)
        self.assertIn('aria-controls="{{ entry_dom_id | escape }}-video"', trigger)
        self.assertIn('aria-expanded="false"', trigger)
        self.assertNotIn("onclick=", trigger)
        self.assertNotIn("onkeydown=", trigger)
        self.assertNotIn('class="abstract btn', trigger)

        self.assertIn("'/assets/js/bibliography.js'", scripts)
        self.assertIn('document.querySelectorAll("[data-bib-disclosure]")', interactions)
        self.assertIn("document.getElementById(panelId)", interactions)
        self.assertIn('panel.classList.toggle("open", shouldOpen)', interactions)

        self.assertRegex(
            source,
            r'id="\{\{ entry_dom_id \| escape \}\}-video" class="abstract video hidden"',
        )

    def test_publication_disclosures_are_keyboard_operable(self) -> None:
        document = load_document("publications/index.html")
        elements = document.descendants()
        ids = {element.attrs["id"] for element in elements if element.attrs.get("id")}

        disclosures = [
            element
            for element in elements
            if element.tag in {"a", "button"}
            and "btn" in element.classes
            and ({"abstract", "bibtex"} & element.classes)
        ]
        self.assertTrue(disclosures, "expected rendered abstract/BibTeX controls")
        for disclosure in disclosures:
            self.assertEqual(disclosure.attrs.get("role"), "button")
            self.assertIn("data-bib-disclosure", disclosure.attrs)
            self.assertEqual(disclosure.attrs.get("aria-expanded"), "false")
            self.assertIn(disclosure.attrs.get("aria-controls"), ids)
            self.assertNotIn("onclick", disclosure.attrs)
            self.assertNotIn("onkeydown", disclosure.attrs)
            if disclosure.tag == "button":
                self.assertEqual(disclosure.attrs.get("type"), "button")
            else:
                self.assertTrue(disclosure.attrs.get("href", "").startswith("#"))

        more_authors = [element for element in elements if "more-authors" in element.classes]
        self.assertTrue(more_authors, "expected a rendered more-authors disclosure")
        for disclosure in more_authors:
            self.assertEqual(disclosure.tag, "button")
            self.assertEqual(disclosure.attrs.get("type"), "button")
            self.assertEqual(disclosure.attrs.get("aria-expanded"), "false")
            self.assertIn("data-more-authors-toggle", disclosure.attrs)
            self.assertNotIn("onclick", disclosure.attrs)
            self.assertNotIn("onkeydown", disclosure.attrs)
            child_attributes = [child.attrs for child in disclosure.descendants()]
            self.assertTrue(any("data-more-authors-collapsed" in attrs for attrs in child_attributes))
            self.assertTrue(any("data-more-authors-expanded" in attrs for attrs in child_attributes))

        source = (PROJECT_ROOT / "_layouts/bib.liquid").read_text(encoding="utf-8")
        interactions = (PROJECT_ROOT / "assets/js/bibliography.js").read_text(encoding="utf-8")
        self.assertNotIn("more_authors_show", source)
        self.assertNotIn("onclick=", source)
        self.assertNotIn("onkeydown=", source)
        self.assertIn('document.querySelectorAll("[data-bib-disclosure]")', interactions)
        self.assertIn('document.querySelectorAll("[data-more-authors-toggle]")', interactions)
        self.assertIn('event.key === " "', interactions)

        bibtex_code = [
            element
            for element in elements
            if element.tag == "pre"
            and any({"bibtex", "hidden"}.issubset(ancestor.classes) for ancestor in _ancestors(element))
        ]
        self.assertTrue(bibtex_code, "expected rendered BibTeX code blocks")
        for code_block in bibtex_code:
            self.assertEqual(code_block.attrs.get("tabindex"), "0")

    def test_content_images_have_meaningful_alternative_text(self) -> None:
        publications = load_document("publications/index.html")
        previews = [
            element
            for element in publications.descendants()
            if element.tag == "img" and "preview" in element.classes
        ]
        self.assertTrue(previews, "expected rendered publication preview images")
        for preview in previews:
            alt = preview.attrs.get("alt", "").strip()
            self.assertGreater(len(alt), 10, f"preview alt should describe the paper: {preview.attrs.get('src')}")
            self.assertNotRegex(alt.lower(), r"\.(png|jpe?g|gif|webp|svg)$")

        about = load_document("index.html")
        profile_images = [
            element
            for element in about.descendants()
            if element.tag == "img"
            and any("profile" in ancestor.classes for ancestor in _ancestors(element))
        ]
        self.assertEqual(len(profile_images), 1, "expected one profile portrait")
        profile_alt = profile_images[0].attrs.get("alt", "")
        self.assertIn("Zhiwei", profile_alt)
        self.assertIn("Li", profile_alt)
        self.assertNotRegex(profile_alt.lower(), r"\.(png|jpe?g|gif|webp|svg)$")

    def test_cv_preserves_data_order_and_a_semantic_heading_outline(self) -> None:
        document = load_document("cv/index.html")
        elements = document.descendants()

        pdf_icons = [element for element in elements if "fa-file-pdf" in element.classes]
        self.assertEqual(len(pdf_icons), 1, "expected one CV PDF icon")
        pdf_link = next((ancestor for ancestor in _ancestors(pdf_icons[0]) if ancestor.tag == "a"), None)
        self.assertIsNotNone(pdf_link)
        self.assertTrue(_accessible_name(pdf_link), "CV PDF link needs an accessible name")
        self.assertEqual(pdf_icons[0].attrs.get("aria-hidden"), "true")

        id_owners: dict[str, list[Element]] = {}
        for element in elements:
            if element.attrs.get("id"):
                id_owners.setdefault(element.attrs["id"], []).append(element)
        duplicate_ids = sorted(identifier for identifier, owners in id_owners.items() if len(owners) > 1)
        self.assertEqual(duplicate_ids, [], f"duplicate IDs: {duplicate_ids}")

        for section_id in ("education", "experience", "awards", "service", "languages"):
            owners = id_owners.get(section_id, [])
            self.assertEqual(len(owners), 1, f"#{section_id} should remain a unique deep link")
            self.assertIn(owners[0].tag, {"h2", "h3"}, f"#{section_id} should identify its heading")

        headings = [element for element in elements if element.tag in {f"h{level}" for level in range(1, 7)}]
        levels = [int(heading.tag[1]) for heading in headings]
        for previous, current in zip(levels, levels[1:]):
            self.assertLessEqual(current, previous + 1, f"heading level jumps from h{previous} to h{current}")

        heading_text = [heading.text_content() for heading in headings]
        self.assertLess(heading_text.index("Education"), heading_text.index("Experience"))

        cv_text = document.text_content()
        self.assertIn("GitHub", cv_text)
        self.assertIn("LinkedIn", cv_text)

    def test_cv_entries_use_one_heading_for_their_primary_title(self) -> None:
        document = load_document("cv/index.html")
        elements = document.descendants()
        id_owners = {element.attrs.get("id"): element for element in elements if element.attrs.get("id")}

        for section_id in ("education", "experience", "awards"):
            section_heading = id_owners[section_id]
            section_card = section_heading.parent
            entries = [
                element
                for element in section_card.descendants()
                if element.tag == "li" and "list-group-item" in element.classes
            ]
            self.assertTrue(entries, f"expected {section_id} entries")
            for entry in entries:
                headings = [element.text_content() for element in entry.descendants() if element.tag == "h3"]
                self.assertEqual(
                    len(headings),
                    1,
                    f"{section_id} entry should expose only its primary title as h3; got {headings}",
                )

        languages_card = id_owners["languages"].parent
        language_headings = [element.text_content() for element in languages_card.descendants() if element.tag == "h3"]
        self.assertEqual(language_headings, [])

        render_source = (PROJECT_ROOT / "_includes/cv/render.liquid").read_text(encoding="utf-8")
        self.assertNotIn("replace: '<h6'", render_source)

    def test_cv_date_tables_do_not_draw_horizontal_cell_borders(self) -> None:
        css = (SITE_DIR / "assets/css/main.css").read_text(encoding="utf-8-sig")
        date_table_rule = re.search(r"\.cv \.list-group-item \.table-cv\{([^}]+)\}", css)
        date_cell_rule = re.search(r"\.cv \.list-group-item \.table-cv td\{([^}]+)\}", css)

        self.assertIsNotNone(date_table_rule, "CV date tables need the pre-migration collapsed layout")
        self.assertIn("border-collapse:collapse", date_table_rule.group(1))
        self.assertIn("border-spacing:0", date_table_rule.group(1))
        self.assertIsNotNone(date_cell_rule, "CV date cells need a scoped border override")
        self.assertIn("border-top:0", date_cell_rule.group(1))

    def test_cv_entries_match_the_reference_type_and_spacing_scale(self) -> None:
        for partial_name in ("education.liquid", "experience.liquid", "awards.liquid"):
            partial = (PROJECT_ROOT / "_includes/cv" / partial_name).read_text(encoding="utf-8")
            self.assertIn(
                "cv-entry-content",
                partial,
                f"{partial_name} should identify the right-hand content column",
            )
            self.assertNotIn("mt-2", partial, f"{partial_name} should not depend on Bootstrap margin ordering")
            self.assertNotIn("mt-md-0", partial, f"{partial_name} should own its responsive margin")

        css = (SITE_DIR / "assets/css/main.css").read_text(encoding="utf-8-sig")
        entry_rules = re.findall(r"\.cv \.list-group-item\{([^}]+)\}", css)
        entry_declarations = ";".join(entry_rules)
        content_rule = re.search(r"\.cv \.cv-entry-content\{([^}]+)\}", css)
        title_rule = re.search(r"\.cv \.cv-entry-content \.title\{([^}]+)\}", css)
        compact_css = css.replace(" ", "")
        desktop_rule = re.search(r"@media\(min-width:768px\)\{\.cv\.cv-entry-content\{([^}]+)\}\}", compact_css)

        self.assertTrue(entry_rules, "CV entries need reference-compatible spacing")
        self.assertIn("padding:.75rem 1.25rem", entry_declarations)
        self.assertIn("font-size:1rem", entry_declarations)
        self.assertIn("line-height:1.5", entry_declarations)
        self.assertIn("font-weight:300", entry_declarations)

        self.assertIsNotNone(content_rule, "CV content needs its own scoped type scale")
        self.assertIn("font-size:1rem", content_rule.group(1))
        self.assertIn("margin-top:.5rem", content_rule.group(1))
        self.assertIsNotNone(title_rule, "CV entry titles need the compact reference scale")
        self.assertIn("font-size:1rem", title_rule.group(1))
        self.assertIn("line-height:1.2", title_rule.group(1))
        self.assertIn("margin-bottom:.5rem", title_rule.group(1))

        self.assertIn("font-size:.95rem!important", compact_css)
        self.assertIn("line-height:1.2", compact_css)
        self.assertIn("margin-bottom:.5rem!important", compact_css)
        self.assertIsNotNone(desktop_rule, "desktop CV columns should cancel the mobile top margin")
        self.assertIn("margin-top:0", desktop_rule.group(1))

    def test_cv_entries_stack_before_the_narrow_two_column_layout_overlaps(self) -> None:
        for partial_name in ("education.liquid", "experience.liquid", "awards.liquid"):
            partial = (PROJECT_ROOT / "_includes/cv" / partial_name).read_text(encoding="utf-8")
            self.assertIn(
                "cv-entry-meta",
                partial,
                f"{partial_name} should identify the date/location column",
            )

        css = (SITE_DIR / "assets/css/main.css").read_text(encoding="utf-8-sig").replace(" ", "")
        mobile_rule = re.search(
            r"@media\(max-width:767\.98px\)\{\.cv\.cv-entry-meta,\.cv\.cv-entry-content\{([^}]+)\}\}",
            css,
        )

        self.assertIsNotNone(mobile_rule, "narrow CV entries should stack before their columns overlap")
        self.assertIn("flex:00100%", mobile_rule.group(1))
        self.assertIn("max-width:100%", mobile_rule.group(1))

    def test_404_is_a_stable_error_page_with_an_explicit_exit(self) -> None:
        document = load_document("404.html")
        refreshes = [
            element
            for element in document.descendants()
            if element.tag == "meta" and element.attrs.get("http-equiv", "").lower() == "refresh"
        ]
        self.assertEqual(refreshes, [], "404 must not force a timed navigation")
        self.assertNotIn("redirected", document.text_content().lower())

        home_links = [
            element
            for element in document.descendants()
            if element.tag == "a" and "home" in element.text_content().lower()
        ]
        self.assertTrue(home_links, "404 should offer an explicit home link")
        self.assertTrue(home_links[0].attrs.get("href"), "home link needs a destination")

    def test_theme_tokens_keep_text_links_and_badges_at_aa_contrast(self) -> None:
        css = (SITE_DIR / "assets/css/main.css").read_text(encoding="utf-8-sig")
        themes = {
            "light": _css_variables(css, ":root"),
            "dark": _css_variables(css, "html[data-theme=dark]"),
        }
        for name, tokens in themes.items():
            backgrounds = (tokens["--global-bg-color"], tokens["--global-card-bg-color"])
            for background in backgrounds:
                for token in ("--global-text-color", "--global-text-color-light", "--global-theme-color"):
                    ratio = _contrast_ratio(tokens[token], background)
                    self.assertGreaterEqual(ratio, 4.5, f"{name} {token} contrast is {ratio:.2f}:1")

            hover_ratio = _contrast_ratio(tokens["--global-hover-text-color"], tokens["--global-theme-color"])
            self.assertGreaterEqual(hover_ratio, 4.5, f"{name} hover contrast is {hover_ratio:.2f}:1")

        year_rule = re.search(r"\.publications h2\.bibliography\{([^}]+)\}", css)
        self.assertIsNotNone(year_rule)
        self.assertIn("color:var(--global-text-color-light)", year_rule.group(1))


if __name__ == "__main__":
    unittest.main()
