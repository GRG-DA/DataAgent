"""Render website/og-image.png from scripts/site_og_card.html.

The social card is what every shared link carries, so it has to match the
page it represents.  Rendering it in a real browser, with the site's own
vendored fonts and its own palette, is the only way to guarantee that -
a card drawn by hand drifts from the site the first time either changes.

The card itself is monochrome, like the site: every value is a true
neutral and the only colours on the page are the four status hues, which
do not appear on the card.

Usage:
    python scripts/site_og_card.py
"""
from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "scripts" / "site_og_card.html"
TARGET = REPO_ROOT / "website" / "og-image.png"

WIDTH, HEIGHT = 1200, 630


def render() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.goto(SOURCE.as_uri())
        page.wait_for_timeout(600)  # let the vendored fonts settle
        page.screenshot(path=str(TARGET), type="png")
        browser.close()

    _compress()


def _compress() -> None:
    """The card is monochrome, so store it as a greyscale PNG.

    A third of the size, and it is a smaller download for the one image
    every social preview fetches.
    """
    from PIL import Image

    image = Image.open(TARGET).convert("L")
    image.save(TARGET, format="PNG", optimize=True)


def main() -> int:
    if not SOURCE.is_file():
        print(f"missing the card source: {SOURCE}")
        return 1
    render()
    size_kb = TARGET.stat().st_size / 1024
    print(f"wrote {TARGET.relative_to(REPO_ROOT)} ({WIDTH}x{HEIGHT}, {size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
