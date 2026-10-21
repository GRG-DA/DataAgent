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


def _third_party_refs() -> list:
    """Every reference to a non-local origin in the site, with its file."""
    found = []
    for path in list(PAGES) + sorted(SITE.glob("js/*.js")) + sorted(SITE.glob("css/*.css")):
        text = path.read_text(encoding="utf-8", errors="replace")
        kind = "css" if path.suffix == ".css" else "html"
        text = _strip_comments(text, kind)
        for match in re.finditer(r"https?://[^\s\"'()<>]+", text):
            url = match.group(0).rstrip("\"'`,;")
            if url.startswith("http://127.0.0.1") or url.startswith("http://localhost"):
                continue
            rel = path.relative_to(ROOT).as_posix()
            found.append((url, rel))
    return found


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


def test_the_only_third_party_script_is_the_payment_hand_off():
    """Razorpay's checkout is a deliberate, user-initiated hand-off."""
    offenders = []
    for url, rel in _third_party_refs():
        if url in ALLOWED_THIRD_PARTY:
            assert rel == ALLOWED_THIRD_PARTY[url], (
                f"{url} is only allowed in {ALLOWED_THIRD_PARTY[url]}, "
                f"found it in {rel}"
            )
            continue
        offenders.append(f"{url}  ({rel})")
    assert not offenders, (
        "the site contacts a third party on page view:\n  "
        + "\n  ".join(sorted(offenders))
    )
