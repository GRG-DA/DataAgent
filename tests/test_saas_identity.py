"""ADR-009 D2/D3/D4 — identity store, bootstrap gate, role management, cascade.

The hosted conversion moves the user directory out of the process (D2),
closes the "first registrant becomes admin" takeover (D3) and makes roles a
managed resource with a delete cascade (D4).

Guarantees exercised here:

* D2 — the user directory lives behind ``api.user_store.UserStore``; the
  SQLite implementation is transactional, migrates a legacy
  ``config/users.json`` exactly once and survives a restart.
* D3 — the first central registrant must present ``DATAAGENT_BOOTSTRAP_TOKEN``
  (and is refused when no token is configured at all).
* D4 — ``PATCH /auth/users/{username}`` is admin-only, validates the role and
  is audited; ``DELETE /auth/users/{username}`` cascades to the account's
  warehouse rows, dashboards, conversations, history, API keys, subscription
  and notification preferences, and refuses to remove the last admin.
"""

from __future__ import annotations

import json

import pytest

BOOTSTRAP_TOKEN = "b00tstrap-token-for-tests"


# ── helpers ───────────────────────────────────────────────────────────


def _legacy_user(username: str = "legacy", **overrides) -> dict:
    """A row in the shape the legacy ``config/users.json`` used."""
    payload = {
        "user_id": f"uid-{username}",
        "username": username,
        "password_hash": "",
        "salt": "",
        "role": "viewer",
        "email": f"{username}@example.com",
        "tenant_id": "",
        "sso_subject": "",
        "sso_provider": "",
        "full_name": "",
        "api_key": "",
        "is_active": True,
        "is_admin": False,
        "mfa_enabled": False,
        "mfa_secret": "",
        "password_changed_at": "",
        "failed_login_attempts": 0,
        "locked_until": "",
        "last_login": "",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from api.main import app
    from api.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def isolated_manager(tmp_path, monkeypatch):
    """A ``UserManager`` with a private store, wired into every module that
    keeps a module-level reference to the global one."""
    import api.auth as auth_mod
    import api.middleware.auth_middleware as mw_mod
    from api.auth import UserManager

    manager = UserManager(auth_file=str(tmp_path / "users.json"))
    monkeypatch.setattr(auth_mod, "user_manager", manager)
    monkeypatch.setattr(mw_mod, "user_manager", manager)
    return manager


def _headers(username: str, role: str = "admin") -> dict:
    from api.auth import create_jwt_token

    return {"Authorization": f"Bearer {create_jwt_token(username, role=role)}"}


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


def _seed_platform_rows(conn, schema: str, user_id: str) -> None:
    """Create the platform tables and one row per user-scoped table."""
    ddl = [
        f"CREATE TABLE IF NOT EXISTS {schema}.dashboards (id VARCHAR, name VARCHAR, owner_id VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.pbi_studios (id VARCHAR, name VARCHAR, dash JSON, owner_id VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.chat_conversations (id VARCHAR, user_id VARCHAR, title VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.chat_messages (id VARCHAR, conversation_id VARCHAR, user_id VARCHAR, role VARCHAR, content VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.query_history (id VARCHAR, user_id VARCHAR, query VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.user_preferences (user_id VARCHAR, prefs JSON)",
        f"CREATE TABLE IF NOT EXISTS {schema}.api_keys (key_id VARCHAR, user_id VARCHAR, name VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.user_subscriptions (user_id VARCHAR, plan VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.notification_preferences (user_id VARCHAR, channel VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.notifications (id VARCHAR, user_id VARCHAR, title VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.webhook_endpoints (id VARCHAR, user_id VARCHAR, name VARCHAR, url VARCHAR)",
    ]
    for stmt in ddl:
        conn.execute(stmt)
    # The api_keys table may already be the PRODUCTION one (the API-key store
    # creates it with NOT NULL columns), so the seed row has to satisfy
    # whichever schema is actually in place.
    api_key_cols = [
        r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = 'api_keys' "
            "ORDER BY ordinal_position",
            [schema],
        ).fetchall()
    ]
    api_key_values = {
        "key_id": "k1", "user_id": user_id, "name": "legacy",
        "key_hash": "k1hash", "key_prefix": "da_k1",
        "scopes": ["read"],
        "created_at": "2026-01-01 00:00:00",
        "expires_at": None, "last_used_at": None,
        "revoked": False, "revoked_at": None,
        "tenant_id": "",
    }
    _api_key_insert = (
        f"INSERT INTO {schema}.api_keys "
        f"({', '.join(api_key_cols)}) "
        f"VALUES ({', '.join(['?'] * len(api_key_cols))})"
    )
    _api_key_params = [api_key_values.get(c) for c in api_key_cols]
    inserts = [
        (f"INSERT INTO {schema}.dashboards (id, name, owner_id) VALUES (?, ?, ?)",
         ["d1", "Board", user_id]),
        (f"INSERT INTO {schema}.pbi_studios (id, name, dash, owner_id) VALUES (?, ?, ?, ?)",
         ["d1", "Board", "{}", user_id]),
        (f"INSERT INTO {schema}.chat_conversations (id, user_id, title) VALUES (?, ?, ?)",
         ["c1", user_id, "Chat"]),
        (f"INSERT INTO {schema}.chat_messages (id, conversation_id, user_id, role, content) VALUES (?, ?, ?, ?, ?)",
         ["m1", "c1", user_id, "user", "hi"]),
        (f"INSERT INTO {schema}.query_history (id, user_id, query) VALUES (?, ?, ?)",
         ["q1", user_id, "select 1"]),
        (f"INSERT INTO {schema}.user_preferences (user_id, prefs) VALUES (?, CAST(? AS JSON))",
         [user_id, "{}"]),
        (_api_key_insert, _api_key_params),
        (f"INSERT INTO {schema}.user_subscriptions (user_id, plan) VALUES (?, ?)",
         [user_id, "pro"]),
        (f"INSERT INTO {schema}.notification_preferences (user_id, channel) VALUES (?, ?)",
         [user_id, "in_app"]),
        (f"INSERT INTO {schema}.notifications (id, user_id, title) VALUES (?, ?, ?)",
         ["n1", user_id, "Ping"]),
        (f"INSERT INTO {schema}.webhook_endpoints (id, user_id, name, url) VALUES (?, ?, ?, ?)",
         ["w1", user_id, "hook", "https://example.invalid/hook"]),
    ]
    for stmt, params in inserts:
        conn.execute(stmt, params)


# ── D2 — the identity store ───────────────────────────────────────────


def test_sqlite_store_roundtrip(tmp_path):
    from api.user_store import SQLiteUserStore, UserRecord

    store = SQLiteUserStore(db_path=tmp_path / "users.db")
    try:
        assert store.add_user(UserRecord(username="alice", user_id="u1",
                                         email="Alice@Example.com")) == "u1"
        row = store.get_user("alice")
        assert row is not None
        assert row["email"] == "alice@example.com"
        assert row["is_active"] is True
        assert row["mfa_enabled"] is False
        assert store.get_user_by_id("u1")["username"] == "alice"
        assert store.get_user_by_email("ALICE@example.com")["username"] == "alice"
        assert [u["username"] for u in store.list_users()] == ["alice"]

        assert store.update_user("alice", role="admin") is True
        assert store.get_user("alice")["role"] == "admin"
        assert store.update_user("ghost", role="admin") is False

        assert store.set_password("alice", "hash-1") is True
        assert store.get_user("alice")["password_hash"] == "hash-1"
        assert store.set_api_key("alice", "da_key") is True
        assert store.get_user_by_api_key("da_key")["username"] == "alice"

        assert store.remove_user("alice") is True
        assert store.get_user("alice") is None
        assert store.remove_user("alice") is False
    finally:
        store.close()


def test_sqlite_store_survives_a_restart(tmp_path):
    from api.user_store import SQLiteUserStore, UserRecord

    db_path = tmp_path / "users.db"
    first = SQLiteUserStore(db_path=db_path)
    first.add_user(UserRecord(username="bob", user_id="u-bob"))
    first.close()

    second = SQLiteUserStore(db_path=db_path)
    try:
        assert second.get_user("bob")["user_id"] == "u-bob"
    finally:
        second.close()


def test_legacy_json_is_migrated_exactly_once(tmp_path):
    from api.user_store import SQLiteUserStore

    legacy = tmp_path / "users.json"
    legacy.write_text(
        json.dumps({"legacy1": _legacy_user("legacy1"),
                    "legacy2": _legacy_user("legacy2")}),
        encoding="utf-8",
    )
    db_path = tmp_path / "users.db"

    store = SQLiteUserStore(db_path=db_path, legacy_json_path=legacy)
    try:
        assert {u["username"] for u in store.list_users()} == {"legacy1", "legacy2"}
    finally:
        store.close()

    # A deleted account must not come back when the store is reopened.
    reopened = SQLiteUserStore(db_path=db_path, legacy_json_path=legacy)
    try:
        assert reopened.remove_user("legacy1") is True
    finally:
        reopened.close()

    again = SQLiteUserStore(db_path=db_path, legacy_json_path=legacy)
    try:
        assert [u["username"] for u in again.list_users()] == ["legacy2"]
    finally:
        again.close()


def test_json_store_roundtrip(tmp_path):
    from api.user_store import JSONUserStore, UserRecord

    path = tmp_path / "users.json"
    store = JSONUserStore(path)
    try:
        assert store.add_user(UserRecord(username="carol", user_id="u-carol",
                                         email="carol@example.com")) == "u-carol"
    finally:
        store.close()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "carol" in data

    reopened = JSONUserStore(path)
    try:
        assert reopened.get_user("carol")["user_id"] == "u-carol"
        assert reopened.get_user_by_email("carol@example.com")["username"] == "carol"
        assert reopened.remove_user("carol") is True
        assert reopened.get_user("carol") is None
    finally:
        reopened.close()


def test_get_user_store_honours_the_env_override(tmp_path, monkeypatch):
    from api.user_store import JSONUserStore, SQLiteUserStore, get_user_store

    monkeypatch.setenv("DATAAGENT_USER_STORE", "json")
    monkeypatch.setenv("DATAAGENT_USER_JSON", str(tmp_path / "users.json"))
    assert isinstance(get_user_store(), JSONUserStore)

    monkeypatch.setenv("DATAAGENT_USER_STORE", "sqlite")
    monkeypatch.setenv("DATAAGENT_USER_DB", str(tmp_path / "users.db"))
    assert isinstance(get_user_store(), SQLiteUserStore)


def test_user_manager_is_backed_by_the_store(tmp_path):
    from api.auth import UserManager

    path = tmp_path / "users.json"
    manager = UserManager(auth_file=str(path))
    user_id = manager.add_user("dave", password="Str0ng!Passw0rd", role="admin",
                                email="dave@example.com")
    assert user_id
    assert manager.add_user("dave", password="x") is None
    assert manager.has_users() is True
    assert manager.authenticate("dave", "Str0ng!Passw0rd") == user_id
    assert manager.authenticate("dave", "wrong") is None
    assert manager.get_user_info("dave")["role"] == "admin"
    assert manager.get_user_by_id(user_id)["username"] == "dave"
    assert manager.get_user_by_email("dave@example.com")["username"] == "dave"
    assert manager.get_user_info_by_id(user_id)["username"] == "dave"
    assert [u["username"] for u in manager.get_users()] == ["dave"]

    assert manager.set_password("dave", "An0ther!Passw0rd") is True
    assert manager.authenticate("dave", "An0ther!Passw0rd") == user_id
    assert manager.change_password("dave", "An0ther!Passw0rd", "Y3tAnother!x") is True
    assert manager.change_password("dave", "wrong", "Y3tAnother!x") is False

    assert manager.remove_user("dave") is True
    assert manager.get_user_info("dave") is None
    assert "dave" not in json.loads(path.read_text(encoding="utf-8"))


def test_user_manager_can_use_the_sqlite_store(tmp_path):
    from api.auth import UserManager
    from api.user_store import SQLiteUserStore

    store = SQLiteUserStore(db_path=tmp_path / "users.db")
    manager = UserManager(store=store)
    user_id = manager.add_user("erin", password="Str0ng!Passw0rd",
                                email="erin@example.com")
    assert user_id
    assert manager.authenticate("erin", "Str0ng!Passw0rd") == user_id
    assert manager.get_user_by_id(user_id)["username"] == "erin"
    assert manager.get_user_by_email("erin@example.com")["username"] == "erin"
    assert manager.update_user("erin", role="data_analyst") is True
    assert manager.get_user_info("erin")["role"] == "data_analyst"
    assert manager.remove_user("erin") is True
    store.close()


def test_user_manager_never_leaks_secrets(tmp_path):
    from api.auth import UserManager

    manager = UserManager(auth_file=str(tmp_path / "users.json"))
    manager.add_user("frank", password="Str0ng!Passw0rd", email="frank@example.com")
    info = manager.get_user_info("frank")
    assert "password_hash" not in info
    assert "salt" not in info
    assert info["has_api_key"] is False


# ── sessions (D2) ─────────────────────────────────────────────────────


def test_session_store_selection_is_explicit():
    pytest.importorskip("redis")
    from api.session import InMemorySessionStore, RedisSessionStore, build_session_store

    assert isinstance(build_session_store(""), InMemorySessionStore)
    assert isinstance(build_session_store("redis://localhost:6379"), RedisSessionStore)


# ── D3 — the bootstrap gate ───────────────────────────────────────────


def test_first_central_registrant_needs_a_bootstrap_token(monkeypatch, isolated_manager):
    from fastapi import HTTPException

    from api.central_accounts import provision_user

    monkeypatch.delenv("DATAAGENT_BOOTSTRAP_TOKEN", raising=False)
    with pytest.raises(HTTPException) as excinfo:
        provision_user("first@example.com", "acct-1", "")
    assert "no bootstrap token is configured" in str(excinfo.value.detail)

    monkeypatch.setenv("DATAAGENT_BOOTSTRAP_TOKEN", BOOTSTRAP_TOKEN)
    with pytest.raises(HTTPException) as excinfo:
        provision_user("first@example.com", "acct-1", "wrong-token")
    assert "A valid bootstrap token is required" in str(excinfo.value.detail)

    user = provision_user("first@example.com", "acct-1", BOOTSTRAP_TOKEN)
    assert user is not None
    assert user["role"] == "admin"

    # The gate only guards the very first account.
    second = provision_user("second@example.com", "acct-2", "")
    assert second["role"] == "viewer"


def test_central_register_refuses_without_the_bootstrap_token(monkeypatch, client, isolated_manager):
    import httpx

    from api.billing import cloud_sync

    monkeypatch.setattr(cloud_sync, "billing_sync_base_url", lambda: "http://billing.test")
    monkeypatch.delenv("DATAAGENT_BOOTSTRAP_TOKEN", raising=False)

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"ok": True, "user_id": "acct-1", "email": "gate@example.com"}

    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: _Resp())
    response = client.post("/auth/accounts/register",
                            json={"email": "gate@example.com", "password": "Str0ng!Passw0rd"})
    assert response.status_code == 403
    assert "no bootstrap token is configured" in response.json()["detail"]


# ── D4 — role management ──────────────────────────────────────────────


def test_patch_user_role_is_admin_only(client, isolated_manager):
    isolated_manager.add_user("target_viewer", password="Str0ng!Passw0rd", role="viewer")
    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")

    denied = client.patch("/auth/users/target_viewer",
                          json={"role": "admin"},
                          headers=_headers("target_viewer", role="viewer"))
    assert denied.status_code == 403

    allowed = client.patch("/auth/users/target_viewer",
                           json={"role": "data_analyst"},
                           headers=_headers("target_admin", role="admin"))
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["success"] is True
    assert body["user"]["role"] == "data_analyst"
    assert isolated_manager.get_user_info("target_viewer")["role"] == "data_analyst"


def test_patch_user_rejects_an_unknown_role(client, isolated_manager):
    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    response = client.patch("/auth/users/target_admin",
                            json={"role": "superuser"},
                            headers=_headers("target_admin", role="admin"))
    assert response.status_code == 400
    assert "superuser" in response.json()["detail"]


def test_patch_user_audits_the_role_change(client, isolated_manager, monkeypatch):
    import api.main as main

    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    calls: list[dict] = []
    monkeypatch.setattr(
        main.audit_logger, "log_role_changed",
        lambda **kwargs: calls.append(kwargs),
    )
    response = client.patch("/auth/users/target_admin",
                            json={"role": "auditor"},
                            headers=_headers("target_admin", role="admin"))
    assert response.status_code == 200
    assert calls, "the role change must be audited"
    assert calls[0]["old_role"] == "admin"
    assert calls[0]["new_role"] == "auditor"


def test_patch_unknown_user_is_404(client, isolated_manager):
    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    response = client.patch("/auth/users/nobody",
                            json={"role": "auditor"},
                            headers=_headers("target_admin", role="admin"))
    assert response.status_code == 404


# ── D4 — the delete cascade ───────────────────────────────────────────


def test_delete_user_cascades(client, isolated_manager, warehouse):
    from config import settings

    schema = settings.duckdb.schema
    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    isolated_manager.add_user("doomed", password="Str0ng!Passw0rd", role="viewer")
    doomed = isolated_manager.get_user_info("doomed")
    _seed_platform_rows(warehouse, schema, doomed["user_id"])

    response = client.delete("/auth/users/doomed",
                             headers=_headers("target_admin", role="admin"))
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert isolated_manager.get_user_info("doomed") is None

    for table in ("dashboards", "pbi_studios", "chat_conversations", "chat_messages",
                  "query_history", "user_preferences", "api_keys",
                  "user_subscriptions", "notification_preferences", "notifications",
                  "webhook_endpoints"):
        count = warehouse.execute(
            f"SELECT COUNT(*) FROM {schema}.{table} WHERE "
            + ("owner_id = ?" if table in ("dashboards", "pbi_studios") else "user_id = ?"),
            [doomed["user_id"]],
        ).fetchone()[0]
        assert count == 0, f"{table} rows survived the delete cascade"


def test_delete_refuses_the_last_admin(client, isolated_manager):
    isolated_manager.add_user("only_admin", password="Str0ng!Passw0rd", role="admin")
    response = client.delete("/auth/users/only_admin",
                             headers=_headers("only_admin", role="admin"))
    assert response.status_code == 409
    assert isolated_manager.get_user_info("only_admin") is not None


def test_delete_drops_the_tenant_schema(client, isolated_manager, warehouse):
    """ADR-009 D1 gives a tenant its own schema — it goes with its last member."""
    from api.tenancy import tenant_schema

    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    isolated_manager.add_user("tenant_user", password="Str0ng!Passw0rd", role="viewer",
                              tenant_id="Acme Corp")
    schema = tenant_schema("Acme Corp")
    warehouse.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    warehouse.execute(f'CREATE TABLE IF NOT EXISTS "{schema}".sales (a INTEGER)')

    response = client.delete("/auth/users/tenant_user",
                             headers=_headers("target_admin", role="admin"))
    assert response.status_code == 200
    assert schema in response.json()["cascade"]["schemas"]
    schemas = warehouse.execute(
        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ?",
        [schema],
    ).fetchall()
    assert schemas == []


def test_delete_keeps_a_shared_tenant_schema(client, isolated_manager, warehouse):
    """A tenant that still has members keeps its data."""
    from api.tenancy import tenant_schema

    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    isolated_manager.add_user("colleague", password="Str0ng!Passw0rd", role="viewer",
                              tenant_id="acme")
    isolated_manager.add_user("leaving", password="Str0ng!Passw0rd", role="viewer",
                              tenant_id="acme")
    schema = tenant_schema("acme")
    warehouse.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    warehouse.execute(f'CREATE TABLE IF NOT EXISTS "{schema}".sales (a INTEGER)')

    response = client.delete("/auth/users/leaving",
                             headers=_headers("target_admin", role="admin"))
    assert response.status_code == 200
    assert response.json()["cascade"]["schemas"] == []
    schemas = warehouse.execute(
        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ?",
        [schema],
    ).fetchall()
    assert len(schemas) == 1


def test_delete_unknown_user_is_404(client, isolated_manager):
    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    response = client.delete("/auth/users/nobody",
                             headers=_headers("target_admin", role="admin"))
    assert response.status_code == 404


def test_delete_keeps_the_account_when_the_warehouse_is_unreachable(
    client, isolated_manager, monkeypatch
):
    """Fail closed: no login without the data it owns."""
    import api.db as db

    isolated_manager.add_user("target_admin", password="Str0ng!Passw0rd", role="admin")
    isolated_manager.add_user("stuck", password="Str0ng!Passw0rd", role="viewer")

    def _locked():
        raise RuntimeError("IO Error: File is already open in another process")

    monkeypatch.setattr(db, "get_connection", _locked)
    response = client.delete("/auth/users/stuck",
                             headers=_headers("target_admin", role="admin"))
    assert response.status_code == 503
    assert isolated_manager.get_user_info("stuck") is not None


def test_missing_schema_error_is_recognised():
    import duckdb

    from api.user_admin import _is_missing_schema_error

    missing = duckdb.CatalogException(
        'Catalog Error: SET search_path: No catalog + schema named "analytics" found.'
    )
    assert _is_missing_schema_error(missing) is True
    assert _is_missing_schema_error(RuntimeError("File is already open")) is False
