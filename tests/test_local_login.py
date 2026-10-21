"""Local username login + native Google OAuth + central accounts — TDD guard.

Covers the user request:
1. SPA must offer username/password login (not Google-only), and
   email/password against the billing server when central accounts are
   configured.
2. Google sign-in uses the native OAuth popup flow — Firebase is fully gone.
3. Backend public auth paths cover local + central account endpoints.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB_AUTH = ROOT / "web" / "js" / "auth.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_auth_js_has_local_login_form():
    src = _read(WEB_AUTH)
    assert "auth-username" in src
    assert "auth-password" in src
    assert "/auth/login" in src
    assert "/auth/register" in src


def test_auth_js_persists_local_session():
    src = _read(WEB_AUTH)
    assert "RBAC.setToken" in src
    assert "RBAC.setUser" in src


def test_auth_js_has_native_google_flow():
    """Google sign-in goes through /auth/google/login — no Firebase SDK."""
    src = _read(WEB_AUTH)
    assert "/auth/google/login" in src
    assert "/auth/google/login/result" in src
    assert "postMessage" in src
    for sdk_symbol in ("firebase.initializeApp", "firebase.auth",
                       "FIREBASE_CONFIG", "firebase/app"):
        assert sdk_symbol not in src, f"Firebase SDK symbol leaked back: {sdk_symbol}"


def test_auth_js_has_central_accounts_mode():
    src = _read(WEB_AUTH)
    assert "/auth/accounts/login" in src
    assert "/auth/accounts/register" in src
    assert "/auth/accounts/status" in src


def test_auth_js_does_not_fake_admin_without_token():
    """Local mode must show login, not inject a token-less admin."""
    src = _read(WEB_AUTH)
    assert "_showOverlay" in src
    # Old behavior faked a local admin and returned true with no token.
    assert 'username: "local"' not in src


def test_firebase_fully_removed():
    """No Firebase module, SDK, config, or dependency may come back."""
    assert not (ROOT / "api" / "firebase_auth.py").exists()
    assert not (ROOT / "api" / "billing" / "firestore_store.py").exists()
    assert not (ROOT / "web" / "firebase-config.js").exists()
    assert not (ROOT / "web" / "vendor" / "firebase-app-compat.js").exists()
    assert not (ROOT / "web" / "vendor" / "firebase-auth-compat.js").exists()
    reqs = _read(ROOT / "requirements.txt")
    assert "firebase-admin" not in reqs
    from config import settings

    assert not hasattr(settings, "firebase")


def test_backend_public_paths_include_local_auth():
    from api.middleware.auth_middleware import AuthMiddleware

    mw = AuthMiddleware(app=None)
    for path in ("/auth/login", "/auth/register", "/auth/verify",
                 "/auth/google/login", "/auth/google/login/result",
                 "/auth/accounts/login", "/auth/accounts/register",
                 "/auth/accounts/status"):
        assert mw._is_public_path(path), f"{path} should remain public"


def test_register_rejects_weak_password():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/auth/register",
                        json={"username": "weak_pw_probe", "password": "short"})
        assert r.status_code == 200
        assert r.json().get("success") is False


def test_login_rejects_unknown_user():
    from fastapi.testclient import TestClient

    from api.main import app
    from api.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/auth/login",
                        json={"username": "no_such_user_xyz", "password": "WrongPass123!@#"})
        assert r.status_code == 401
