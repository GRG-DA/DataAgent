"""The marketing site must not leak a visitor's IP to a third party.

The site is for a local-first, offline-first product whose own pages say
"no telemetry".  Two third-party origins used to be contacted on every page
view:

* ``cdn.jsdelivr.net`` — the GSAP motion engine.  Now vendored.
* ``fonts.googleapis.com`` / ``fonts.gstatic.com`` — the webfonts.  Now
  vendored.

Both sent the visitor's IP address to a third party on every page view, and
both left an offline visitor with a broken page.

The one remaining third-party script is Razorpay's checkout, which is
injected by ``website/js/billing.js`` and is only loaded when a buyer
clicks "pay" on a configured billing server.  That is a deliberate,
user-initiated payment hand-off, so it is allowed — but only there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "website"
PAGES = sorted(SITE.glob("*.html"))

#: A third-party origin the site is allowed to contact, and where.
ALLOWED_THIRD_PARTY = {
    "https://checkout.razorpay.com/v1/checkout.js": "website/js/billing.js",
}


def _strip_comments(text: str, kind: str) -> str:
    """Remove comments - a comment MENTIONING an origin is not a request."""
    if kind == "css":
        return re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


#: XML namespace URIs.  They look like origins but are identifiers - a
#: browser NEVER fetches them.  "http://www.w3.org/2000/svg" is the SVG
#: namespace and appears in every inline <svg>.
XML_NAMESPACES = (
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/2000/xmlns/",
)


def _scan() -> tuple:
    """Return (subresources, anchors) that point at a non-local origin.

    The distinction matters.  A SUBRESOURCE (a script, a stylesheet, an
    image, an iframe, a CSS url()) is fetched automatically the moment the
    page loads - that is the leak this project cares about.  An ANCHOR href is
    only fetched if the visitor clicks it, which is a deliberate navigation
    to a documented external site (Ollama's download page, the GitHub
    release).  A page that links out is normal; a page that silently
    phones home is not.
    """
    subresources = []
    anchors = []
    for path in list(PAGES) + sorted(SITE.glob("js/*.js")) + sorted(SITE.glob("css/*.css")):
        text = path.read_text(encoding="utf-8", errors="replace")
        kind = "css" if path.suffix == ".css" else "html"
        text = _strip_comments(text, kind)
        rel = path.relative_to(ROOT).as_posix()

        if kind == "css":
            for m in re.finditer(r"url\(\s*[\"']?(https?://[^\s\"')]+)", text):
                subresources.append((m.group(1), rel))
            # An @import may name its target as a bare string rather than
            # inside url(), and that form fetches just the same.
            for m in re.finditer(
                r"@import\s+(?:url\(\s*)?[\"'](https?://[^\"']+)[\"']", text, re.I,
            ):
                subresources.append((m.group(1), rel))
            continue

        for m in re.finditer(
            r"<(script|img|iframe|source|video|audio|embed)\b[^>]*\bsrc\s*=\s*[\"\']([^\"\']+)",
            text, re.I,
        ):
            subresources.append((m.group(2), rel))
        for m in re.finditer(
            r"<link\b[^>]*\brel\s*=\s*[\"\']?stylesheet[\"\']?[^>]*\bhref\s*=\s*[\"\']([^\"\']+)",
            text, re.I,
        ):
            subresources.append((m.group(1), rel))
        for m in re.finditer(r"\bfrom\s+[\"\'](https?://[^\"\']+)", text):
            subresources.append((m.group(1), rel))
        for m in re.finditer(r"\bhref\s*=\s*[\"\'](https?://[^\"\']+)", text, re.I):
            anchors.append((m.group(1), rel))

    def _local(u):
        # A RELATIVE url is same-origin by definition.
        if not u.startswith(("http://", "https://", "//")):
            return True
        return u.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1"))

    def _ns(u):
        return u in XML_NAMESPACES or u.startswith(XML_NAMESPACES)

    subresources = [(u, r) for u, r in subresources if not _local(u) and not _ns(u)]
    anchors = [(u, r) for u, r in anchors if not _local(u) and not _ns(u)]
    return subresources, anchors



def test_no_page_loads_a_font_from_google():
    for page in PAGES:
        text = _strip_comments(page.read_text(encoding="utf-8"), "html")
        assert "fonts.googleapis.com" not in text, (
            f"{page.name} loads fonts from Google Fonts - that sends the "
            "visitor's IP to a third party on every page view"
        )
        assert "fonts.gstatic.com" not in text, (
            f"{page.name} preconnects to fonts.gstatic.com"
        )


def test_no_stylesheet_imports_a_font_from_google():
    for css in sorted(SITE.glob("css/*.css")):
        text = _strip_comments(css.read_text(encoding="utf-8"), "css")
        assert "fonts.googleapis.com" not in text, (
            f"{css.name} @imports a font from Google Fonts"
        )


def test_the_fonts_are_vendored():
    fonts = SITE / "vendor" / "fonts"
    assert fonts.is_dir(), "website/vendor/fonts is missing"
    woff2 = list(fonts.glob("*.woff2"))
    assert len(woff2) >= 20, (
        f"only {len(woff2)} woff2 files are vendored - expected the four "
        "families (Inter, JetBrains Mono, Bricolage Grotesque, Plus Jakarta "
        "Sans) in latin + latin-ext"
    )
    for family in ("Inter", "JetBrainsMono", "BricolageGrotesque", "PlusJakartaSans"):
        assert list(fonts.glob(f"{family}-*.woff2")), f"{family} is not vendored"


def test_the_font_manifest_records_the_hashes():
    manifest_path = SITE / "vendor" / "fonts" / "manifest.json"
    assert manifest_path.is_file(), "the font manifest is missing"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest, "the font manifest is empty"
    for name, entry in manifest.items():
        assert entry["sha256"], f"{name} has no recorded sha256"
        assert (SITE / "vendor" / "fonts" / name).is_file(), f"{name} is not on disk"


def test_fonts_css_declares_every_vendored_face():
    css = (SITE / "css" / "fonts.css").read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{", css)
    manifest = json.loads(
        (SITE / "vendor" / "fonts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(faces) == len(manifest), (
        f"fonts.css declares {len(faces)} faces but {len(manifest)} files are "
        "vendored"
    )
    for name in manifest:
        assert name in css, f"{name} is vendored but never declared"


def test_no_subresource_is_loaded_from_a_third_party():
    """A page must fetch NOTHING from another origin while it loads.

    Razorpay's checkout is the one documented exception: it is injected by
    website/js/billing.js on a click, and only on a deployment that has a
    billing server configured.
    """
    subresources, _anchors = _scan()
    offenders = []
    for url, rel in subresources:
        if url in ALLOWED_THIRD_PARTY:
            assert rel == ALLOWED_THIRD_PARTY[url], (
                f"{url} is only allowed in {ALLOWED_THIRD_PARTY[url]}, "
                f"found it in {rel}"
            )
            continue
        offenders.append(f"{url}  ({rel})")
    assert not offenders, (
        "the site loads a subresource from a third party on page view:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_every_external_link_is_a_documented_one():
    """An anchor to another site is fine - but it must be a known one.

    A page that links out is normal (the Ollama download, the GitHub
    release). A page that quietly grows a new outbound destination is how
    a privacy claim quietly stops being true, so the set is pinned.
    """
    _subresources, anchors = _scan()
    known = {
        "https://github.com/",
        "https://github.com/DataAgent/DataAgent",
        "https://ollama.ai/download",
        "https://billing.yourdomain.com",
    }
    offenders = sorted({f"{u}  ({r})" for u, r in anchors if u not in known})
    assert not offenders, (
        "unexpected external link destinations:\n  " + "\n  ".join(offenders)
    )
