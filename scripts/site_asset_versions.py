"""Content-addressed cache-busters for the marketing site.

A stylesheet that changes without its cache-buster changing never reaches
a returning visitor: the file on disk is new, the URL is not, and the
browser serves the OLD file from its cache.  That is how a redesign ships
to nobody.

So the cache-buster IS the content hash.  ``?v=`` is the first eight hex
characters of the asset's SHA-256.  Two consequences:

* there is no number to remember to bump;
* a test can recompute the hash and know the truth without a manifest to
  keep in sync.

Usage:
    python scripts/site_asset_versions.py          # report what is stale
    python scripts/site_asset_versions.py --bump   # rewrite the ?v= in the pages
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"

#: The asset kinds whose bytes are cached hard by the browser.
BUMPED_SUFFIXES = (".css", ".js")

#: A local asset reference in an href/src/poster attribute.
_ASSET_RE = re.compile(
    r"(?P<attr>\b(?:href|src|poster)\s*=\s*[\"'])(?P<path>[^\"'>]+)(?P<tail>[\"'])"
)
_VERSION_RE = re.compile(r"([?&])v=[0-9a-fA-F]+")


def _is_local_asset(path: str) -> bool:
    if not path or path.startswith(("http://", "https://", "//", "#", "data:", "mailto:")):
        return False
    return path.split("?", 1)[0].lower().endswith(BUMPED_SUFFIXES)


def asset_version(path: pathlib.Path) -> str:
    """The cache-buster for an asset: the first eight hex of its SHA-256."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:8]


def _rewrite(text: str, versions: dict[str, str]) -> str:
    def _replace(match: re.Match) -> str:
        raw = match.group("path")
        if not _is_local_asset(raw):
            return match.group(0)
        relative = raw.split("?", 1)[0].lstrip("/")
        version = versions.get(relative)
        if version is None:
            return match.group(0)
        bare = re.sub(r"[?&]v=[0-9a-fA-F]+", "", raw)
        separator = "&" if "?" in bare else "?"
        return f"{match.group('attr')}{bare}{separator}v={version}{match.group('tail')}"

    return _ASSET_RE.sub(_replace, text)


def _collect(pages: list[pathlib.Path]) -> dict[str, str]:
    """Every local asset the pages reference, and its content hash."""
    assets: dict[str, str] = {}
    for page in pages:
        for match in _ASSET_RE.finditer(page.read_text(encoding="utf-8")):
            raw = match.group("path")
            if not _is_local_asset(raw):
                continue
            relative = raw.split("?", 1)[0].lstrip("/")
            asset = SITE / relative
            if asset.is_file():
                assets[relative] = asset_version(asset)
    return assets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bump", action="store_true", help="rewrite the ?v= in the pages")
    args = parser.parse_args()

    pages = sorted(SITE.glob("*.html"))
    versions = _collect(pages)

    stale: list[str] = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for match in _ASSET_RE.finditer(text):
            raw = match.group("path")
            if not _is_local_asset(raw):
                continue
            relative = raw.split("?", 1)[0].lstrip("/")
            expected = versions.get(relative)
            if expected is None:
                continue
            found = re.search(r"[?&]v=([0-9a-fA-F]+)", raw)
            if not found or found.group(1) != expected:
                stale.append(f"{page.name}: {relative} -> {found.group(1) if found else 'NONE'} (want {expected})")

    if args.bump:
        for page in pages:
            text = page.read_text(encoding="utf-8")
            updated = _rewrite(text, versions)
            if updated != text:
                page.write_text(updated, encoding="utf-8")
        print(f"bumped {len(versions)} asset reference(s) across {len(pages)} page(s)")
        return 0

    if stale:
        print(f"{len(stale)} stale cache-buster(s):")
        for line in stale:
            print("  ", line)
        print("\nrun: python scripts/site_asset_versions.py --bump")
        return 1
    print(f"all cache-busters are current ({len(versions)} asset(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
