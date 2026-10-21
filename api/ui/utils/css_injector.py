"""CSS injection helpers for the UI design system."""

import re

_DESIGN_SYSTEM_CSS = "/* dataagent design system */:root{color-scheme:light dark}"

_HEAD_RE = re.compile(r"</head\s*>", re.IGNORECASE)


def _insert_style(html: str, css: str) -> str:
    """Insert ``<style>{css}</style>`` before ``</head>`` or prepend."""
    tag = f"<style>{css}</style>"
    match = _HEAD_RE.search(html)
    if match:
        return html[: match.start()] + tag + html[match.start():]
    return tag + html


def inject_design_system(html: str = "") -> str:
    """Inject design-system CSS into an HTML page."""
    if not html:
        return html
    return _insert_style(html, _DESIGN_SYSTEM_CSS)


def inject_page_css(html: str = "", css: str = "") -> str:
    """Inject page-specific CSS into an HTML page."""
    if not html or not css:
        return html
    return _insert_style(html, css)
