"""The site must state ONE truth about the release, in ONE place.

Two things were wrong and both are launch-visible:

* the installer size was quoted four different ways across three pages, so
  a visitor could read two different numbers on two different pages of the
  same site, and the release checklist's "compare the number in
  download.html with the real installer" could only ever be satisfied for
  one of them;
* the deployed domain was once a placeholder in TWENTY-ONE files, but the
  deployment notes in robots.txt and sitemap.xml said there were TWO to
  change. An operator who followed the notes exactly deployed a site whose
  every shared link carried an og:url pointing at a domain nobody owns.
  The site now names the real domain, so the test guards consistency.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"

if not SITE.is_dir():
    pytest.skip(
        "website/ (the marketing site) ships separately and is not part of "
        "this repository - see ADR-023",
        allow_module_level=True,
    )

#: The deployed domain.
DEPLOYED_DOMAIN = "data-agent.me"

#: The files that carry the deployed domain: the 17 pages, robots.txt,
#: sitemap.xml, and the two host configs whose CSP allows the billing API.
#: A NEW file that carries it must be added here, and the deployment notes
#: must be updated to match.
EXPECTED_DOMAIN_FILES = {
    "403.html", "404.html", "about.html", "account.html", "billing.html",
    "blog-post.html", "blog.html", "changelog.html", "comparison.html",
    "contact.html", "docs.html", "download.html", "faq.html", "features.html",
    "index.html", "security.html", "usecases.html", "robots.txt", "sitemap.xml",
    "netlify.toml", "vercel.json",
}

#: Only these are scanned for the domain: a binary asset that happens to
#: contain the bytes is not a page.
TEXT_SUFFIXES = {".html", ".txt", ".xml", ".css", ".js", ".json", ".toml", ".md"}


@pytest.fixture(scope="module")
def pages() -> list[pathlib.Path]:
    return sorted(SITE.glob("*.html"))


def _installer_sizes() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for page in sorted(SITE.glob("*.html")):
        hits = re.findall(r"roughly\s*\d+\s*(?:&nbsp;|&#160;|\s)*MB", page.read_text(encoding="utf-8"))
        if hits:
            found[page.name] = hits
    return found


def test_the_site_quotes_one_installer_size() -> None:
    """Every page that states the download size states the SAME number."""
    quoted = _installer_sizes()
    assert quoted, "no page states the installer size - that is a gap, not a pass"
    numbers = {
        re.search(r"\d+", value).group(0)
        for found in quoted.values()
        for value in found
    }
    assert len(numbers) == 1, (
        "the site quotes more than one installer size:\n  "
        + "\n  ".join(f"{name}: {value}" for name, found in quoted.items() for value in found)
    )


def test_the_quoted_installer_size_is_the_one_constant() -> None:
    """The pages quote the constant in scripts/site_installer_size.py."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import site_installer_size  # noqa: PLC0415  (import after the path insert)

    quoted = _installer_sizes()
    numbers = {
        re.search(r"\d+", value).group(0)
        for found in quoted.values()
        for value in found
    }
    assert numbers == {str(site_installer_size.INSTALLER_SIZE_MB)}, (
        f"the site quotes {sorted(numbers)} MB but the constant says "
        f"{site_installer_size.INSTALLER_SIZE_MB} MB - run "
        f"'python scripts/site_installer_size.py --set {site_installer_size.INSTALLER_SIZE_MB}'"
    )


def test_the_deployed_domain_is_only_where_it_is_expected() -> None:
    """A new file carrying the domain must be declared here.

    The deployment notes can only be honest if they name every file that
    has to change, and the only way to know that is to keep the list
    honest.
    """
    found = set()
    for path in SITE.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if DEPLOYED_DOMAIN in text:
            found.add(path.name)
    assert found == EXPECTED_DOMAIN_FILES, (
        "the deployed domain is in files the deployment notes do not name:\n"
        f"  unexpected: {sorted(found - EXPECTED_DOMAIN_FILES)}\n"
        f"  missing:    {sorted(EXPECTED_DOMAIN_FILES - found)}"
    )


def test_every_shared_link_uses_the_sitemap_domain() -> None:
    """og:url, the sitemap and robots.txt must name the SAME host.

    An og:url pointing at a different host than the sitemap is a broken
    preview card on every shared link, and it is invisible: the page
    still renders, the tag is still there, the link is just wrong.
    """
    sitemap = (SITE / "sitemap.xml").read_text(encoding="utf-8")
    hosts = {m.group(1) for m in re.finditer(r"<loc>https?://([^/]+)/", sitemap)}
    assert hosts, "the sitemap lists no absolute URLs"
    assert len(hosts) == 1, f"the sitemap names more than one host: {sorted(hosts)}"
    host = hosts.pop()

    robots = (SITE / "robots.txt").read_text(encoding="utf-8")
    sitemap_line = re.search(r"^Sitemap:\s*https?://([^/]+)/", robots, re.M)
    assert sitemap_line, "robots.txt has no absolute Sitemap: line"
    assert sitemap_line.group(1) == host, (
        f"robots.txt points at {sitemap_line.group(1)} but the sitemap is on {host}"
    )

    for page in sorted(SITE.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        for og_host in re.findall(r'og:url"\s+content="https?://([^/]+)/', text):
            assert og_host == host, (
                f"{page.name}: og:url host {og_host} != the sitemap host {host}"
            )


def test_the_deployment_notes_cover_the_shared_links() -> None:
    """The notes must name the deployed domain and the og:url tags.

    The domain is in twenty-one files, and the notes used to say two.  The
    og:url tags are the ones a visitor actually sees, so the notes have to
    name them.
    """
    checklist = (REPO_ROOT / "docs" / "WEBSITE-CHECKLIST.md").read_text(encoding="utf-8")
    assert DEPLOYED_DOMAIN in checklist, "the checklist never mentions the deployed domain"
    assert "og:url" in checklist, (
        "the checklist does not tell the operator that the og:url tags carry "
        "the deployed domain - those are the links a visitor sees"
    )
    for name in ("robots.txt", "sitemap.xml"):
        text = (SITE / name).read_text(encoding="utf-8")
        assert DEPLOYED_DOMAIN in text, f"{name} does not mention the deployed domain"


def test_the_social_card_is_monochrome() -> None:
    """The card is what every shared link carries, so it must match the site.

    The card is generated from ``scripts/site_og_card.html`` in a real
    browser, with the site's own vendored fonts and palette, so the SOURCE
    is what decides what the card looks like.  Every colour in it must be a
    true neutral - R = G = B - exactly like the palette it comes from.
    """
    source = (REPO_ROOT / "scripts" / "site_og_card.html").read_text(encoding="utf-8")
    offenders = []
    for match in re.finditer(r"#([0-9a-fA-F]{6})\b", source):
        body = match.group(1)
        red, green, blue = (int(body[i : i + 2], 16) for i in (0, 2, 4))
        if not (red == green == blue):
            line = source[: match.start()].count("\n") + 1
            offenders.append(f"line {line}: #{body}")
    assert not offenders, (
        "the social card must be monochrome:\n  " + "\n  ".join(offenders)
    )


def test_the_social_card_is_the_right_size() -> None:
    """1200x630 is what every social platform expects."""
    card = SITE / "og-image.png"
    assert card.is_file(), "website/og-image.png is missing"
    raw = card.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "og-image.png is not a PNG"
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    assert (width, height) == (1200, 630), (
        f"the social card is {width}x{height}; it must be 1200x630"
    )
