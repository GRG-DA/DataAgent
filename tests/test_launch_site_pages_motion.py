"""Per-page motion coverage for the three showcase pages of the marketing site.

The marketing site (``website/``) is a static, no-build-step site.  Its motion
engine (``website/js/fx-core.js`` + ``website/css/premium.css``) owns every
animation, and the pages opt in through a small vocabulary of public class
names — ``.animate-on-scroll`` and ``.animate-delay-N`` among them.

This file guards the *page-level* motion of the three showcase pages
(``index.html``, ``features.html``, ``comparison.html``) against the four
failure modes that are specific to them:

1. **A page that loads no page stylesheet.** ``website/css/pages-motion.css``
   carries the per-page effects and the re-binding of the legacy reveal
   vocabulary; a page that does not link it silently loses them.
2. **A stylesheet that animates a layout property.** ``top`` / ``height`` /
   ``width`` / ``margin`` / ``padding`` force a layout pass on every frame,
   which is exactly what makes a marketing page stutter and shift.
3. **A section that arrives unanimated.** When only some sections reveal, the
   page reads as broken: one band animates in, the next one is simply *there*.
   Every ``<section>`` on the three pages must contain at least one reveal
   element, and every page must carry a floor of them.
4. **Motion that is no longer an enhancement.** Every rule the new stylesheet
   adds needs a reduced-motion counterpart: the ``prefers-reduced-motion``
   media query for the OS setting and the ``html.fx-reduced`` class for the
   ``?fx=off`` query flag (which the media query cannot see).

Plus the invariants that must survive any motion work: no third-party origin
on the three pages, an identical navigation, one ``<h1>`` per page and a
non-empty ``<title>``.

Run with::

    python -m pytest tests/test_launch_site_pages_motion.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

if not (ROOT / "website").is_dir():
    pytest.skip(
        "website/ (the marketing site) ships separately and is not part of "
        "this repository - see ADR-023",
        allow_module_level=True,
    )

#: The site under test.  Overridable so the same guards can be pointed at a
#: pristine copy of the site (used to prove the guards actually fail).
SITE = Path(os.environ.get("DATAAGENT_SITE_ROOT", ROOT / "website"))

PAGES = ("index.html", "features.html", "comparison.html")

MOTION_STYLESHEET = "css/pages-motion.css"
MOTION_CSS = SITE / "css" / "pages-motion.css"

#: Floor of ``animate-on-scroll`` elements per page.  These are the counts the
#: three pages carried before the per-page motion work (11 / 30 / 9); the
#: home page has since grown (the hero pills, the rail header, the privacy and
#: CTA headings) and the other two hold their floor.
MIN_REVEALS = {
    "index.html": 14,
    "features.html": 30,
    "comparison.html": 9,
}

#: The class that marks an element as *revealed* in the markup.  fx-core.js
#: adds ``.fx-reveal`` at runtime and removes it with ``.is-in``; the markup
#: class is what the pages declare.
REVEAL_CLASS = "animate-on-scroll"

#: Classes fx-core.js reveals on its own (see ``initReveals`` in
#: website/js/fx-core.js).  They count as reveal coverage even without the
#: markup class.
ENGINE_TARGET_CLASSES = frozenset(
    {
        "card",
        "stat-panel",
        "plan-card",
        "blog-card",
        "req-card",
        "rail-card",
        "highlight-box",
        "faq-item",
        "contact-link-card",
        "arch-card",
        "learn-panel",
        "download-card",
        "timeline-item",
        "section-header",
    }
)

#: Properties whose animation forces a layout pass.
LAYOUT_PROPERTIES = ("top", "height", "width", "margin", "padding")

#: Subresource sources that may point at another origin.  The Google Fonts
#: stylesheet is the pre-existing, documented exception — it was on every page
#: of the site before the webfonts were vendored, and the vendored
#: ``website/css/fonts.css`` replaces it (see
#: ``tests/test_launch_site_motion.py`` for the same exception site-wide).
#: ``fonts.gstatic.com`` is the font-file origin of that same stylesheet.
ALLOWED_THIRD_PARTY = {
    "https://fonts.googleapis.com",
    "https://fonts.gstatic.com",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)


class _Element:
    """A very small DOM node — enough to walk the page."""

    __slots__ = ("tag", "attrs", "classes", "children", "parent", "text")

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs = dict(attrs)
        self.classes = frozenset((self.attrs.get("class") or "").split())
        self.children: list[_Element] = []
        self.parent: _Element | None = None
        self.text: list[str] = []

    @property
    def text_content(self) -> str:
        return "".join(self.text)

    def iter_descendants(self):
        for child in self.children:
            yield child
            yield from child.iter_descendants()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("#root", [])
        self._stack: list[_Element] = [self.root]

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, attrs)
        element.parent = self._stack[-1]
        self._stack[-1].children.append(element)
        if tag not in VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag, attrs):
        element = _Element(tag, attrs)
        element.parent = self._stack[-1]
        self._stack[-1].children.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        self._stack[-1].text.append(data)


def _parse(html: str) -> _Element:
    parser = _Tree()
    parser.feed(html)
    parser.close()
    return parser.root


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_comments(css: str) -> str:
    """Comments are prose, not declarations."""
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.DOTALL)


def _page(name: str) -> str:
    return _read(SITE / name)


def _tree(name: str) -> _Element:
    return _parse(_page(name))


def _sections(root: _Element) -> list[_Element]:
    return [el for el in root.iter_descendants() if el.tag == "section"]


def _is_reveal(element: _Element) -> bool:
    if REVEAL_CLASS in element.classes:
        return True
    return bool(element.classes & ENGINE_TARGET_CLASSES)


def _origin_of(url: str) -> str | None:
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        return None
    match = re.match(r"^(https?:)?//([^/?#]+)", url)
    if not match:
        return None
    scheme = match.group(1) or "https:"
    return f"{scheme}//{match.group(2)}"


def _nav_hrefs(root: _Element) -> set[str]:
    hrefs: set[str] = set()
    for nav in root.iter_descendants():
        if nav.tag != "nav":
            continue
        for anchor in nav.iter_descendants():
            if anchor.tag != "a":
                continue
            href = anchor.attrs.get("href")
            if href:
                hrefs.add(href)
    return hrefs


def _stylesheets(html: str) -> list[str]:
    return re.findall(
        r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*\bhref\s*=\s*["']([^"']+)["']""",
        html,
        re.IGNORECASE,
    )


# --------------------------------------------------------------------------
# 1. the stylesheet exists and every page loads it
# --------------------------------------------------------------------------

def test_page_motion_stylesheet_exists():
    assert MOTION_CSS.is_file(), (
        f"{MOTION_CSS} is missing — the per-page motion layer has to live in "
        "one file so it never collides with premium.css"
    )
    assert MOTION_CSS.stat().st_size > 500, (
        f"{MOTION_CSS.name} looks truncated ({MOTION_CSS.stat().st_size} bytes)"
    )


@pytest.mark.parametrize("page", PAGES)
def test_every_page_links_the_motion_stylesheet(page):
    sheets = _stylesheets(_page(page))
    matching = [href for href in sheets if href.split("?")[0] == MOTION_STYLESHEET]
    assert len(matching) == 1, (
        f"{page} must link {MOTION_STYLESHEET} exactly once, found {matching}"
    )


@pytest.mark.parametrize("page", PAGES)
def test_the_motion_stylesheet_loads_after_the_shared_design_system(page):
    """It must be able to win the cascade against premium.css."""
    sheets = _stylesheets(_page(page))
    names = [href.split("?")[0] for href in sheets]
    assert MOTION_STYLESHEET in names
    assert names.index(MOTION_STYLESHEET) > names.index("css/premium.css"), (
        f"{page}: {MOTION_STYLESHEET} must load after css/premium.css"
    )


# --------------------------------------------------------------------------
# 2. the stylesheet is a safe, reduced-motion-aware enhancement
# --------------------------------------------------------------------------

def test_the_motion_stylesheet_has_a_reduced_motion_block():
    css = _read(MOTION_CSS)
    assert "@media (prefers-reduced-motion: reduce)" in css, (
        "pages-motion.css animates things and must neutralise them for "
        "prefers-reduced-motion"
    )


def test_the_motion_stylesheet_has_an_fx_reduced_counterpart():
    """``?fx=off`` sets ``html.fx-reduced``; the media query cannot see it."""
    css = _read(MOTION_CSS)
    assert "html.fx-reduced" in css, (
        "pages-motion.css must also handle the ?fx=off query flag through the "
        "html.fx-reduced class fx-core.js sets for it"
    )


def test_the_motion_stylesheet_never_animates_a_layout_property():
    """Animating top/height/width forces a layout pass on every frame."""
    css = _strip_comments(_read(MOTION_CSS))
    declaration = re.compile(
        r"(?P<prop>transition(?:-property)?|animation(?:-name)?)\s*:\s*(?P<value>[^;{}]+)",
        re.IGNORECASE,
    )
    offenders = []
    for match in declaration.finditer(css):
        value = match.group("value")
        for prop in LAYOUT_PROPERTIES:
            if re.search(rf"\b{prop}\b", value, re.IGNORECASE):
                offenders.append(f"{match.group('prop')}: {value.strip()}")
    assert not offenders, (
        "pages-motion.css animates layout properties — animate "
        "opacity/transform/translate instead:\n  " + "\n  ".join(offenders)
    )


def test_the_motion_stylesheet_keeps_the_engine_hooks_intact():
    """The public engine class names must keep their meaning.

    The engine reveals every ``.fx-reveal`` element it owns. The page
    stylesheet may only extend that behaviour to the elements that opt in
    through the legacy vocabulary — it must never redefine the hook itself.
    """
    css = _strip_comments(_read(MOTION_CSS))
    selectors = re.findall(r"(?:^|[{}])\s*([^{}]+?)\s*\{", css, re.DOTALL)
    offenders = []
    for selector in selectors:
        for part in selector.split(","):
            part = part.strip()
            if ".fx-reveal" not in part:
                continue
            if ".animate-on-scroll" in part or ".animate-delay-" in part:
                continue
            offenders.append(part)
    assert not offenders, (
        "pages-motion.css must not redefine the engine's .fx-reveal hook:\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# 3. reveal coverage
# --------------------------------------------------------------------------

@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_enough_reveal_elements(page):
    count = _page(page).count(REVEAL_CLASS)
    assert count >= MIN_REVEALS[page], (
        f"{page} has {count} '{REVEAL_CLASS}' elements, expected at least "
        f"{MIN_REVEALS[page]}"
    )


@pytest.mark.parametrize("page", PAGES)
def test_every_section_contains_a_reveal_element(page):
    """No section may arrive unanimated while its neighbours animate."""
    sections = _sections(_tree(page))
    assert sections, f"{page} has no <section> to check"
    empty = []
    for index, section in enumerate(sections):
        if not any(_is_reveal(el) for el in section.iter_descendants()):
            label = section.attrs.get("class") or section.attrs.get("aria-label") or "?"
            empty.append(f"section #{index} ({label})")
    assert not empty, (
        f"{page}: these sections contain no reveal element:\n  " + "\n  ".join(empty)
    )


@pytest.mark.parametrize("page", PAGES)
def test_every_section_contains_an_explicitly_marked_reveal(page):
    """Stricter than the rule above: the markup must declare it."""
    sections = _sections(_tree(page))
    empty = []
    for index, section in enumerate(sections):
        if not any(REVEAL_CLASS in el.classes for el in section.iter_descendants()):
            label = section.attrs.get("class") or section.attrs.get("aria-label") or "?"
            empty.append(f"section #{index} ({label})")
    assert not empty, (
        f"{page}: these sections contain no '{REVEAL_CLASS}' element:\n  "
        + "\n  ".join(empty)
    )


# --------------------------------------------------------------------------
# 4. the page structure must survive the motion work
# --------------------------------------------------------------------------

@pytest.mark.parametrize("page", PAGES)
def test_no_third_party_origin_on_the_pages(page):
    html = _page(page)
    tags = re.findall(r"<(?:script|link|img|iframe|source)\b[^>]*>", html, re.IGNORECASE)
    offenders = []
    for tag in tags:
        for url in re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", tag):
            origin = _origin_of(url)
            if origin and origin not in ALLOWED_THIRD_PARTY:
                offenders.append(f"{page}: {url}")
    assert not offenders, "third-party subresources found:\n" + "\n".join(offenders)


@pytest.mark.parametrize("page", PAGES)
def test_no_script_is_loaded_from_a_third_party(page):
    for url in re.findall(
        r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", _page(page), re.IGNORECASE
    ):
        origin = _origin_of(url)
        assert origin is None or origin in ALLOWED_THIRD_PARTY, (
            f"{page} loads a script from {origin}"
        )


def test_the_three_pages_share_the_home_page_navigation():
    home = _nav_hrefs(_tree("index.html"))
    assert home, "index.html has no navigation links"
    for page in PAGES:
        assert _nav_hrefs(_tree(page)) == home, (
            f"{page} navigation differs from index.html: "
            f"{_nav_hrefs(_tree(page)) ^ home}"
        )


@pytest.mark.parametrize("page", PAGES)
def test_each_page_has_exactly_one_h1(page):
    headings = [el for el in _tree(page).iter_descendants() if el.tag == "h1"]
    assert len(headings) == 1, f"{page} has {len(headings)} <h1> elements"


@pytest.mark.parametrize("page", PAGES)
def test_each_page_has_a_non_empty_title(page):
    titles = [el for el in _tree(page).iter_descendants() if el.tag == "title"]
    assert len(titles) == 1, f"{page} must have exactly one <title>"
    assert titles[0].text_content.strip(), f"{page} has an empty <title>"


# --------------------------------------------------------------------------
# 5. the reading rail
# --------------------------------------------------------------------------

@pytest.mark.parametrize("page", PAGES)
def test_each_page_carries_the_reading_rail(page):
    rails = [
        el
        for el in _tree(page).iter_descendants()
        if "fx-read-rail" in el.classes
    ]
    assert len(rails) == 1, f"{page} must carry exactly one .fx-read-rail"
    assert rails[0].attrs.get("aria-hidden") == "true", (
        f"{page}: the reading rail is decorative and must be aria-hidden"
    )
