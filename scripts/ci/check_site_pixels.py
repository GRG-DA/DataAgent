"""Scan the RENDERED site for colour that should not be there.

The palette is monochrome, and the guards check the source: the CSS tokens
are neutral, the literals are neutral, the glyphs are neutral. This checks
the thing that actually ships - the pixels - by screenshotting every page
in a real browser and measuring the saturation of what is drawn.

The four STATUS hues are allowed, and so is the small green "v1.0.0" dot,
so a page is not expected to be perfectly grey. What it must not be is
VIOLET, CYAN or any other brand hue: those are the colours the conversion
was about.

Usage:
    python scripts/ci/check_site_pixels.py
"""
from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SITE = REPO_ROOT / "website"

#: A pixel counts as coloured when its channels differ by more than this.
SATURATION_FLOOR = 0.22

#: ...and it must be BRIGHT ENOUGH to see.  A pixel of (0, 1, 1) is black:
#: its "hue" is a one-bit rounding artefact, and counting it would report
#: a violet page on a page that is pure black.  Nothing below this value is
#: visible against the near-black surface.
MINIMUM_BRIGHTNESS = 24

#: The hue bands that are NOT allowed.
#:
#: The palette keeps FOUR status hues - a sage green (hue ~130), a dusty
#: amber (~40), a brick red (~0) and a slate blue (~216).  They are
#: allowed, and each one is paired with a glyph, a shape or a label.
#:
#: What is NOT allowed is the old BRAND palette: the violet (~258), the
#: rose (~329) and the cyan (~188).  Nothing in the palette is violet,
#: rose or cyan, so a pixel in those bands is a regression - and a hue
#: that is not part of the palette reads as an oversight rather than as
#: an accent.
FORBIDDEN_HUES = (
    (245, 300, "violet"),   # the old brand hue
    (300, 345, "rose"),     # the old rare-emphasis hue
    (175, 205, "cyan"),     # the old accent hue
)


def main() -> int:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    pages = sorted(SITE.glob("*.html"))
    findings: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for path in pages:
            page.goto((path).as_uri())
            page.wait_for_timeout(2600)
            shot = path.parent.parent / "outputs" / f"pixel-{path.stem}.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot))
            image = Image.open(shot).convert("RGB")
            pixels = image.load()
            counts = {name: 0 for _, _, name in FORBIDDEN_HUES}
            total = 0
            for y in range(0, image.height, 3):
                for x in range(0, image.width, 3):
                    r, g, b = pixels[x, y]
                    total += 1
                    high, low = max(r, g, b), min(r, g, b)
                    if high < MINIMUM_BRIGHTNESS:
                        continue  # too dark to see: not colour, just rounding
                    if high == 0 or (high - low) / high < SATURATION_FLOOR:
                        continue
                    hue = 0.0
                    if high == r:
                        hue = (60 * ((g - b) / (high - low))) % 360
                    elif high == g:
                        hue = 60 * ((b - r) / (high - low)) + 120
                    else:
                        hue = 60 * ((r - g) / (high - low)) + 240
                    for low_h, high_h, name in FORBIDDEN_HUES:
                        if low_h <= hue <= high_h:
                            counts[name] += 1
                            break
            for name, count in counts.items():
                share = count / total * 100
                if share > 0.35:
                    findings.append(
                        f"{path.name}: {share:.2f}% of the sampled pixels are {name}"
                    )
            print(f"  {path.name:<22} {counts}")
        browser.close()

    if findings:
        print("\nFAILED - colour that is not part of the palette:")
        for line in findings:
            print("  ", line)
        return 1
    print("\nOK: no violet, no rose and no cyan on any page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
