"""Marketing-site motion-engine guards.

The marketing site (`website/`) is a static, no-build-step site. It is also the
front door of an **offline-first** product whose own homepage says the desktop
app ships no telemetry. That combination puts the motion engine under two hard
constraints that this file guards:

1. **No third-party subresources.** Every script and stylesheet must be served
   from the site itself (`website/vendor/…`). A `<script src="https://cdn…">`
   on every page sends the visitor's IP to a third party on every page view —
   the exact behaviour the product claims not to have.
2. **No content trapped behind an animation.** The reveal system must always
   end up showing its content, and the WebGL hero must have a visible
   fallback.

The historical bug this file exists for: the pages declared an import map
whose values were **bare relative paths** (``"three": "vendor/three/build/
three.module.js"``). Per the import-maps spec a mapping value must be a
*URL-like string*; a path that does not start with ``/``, ``./``, ``../`` or a
scheme is parsed as a **bare specifier**, so Chromium dropped the value,
stored a null for the key and then refused to resolve the bare specifier
``three``:

    Ignored an import map value of "three": Bare specifier: vendor/three/build/three.module.js
    Failed to resolve module specifier "three". Import Map: "three" matches with "three" but is blocked by a null value

The hero therefore never rendered a canvas. ``test_import_map_values_are_url_like``
is the regression guard for exactly that.

Run with::

    python -m pytest tests/test_launch_site_motion.py -q -p no:cacheprovider
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = ROOT / "website"
JS_DIR = WEBSITE / "js"
CSS_DIR = WEBSITE / "css"
VENDOR_GSAP = WEBSITE / "vendor" / "gsap"

if not WEBSITE.is_dir():
    pytest.skip(
        "website/ (the marketing site) ships separately and is not part of "
        "this repository - see ADR-023",
        allow_module_level=True,
    )

PAGES = sorted(WEBSITE.glob("*.html"))
SCRIPTS = sorted(JS_DIR.glob("*.js"))
STYLESHEETS = sorted(CSS_DIR.glob("*.css"))

#: The marketing pages.  404.html and 403.html are also in website/ but are
#: error pages, not marketing pages - they are allow-listed separately.
EXPECTED_PAGES = (
    "index", "about", "features", "comparison", "usecases", "billing",
    "docs", "faq", "blog", "blog-post", "changelog", "security",
    "contact", "download", "account",
)
EXPECTED_PAGE_COUNT = len(EXPECTED_PAGES)

#: Subresource sources that may point at another origin. The webfonts are
#: vendored now (website/vendor/fonts/), so there is NO third-party
#: stylesheet origin left. Keep this empty on purpose - it is the guard that
#: stops a CDN creeping back in.
#: stylesheet is pre-existing (it was already on all 14 pages before this
#: work) and is the ONLY remaining cross-origin subresource. It loads a
#: stylesheet + woff2 files — no script, no beacon — but it is still a
#: third-party request and is reported by
#: ``test_only_allowed_third_party_subresource_remains``.
# The webfonts are vendored now (website/vendor/fonts/), so there is NO
# third-party stylesheet origin left. Keep this empty on purpose.
ALLOWED_THIRD_PARTY_STYLESHEETS: set = set()

#: Origins that may appear in prose (an ``<a href>``) or in a comment.
ALLOWED_PROSE_ORIGINS = {
    "https://go.microsoft.com",
    "https://ollama.ai",
    "https://billing.yourdomain.com",
    "https://github.com",
}

#: The payment provider's checkout script. It is loaded *by a click* on the
#: pricing page, only when a billing server is actually configured — and
#: the shipped default (``http://127.0.0.1:8090``) means it is never
#: loaded at all. It is not a page-view beacon, so it is allowed, but it
#: must stay confined to the checkout module.
ALLOWED_USER_INITIATED_SCRIPTS = {
    "https://checkout.razorpay.com/v1/checkout.js": "js/billing.js",
}

#: GSAP files that must exist locally, with the SHA256 recorded in the
#: vendored README.
GSAP_FILES = ("gsap.min.js", "ScrollTrigger.min.js", "SplitText.min.js")

#: Properties whose animation forces a layout pass. The motion engine must
#: only animate compositor-friendly properties.
LAYOUT_PROPERTIES = {
    "top", "left", "right", "bottom", "width", "height",
    "margin", "marginTop", "marginLeft", "padding", "paddingTop",
    "fontSize", "lineHeight", "borderWidth", "inset",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SCRIPT_SRC = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)
_STYLESHEET_HREF = re.compile(
    r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*\bhref\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_STYLESHEET_HREF_REVERSED = re.compile(
    r"""<link\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*\brel\s*=\s*["']stylesheet["']""",
    re.IGNORECASE,
)
_ICON_HREF = re.compile(
    r"""<link\b[^>]*\brel\s*=\s*["'](?:icon|apple-touch-icon|manifest)["'][^>]*\bhref\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_IMPORT_MAP = re.compile(
    r"""<script\b[^>]*\btype\s*=\s*["']importmap["'][^>]*>(.*?)</script\s*>""",
    re.IGNORECASE | re.DOTALL,
)
_ANY_URL = re.compile(r"""https?://[^\s"'<>)\\]+""")


def _subresource_sources(html: str) -> list[str]:
    """Every script / stylesheet / icon URL a page pulls in."""
    found = []
    found += _SCRIPT_SRC.findall(html)
    found += _STYLESHEET_HREF.findall(html)
    found += _STYLESHEET_HREF_REVERSED.findall(html)
    found += _ICON_HREF.findall(html)
    return found


def _origin_of(url: str) -> str | None:
    """``https://host/path`` -> ``https://host``; relative URLs -> ``None``."""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        return None
    match = re.match(r"^(https?:)?//([^/?#]+)", url)
    if not match:
        return None
    scheme = match.group(1) or "https:"
    return f"{scheme}//{match.group(2)}"


def _import_map_of(html: str) -> dict:
    match = _IMPORT_MAP.search(html)
    assert match, "no <script type=\"importmap\"> found"
    return json.loads(match.group(1))


# --------------------------------------------------------------------------
# 1. the pages themselves
# --------------------------------------------------------------------------

def test_site_has_the_expected_pages():
    """Every expected marketing page exists, and nothing unexpected was added.

    A bare count is brittle: the site legitimately gained 404.html and
    403.html.  Assert the set instead - the expected pages are present, and
    anything else is a known error page.
    """
    found = {p.name for p in PAGES}
    expected = {f"{name}.html" for name in EXPECTED_PAGES}
    missing = sorted(expected - found)
    assert not missing, f"missing marketing pages: {missing}"
    extra = sorted(found - expected)
    known_extra = {"404.html", "403.html"}
    assert set(extra) <= known_extra, (
        f"unexpected pages in website/: {sorted(set(extra) - known_extra)}"
    )


def test_every_page_loads_the_motion_engine():
    for page in PAGES:
        html = _read(page)
        assert "js/fx-core.js" in html, f"{page.name} does not load js/fx-core.js"
        assert "js/fx-webgl.js" in html, f"{page.name} does not load js/fx-webgl.js"
        assert "js/main.js" in html, f"{page.name} does not load js/main.js"


def test_every_page_declares_exactly_one_import_map():
    for page in PAGES:
        html = _read(page)
        assert len(_IMPORT_MAP.findall(html)) == 1, (
            f"{page.name} must declare exactly one import map"
        )


def test_import_map_is_identical_on_every_page():
    maps = {page.name: _import_map_of(_read(page)) for page in PAGES}
    distinct = {json.dumps(m, sort_keys=True) for m in maps.values()}
    assert len(distinct) == 1, f"import map differs between pages: {maps}"


def test_import_map_values_are_url_like():
    """A mapping value must be a *URL-like string*.

    A path that starts with neither a scheme nor ``/``, ``./`` nor ``../`` is
    parsed as a **bare specifier**; Chromium ignores such a value, stores a
    null for the key and then refuses to resolve the specifier. That is what
    killed the WebGL hero.
    """
    for page in PAGES:
        for section in ("imports", "scopes"):
            for key, value in _import_map_of(_read(page)).get(section, {}).items():
                if value is None:
                    continue
                assert isinstance(value, str)
                assert re.match(r"^(\./|/|[a-zA-Z][a-zA-Z0-9+.-]*:)", value), (
                    f"{page.name}: import map value {value!r} for {key!r} is not "
                    "URL-like — a relative path must start with './' or '/'"
                )


def test_import_map_points_at_the_vendored_three_build():
    for page in PAGES:
        imports = _import_map_of(_read(page))["imports"]
        assert imports.get("three", "").endswith("vendor/three/build/three.module.js"), (
            f"{page.name}: 'three' must map to the vendored three.module.js"
        )
        assert "three/addons/" in imports, (
            f"{page.name}: the 'three/addons/' prefix mapping is required by "
            "fx-webgl.js (postprocessing addons)"
        )


def test_module_script_comes_after_the_import_map():
    """An import map must be registered before the module that uses it."""
    for page in PAGES:
        html = _read(page)
        map_at = _IMPORT_MAP.search(html)
        module_at = re.search(r"""<script\b[^>]*\btype\s*=\s*["']module["']""", html, re.I)
        assert map_at and module_at
        assert map_at.start() < module_at.start(), (
            f"{page.name}: the import map must precede the module script"
        )


# --------------------------------------------------------------------------
# 2. no third-party subresources
# --------------------------------------------------------------------------

def test_no_third_party_script_or_style_sources():
    """Every script / stylesheet must be served by the site itself."""
    offenders = []
    for page in PAGES:
        for url in _subresource_sources(_read(page)):
            origin = _origin_of(url)
            if origin is None:
                continue
            if origin in ALLOWED_THIRD_PARTY_STYLESHEETS:
                continue
            offenders.append(f"{page.name}: {url}")
    for script in SCRIPTS:
        text = _read(script)
        for match in re.finditer(r"""\.src\s*=\s*["'](https?://[^"']+)["']""", text):
            url = match.group(1)
            allowed_in = ALLOWED_USER_INITIATED_SCRIPTS.get(url)
            if allowed_in:
                assert script.name == Path(allowed_in).name, (
                    f"{url} may only be loaded by {allowed_in}, not {script.name}"
                )
                continue
            offenders.append(f"{script.name}: {url}")
        for match in re.finditer(r"""import\s[^;]*?from\s*["'](https?://[^"']+)["']""", text):
            offenders.append(f"{script.name}: {match.group(1)}")
    assert not offenders, "third-party subresources found:\n" + "\n".join(offenders)


def test_only_allowed_third_party_subresource_remains():
    """The Google Fonts stylesheet is the single documented exception.

    It was already present on every page before this work; it loads a
    stylesheet and woff2 files only (no script, no analytics). Everything
    else — GSAP, Three.js, Lenis — is vendored locally.
    """
    found = set()
    for page in PAGES:
        for url in _subresource_sources(_read(page)):
            origin = _origin_of(url)
            if origin:
                found.add(origin)
    for sheet in STYLESHEETS:
        for url in re.findall(r"""@import\s+url\(\s*["']?(https?://[^"')]+)""", _read(sheet)):
            origin = _origin_of(url)
            if origin:
                found.add(origin)
    assert found <= ALLOWED_THIRD_PARTY_STYLESHEETS, (
        f"unexpected third-party origins: {sorted(found - ALLOWED_THIRD_PARTY_STYLESHEETS)}"
    )


def test_the_vendored_three_build_is_present():
    """The import map is only useful if the files it points at exist."""
    for relative in (
        "vendor/three/build/three.module.js",
        "vendor/three/build/three.core.js",
        "vendor/three/examples/jsm/postprocessing/EffectComposer.js",
        "vendor/three/examples/jsm/postprocessing/RenderPass.js",
        "vendor/three/examples/jsm/postprocessing/UnrealBloomPass.js",
        "vendor/three/examples/jsm/postprocessing/OutputPass.js",
    ):
        path = WEBSITE / relative
        assert path.is_file(), f"{relative} is missing"
        assert path.stat().st_size > 100, f"{relative} looks truncated"


def test_no_page_references_a_javascript_cdn():
    """No script or stylesheet tag may point at a CDN.

    Prose and comments may still *mention* a CDN (they are not requests), but
    a live tag must not.
    """
    for page in PAGES:
        html = _read(page)
        for tag in re.findall(r"<(?:script|link)\b[^>]*>", html, re.IGNORECASE):
            assert "cdn." not in tag, f"{page.name} loads a subresource from a CDN: {tag}"


# --------------------------------------------------------------------------
# 3. vendored GSAP
# --------------------------------------------------------------------------

def test_gsap_is_vendored():
    for name in GSAP_FILES:
        path = VENDOR_GSAP / name
        assert path.is_file(), f"{path} is missing — GSAP must be vendored locally"
        assert path.stat().st_size > 1000, f"{path} looks truncated"


def test_gsap_readme_records_version_and_licence():
    readme = (VENDOR_GSAP / "README.md").read_text(encoding="utf-8")
    assert "3.13.0" in readme, "the vendor README must record the GSAP version"
    assert "gsap.com/standard-license" in readme, (
        "the vendor README must record the licence"
    )
    assert "website/vendor/gsap" in readme or "gsap" in readme.lower(), (
        "the vendor README must record the upstream URL"
    )


def test_gsap_vendored_hashes_match_the_readme():
    readme = (VENDOR_GSAP / "README.md").read_text(encoding="utf-8")
    recorded = {
        match.group(1): match.group(2)
        for match in re.finditer(r"\|\s*`([A-Za-z]+\.min\.js)`\s*\|[^|]*\|\s*`([0-9a-f]{64})`", readme)
    }
    assert set(recorded) == set(GSAP_FILES), (
        f"the vendor README must record a SHA256 for each of {GSAP_FILES}, "
        f"found {sorted(recorded)}"
    )
    for name, expected in recorded.items():
        actual = _sha256(VENDOR_GSAP / name)
        assert actual == expected, (
            f"{name}: SHA256 mismatch — README says {expected}, file is {actual}"
        )


def test_every_page_loads_the_local_gsap():
    for page in PAGES:
        html = _read(page)
        for name in GSAP_FILES:
            assert f"vendor/gsap/{name}" in html, f"{page.name} does not load vendor/gsap/{name}"


def test_gsap_is_loaded_before_the_motion_engine():
    for page in PAGES:
        html = _read(page)
        assert "vendor/gsap/gsap.min.js" in html, (
            f"{page.name}: the vendored GSAP is not loaded at all"
        )
        assert html.index("vendor/gsap/gsap.min.js") < html.index("js/fx-core.js"), (
            f"{page.name}: GSAP must load before js/fx-core.js reads window.gsap"
        )


# --------------------------------------------------------------------------
# 4. motion behaviour: fallback, failsafe, reduced motion
# --------------------------------------------------------------------------

def test_css_has_a_reduced_motion_block():
    for sheet in STYLESHEETS:
        text = _read(sheet)
        if ".fx-reveal" in text:
            assert "prefers-reduced-motion" in text, (
                f"{sheet.name} styles .fx-reveal but has no reduced-motion block"
            )


def test_reduced_motion_block_neutralises_the_reveal_transforms():
    """``prefers-reduced-motion: reduce`` must make the reveal inert."""
    neutralised = []
    for sheet in STYLESHEETS:
        text = _read(sheet)
        for block in re.finditer(
            r"@media\s*\(prefers-reduced-motion\s*:\s*reduce\)\s*\{", text
        ):
            start = block.end()
            depth = 1
            index = start
            while index < len(text) and depth:
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                index += 1
            body = text[start:index]
            if ".fx-reveal" in body and "opacity: 1" in body and "transform: none" in body:
                neutralised.append(sheet.name)
    assert neutralised, (
        "no stylesheet neutralises .fx-reveal inside a prefers-reduced-motion block"
    )


def test_reveal_engine_has_a_failsafe():
    core = _read(JS_DIR / "fx-core.js")
    assert "fx-reveal" in core
    assert re.search(r"FAILSAFE|failsafe", core, re.IGNORECASE), (
        "fx-core.js must have a failsafe that reveals content if the observer "
        "never fires"
    )


def test_reveal_engine_reveals_the_viewport_on_load():
    core = _read(JS_DIR / "fx-core.js")
    assert re.search(r"revealInView|revealViewport|sweep", core), (
        "fx-core.js must reveal what is already in the viewport on load"
    )


def test_webgl_has_a_visible_fallback():
    webgl = _read(JS_DIR / "fx-webgl.js")
    assert "fx-no-webgl" in webgl, "fx-webgl.js must flag a missing WebGL context"
    css = "\n".join(_read(sheet) for sheet in STYLESHEETS)
    assert "fx-no-webgl" in css, "the CSS must style the no-WebGL fallback"


def test_motion_never_animates_layout_properties():
    """Animating top/height/width forces layout on every frame."""
    for script in (JS_DIR / "fx-core.js", JS_DIR / "fx-webgl.js"):
        text = _read(script)
        for match in re.finditer(r"gsap\.(?:to|from|fromTo|set)\(([^;]*?)\)\s*;", text, re.DOTALL):
            body = match.group(1)
            for prop in re.finditer(r"([A-Za-z]+)\s*:", body):
                name = prop.group(1)
                if name in LAYOUT_PROPERTIES:
                    pytest.fail(
                        f"{script.name}: animates layout property {name!r} — "
                        "animate transform/opacity instead"
                    )


def test_tilt_is_capped():
    """A 3D tilt per card is expensive; the engine must cap it."""
    core = _read(JS_DIR / "fx-core.js")
    match = re.search(r"MAX_TILT\s*:\s*(\d+)", core)
    assert match, "fx-core.js must cap the number of GSAP-tilted elements"
    assert 0 < int(match.group(1)) <= 64, (
        f"MAX_TILT of {match.group(1)} is not a sane budget"
    )
    assert re.search(r"MAX_MAGNETIC\s*:\s*(\d+)", core), (
        "fx-core.js must cap the number of magnetic elements too"
    )


def test_fx_public_class_names_are_preserved():
    """Other pages build on these hooks — the engine may add, never repurpose."""
    core = _read(JS_DIR / "fx-core.js")
    css = "\n".join(_read(sheet) for sheet in STYLESHEETS)
    for name in ("fx-reveal", "fx-spot", "fx-tilt", "fx-lines"):
        assert name in core, f"fx-core.js no longer handles {name}"
        assert name in css, f"the CSS no longer styles {name}"
