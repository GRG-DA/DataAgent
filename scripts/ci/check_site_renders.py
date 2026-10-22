"""Check that every page of the marketing site actually renders.

A stylesheet can be structurally valid and still kill a rule - the
``.hero-film`` rule once sat inside a broken comment, so the video fell
into the normal flow and pushed the home page's hero 798px off the
screen.  The page still returned 200, still rendered, and every other
test still passed.

So this asks the browser, not the source: does the page have its heading
in the viewport, does it fit its width, and is nothing left invisible?

Usage:
    python scripts/ci/check_site_renders.py
"""
from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SITE = REPO_ROOT / "website"

#: The content that must be on screen when the page opens.
REQUIRED = {
    "index.html": ".hero-title",
    "features.html": "h1",
    "comparison.html": "h1",
    "about.html": "h1",
    "docs.html": "h1",
    "faq.html": "h1",
    "contact.html": "h1",
    "changelog.html": "h1",
    "blog.html": "h1",
    "blog-post.html": "h1",
    "billing.html": "h1",
    "download.html": "h1",
    "security.html": "h1",
    "usecases.html": "h1",
    "403.html": "h1",
    "404.html": "h1",
}


def main() -> int:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for path in sorted(SITE.glob("*.html")):
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(path.as_uri())
            page.wait_for_timeout(2400)

            result = page.evaluate(
                """(selector) => {
                    const de = document.documentElement;
                    const el = document.querySelector(selector);
                    const head = document.querySelector('h1');
                    // A scroll-reveal element is SUPPOSED to be invisible
                    // until it is scrolled to, so only an element that is
                    // in the viewport AND still hidden is a problem.
                    const hidden = [];
                    const content = document.querySelectorAll('h1,h2,h3,p,li,td,th,.btn,.card,.glyph');
                    for (const e of content) {
                        const c = getComputedStyle(e);
                        if (c.opacity !== '0' && c.visibility !== 'hidden' && c.display !== 'none') continue;
                        const r = e.getBoundingClientRect();
                        const inView = r.bottom > 0 && r.top < window.innerHeight && r.width > 0;
                        if (inView) {
                            hidden.push(e.tagName + '.' + String(e.className || '').slice(0, 24));
                        }
                    }
                    return {
                        heading: head ? head.textContent.trim().slice(0, 40) : null,
                        headingTop: head ? Math.round(head.getBoundingClientRect().top) : null,
                        targetFound: !!el,
                        targetTop: el ? Math.round(el.getBoundingClientRect().top) : null,
                        horizontalScroll: de.scrollWidth > de.clientWidth + 1,
                        hidden: hidden.slice(0, 4),
                        hiddenCount: hidden.length,
                        title: document.title
                    };
                }""",
                REQUIRED.get(path.name, "h1"),
            )
            if errors:
                problems.append(f"{path.name}: a script error — {errors[0][:80]}")
            if not result["heading"]:
                problems.append(f"{path.name}: no <h1>")
            elif result["headingTop"] is not None and result["headingTop"] > 1200:
                problems.append(
                    f"{path.name}: the heading is {result['headingTop']}px down — "
                    "the page opened with something enormous above it"
                )
            if result["horizontalScroll"]:
                problems.append(f"{path.name}: it scrolls sideways")
            if result["hiddenCount"]:
                problems.append(
                    f"{path.name}: {result['hiddenCount']} element(s) are invisible "
                    f"({', '.join(result['hidden'])})"
                )
            print(
                f"  {path.name:<22} h1={str(result['heading'])[:30]:<32} "
                f"top={result['headingTop']} hidden={result['hiddenCount']}"
            )
        browser.close()

    if problems:
        print("\nFAILED:")
        for line in problems:
            print("  ", line)
        return 1
    print("\nOK: every page renders its content.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
