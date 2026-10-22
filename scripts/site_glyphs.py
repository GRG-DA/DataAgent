"""Replace the site's pictographic emoji with real SVG icons.

The site is monochrome. A colour emoji is a hue on a black-and-white page,
and a hue that is not part of the palette reads as an oversight rather
than as an accent. So the emoji - which are the site's icon language -
become SVG line glyphs: crisp at any size, coloured by ``currentColor``,
and drawn in the same 1.6px stroke as the rest of the design.

Every glyph is a 24x24 line icon. The stroke, the fill and the size all
come from the ``.glyph`` class in css/style.css, so the markup stays
small and the set can be restyled in one place.

Usage:
    python scripts/site_glyphs.py          # report what would change
    python scripts/site_glyphs.py --apply  # rewrite the pages
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"

#: The icon set.  24x24, line, stroke-width 1.6, round caps and joins.
ICONS = {
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18"/>',
    "graduation": '<path d="M2 9 12 4l10 5-10 5L2 9Z"/><path d="M6 11v5c0 1.5 3 3 6 3s6-1.5 6-3v-5"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/>',
    "health": '<path d="M3 12h4l2-5 3 10 2.5-7 1.5 2h5"/>',
    "package": '<path d="M12 3 3 7.5v9L12 21l9-4.5v-9L12 3Z"/><path d="m3 7.5 9 4.5 9-4.5M12 12v9"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2 20c0-3.5 3-5.5 7-5.5s7 2 7 5.5"/><path d="M16 5.2a3.5 3.5 0 0 1 0 6.6M17 14.8c3 .6 5 2.4 5 5.2"/>',
    "bulb": '<path d="M9.5 18h5M10.5 21h3M12 3a6 6 0 0 1 3.5 10.9V16h-7v-2.1A6 6 0 0 1 12 3Z"/>',
    "chat": '<path d="M21 11.5a8 8 0 0 1-8 8H8l-4 2.5v-4.8A8 8 0 0 1 13 4h.5a8 8 0 0 1 7.5 7.5Z"/>',
    "coin": '<circle cx="12" cy="12" r="9"/><path d="M15 9.6c0-1.3-1.3-2.3-3-2.3s-3 1-3 2.3 1.3 2.1 3 2.4 3 1 3 2.4-1.3 2.3-3 2.3-3-1-3-2.3M12 5.5v13"/>',
    "laptop": '<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5"/>',
    "chart-line": '<path d="M4 4v16h16"/><path d="m7 15 3.5-4 3 2.5L19 7"/>',
    "chart-bar": '<path d="M4 4v16h16"/><path d="M8 20v-6M12.5 20V8M17 20v-10"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 9.5h18M3 15h18M9 9.5V20M15 9.5V20"/>',
    "book": '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 0-3 3V4Z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>',
    "library": '<rect x="3" y="4" width="4" height="16" rx="1"/><rect x="8.5" y="4" width="4" height="16" rx="1"/><path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4Z"/>',
    "memo": '<path d="M6 3h9l4 4v14H6Z"/><path d="M15 3v4h4"/><path d="M9 12h7M9 16h5"/>',
    "export": '<path d="M12 15V3M8 7l4-4 4 4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "download": '<path d="M12 3v12M8 11l4 4 4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "presentation": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M12 16v4M8.5 20h7"/>',
    "camera": '<path d="M4 8h3l1.5-2.5h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z"/><circle cx="12" cy="13" r="3.5"/>',
    "plug": '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0V8ZM12 16v5"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
    "lock-key": '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15.5" r="1.4"/><path d="M12 17v2"/>',
    "key": '<circle cx="8" cy="12" r="4"/><path d="M12 12h9M18 12v3.5M15 12v2.5"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "link": '<path d="M10 14a4 4 0 0 0 6 .5l2-2a4 4 0 0 0-6-6l-1 1"/><path d="M14 10a4 4 0 0 0-6-.5l-2 2a4 4 0 0 0 6 6l1-1"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M9 20h6M12 16v4"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 10 3 2.5L7 15M13 15h4"/>',
    "layout": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M9.5 9.5V20"/>',
    "database": '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6"/><path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
    "pipeline": '<path d="M4 6h5M15 6h5M12 6v0"/><circle cx="3.5" cy="6" r="1.5"/><circle cx="20.5" cy="6" r="1.5"/><path d="M12 8v4a3 3 0 0 0 3 3h3"/><circle cx="20.5" cy="15" r="1.5"/><path d="M12 8v3a3 3 0 0 1-3 3H6"/><circle cx="3.5" cy="14" r="1.5"/>',
    "shield": '<path d="M12 3 4 6v6c0 4.5 3.4 7.6 8 9 4.6-1.4 8-4.5 8-9V6l-8-3Z"/>',
    "robot": '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9.5 13h.01M14.5 13h.01M9.5 16.5h5"/><circle cx="12" cy="3" r="1.2"/>',
    "id-card": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 16c0-1.7 1.3-2.6 3-2.6s3 .9 3 2.6M13.5 10h4.5M13.5 13.5h3"/>',
    "scale": '<path d="M12 4v16M8 20h8M12 6 5 9M12 6l7 3"/><path d="M5 9 2.5 15a3.5 3.5 0 0 0 5 0L5 9ZM19 9l-2.5 6a3.5 3.5 0 0 0 5 0L19 9Z"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.6 2.6 0 0 1 5 .6c0 1.7-2.5 2-2.5 3.9"/><path d="M12 17.5h.01"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
}

#: The emoji the site used, and the glyph each one becomes.
EMOJI = {
    0x26A1: "bolt",          # Multi-Provider AI
    0x2709: "mail",          # Contact / Send Message / Email
    0x1F310: "globe",        # Local LLM Support / Local-first & Private
    0x1F393: "graduation",   # Education & Research / Open by default
    0x1F3AF: "target",       # Marketing & Growth
    0x1F3E7: "health",       # Healthcare & Life Science
    0x1F457: "package",      # Windows .exe
    0x1F464: "user",         # Row-Level Security
    0x1F465: "users",        # Multi-Provider AI / SSO + RBAC
    0x1F4A1: "bulb",         # Smart Suggestions
    0x1F4AC: "chat",         # Natural Language Chat
    0x1F4B0: "coin",         # Pricing / Free tier
    0x1F4BB: "laptop",       # Install
    0x1F4C4: "file",         # Power BI Export
    0x1F4C8: "chart-line",   # Automated Insights / Dashboards
    0x1F4CA: "chart-bar",    # Interactive Dashboards / 30+ Chart Types
    0x1F4CB: "table",        # Excel Export / 30+ Chart Types
    0x1F4D6: "book",         # Documentation
    0x1F4DA: "library",      # Data Catalog
    0x1F4DD: "memo",         # Audit Logging / Audit logs
    0x1F4E3: "export",       # PDF Report Export
    0x1F4E5: "download",     # CSV/Excel Upload / Exports
    0x1F4E7: "mail",         # Email
    0x1F4EE: "presentation",  # PowerPoint Export
    0x1F4F7: "camera",       # Chart Screenshot
    0x1F50C: "plug",         # Database Connectors / Show the work
    0x1F50D: "search",       # SQL Explorer / Safe SQL by construction
    0x1F510: "lock-key",     # Role-Based Access
    0x1F511: "key",          # API Key Management
    0x1F512: "lock",         # Data Stays On Your Machine / SSO
    0x1F517: "link",         # Data Lineage
    0x1F5A5: "monitor",      # Local / Self-Hosted Install
    0x1F5A8: "terminal",     # Raw SQL Editor / SQL Generation
    0x1F5BC: "layout",       # Dashboard Builder
    0x1F5C3: "database",     # Data Management / Connect data
    0x1F6E0: "pipeline",     # ETL Pipelines / Contribute
    0x1F6E1: "shield",       # Self-Hosted / Enterprise & Security
    0x1F916: "robot",        # AI Chat Interface / AI & Analytics
    0x1F935: "id-card",      # Auth & sessions
    0x1F9E0: "scale",        # Public Sector & Regulated
    0x2753: "help",         # FAQ - the only other codepoint with a colour
                              # presentation by default
    0x2699: "gear",         # the settings mark
}

#: U+FE0F, the VARIATION SELECTOR-16, forces the emoji presentation of the
#: character before it.  With a real SVG there is nothing to force.
VARIATION_SELECTOR = 0xFE0F

ENTITY_RE = re.compile(r"&#(\d{4,7});")


def svg_for(name: str) -> str:
    return (
        f'<svg class="glyph" viewBox="0 0 24 24" aria-hidden="true" '
        f'focusable="false">{ICONS[name]}</svg>'
    )


def convert(text: str) -> tuple[str, int]:
    """Replace every pictographic entity with its SVG glyph."""
    count = 0

    def _replace(match: re.Match) -> str:
        nonlocal count
        code = int(match.group(1))
        if code == VARIATION_SELECTOR:
            return ""
        name = EMOJI.get(code)
        if name is None:
            return match.group(0)
        count += 1
        return svg_for(name)

    return ENTITY_RE.sub(_replace, text), count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite the pages")
    args = parser.parse_args()

    pages = sorted(SITE.glob("*.html"))
    total = 0
    changed = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        updated, count = convert(text)
        total += count
        if count:
            changed.append((page.name, count))
        if args.apply and updated != text:
            page.write_text(updated, encoding="utf-8")

    if args.apply:
        print(f"replaced {total} emoji with SVG glyphs across {len(changed)} page(s):")
        for name, count in changed:
            print(f"   {name:<22} {count}")
        return 0

    print(f"{total} emoji would become SVG glyphs across {len(changed)} page(s):")
    for name, count in changed:
        print(f"   {name:<22} {count}")
    print("\nrun: python scripts/site_glyphs.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
