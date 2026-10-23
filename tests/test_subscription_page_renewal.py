"""RED tests: subscription page renders renewal state + manage link (Phase 4).

Static checks on web/js/pages/subscription.js: the page consumes
sub.renewal from GET /subscriptions/me, renders an auto-renew / cancels-on
line with a last-checked stamp, and keeps the trial + legacy product-key
blocks intact.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "js" / "pages" / "subscription.js"


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_consumes_renewal_payload():
    assert "sub.renewal" in _src(), "subscription page must read sub.renewal"


def test_page_renders_renewal_and_cancel_lines():
    src = _src()
    assert "Auto-renews on" in src
    assert "Cancels on" in src
    assert "renewal-status-line" in src


def test_page_shows_last_checked_and_manage_link():
    src = _src()
    assert "Last checked" in src
    assert "Manage on the billing site" in src
    assert "manage-subscription" in src


def test_page_keeps_trial_and_key_activation():
    src = _src()
    assert "product-key-input" in src
    assert "trial-start-btn" in src


# ── one-time windows (ADR-016: FamGateway 31-day Pro) ────────────────


def test_page_renders_expiry_for_one_time_window():
    """renews_at is None for one-time windows; the expiry line must say
    what happens instead of hiding the block entirely."""
    src = _src()
    assert "expires_at" in src
    assert "renews_at" in src
    assert "Renew by paying again" in src
