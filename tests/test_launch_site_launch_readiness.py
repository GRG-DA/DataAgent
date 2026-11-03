"""Launch-readiness guards for the marketing site's launch artefacts.

The marketing site (``website/``) is a static, no-build-step site: no
framework, no server.  Everything needed to publish it is hand-written, and
none of it is covered by the existing suites:

* **Crawl control** — ``robots.txt`` / ``sitemap.xml``.  The product's own
  legal pages are served from *routes* by the app (``/legal/…``); a crawler
  that stumbles onto an unfilled legal template must not index it.
* **Error pages** — ``404.html`` / ``403.html``.  A static host serves its
  own 404; without one the visitor gets the host's default page, which
  looks like a different site (and a different product).
* **Security headers** — the same header set has to be declared for three
  different hosts (``_headers`` for Netlify, ``netlify.toml`` for
  Netlify's build config, ``vercel.json`` for Vercel).  A host change that
  silently drops one header is exactly the kind of regression that is
  invisible in review and visible in production, so the three sets are
  asserted to be *identical*, not merely present.
* **The app's reverse proxy** — ``nginx.conf``.  It already had baseline
  hardening, but the headers were declared where nginx's ``add_header``
  inheritance rule silently dropped them, and HSTS must never be sent
  over plain HTTP.
* **Static checks that run in CI** — a broken-link checker and an
  accessibility checker.  Both parse the HTML with ``html.parser`` so a
  JavaScript string literal is never mistaken for a link.

Run with::

    python -m pytest tests/test_launch_site_launch_readiness.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = ROOT / "website"

if not WEBSITE.is_dir():
    pytest.skip(
        "website/ (the marketing site) ships separately and is not part of "
        "this repository - see ADR-023",
        allow_module_level=True,
    )

ROBOTS = WEBSITE / "robots.txt"
SITEMAP = WEBSITE / "sitemap.xml"
NOT_FOUND_PAGE = WEBSITE / "404.html"
FORBIDDEN_PAGE = WEBSITE / "403.html"
NETLIFY_HEADERS = WEBSITE / "_headers"
NETLIFY_TOML = WEBSITE / "netlify.toml"
VERCEL_JSON = WEBSITE / "vercel.json"
NGINX_CONF = ROOT / "nginx.conf"
LINK_CHECKER = ROOT / "scripts" / "ci" / "check_site_links.py"
A11Y_CHECKER = ROOT / "scripts" / "ci" / "check_site_a11y.py"

#: The two error pages are deliberately NOT in the sitemap.
ERROR_PAGES = {"404.html", "403.html"}

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

#: The exact header set every host config must carry.  A host change that
#: drops one of these is the regression this file exists to catch.
REQUIRED_HEADERS = (
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
)

#: Private / legal render routes a crawler must not index.
PRIVATE_ROUTES = ("/legal/", "/share/", "/auth/")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_netlify_headers_file(path: Path) -> dict[str, str]:
    """Parse a Netlify ``_headers`` file into ``{header-name: value}``.

    The format is a list of path blocks followed by indented
    ``Name: value`` lines.  A block header (``/*``) has no colon and is
    skipped; a comment starts with ``#``.
    """
    headers: dict[str, str] = {}
    for raw in _read(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def _parse_netlify_toml(path: Path) -> dict[str, str]:
    """Parse ``netlify.toml``'s ``[[headers]]`` blocks."""
    data = tomllib.loads(_read(path))
    headers: dict[str, str] = {}
    for block in data.get("headers", []):
        for name, value in block.get("values", {}).items():
            headers[name] = str(value)
    return headers


def _parse_vercel_json(path: Path) -> dict[str, str]:
    """Parse ``vercel.json``'s ``headers`` array."""
    data = json.loads(_read(path))
    headers: dict[str, str] = {}
    for block in data.get("headers", []):
        for item in block.get("headers", []):
            headers[item["key"]] = item["value"]
    return headers


def _uncommented(text: str) -> str:
    """Drop whole-line ``#`` comments (nginx block comments)."""
    kept = []
    for raw in text.splitlines():
        if not raw.lstrip().startswith("#"):
            kept.append(raw)
    return "\n".join(kept)


def _csp_directives(csp: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for chunk in csp.split(";"):
        parts = chunk.split()
        if not parts:
            continue
        out[parts[0].lower()] = [p for p in parts[1:]]
    return out


def _host_configs() -> dict[str, dict[str, str]]:
    """The header set each of the three host configs declares."""
    return {
        "_headers": _parse_netlify_headers_file(NETLIFY_HEADERS),
        "netlify.toml": _parse_netlify_toml(NETLIFY_TOML),
        "vercel.json": _parse_vercel_json(VERCEL_JSON),
    }


# --------------------------------------------------------------------------
# 1. the artefacts exist
# --------------------------------------------------------------------------

def test_every_launch_artefact_exists():
    missing = [
        str(path.relative_to(ROOT))
        for path in (
            ROBOTS,
            SITEMAP,
            NOT_FOUND_PAGE,
            FORBIDDEN_PAGE,
            NETLIFY_HEADERS,
            NETLIFY_TOML,
            VERCEL_JSON,
            LINK_CHECKER,
            A11Y_CHECKER,
        )
        if not path.is_file()
    ]
    assert not missing, "missing launch artefacts: " + ", ".join(missing)


# --------------------------------------------------------------------------
# 2. robots.txt
# --------------------------------------------------------------------------

def test_robots_allows_crawling_and_points_at_the_sitemap():
    text = _read(ROBOTS)
    assert "User-agent: *" in text, "robots.txt must address every crawler"
    assert "Allow: /" in text, "robots.txt must not block the public pages"
    sitemap_lines = [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().lower().startswith("sitemap:")
    ]
    assert sitemap_lines, "robots.txt must declare a Sitemap: URL"
    for url in sitemap_lines:
        assert url.startswith("http"), f"sitemap URL must be absolute: {url}"
        assert url.endswith("/sitemap.xml"), f"unexpected sitemap URL: {url}"


def test_robots_disallows_the_private_render_routes():
    text = _read(ROBOTS)
    disallows = [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().lower().startswith("disallow:")
    ]
    for route in PRIVATE_ROUTES:
        assert route in disallows, (
            f"robots.txt must disallow {route} - the app renders the legal "
            "pages from routes and a crawler must not index an unfilled "
            "template"
        )


# --------------------------------------------------------------------------
# 3. sitemap.xml
# --------------------------------------------------------------------------

def test_sitemap_is_valid_xml():
    root = ET.fromstring(_read(SITEMAP))
    assert root.tag == f"{SITEMAP_NS}urlset", f"unexpected root tag {root.tag}"


def test_sitemap_lists_every_non_error_page():
    root = ET.fromstring(_read(SITEMAP))
    locs = [
        (node.findtext(f"{SITEMAP_NS}loc") or "").strip()
        for node in root.findall(f"{SITEMAP_NS}url")
    ]
    listed: set[str] = set()
    for loc in locs:
        if not loc:
            continue
        # The homepage is listed as ".../" — it is website/index.html.
        listed.add("index.html" if loc.endswith("/") else loc.rsplit("/", 1)[-1])
    expected = {
        page.name for page in WEBSITE.glob("*.html") if page.name not in ERROR_PAGES
    }
    missing = sorted(expected - listed)
    assert not missing, "pages missing from sitemap.xml: " + ", ".join(missing)
    extra = sorted(listed - expected)
    assert not extra, "sitemap.xml lists pages that do not exist: " + ", ".join(extra)


def test_sitemap_excludes_the_error_pages():
    text = _read(SITEMAP)
    for name in ERROR_PAGES:
        assert f"/{name}" not in text, f"{name} must not be in the sitemap"


def test_sitemap_entries_carry_lastmod_changefreq_and_priority():
    root = ET.fromstring(_read(SITEMAP))
    urls = root.findall(f"{SITEMAP_NS}url")
    assert urls, "the sitemap has no <url> entries"
    for node in urls:
        loc = node.findtext(f"{SITEMAP_NS}loc") or "?"
        assert node.findtext(f"{SITEMAP_NS}lastmod"), f"{loc}: no <lastmod>"
        changefreq = node.findtext(f"{SITEMAP_NS}changefreq")
        assert changefreq in {
            "always", "hourly", "daily", "weekly", "monthly", "yearly", "never",
        }, f"{loc}: bad <changefreq> {changefreq!r}"
        priority = node.findtext(f"{SITEMAP_NS}priority")
        assert priority is not None, f"{loc}: no <priority>"
        assert 0.0 <= float(priority) <= 1.0, f"{loc}: bad <priority> {priority!r}"


def test_sitemap_locs_are_absolute():
    root = ET.fromstring(_read(SITEMAP))
    for node in root.findall(f"{SITEMAP_NS}url"):
        loc = (node.findtext(f"{SITEMAP_NS}loc") or "").strip()
        assert loc.startswith("http"), f"sitemap <loc> must be absolute: {loc}"


# --------------------------------------------------------------------------
# 4. the error pages
# --------------------------------------------------------------------------

def test_error_pages_are_noindex():
    for path in (NOT_FOUND_PAGE, FORBIDDEN_PAGE):
        text = _read(path)
        assert 'name="robots"' in text, f"{path.name} must declare a robots meta tag"
        assert "noindex" in text, f"{path.name} must be noindex"


def test_error_pages_link_home():
    for path in (NOT_FOUND_PAGE, FORBIDDEN_PAGE):
        assert 'href="index.html"' in _read(path), f"{path.name} must link home"


def test_error_pages_use_the_site_stylesheets():
    for path in (NOT_FOUND_PAGE, FORBIDDEN_PAGE):
        text = _read(path)
        for sheet in ("css/style.css", "css/pro.css", "css/premium.css"):
            assert sheet in text, f"{path.name} does not load {sheet}"


def test_error_pages_make_no_third_party_request():
    """No SUBRESOURCE from another origin.

    A URL in a meta tag (og:url, og:image) is metadata - a crawler reads
    it, the browser does not fetch it. What must not happen is the page
    LOADING something from another origin, so that is what is checked.
    """
    import re

    for path in (NOT_FOUND_PAGE, FORBIDDEN_PAGE):
        text = _read(path)
        for m in re.finditer(
            r"<(script|img|iframe|source|video|audio|embed)\b[^>]*\bsrc\s*=\s*[\"\']([^\"\']+)",
            text, re.I,
        ):
            url = m.group(2)
            assert not url.startswith(("http://", "https://", "//")), (
                f"{path.name} loads a subresource from another origin: {url}"
            )
        for m in re.finditer(
            r"<link\b[^>]*\brel\s*=\s*[\"\']?stylesheet[\"\']?[^>]*\bhref\s*=\s*[\"\']([^\"\']+)",
            text, re.I,
        ):
            url = m.group(1)
            assert not url.startswith(("http://", "https://", "//")), (
                f"{path.name} loads a stylesheet from another origin: {url}"
            )
        assert "//cdn." not in text and "//fonts." not in text


# --------------------------------------------------------------------------
# 5. the three host configs
# --------------------------------------------------------------------------

def test_host_configs_parse():
    tomllib.loads(_read(NETLIFY_TOML))
    json.loads(_read(VERCEL_JSON))
    assert _parse_netlify_headers_file(NETLIFY_HEADERS)


def test_the_three_host_configs_carry_the_same_security_headers():
    configs = _host_configs()
    for name, headers in configs.items():
        missing = [h for h in REQUIRED_HEADERS if h not in headers]
        assert not missing, f"{name} is missing: {', '.join(missing)}"

    reference = configs["_headers"]
    for name, headers in configs.items():
        assert set(headers) == set(reference), (
            f"{name} carries a different header set than _headers: "
            f"{sorted(set(headers) ^ set(reference))}"
        )
        for header in REQUIRED_HEADERS:
            assert headers[header] == reference[header], (
                f"{name}: {header} differs from _headers\n"
                f"  _headers:    {reference[header]}\n"
                f"  {name}: {headers[header]}"
            )


def test_the_csp_allows_self_and_no_wildcard():
    for name, headers in _host_configs().items():
        csp = headers.get("Content-Security-Policy", "")
        assert csp, f"{name} has no Content-Security-Policy"
        directives = _csp_directives(csp)
        assert "default-src" in directives, f"{name}: no default-src directive"
        assert "'self'" in directives["default-src"], (
            f"{name}: default-src must allow 'self'"
        )
        for directive, values in directives.items():
            for value in values:
                assert "*" not in value, (
                    f"{name}: {directive} allows the wildcard {value!r}"
                )


def test_the_csp_carries_the_required_directives():
    for name, headers in _host_configs().items():
        directives = _csp_directives(headers.get("Content-Security-Policy", ""))
        for required in (
            "default-src", "script-src", "style-src", "font-src", "img-src",
            "connect-src", "frame-ancestors", "base-uri", "form-action",
            "object-src",
        ):
            assert required in directives, f"{name}: CSP has no {required}"


def test_hsts_is_declared_with_the_two_year_max_age():
    for name, headers in _host_configs().items():
        value = headers.get("Strict-Transport-Security", "")
        assert "max-age=63072000" in value, f"{name}: HSTS max-age is wrong: {value!r}"
        assert "includeSubDomains" in value, f"{name}: HSTS must includeSubDomains"


def test_the_host_configs_declare_the_404_page():
    assert "404.html" in _read(NETLIFY_TOML), "netlify.toml must declare the 404 page"
    assert "404.html" in _read(VERCEL_JSON), "vercel.json must declare the 404 page"


def test_the_host_configs_map_clean_urls_to_the_html_files():
    netlify = tomllib.loads(_read(NETLIFY_TOML))
    targets = {block.get("to", "") for block in netlify.get("redirects", [])}
    assert "/download.html" in targets, (
        "netlify.toml must rewrite /download to /download.html"
    )
    vercel = json.loads(_read(VERCEL_JSON))
    destinations = {
        block.get("destination", "")
        for block in list(vercel.get("redirects", [])) + list(vercel.get("rewrites", []))
    }
    assert "/download.html" in destinations, (
        "vercel.json must rewrite /download to /download.html"
    )


def test_the_two_hosts_declare_the_same_clean_urls():
    """Netlify and Vercel must map the SAME clean URLs.

    They drifted once: Netlify mapped ``/pricing`` to ``/billing.html``
    and Vercel mapped ``/billing``, so the same URL worked on one host and
    404'd on the other.  Nothing links to a clean URL today, which is
    exactly why the divergence was invisible.
    """
    netlify = tomllib.loads(_read(NETLIFY_TOML))
    netlify_map = {
        block.get("from", ""): block.get("to", "")
        for block in netlify.get("redirects", [])
    }
    vercel = json.loads(_read(VERCEL_JSON))
    vercel_map = {
        block.get("source", ""): block.get("destination", "")
        for block in list(vercel.get("redirects", [])) + list(vercel.get("rewrites", []))
    }
    assert netlify_map, "netlify.toml declares no redirects"
    assert vercel_map, "vercel.json declares no rewrites"
    assert netlify_map == vercel_map, (
        "the two hosts map different clean URLs:\n"
        f"  only in netlify.toml: {sorted(set(netlify_map) - set(vercel_map))}\n"
        f"  only in vercel.json:   {sorted(set(vercel_map) - set(netlify_map))}\n"
        "  they must declare the SAME set"
    )


# --------------------------------------------------------------------------
# 6. the app's reverse proxy
# --------------------------------------------------------------------------

def test_nginx_declares_the_app_security_headers():
    text = _read(NGINX_CONF)
    live = _uncommented(text)
    for header in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
        "Content-Security-Policy",
    ):
        assert f"add_header {header}" in live, (
            f"nginx.conf does not send {header} on the live server block"
        )


def test_nginx_never_sends_hsts_over_plain_http():
    live = _uncommented(_read(NGINX_CONF))
    assert "Strict-Transport-Security" not in live, (
        "Strict-Transport-Security must only be sent over HTTPS - the live "
        "server block listens on port 80"
    )
    assert "Strict-Transport-Security" in _read(NGINX_CONF), (
        "the commented TLS block must carry the HSTS header"
    )


def test_nginx_keeps_the_existing_routing():
    text = _read(NGINX_CONF)
    for needle in (
        "location /api/",
        "location /health",
        "location /llm/chat/stream",
        "proxy_pass http://api:8002/health;",
        "limit_req zone=api burst=60 nodelay;",
    ):
        assert needle in text, f"nginx.conf lost {needle!r}"


def test_nginx_does_not_reuse_the_marketing_site_csp():
    nginx_csp = ""
    for line in _read(NGINX_CONF).splitlines():
        if "add_header Content-Security-Policy" in line and not line.lstrip().startswith("#"):
            nginx_csp = line
            break
    assert nginx_csp, "nginx.conf has no live Content-Security-Policy"
    marketing_csp = _parse_netlify_headers_file(NETLIFY_HEADERS)["Content-Security-Policy"]
    assert nginx_csp != marketing_csp, (
        "the app must not inherit the marketing site's CSP - they are "
        "different origins with different needs"
    )
    assert "frame-src" in nginx_csp, (
        "the app's CSP must allow frame-src - the Google OAuth popup is a "
        "frame and a CSP without it breaks sign-in"
    )

# --------------------------------------------------------------------------
# 7. the CI checkers
# --------------------------------------------------------------------------

def test_the_checker_scripts_compile():
    for path in (LINK_CHECKER, A11Y_CHECKER):
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert proc.returncode == 0, f"{path.name} does not compile:\n{proc.stderr}"


def test_the_link_checker_exits_zero_on_the_current_tree():
    proc = subprocess.run(
        [sys.executable, str(LINK_CHECKER)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, (
        "the marketing site has broken internal links or anchors:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_the_a11y_checker_runs_and_reports():
    proc = subprocess.run(
        [sys.executable, str(A11Y_CHECKER)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode in (0, 1), (
        f"the a11y checker must exit 0 (clean) or 1 (findings), got "
        f"{proc.returncode}:\n{proc.stderr}"
    )
    assert "file" in proc.stdout.lower() or "finding" in proc.stdout.lower() or (
        proc.stdout.strip()
    ), "the a11y checker printed nothing"


def test_every_page_has_social_sharing_tags():
    """A link pasted into a chat must render a card, not a bare URL.

    Every page needs the Open Graph and Twitter card tags, and they must
    point at the site's own 1200x630 preview image.
    """
    import re

    required = (
        "og:title", "og:description", "og:url", "og:image",
        "og:site_name", "twitter:card", "twitter:title", "twitter:image",
    )
    for page in sorted(WEBSITE.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        for tag in required:
            assert tag in html, f"{page.name} is missing the {tag} sharing tag"
        assert "/og-image.png" in html, f"{page.name} does not point at the preview image"
        m = re.search(r'<meta property="og:title" content="(.*?)"', html)
        assert m and m.group(1).strip(), f"{page.name} has an empty og:title"
        m = re.search(r'<meta property="og:description" content="(.*?)"', html)
        assert m and len(m.group(1).strip()) > 30, (
            f"{page.name} has a too-short og:description"
        )


def test_the_preview_image_exists_and_is_the_right_size():
    """The preview image is what a link renders as - it must be 1200x630."""
    from PIL import Image

    img_path = WEBSITE / "og-image.png"
    assert img_path.is_file(), "website/og-image.png is missing"
    with Image.open(img_path) as im:
        assert im.size == (1200, 630), f"the preview image is {im.size}, not 1200x630"
