"""Frontend contract for the account-lifecycle screens.

Source-contract checks (the same pattern as tests/test_chat_pro_ui.py) that
guard the pieces of the account UI which have no other automated coverage:

  1. Every account-lifecycle endpoint has a method in ``web/js/api.js``.
  2. No account secret is ever put in a URL query string.
  3. The token-carrying views are loaded by ``web/index.html``.
  4. The legal pages are reachable from the footer.
  5. The workspace chip sits next to the role badge in the user menu.
  6. The mobile header cannot overlap the notification bell.

These are cheap guards, not a substitute for the browser check.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
API_JS = WEB / "js" / "api.js"
INDEX_HTML = WEB / "index.html"
STYLES_CSS = WEB / "css" / "styles.css"
FRAGMENTS_JS = WEB / "js" / "account" / "fragments.js"

#: Every endpoint the account screens talk to, with the HTTP verb and the
#: path as it has to appear in ``web/js/api.js`` (the per-user routes are
#: written as template literals).
ACCOUNT_ENDPOINTS = (
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("POST", "/auth/verify-email"),
    ("POST", "/auth/resend-verification"),
    ("POST", "/auth/invite"),
    ("POST", "/auth/accept-invite"),
    ("POST", "/auth/delete-account"),
    ("GET", "/auth/me"),
    ("GET", "/auth/users"),
    ("PATCH", "/auth/users/${encodeURIComponent(username)}"),
    ("DELETE", "/auth/users/${encodeURIComponent(username)}"),
)

#: Files that may hold a secret in the URL. The token always travels in the
#: fragment, which the browser never sends to the server.
TEXT_SUFFIXES = (".js", ".html", ".css", ".json", ".mjs")


def _web_files():
    for path in WEB.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def test_account_endpoints_are_wired_in_the_api_client():
    src = API_JS.read_text(encoding="utf-8")
    missing = [path for _verb, path in ACCOUNT_ENDPOINTS if path not in src]
    assert not missing, (
        "web/js/api.js is missing a method for: " + ", ".join(missing)
    )


def test_no_account_token_is_ever_put_in_a_query_string():
    """A query string travels to the server and lands in the access log."""
    offenders = []
    for path in _web_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "?token=" in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, (
        "a token must travel in the URL fragment, never in a query string — "
        "offenders: " + ", ".join(offenders)
    )


def test_account_views_are_loaded_by_the_page():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for module in (
        "js/account/fragments.js",
        "js/account/views.js",
        "js/account/forms.js",
        "js/account/delete.js",
        "js/account.js",
    ):
        assert module in html, f"{module} is not loaded by web/index.html"


def test_token_is_cleared_from_the_address_bar():
    src = FRAGMENTS_JS.read_text(encoding="utf-8")
    assert "replaceState" in src, (
        "the token must be dropped from the address bar with "
        "history.replaceState once it has been read"
    )


def test_legal_pages_are_linked_from_the_footer():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "app-footer" in html, "the footer element is missing from web/index.html"
    for page in ("privacy", "terms", "dpa", "security"):
        assert f"legal/{page}.html" in html, (
            f"the footer must link the {page} legal page"
        )
        assert (WEB / "legal" / f"{page}.html").is_file(), (
            f"web/legal/{page}.html is missing"
        )


def test_workspace_chip_sits_next_to_the_role_badge():
    html = INDEX_HTML.read_text(encoding="utf-8")
    role = re.search(r'<span id="ud-role"[^>]*>', html)
    tenant = re.search(r'<span id="ud-tenant"[^>]*>', html)
    assert role and tenant, "the user menu must carry a role badge and a workspace chip"
    # The tenant chip has to live inside the same flex row as the role badge,
    # otherwise it drops to a line of its own instead of sitting next to it.
    row_start = html.rfind("<div", 0, role.start())
    row_end = html.find("</div>", role.end())
    assert row_start != -1 and row_end != -1, "the user-menu badge row is missing"
    assert role.start() < tenant.start() < row_end, (
        "the workspace chip must be a sibling of the role badge in the user menu"
    )


def test_mobile_header_cannot_overlap_the_icon_cluster():
    css = STYLES_CSS.read_text(encoding="utf-8")
    block = css[css.find("@media (max-width: 768px)"):]
    assert "#topnav-nav" in block and "overflow-x: auto" in block, (
        "on a phone the navigation must scroll instead of spilling over the "
        "notification bell and the Settings icon"
    )
