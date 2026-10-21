"""Regression tests for SPA route-race, audit auth, and upgrade-toast fixes.

Found during UI screenshot verification (2026-09-24):
1. Concurrent _handleRoute renders let a slow page overwrite the next page
   (#notifications showed Settings content).
2. web/js/pages/audit.js used bare fetch() without Authorization → 401.
3. Free-plan 403s stacked one error toast per API call (no overlay path).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "js"


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Route generation token abandons stale async route renders
# ---------------------------------------------------------------------------

def test_app_js_has_route_generation_token():
    src = _read("app.js")
    assert "let _routeToken" in src
    assert "const routeToken = ++_routeToken" in src
    assert "const _stale = () => routeToken !== _routeToken" in src
    # Stale checks must guard the async continuation points.
    assert src.count("if (_stale()) return;") >= 4
    # After a lost render race, re-run the current hash so the DOM matches.
    assert "if (_stale())" in src
    assert "_handleRoute();" in src


def test_app_js_resets_loading_on_route_change():
    src = _read("app.js")
    assert "API.resetLoading" in src


def test_api_client_reset_loading_and_epoch():
    src = _read("api.js")
    assert "function _resetLoading" in src
    assert "resetLoading:" in src
    assert "_loadEpoch" in src
    # Orphaned completions from a previous route must not hide the overlay.
    assert "epoch !== _loadEpoch" in src
    assert "_hideLoading(epoch)" in src


def test_settings_background_loads_are_silent():
    """Slow settings panel fetches must not hold the global loading overlay."""
    src = _read("api.js")
    assert 'listSchedules: () => GET("/schedule/list", { silent: true })' in src
    assert 'listAlerts: () => GET("/alerts/list", { silent: true })' in src
    assert 'llmDependencies: () => GET("/llm/dependencies", { silent: true })' in src
    assert 'llmProviders: () => GET("/llm/providers", { silent: true })' in src
    assert 'systemHealth: () => GET("/health/system", { silent: true })' in src
    assert 'listApiKeys: () => GET("/auth/api-keys", { silent: true })' in src
    # Background polls queued behind slow settings fetches (browser connection
    # limit) must not hold the overlay either.
    assert 'health: () => GET("/health", { silent: true })' in src
    assert 'getUnreadCount: () => GET("/notifications/unread/count", { silent: true })' in src


def test_app_health_check_is_silent():
    """_checkHealth is a background status poll; it must not drive #loading-overlay."""
    src = _read("app.js")
    assert 'window.API.get("/health", { silent: true })' in src


def test_app_js_route_gate_keeps_overlay_and_redirects():
    src = _read("app.js")
    assert "keepUpgradeOverlay" in src
    assert 'window.location.hash !== "#dashboard"' in src
    # Stale overlay dismissed unless the keep is still valid (wall-clock
    # window covers cold-boot double renders; one-shot data-keep covers the
    # redirect's own render).
    assert "upgradeOverlayKeepValid" in src
    assert "clearUpgradeOverlayKeep" in src
    rsrc = _read("components/rbac.js")
    assert "data-keep-until" in rsrc
    assert "upgradeOverlayKeepValid" in rsrc


# ---------------------------------------------------------------------------
# 2. Audit page must authenticate raw fetch() calls
# ---------------------------------------------------------------------------

def test_audit_page_sends_bearer_token():
    src = _read("pages/audit.js")
    assert 'Authorization" ] = "Bearer "' in src or 'Authorization"] = "Bearer "' in src
    assert "RBAC.getToken" in src
    # Both list and export paths must attach the header.
    assert src.count("Bearer ") >= 2
    # Absolute API base so path is correct when SPA is on another origin.
    assert "__API_BASE__" in src
    # Must not use bare fetch without building headers.
    assert "await fetch(url);" not in src
    assert "await fetch((window.__API_BASE__" in src


# ---------------------------------------------------------------------------
# 3. Upgrade-required 403s open the overlay once, never toast-spam
# ---------------------------------------------------------------------------

def test_api_client_marks_upgrade_required_errors():
    src = _read("api.js")
    assert 'X-Upgrade-Required' in src
    assert "error.upgradeRequired = true" in src
    assert "error.feature" in src
    assert "showUpgradeOverlay" in src


def test_api_client_skips_toast_for_upgrade_required():
    src = _read("api.js")
    assert "const isUpgrade = err && err.upgradeRequired" in src
    assert "if (!quiet && !isUpgrade" in src


def test_api_client_syncs_entitlements_on_upgrade_403():
    """Client must drop the false-positive feature flag after a server 403."""
    src = _read("api.js")
    assert "setEntitlements" in src
    assert "getEntitlements" in src
    assert "if (error.feature in ent) ent[error.feature] = false" in src


# ---------------------------------------------------------------------------
# 4. RBAC overlay helpers used by the gate + API layer
# ---------------------------------------------------------------------------

def test_rbac_exports_overlay_keep_helpers():
    src = _read("components/rbac.js")
    assert "function keepUpgradeOverlay" in src
    assert "function clearUpgradeOverlayKeep" in src
    assert "function dismissUpgradeOverlay" in src
    assert "keepUpgradeOverlay" in src.split("window.RBAC =")[-1]
    assert "clearUpgradeOverlayKeep" in src.split("window.RBAC =")[-1]


def test_rbac_show_upgrade_overlay_dismisses_previous():
    src = _read("components/rbac.js")
    # showUpgradeOverlay must not stack multiple overlays.
    body = src.split("function showUpgradeOverlay", 1)[1]
    assert body.startswith("(feature)") or "(feature)" in body[:40]
    assert "dismissUpgradeOverlay();" in body[:400]
