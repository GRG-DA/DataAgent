"""A stylesheet that changes without its cache-buster changing never ships.

The failure this guards is exact and it is invisible: the file on disk is
new, the URL is not, the browser serves the OLD file from its HTTP cache,
and the redesign reaches nobody who has been to the site before.  It has
already happened twice on this site.

The cache-buster IS the content hash - the first eight hex characters of
the asset's SHA-256 - so there is no number to remember to bump, and this
test can recompute the truth without a manifest to keep in sync.

    python scripts/site_asset_versions.py --bump   # after changing an asset
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"

#: The asset kinds whose bytes the browser caches hard.
BUMPED_SUFFIXES = (".css", ".js", ".webm", ".mp4", ".jpg", ".jpeg", ".png", ".webp", ".svg")

_ASSET_RE = re.compile(
    r"\b(?:href|src|poster)\s*=\s*[\"']([^\"'>]+)[\"']"
)
_VERSION_RE = re.compile(r"[?&]v=([0-9a-fA-F]+)")


def _is_local_asset(raw: str) -> bool:
    if not raw or raw.startswith(("http://", "https://", "//", "#", "data:", "mailto:")):
        return False
    return raw.split("?", 1)[0].lower().endswith(BUMPED_SUFFIXES)


def _pages() -> list[pathlib.Path]:
    return sorted(SITE.glob("*.html"))


@pytest.fixture(scope="module")
def pages() -> list[pathlib.Path]:
    return _pages()


def test_every_local_asset_reference_carries_a_cache_buster(pages) -> None:
    """No stylesheet, script or media file may be referenced without a ?v=.

    A reference with no cache-buster can never pick up a fix.
    """
    missing: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for raw in _ASSET_RE.findall(text):
            if not _is_local_asset(raw):
                continue
            if not _VERSION_RE.search(raw):
                missing.append(f"{page.name}: {raw}")
    assert not missing, (
        "every local stylesheet and script needs a ?v= cache-buster:\n  "
        + "\n  ".join(missing)
    )


def test_every_cache_buster_matches_the_asset_content(pages) -> None:
    """The ?v= must be the asset's own content hash.

    A number that has to be remembered is a number that will be forgotten,
    and the day it is forgotten the change does not ship.
    """
    stale: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for raw in _ASSET_RE.findall(text):
            if not _is_local_asset(raw):
                continue
            relative = raw.split("?", 1)[0].lstrip("/")
            asset = SITE / relative
            if not asset.is_file():
                continue
            expected = hashlib.sha256(asset.read_bytes()).hexdigest()[:8]
            found = _VERSION_RE.search(raw)
            if not found or found.group(1) != expected:
                stale.append(
                    f"{page.name}: {relative} -> "
                    f"{found.group(1) if found else 'NONE'} (want {expected})"
                )
    assert not stale, (
        "a changed asset must change its cache-buster:\n  "
        + "\n  ".join(stale)
        + "\n\nrun: python scripts/site_asset_versions.py --bump"
    )


def test_the_bump_script_agrees_with_the_pages(pages) -> None:
    """The tool that fixes this must agree with the test that checks it."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "site_asset_versions.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "scripts/site_asset_versions.py reports stale cache-busters:\n"
        + result.stdout
        + result.stderr
    )
