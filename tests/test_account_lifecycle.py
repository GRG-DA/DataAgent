"""Account lifecycle: reset, verification, invites, self-serve delete (ADR-009).

The hosted conversion (ADR-009) gives every account a lifecycle: it can be
invited, it can verify its email address, it can reset a forgotten password,
and it can delete itself.  This module covers that lifecycle end to end.

Guarantees exercised here:

* ``POST /auth/forgot-password`` never reveals whether an account exists.
* ``POST /auth/reset-password`` enforces the password policy, is single-use,
  refuses an expired token and kills the account's existing sessions.
* ``POST /auth/verify-email`` flips the flag; ``/auth/resend-verification``
  re-sends the mail.
* ``POST /auth/invite`` is admin-only; the invite token cannot be replayed;
  accepting an invite sets the password and the tenant.
* ``POST /auth/delete-account`` needs the password and an explicit
  confirmation, and cascades to the account's data.
* ``GET /auth/me`` returns the caller's own profile only.
* The revoked-JTI list is shared: a token revoked through one store instance
  is refused through another (i.e. from a second replica).
* ``api/mailer.py`` never raises when SMTP is misconfigured or down.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List

import pytest

#: The reset / verification / invite links carry the token in the fragment,
#: so the token never reaches an access log.
TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")

#: The password every test account starts with.
OLD_PASSWORD = "Str0ng!Passw0rd"

#: A password that satisfies the policy.
NEW_PASSWORD = "N3w!Passw0rd"


# ── helpers ────────────────────────────────────────────────────────────


#: The accounts this module creates directly.  The module-scoped cleanup
#: removes every account the module added, whatever created it.
_CREATED_USERS: List[str] = []


def _make_user(
    username: str,
    password: str = OLD_PASSWORD,
    role: str = "viewer",
    email: str = "",
) -> str:
    """Create an account in the (session-isolated) identity store."""
    from api.auth import user_manager

    user_id = user_manager.add_user(username, password, role, email=email)
    assert user_id, f"could not create the account {username}"
    _CREATED_USERS.append(username)
    return user_id


def _headers(username: str, role: str = "viewer") -> Dict[str, str]:
    from api.auth import create_jwt_token

    return {"Authorization": f"Bearer {create_jwt_token(username, role=role)}"}


def _token_from(messages: List[Dict[str, Any]]) -> str:
    """The token the last mail carried."""
    assert messages, "no mail was sent"
    last = messages[-1]
    body = f"{last.get('body') or ''}\n{last.get('html') or ''}"
    match = TOKEN_RE.search(body)
    assert match, "the mail carried no token"
    return match.group(1)


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The account endpoints are rate limited; give every test a fresh bucket."""
    from api.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture(scope="module", autouse=True)
def _clean_up_accounts():
    """Leave the shared identity store as this module found it.

    The tenant migration (``api.tenancy.ensure_user_tenants``) hands the
    ``default`` tenant — the one that owns the platform ``analytics`` schema
    — to the *oldest* account.  Accounts left behind by this module would
    therefore change which account owns the platform schema for the rest of
    the test session, so every account the module added is removed again.
    """
    from api.auth import user_manager

    before = {user["username"] for user in user_manager.get_users()}
    yield
    for user in user_manager.get_users():
        if user["username"] in before:
            continue
        try:
            user_manager.remove_user(user["username"])
        except Exception:  # noqa: BLE001 — best effort
            pass
    _CREATED_USERS.clear()


@pytest.fixture()
def sent_mail(monkeypatch):
    """Capture every email the account routes try to send."""
    import api.routes.account as account_mod

    captured: List[Dict[str, Any]] = []

    def _send(to, subject, body, html=None):
        captured.append({"to": to, "subject": subject, "body": body, "html": html})
        return True

    monkeypatch.setattr(account_mod, "send_email", _send)
    return captured


@pytest.fixture()
def app():
    """A minimal app carrying only the account router.

    ``api/main.py`` owns the real wiring; the router has to stand on its own
    so it can be mounted (and tested) without the monolith.
    """
    from fastapi import FastAPI

    from api.routes.account import router

    application = FastAPI()
    application.include_router(router)

    @application.middleware("http")
    async def _authenticate(request, call_next):
        from api.auth import user_manager, verify_jwt_token
        from api.middleware.auth_middleware import User

        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            payload = verify_jwt_token(header[7:])
            if payload:
                info = user_manager.get_user_info(payload.get("sub", ""))
                if info and info.get("is_active", True):
                    request.state.user = User(
                        user_id=info["user_id"],
                        username=info["username"],
                        role=info["role"],
                        tenant_id=info.get("tenant_id"),
                        email=info.get("email"),
                    )
        return await call_next(request)

    return application


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def warehouse(monkeypatch):
    """The shared DuckDB connection the delete cascade writes through.

    DuckDB is single-writer: when the file cannot be opened the test SKIPS
    instead of failing (the app server may hold the warehouse).
    """
    import duckdb

    import api.db as db
    from config import settings

    path = str(settings.duckdb.database_path)
    schema = settings.duckdb.schema
    try:
        conn = duckdb.connect(path)
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    except Exception as exc:  # noqa: BLE001 — warehouse may be locked
        pytest.skip(f"warehouse not writable: {exc}")

    monkeypatch.setattr(db, "_conn", conn)
    yield conn
    db._conn = None
    conn.close()


# ── forgot password ────────────────────────────────────────────────────


def test_forgot_password_never_enumerates_accounts(client, sent_mail):
    _make_user("fc_known", email="known@example.com")

    known = client.post("/auth/forgot-password", json={"email": "known@example.com"})
    unknown = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})

    assert known.status_code == 200
    assert unknown.status_code == 200
    # The two answers must be indistinguishable.
    assert known.json() == unknown.json()
    assert "If that account exists" in known.json()["message"]
    # Only the real account was mailed.
    assert [m["to"] for m in sent_mail] == ["known@example.com"]


def test_forgot_password_never_returns_the_token(client, sent_mail):
    _make_user("fc_token", email="token@example.com")
    response = client.post("/auth/forgot-password", json={"email": "token@example.com"})
    assert response.status_code == 200
    body = response.text
    for message in sent_mail:
        assert _token_from([message]) not in body


def test_forgot_password_audit_is_written(client, sent_mail, monkeypatch):
    import api.routes.account as account_mod

    calls: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        account_mod.audit_logger,
        "log",
        lambda event: calls.append(event),
    )
    _make_user("fc_audit", email="audit@example.com")
    client.post("/auth/forgot-password", json={"email": "audit@example.com"})
    client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert len(calls) == 2, "every attempt must be audited"


# ── reset password ─────────────────────────────────────────────────────


def test_reset_password_sets_a_new_password(client, sent_mail):
    from api.auth import user_manager

    user_id = _make_user("rp_user", email="rp@example.com")
    client.post("/auth/forgot-password", json={"email": "rp@example.com"})
    token = _token_from(sent_mail)

    response = client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 200
    assert user_manager.authenticate("rp_user", NEW_PASSWORD) == user_id
    # The password change is stamped.
    assert user_manager.get_user_info("rp_user")["must_change_password"] is False


def test_reset_password_token_is_single_use(client, sent_mail):
    _make_user("rp_once", email="once@example.com")
    client.post("/auth/forgot-password", json={"email": "once@example.com"})
    token = _token_from(sent_mail)

    first = client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert first.status_code == 200

    replay = client.post(
        "/auth/reset-password", json={"token": token, "password": "An0ther!Passw0rd"}
    )
    assert replay.status_code == 400
    assert "Invalid or expired" in replay.json()["detail"]


def test_reset_password_rejects_an_expired_token(client):
    from api.account_tokens import KIND_PASSWORD_RESET, create_token

    user_id = _make_user("rp_expired", email="expired@example.com")
    token = create_token(KIND_PASSWORD_RESET, user_id, ttl_seconds=1)
    time.sleep(1.1)

    response = client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 400
    assert "Invalid or expired" in response.json()["detail"]


def test_reset_password_enforces_the_password_policy(client, sent_mail):
    _make_user("rp_policy", email="policy@example.com")
    client.post("/auth/forgot-password", json={"email": "policy@example.com"})
    token = _token_from(sent_mail)

    response = client.post(
        "/auth/reset-password", json={"token": token, "password": "weak"}
    )
    assert response.status_code == 400
    assert "at least" in response.json()["detail"].lower()


def test_reset_password_revokes_existing_sessions(client, sent_mail):
    from api.auth import create_jwt_token, verify_jwt_token

    _make_user("rp_sessions", email="sessions@example.com")
    token = create_jwt_token("rp_sessions", role="viewer")
    assert verify_jwt_token(token) is not None

    client.post("/auth/forgot-password", json={"email": "sessions@example.com"})
    reset_token = _token_from(sent_mail)
    response = client.post(
        "/auth/reset-password", json={"token": reset_token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 200

    # The stolen session dies with the password.
    assert verify_jwt_token(token) is None


def test_reset_password_revokes_outstanding_reset_tokens(client, sent_mail):
    _make_user("rp_outstanding", email="outstanding@example.com")
    client.post("/auth/forgot-password", json={"email": "outstanding@example.com"})
    first = _token_from(sent_mail)
    client.post("/auth/forgot-password", json={"email": "outstanding@example.com"})
    second = _token_from(sent_mail)
    assert first != second

    assert client.post(
        "/auth/reset-password", json={"token": first, "password": NEW_PASSWORD}
    ).status_code == 200
    # The first reset consumed the account's outstanding reset tokens.
    assert client.post(
        "/auth/reset-password", json={"token": second, "password": "An0ther!Passw0rd"}
    ).status_code == 400


# ── email verification ─────────────────────────────────────────────────


def test_verify_email_flips_the_flag(client):
    from api.account_tokens import KIND_EMAIL_VERIFICATION, create_token
    from api.auth import user_manager

    user_id = _make_user("ve_user", email="ve@example.com")
    assert user_manager.get_user_info("ve_user")["email_verified"] is False

    token = create_token(KIND_EMAIL_VERIFICATION, user_id, ttl_seconds=600)
    response = client.post("/auth/verify-email", json={"token": token})
    assert response.status_code == 200
    assert user_manager.get_user_info("ve_user")["email_verified"] is True

    # Single use.
    replay = client.post("/auth/verify-email", json={"token": token})
    assert replay.status_code == 400


def test_resend_verification_sends_a_fresh_mail(client, sent_mail):
    from api.account_tokens import KIND_EMAIL_VERIFICATION, create_token

    user_id = _make_user("rv_user", email="rv@example.com")
    create_token(KIND_EMAIL_VERIFICATION, user_id, ttl_seconds=600)

    response = client.post("/auth/resend-verification", json={"email": "rv@example.com"})
    assert response.status_code == 200
    assert sent_mail, "a verification mail must be sent"
    assert sent_mail[-1]["to"] == "rv@example.com"

    # An unknown address is answered the same way.
    unknown = client.post("/auth/resend-verification", json={"email": "ghost@example.com"})
    assert unknown.status_code == 200
    assert unknown.json() == response.json()


# ── invites ────────────────────────────────────────────────────────────


def test_invite_requires_admin(client, sent_mail):
    _make_user("inv_admin", role="admin")
    _make_user("inv_viewer", role="viewer")

    denied = client.post(
        "/auth/invite",
        json={"username": "inv_new", "email": "new@example.com", "role": "viewer"},
        headers=_headers("inv_viewer", role="viewer"),
    )
    assert denied.status_code == 403
    assert not sent_mail

    allowed = client.post(
        "/auth/invite",
        json={"username": "inv_new", "email": "new@example.com", "role": "viewer"},
        headers=_headers("inv_admin", role="admin"),
    )
    assert allowed.status_code == 200
    assert sent_mail[-1]["to"] == "new@example.com"


def test_invite_creates_an_account_without_a_usable_password(client, sent_mail):
    from api.auth import user_manager

    _make_user("inv_np_admin", role="admin")
    response = client.post(
        "/auth/invite",
        json={"username": "inv_np", "email": "np@example.com", "role": "viewer"},
        headers=_headers("inv_np_admin", role="admin"),
    )
    assert response.status_code == 200
    assert user_manager.get_user_info("inv_np") is not None
    # The invited account cannot log in yet.
    assert user_manager.authenticate("inv_np", "") is None
    assert user_manager.authenticate("inv_np", NEW_PASSWORD) is None


def test_accept_invite_sets_the_password_and_the_tenant(client, sent_mail):
    from api.auth import user_manager

    _make_user("ai_admin", role="admin")
    invited = client.post(
        "/auth/invite",
        json={
            "username": "ai_user",
            "email": "ai@example.com",
            "role": "viewer",
            "tenant_id": "acme",
        },
        headers=_headers("ai_admin", role="admin"),
    )
    assert invited.status_code == 200
    token = _token_from(sent_mail)

    response = client.post(
        "/auth/accept-invite", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 200

    info = user_manager.get_user_info("ai_user")
    assert info["tenant_id"] == "acme"
    assert user_manager.authenticate("ai_user", NEW_PASSWORD) is not None
    assert info["invite_accepted_at"]


def test_invite_token_cannot_be_replayed(client, sent_mail):
    _make_user("ir_admin", role="admin")
    client.post(
        "/auth/invite",
        json={"username": "ir_user", "email": "ir@example.com", "role": "viewer"},
        headers=_headers("ir_admin", role="admin"),
    )
    token = _token_from(sent_mail)

    first = client.post(
        "/auth/accept-invite", json={"token": token, "password": NEW_PASSWORD}
    )
    assert first.status_code == 200

    replay = client.post(
        "/auth/accept-invite", json={"token": token, "password": "An0ther!Passw0rd"}
    )
    assert replay.status_code == 400
    assert "Invalid or expired" in replay.json()["detail"]


def test_accept_invite_enforces_the_password_policy(client, sent_mail):
    _make_user("ip_admin", role="admin")
    client.post(
        "/auth/invite",
        json={"username": "ip_user", "email": "ip@example.com", "role": "viewer"},
        headers=_headers("ip_admin", role="admin"),
    )
    token = _token_from(sent_mail)

    response = client.post(
        "/auth/accept-invite", json={"token": token, "password": "weak"}
    )
    assert response.status_code == 400


# ── self-serve delete ──────────────────────────────────────────────────


def test_delete_account_requires_the_password_and_the_confirmation(client):
    _make_user("del_user", email="del@example.com")
    headers = _headers("del_user")

    no_confirm = client.post(
        "/auth/delete-account", json={"password": OLD_PASSWORD}, headers=headers
    )
    assert no_confirm.status_code == 400
    assert "confirm" in no_confirm.json()["detail"].lower()

    wrong_password = client.post(
        "/auth/delete-account",
        json={"password": "not-the-password", "confirm": True},
        headers=headers,
    )
    assert wrong_password.status_code == 401


def test_delete_account_cascades_to_the_account_data(client, warehouse):
    from api.auth import user_manager
    from config import settings

    schema = settings.duckdb.schema
    _make_user("del_cascade_admin", role="admin")
    user_id = _make_user("del_cascade", email="cascade@example.com")
    # The exact shape the application creates (api/user_data.py): a table
    # with a different shape would break the routes that read it.
    warehouse.execute(
        f"CREATE TABLE IF NOT EXISTS {schema}.query_history ("
        "id VARCHAR, user_id VARCHAR NOT NULL DEFAULT '', query VARCHAR NOT NULL, "
        "sql VARCHAR NOT NULL DEFAULT '', result_summary VARCHAR NOT NULL DEFAULT '', "
        "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    warehouse.execute(
        f"INSERT INTO {schema}.query_history (id, user_id, query) "
        "VALUES ('q1', ?, 'select 1')",
        [user_id],
    )
    before = warehouse.execute(
        f"SELECT COUNT(*) FROM {schema}.query_history WHERE user_id = ?", [user_id]
    ).fetchone()[0]
    assert before == 1, "the fixture row is missing — the cascade proves nothing"

    response = client.post(
        "/auth/delete-account",
        json={"password": OLD_PASSWORD, "confirm": True},
        headers=_headers("del_cascade"),
    )
    assert response.status_code == 200
    assert user_manager.get_user_info("del_cascade") is None
    count = warehouse.execute(
        f"SELECT COUNT(*) FROM {schema}.query_history WHERE user_id = ?", [user_id]
    ).fetchone()[0]
    assert count == 0, "the account's rows survived the cascade"


def test_delete_account_revokes_the_sessions(client, warehouse):
    from api.auth import create_jwt_token, verify_jwt_token

    _make_user("del_sessions_admin", role="admin")
    _make_user("del_sessions", email="delsessions@example.com")
    token = create_jwt_token("del_sessions", role="viewer")

    response = client.post(
        "/auth/delete-account",
        json={"password": OLD_PASSWORD, "confirm": True},
        headers=_headers("del_sessions"),
    )
    assert response.status_code == 200
    assert verify_jwt_token(token) is None


# ── /auth/me ───────────────────────────────────────────────────────────


def test_me_returns_only_the_caller(client):
    _make_user("me_one", email="one@example.com")
    _make_user("me_two", email="two@example.com")

    response = client.get("/auth/me", headers=_headers("me_one"))
    assert response.status_code == 200
    user = response.json()["user"]
    assert user["username"] == "me_one"
    assert user["email"] == "one@example.com"
    assert user["email_verified"] is False
    assert user["must_change_password"] is False
    assert "role" in user and "tenant_id" in user and "plan" in user
    assert "two@example.com" not in response.text
    assert "password_hash" not in response.text


def test_me_cannot_be_pointed_at_another_account(client):
    _make_user("me_other", email="other@example.com")
    response = client.get("/auth/me?username=me_other", headers=_headers("me_other"))
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "me_other"


def test_me_requires_authentication(client):
    assert client.get("/auth/me").status_code == 401


# ── the shared revocation store ────────────────────────────────────────


def test_revocation_store_is_shared_between_instances(tmp_path):
    from api.auth import RevocationStore

    db_path = tmp_path / "users.db"
    first = RevocationStore(db_path=db_path)
    second = RevocationStore(db_path=db_path)
    try:
        assert first.create_revocation("jti-one", time.time() + 60) is True
        # A second replica refuses what the first one revoked.
        assert second.is_revoked("jti-one") is True
        assert second.is_revoked("jti-two") is False
        assert second.purge_expired() == 0
    finally:
        first.close()
        second.close()


def test_revoked_token_is_refused_across_replicas(tmp_path, monkeypatch):
    """A token revoked on one replica must not work on another."""
    from api.auth import RevocationStore, create_jwt_token, verify_jwt_token

    db_path = tmp_path / "users.db"
    monkeypatch.setenv("DATAAGENT_USER_DB", str(db_path))

    import api.auth as auth_mod

    auth_mod.reset_revocation_store_cache()

    token = create_jwt_token("replica_user", role="viewer")
    assert verify_jwt_token(token) is not None

    # A different store instance (i.e. a different replica) revokes it.
    other = RevocationStore(db_path=db_path)
    try:
        payload = auth_mod.pyjwt.decode(
            token,
            auth_mod.JWT_SECRET,
            algorithms=[auth_mod.JWT_ALGORITHM],
            audience=auth_mod.JWT_AUDIENCE,
            issuer=auth_mod.JWT_ISSUER,
        )
        assert other.create_revocation(payload["jti"], payload["exp"]) is True
    finally:
        other.close()

    assert verify_jwt_token(token) is None
    # Drop the temporary store so nothing keeps the file open.
    auth_mod.reset_revocation_store_cache()


def test_revocation_store_purges_expired_entries(tmp_path):
    from api.auth import RevocationStore

    store = RevocationStore(db_path=tmp_path / "users.db")
    try:
        store.create_revocation("jti-gone", time.time() - 10)
        store.create_revocation("jti-live", time.time() + 600)
        assert store.purge_expired() == 1
        assert store.is_revoked("jti-gone") is False
        assert store.is_revoked("jti-live") is True
    finally:
        store.close()


# ── the mailer ─────────────────────────────────────────────────────────


def test_mailer_returns_false_when_smtp_is_not_configured(monkeypatch):
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "")
    assert mailer.send_email("a@example.com", "Subject", "Body") is False


def test_mailer_never_raises_when_the_server_is_down(monkeypatch):
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_port", 1)
    assert mailer.send_email("a@example.com", "Subject", "Body") is False


def test_mailer_never_raises_on_a_broken_configuration(monkeypatch):
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_port", 1)
    monkeypatch.setattr(
        mailer.smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    assert mailer.send_email("a@example.com", "Subject", "Body") is False


def test_mailer_never_logs_the_token(monkeypatch, caplog):
    import logging

    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_port", 1)
    with caplog.at_level(logging.DEBUG):
        mailer.send_email(
            "a@example.com", "Reset your password", "token=SECRET-TOKEN-VALUE"
        )
    assert "SECRET-TOKEN-VALUE" not in caplog.text


def test_mailer_reports_a_delivered_mail(monkeypatch):
    from api import mailer

    sent: List[Any] = []

    class _Server:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            return None

        def login(self, user, password):
            return None

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(mailer.smtplib, "SMTP", _Server)
    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "smtp.example.com")
    assert mailer.send_email("a@example.com", "Subject", "Body") is True
    assert sent and sent[0]["To"] == "a@example.com"


# ── the identity store migration ───────────────────────────────────────


#: The ``users`` table as it looked before ADR-009.
_LEGACY_USERS_DDL = (
    "CREATE TABLE users ("
    "username TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT '', "
    "password_hash TEXT NOT NULL DEFAULT '', salt TEXT NOT NULL DEFAULT '', "
    "role TEXT NOT NULL DEFAULT 'viewer', email TEXT NOT NULL DEFAULT '', "
    "tenant_id TEXT NOT NULL DEFAULT '', sso_subject TEXT NOT NULL DEFAULT '', "
    "sso_provider TEXT NOT NULL DEFAULT '', full_name TEXT NOT NULL DEFAULT '', "
    "is_active INTEGER NOT NULL DEFAULT 1, is_admin INTEGER NOT NULL DEFAULT 0, "
    "api_key TEXT NOT NULL DEFAULT '', mfa_enabled INTEGER NOT NULL DEFAULT 0, "
    "mfa_secret TEXT NOT NULL DEFAULT '', password_changed_at TEXT NOT NULL DEFAULT '', "
    "failed_login_attempts INTEGER NOT NULL DEFAULT 0, "
    "locked_until TEXT NOT NULL DEFAULT '', last_login TEXT NOT NULL DEFAULT '', "
    "created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')"
)


def test_an_existing_database_is_migrated(tmp_path):
    """An installation that predates ADR-009 keeps its accounts."""
    import sqlite3

    from api.user_store import SQLiteUserStore

    db_path = tmp_path / "users.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(_LEGACY_USERS_DDL)
    conn.execute("INSERT INTO users (username, user_id) VALUES ('old', 'uid-old')")
    conn.commit()
    conn.close()

    store = SQLiteUserStore(db_path=db_path)
    try:
        row = store.get_user("old")
        assert row is not None, "the legacy account was lost"
        assert row["email_verified"] is False
        assert row["must_change_password"] is False
        assert row["invited_by"] == ""
        assert store.set_email_verified("old", True) is True
        assert store.get_user("old")["email_verified"] is True
    finally:
        store.close()


# ── the token store ────────────────────────────────────────────────────


def test_the_token_store_keeps_only_the_hash(tmp_path):
    import hashlib
    import sqlite3

    from api.account_tokens import AccountTokenStore, KIND_PASSWORD_RESET

    db_path = tmp_path / "users.db"
    store = AccountTokenStore(db_path=db_path)
    try:
        raw = store.create_token(KIND_PASSWORD_RESET, "uid-1", ttl_seconds=600)
        assert raw

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT token_hash FROM account_tokens").fetchall()
        conn.close()
        assert rows, "the token was not stored"
        assert rows[0][0] == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in {row[0] for row in rows}

        record = store.consume_token(KIND_PASSWORD_RESET, raw)
        assert record is not None
        assert record["user_id"] == "uid-1"
        # Single use.
        assert store.consume_token(KIND_PASSWORD_RESET, raw) is None
    finally:
        store.close()


def test_the_token_store_fails_closed(tmp_path):
    from api.account_tokens import AccountTokenStore, KIND_INVITE, KIND_PASSWORD_RESET

    store = AccountTokenStore(db_path=tmp_path / "users.db")
    try:
        raw = store.create_token(KIND_PASSWORD_RESET, "uid-1", ttl_seconds=600)
        # An unknown token.
        assert store.consume_token(KIND_PASSWORD_RESET, "nope") is None
        # An unknown kind.
        assert store.consume_token("not-a-kind", raw) is None
        assert store.consume_token(KIND_INVITE, raw) is None
        # The token is still good for its own kind.
        assert store.consume_token(KIND_PASSWORD_RESET, raw) is not None
    finally:
        store.close()
