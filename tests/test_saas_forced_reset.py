"""Forced password reset semantics (admin reset route + login enforcement)."""
from __future__ import annotations

import pytest


@pytest.fixture
def admin_client():
    from fastapi.testclient import TestClient

    from api.main import app
    from tests.conftest import auth_headers

    with TestClient(app, headers=auth_headers("force_reset_admin", "Str0ng!Passw0rd",
                                               role="admin")) as client:
        yield client


def _make_user(username: str) -> None:
    from tests.conftest import ensure_user

    ensure_user(username, "Str0ng!Passw0rd", role="user")


def test_a_forced_reset_blocks_the_next_login(admin_client):
    """The account must not get a session until the password is changed."""
    _make_user("force_reset_user")
    r = admin_client.post("/auth/users/force_reset_user/password",
                          json={"password": "Reset!Passw0rd2026"})
    assert r.status_code == 200, r.text

    login = admin_client.post("/auth/login",
                              json={"username": "force_reset_user",
                                    "password": "Reset!Passw0rd2026"})
    assert login.status_code == 403, login.text
    assert login.json().get("code") == "must_change_password"
    assert "token" not in login.json()


def test_a_forced_reset_revokes_existing_sessions(admin_client):
    """A stolen session must not outlive the credential it was issued for."""
    _make_user("force_reset_sessions")
    before = admin_client.post("/auth/login",
                               json={"username": "force_reset_sessions",
                                     "password": "Str0ng!Passw0rd"})
    assert before.status_code == 200, before.text
    token = before.json().get("token")
    assert token

    r = admin_client.post("/auth/users/force_reset_sessions/password",
                          json={"password": "Reset!Passw0rd2026"})
    assert r.status_code == 200, r.text

    # The session opened with the old password must no longer verify.
    check = admin_client.get("/auth/verify",
                             headers={"Authorization": "Bearer " + token})
    assert check.status_code in (401, 403) or not check.json().get("authenticated"), check.text


def test_the_reset_enforces_the_password_policy(admin_client):
    """A weak replacement password is refused."""
    _make_user("force_reset_weak")
    r = admin_client.post("/auth/users/force_reset_weak/password",
                          json={"password": "short"})
    assert r.status_code == 400, r.text


def test_the_reset_reports_a_missing_account(admin_client):
    r = admin_client.post("/auth/users/nobody-here/password",
                          json={"password": "Reset!Passw0rd2026"})
    assert r.status_code == 404, r.text


def test_a_non_admin_cannot_reset_a_password(admin_client):
    from fastapi.testclient import TestClient

    from api.main import app
    from tests.conftest import auth_headers

    _make_user("force_reset_plain")
    with TestClient(app, headers=auth_headers("force_reset_plain", "Str0ng!Passw0rd",
                                               role="user")) as client:
        r = client.post("/auth/users/force_reset_plain/password",
                        json={"password": "Reset!Passw0rd2026"})
    assert r.status_code == 403, r.text
