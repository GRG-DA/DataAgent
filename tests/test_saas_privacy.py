"""Data-privacy workstream for the hosted SaaS (ADR-009 follow-up).

Two guarantees are guarded here.

**1. The compliance tables are tenant-scoped.**  ``privacy_requests`` and
``consent_records`` live in the shared platform schema, so without a
``tenant_id`` column a request filed by one tenant is listable, readable
and *executable* by every other tenant — a data subject's deletion
request from tenant A could be run by tenant B against tenant B's tables,
and a consent record could be read across the whole installation.

**2. A user can take their data with them (GDPR Art. 15/20).**  The export
returns everything the platform holds about the caller — and only about
the caller: never another user's rows, never another tenant's tables and
never a password hash, an API key or its hash.  A cap that is hit is
reported explicitly (``"truncated": true``) instead of silently dropping
rows.

Every test uses a throwaway DuckDB file, so the suite never touches the
real warehouse and never collides with a running server.
"""
from __future__ import annotations

import json

import duckdb
import pytest

from api.tenancy import (
    DEFAULT_TENANT_ID,
    apply_tenant_search_path,
    tenant_schema,
    use_tenant,
)

ALICE = "u_alice"
BOB = "u_bob"

PASSWORD = "Str0ng!Passw0rd!x"


# ── helpers ─────────────────────────────────────────────────────────────


def _columns(path: str, schema: str, table: str) -> set[str]:
    """The column names of *table* in *schema*."""
    con = duckdb.connect(path)
    try:
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, table],
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def _make_user(username: str, tenant_id: str = ALICE, role: str = "user") -> dict:
    """Create (or fetch) an account in the throwaway identity store."""
    from api.auth import user_manager

    existing = user_manager.get_user_info(username)
    if existing:
        return existing
    user_manager.add_user(
        username,
        PASSWORD,
        role,
        email=f"{username}@example.invalid",
        tenant_id=tenant_id,
    )
    return user_manager.get_user_info(username) or {}


def _platform_ddl(schema: str = "analytics") -> list[str]:
    """The platform tables a user's export and erasure touch."""
    return [
        f"CREATE TABLE IF NOT EXISTS {schema}.chat_conversations "
        "(id VARCHAR, user_id VARCHAR, title VARCHAR, created_at TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS {schema}.chat_messages "
        "(id VARCHAR, conversation_id VARCHAR, user_id VARCHAR, role VARCHAR, "
        "content VARCHAR, created_at TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS {schema}.query_history "
        "(id VARCHAR, user_id VARCHAR, query VARCHAR, sql VARCHAR, "
        "result_summary VARCHAR, created_at TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS {schema}.user_preferences "
        "(user_id VARCHAR, prefs JSON, updated_at TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS {schema}.notification_preferences "
        "(user_id VARCHAR, channel VARCHAR, enabled BOOLEAN, digest BOOLEAN)",
        f"CREATE TABLE IF NOT EXISTS {schema}.api_keys "
        "(key_id VARCHAR, user_id VARCHAR, name VARCHAR, key_hash VARCHAR, "
        "key_prefix VARCHAR, scopes VARCHAR[], created_at TIMESTAMP, "
        "expires_at TIMESTAMP, last_used_at TIMESTAMP, revoked BOOLEAN)",
        f"CREATE TABLE IF NOT EXISTS {schema}.user_subscriptions "
        "(user_id VARCHAR, plan VARCHAR, status VARCHAR, started_at TIMESTAMP, "
        "expires_at TIMESTAMP, updated_at TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS {schema}.comments "
        "(id VARCHAR, target_type VARCHAR, target_id VARCHAR, author_id VARCHAR, "
        "content VARCHAR, created_at TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS {schema}.annotations "
        "(id VARCHAR, target_type VARCHAR, target_id VARCHAR, author_id VARCHAR, "
        "content VARCHAR, created_at TIMESTAMP)",
        f"CREATE TABLE IF NOT EXISTS {schema}.notifications "
        "(id VARCHAR, user_id VARCHAR, title VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.webhook_endpoints "
        "(id VARCHAR, user_id VARCHAR, name VARCHAR, url VARCHAR)",
        f"CREATE TABLE IF NOT EXISTS {schema}.dashboards "
        "(id VARCHAR, name VARCHAR, owner_id VARCHAR)",
    ]


@pytest.fixture()
def compliance(tmp_path):
    """A compliance manager on a warehouse with two tenants' schemas."""
    from api.compliance import ComplianceManager

    path = str(tmp_path / "compliance.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    for tenant in (ALICE, BOB):
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_schema(tenant)}"')
    con.close()
    return ComplianceManager(db_path=path)


@pytest.fixture()
def export_warehouse(tmp_path):
    """A warehouse with the platform tables and two tenants' data tables."""
    path = str(tmp_path / "export.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    for tenant in (ALICE, BOB):
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_schema(tenant)}"')
    for stmt in _platform_ddl():
        con.execute(stmt)
    con.execute(f'CREATE TABLE "{tenant_schema(ALICE)}".sales (amount DOUBLE)')
    con.execute("INSERT INTO \"{0}\".sales VALUES (10.0), (20.0)".format(
        tenant_schema(ALICE)
    ))
    con.execute(f'CREATE TABLE "{tenant_schema(BOB)}".bob_secret (amount DOUBLE)')
    con.execute("INSERT INTO \"{0}\".bob_secret VALUES (99.0)".format(
        tenant_schema(BOB)
    ))
    # The default tenant's schema *is* the platform schema, so a data table
    # can sit next to the platform tables there.
    con.execute("CREATE TABLE analytics.tenant_sales (amount DOUBLE)")
    con.execute("INSERT INTO analytics.tenant_sales VALUES (7.0)")
    con.close()
    return path


@pytest.fixture()
def tenant_con(export_warehouse):
    """A connection with Alice's tenant schema first on the search path."""
    con = duckdb.connect(export_warehouse)
    apply_tenant_search_path(con, ALICE)
    yield con
    con.close()


# ── 1. the compliance tables are tenant-scoped ──────────────────────────


def test_privacy_requests_carry_a_tenant_id(compliance):
    """The table gets a tenant column through an idempotent migration."""
    assert "tenant_id" in _columns(compliance.db_path, "analytics", "privacy_requests")


def test_consent_records_carry_a_tenant_id(compliance):
    assert "tenant_id" in _columns(compliance.db_path, "analytics", "consent_records")


def test_the_tenant_migration_is_idempotent(tmp_path):
    """Re-opening the manager must not fail on an existing tenant column."""
    from api.compliance import ComplianceManager

    path = str(tmp_path / "twice.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.close()

    ComplianceManager(db_path=path)
    ComplianceManager(db_path=path)

    assert "tenant_id" in _columns(path, "analytics", "privacy_requests")
    assert "tenant_id" in _columns(path, "analytics", "consent_records")


def test_a_privacy_request_is_stamped_with_the_tenant(compliance):
    with use_tenant(ALICE):
        req = compliance.submit_request("access", "a@example.invalid", "A")
    assert req.tenant_id == ALICE


def test_a_privacy_request_is_not_listed_for_another_tenant(compliance):
    with use_tenant(ALICE):
        compliance.submit_request("access", "a@example.invalid")
    with use_tenant(BOB):
        assert compliance.list_requests()["total"] == 0


def test_a_privacy_request_is_not_readable_by_another_tenant(compliance):
    with use_tenant(ALICE):
        req = compliance.submit_request("access", "a@example.invalid")
    with use_tenant(BOB):
        assert compliance.get_request(req.id) is None


def test_a_privacy_request_cannot_be_executed_by_another_tenant(compliance):
    """Another tenant must not be able to run a subject's request."""
    with use_tenant(ALICE):
        req = compliance.submit_request("access", "a@example.invalid")
    with use_tenant(BOB):
        assert compliance.execute_request(req.id) is False
    # ... and the request is untouched, not silently completed.
    with use_tenant(ALICE):
        assert compliance.get_request(req.id)["status"] == "pending"


def test_a_privacy_request_is_visible_to_its_own_tenant(compliance):
    with use_tenant(ALICE):
        req = compliance.submit_request("access", "a@example.invalid")
        assert compliance.get_request(req.id) is not None
        assert compliance.list_requests()["total"] == 1


def test_consent_is_tenant_scoped(compliance):
    with use_tenant(ALICE):
        compliance.record_consent("subject-1", "analytics", True)
    with use_tenant(BOB):
        assert compliance.check_consent("subject-1", "analytics") is False
        assert compliance.list_consent("subject-1") == []


def test_consent_revocation_is_tenant_scoped(compliance):
    """Another tenant must not be able to revoke a subject's consent."""
    with use_tenant(ALICE):
        compliance.record_consent("subject-1", "analytics", True)
    with use_tenant(BOB):
        assert compliance.revoke_consent("subject-1", "analytics") is False
    with use_tenant(ALICE):
        assert compliance.check_consent("subject-1", "analytics") is True


def test_consent_listing_is_tenant_scoped(compliance):
    with use_tenant(ALICE):
        compliance.record_consent("subject-1", "analytics", True)
    with use_tenant(BOB):
        compliance.record_consent("subject-2", "marketing", False)
    with use_tenant(ALICE):
        subjects = {row["subject_id"] for row in compliance.list_consent()}
    assert subjects == {"subject-1"}


# ── 2. the GDPR export ──────────────────────────────────────────────────


def test_the_export_contains_the_callers_own_records(export_warehouse, tenant_con):
    """Profile, subscription, chats, history, preferences and comments."""
    from api.user_data import build_user_export

    user = _make_user("export_alice")
    uid = user["user_id"]
    tenant_con.execute(
        "INSERT INTO analytics.chat_conversations (id, user_id, title) "
        "VALUES ('c1', ?, 'Alice chat')",
        [uid],
    )
    tenant_con.execute(
        "INSERT INTO analytics.chat_messages (id, conversation_id, user_id, role, content) "
        "VALUES ('m1', 'c1', ?, 'user', 'hello')",
        [uid],
    )
    tenant_con.execute(
        "INSERT INTO analytics.query_history (id, user_id, query) VALUES ('q1', ?, 'select 1')",
        [uid],
    )
    tenant_con.execute(
        "INSERT INTO analytics.user_subscriptions (user_id, plan, status) VALUES (?, 'pro', 'active')",
        [uid],
    )
    tenant_con.execute(
        "INSERT INTO analytics.comments (id, target_type, target_id, author_id, content) "
        "VALUES ('cm1', 'table', 'sales', ?, 'looks wrong')",
        [uid],
    )

    doc = build_user_export(uid, ALICE, con=tenant_con)

    assert doc["profile"]["username"] == "export_alice"
    assert doc["profile"]["user_id"] == uid
    assert doc["subscription"]["plan"] == "pro"
    assert [c["title"] for c in doc["conversations"]] == ["Alice chat"]
    assert [m["content"] for m in doc["messages"]] == ["hello"]
    assert [q["query"] for q in doc["query_history"]] == ["select 1"]
    assert [c["content"] for c in doc["comments"]] == ["looks wrong"]
    assert doc["subject"]["tenant_id"] == ALICE


def test_the_export_never_carries_another_users_rows(export_warehouse, tenant_con):
    """Two users of the same tenant see only their own rows."""
    from api.user_data import build_user_export

    alice = _make_user("export_alice")
    other = _make_user("export_other", tenant_id=ALICE)
    tenant_con.execute(
        "INSERT INTO analytics.chat_conversations (id, user_id, title) "
        "VALUES ('c1', ?, 'Mine'), ('c2', ?, 'Theirs')",
        [alice["user_id"], other["user_id"]],
    )

    doc = build_user_export(alice["user_id"], ALICE, con=tenant_con)
    titles = [c["title"] for c in doc["conversations"]]
    assert titles == ["Mine"]


def test_the_export_contains_the_api_key_metadata_but_never_the_hash(
    export_warehouse, tenant_con
):
    """Key *metadata* is the user's data; the hash is not."""
    from api.user_data import build_user_export

    user = _make_user("export_alice")
    tenant_con.execute(
        "INSERT INTO analytics.api_keys "
        "(key_id, user_id, name, key_hash, key_prefix, scopes, revoked) "
        "VALUES ('k1', ?, 'laptop', 'super-secret-hash', 'da_live_', ['read'], FALSE)",
        [user["user_id"]],
    )

    doc = build_user_export(user["user_id"], ALICE, con=tenant_con)

    assert len(doc["api_keys"]) == 1
    key = doc["api_keys"][0]
    assert key["key_id"] == "k1"
    assert key["name"] == "laptop"
    assert "key_hash" not in key
    assert "super-secret-hash" not in json.dumps(doc)


def test_the_export_contains_the_tenants_tables_with_a_shared_data_note(
    export_warehouse, tenant_con
):
    """The tenant's data tables travel with the export, clearly labelled."""
    from api.user_data import build_user_export

    user = _make_user("export_alice")
    doc = build_user_export(user["user_id"], ALICE, con=tenant_con)

    tables = doc["tenant_data"]["tables"]
    assert "sales" in tables
    assert sorted(r["amount"] for r in tables["sales"]["rows"]) == [10.0, 20.0]
    assert doc["tenant_data"]["shared"] is True
    assert "shared" in doc["tenant_data"]["note"].lower()


def test_the_export_never_carries_another_tenants_tables(export_warehouse, tenant_con):
    """A tenant's export must not contain another tenant's data."""
    from api.user_data import build_user_export

    user = _make_user("export_alice")
    doc = build_user_export(user["user_id"], ALICE, con=tenant_con)

    assert "bob_secret" not in doc["tenant_data"]["tables"]
    assert "99.0" not in json.dumps(doc)


def test_the_export_never_carries_a_secret(export_warehouse, tenant_con):
    """No password hash, no key hash, no token, no session id."""
    from api.user_data import build_user_export

    user = _make_user("export_alice")
    doc = build_user_export(user["user_id"], ALICE, con=tenant_con)
    blob = json.dumps(doc).lower()

    for forbidden in (
        "password_hash",
        "key_hash",
        "mfa_secret",
        "secret_hash",
        "session_id",
        "smtp_password",
    ):
        assert forbidden not in blob, forbidden


def test_the_export_never_carries_a_platform_table(export_warehouse, tenant_con):
    """The default tenant's schema *is* the platform schema.

    Its platform tables hold other people's rows, so they must never be
    exported as "tenant data" — only the tenant's own data tables.
    """
    from api.user_data import build_user_export

    user = _make_user("export_default")
    doc = build_user_export(user["user_id"], DEFAULT_TENANT_ID, con=tenant_con)

    tables = doc["tenant_data"]["tables"]
    for platform_table in (
        "api_keys", "chat_conversations", "chat_messages", "query_history",
        "user_preferences", "user_subscriptions", "notification_preferences",
        "comments", "annotations", "dashboards",
    ):
        assert platform_table not in tables, platform_table
    # The tenant's own data table still travels with the export.
    assert "tenant_sales" in tables


def test_the_export_marks_truncation_instead_of_dropping_data(
    export_warehouse, tenant_con, monkeypatch
):
    """A cap that is hit is reported, never applied silently."""
    import api.user_data as user_data
    from api.user_data import build_user_export

    monkeypatch.setattr(user_data, "MAX_EXPORT_ROWS_PER_TABLE", 2)

    user = _make_user("export_alice")
    tenant_con.execute(
        "INSERT INTO analytics.query_history (id, user_id, query) VALUES "
        "('q1', ?, 'a'), ('q2', ?, 'b'), ('q3', ?, 'c'), ('q4', ?, 'd')",
        [user["user_id"]] * 4,
    )

    doc = build_user_export(user["user_id"], ALICE, con=tenant_con)

    assert len(doc["query_history"]) == 2
    assert doc["truncated"] is True
    assert doc["truncation"]["by_table"]["query_history"]["rows_exported"] == 2
    assert doc["truncation"]["by_table"]["query_history"]["rows_available"] == 4


def test_the_export_is_bounded_in_size(export_warehouse, tenant_con, monkeypatch):
    """The document never grows past the byte cap."""
    import api.user_data as user_data
    from api.user_data import build_user_export

    monkeypatch.setattr(user_data, "MAX_EXPORT_BYTES", 4096)

    user = _make_user("export_alice")
    tenant_con.execute(
        "INSERT INTO analytics.query_history (id, user_id, query) VALUES "
        + ", ".join(["('q{i}', ?, 'padding-{i}')" for i in range(200)]),
        [user["user_id"]] * 200,
    )

    doc = build_user_export(user["user_id"], ALICE, con=tenant_con)
    blob = json.dumps(doc)

    assert len(blob.encode("utf-8")) <= 4096
    assert doc["truncated"] is True


# ── 3. per-user erasure ─────────────────────────────────────────────────


def test_per_user_erasure_removes_only_that_users_platform_rows(
    export_warehouse, tenant_con
):
    """A user's own records go; the other member's stay."""
    from api.user_data import delete_user_records

    mine = _make_user("erase_alice")
    theirs = _make_user("erase_other", tenant_id=ALICE)
    tenant_con.execute(
        "INSERT INTO analytics.chat_conversations (id, user_id, title) "
        "VALUES ('c1', ?, 'Mine'), ('c2', ?, 'Theirs')",
        [mine["user_id"], theirs["user_id"]],
    )
    tenant_con.execute(
        "INSERT INTO analytics.query_history (id, user_id, query) "
        "VALUES ('q1', ?, 'mine'), ('q2', ?, 'theirs')",
        [mine["user_id"], theirs["user_id"]],
    )

    summary = delete_user_records(mine["user_id"], con=tenant_con)

    assert "chat_conversations" in summary["tables"]
    left = tenant_con.execute(
        "SELECT user_id FROM analytics.chat_conversations ORDER BY id"
    ).fetchall()
    assert left == [(theirs["user_id"],)]
    left_hist = tenant_con.execute(
        "SELECT user_id FROM analytics.query_history ORDER BY id"
    ).fetchall()
    assert left_hist == [(theirs["user_id"],)]


def test_per_user_erasure_keeps_the_tenants_shared_tables(export_warehouse, tenant_con):
    """The tenant's data tables are shared — they must survive an erasure."""
    from api.user_data import delete_user_records

    user = _make_user("erase_alice")
    summary = delete_user_records(user["user_id"], con=tenant_con)

    assert summary["tenant_tables_removed"] == []
    rows = tenant_con.execute(f'SELECT COUNT(*) FROM "{tenant_schema(ALICE)}".sales').fetchone()
    assert rows[0] == 2


def test_per_user_erasure_does_not_touch_another_tenants_schema(
    export_warehouse, tenant_con
):
    """Erasure never reaches outside the caller's tenant."""
    from api.user_data import delete_user_records

    user = _make_user("erase_alice")
    delete_user_records(user["user_id"], con=tenant_con)

    rows = tenant_con.execute(
        f'SELECT COUNT(*) FROM "{tenant_schema(BOB)}".bob_secret'
    ).fetchone()
    assert rows[0] == 1


def test_per_user_erasure_writes_through_the_shared_connection(
    export_warehouse, monkeypatch
):
    """Without an explicit connection the erasure uses the shared one.

    The shared connection is the single writer of the warehouse, so the
    erasure must not open a second handle on the same file.
    """
    import api.db as db
    from api.user_data import delete_user_records

    con = duckdb.connect(export_warehouse)
    for stmt in _platform_ddl():
        con.execute(stmt)
    monkeypatch.setattr(db, "get_connection", lambda: con)

    user = _make_user("erase_shared")
    con.execute(
        "INSERT INTO analytics.query_history (id, user_id, query) VALUES ('q1', ?, 'mine')",
        [user["user_id"]],
    )

    summary = delete_user_records(user["user_id"])

    assert "query_history" in summary["tables"]
    assert con.execute("SELECT COUNT(*) FROM analytics.query_history").fetchone()[0] == 0
    con.close()


# ── 4. the self-service endpoint ────────────────────────────────────────


@pytest.fixture()
def export_client(export_warehouse, monkeypatch):
    """A client whose export reads the throwaway warehouse."""
    from fastapi.testclient import TestClient

    import api.state as state
    from etl.load import WarehouseLoader
    from config import settings

    loader = WarehouseLoader(
        db_path=export_warehouse,
        schema=settings.duckdb.schema,
        config=settings.duckdb.config,
    )
    monkeypatch.setattr(state, "loader", loader)

    from api.main import app
    from api.middleware.rate_limit import reset_rate_limits

    reset_rate_limits()
    return TestClient(app, raise_server_exceptions=False)


def test_the_export_endpoint_returns_the_callers_own_data(export_client, export_warehouse):
    """``GET /auth/me/export`` answers with the caller's document."""
    from api.auth import create_jwt_token

    user = _make_user("endpoint_alice", tenant_id=ALICE)
    headers = {"Authorization": f"Bearer {create_jwt_token('endpoint_alice', role='user')}"}

    response = export_client.get("/auth/me/export", headers=headers)

    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["subject"]["user_id"] == user["user_id"]
    assert doc["profile"]["username"] == "endpoint_alice"
    assert "attachment" in response.headers.get("content-disposition", "")


def test_the_export_endpoint_refuses_another_user(export_client):
    """A caller may not ask for somebody else's data."""
    from api.auth import create_jwt_token

    _make_user("endpoint_alice", tenant_id=ALICE)
    _make_user("endpoint_other", tenant_id=BOB)
    headers = {"Authorization": f"Bearer {create_jwt_token('endpoint_alice', role='user')}"}

    response = export_client.get("/auth/me/export?user_id=somebody-else", headers=headers)

    assert response.status_code == 403, response.text


def test_the_export_endpoint_never_carries_another_tenants_tables(export_client):
    """The HTTP export is tenant-scoped, exactly like the library call."""
    from api.auth import create_jwt_token

    _make_user("endpoint_alice", tenant_id=ALICE)
    headers = {"Authorization": f"Bearer {create_jwt_token('endpoint_alice', role='user')}"}

    response = export_client.get("/auth/me/export", headers=headers)

    assert response.status_code == 200, response.text
    doc = response.json()
    assert "bob_secret" not in doc["tenant_data"]["tables"]
    assert "sales" in doc["tenant_data"]["tables"]
    assert "99.0" not in json.dumps(doc)


def test_the_export_endpoint_requires_authentication(export_client):
    response = export_client.get("/auth/me/export")
    assert response.status_code == 401, response.text


def test_the_export_endpoint_is_audited(export_client, monkeypatch):
    """An export is an access to personal data — it leaves a trace."""
    import api.routes.account as account_mod
    from api.auth import create_jwt_token

    recorded = []

    class _Recorder:
        def log(self, event):
            recorded.append(event)

    monkeypatch.setattr(account_mod, "audit_logger", _Recorder())
    _make_user("endpoint_alice", tenant_id=ALICE)
    headers = {"Authorization": f"Bearer {create_jwt_token('endpoint_alice', role='user')}"}

    response = export_client.get("/auth/me/export", headers=headers)

    assert response.status_code == 200, response.text
    assert recorded, "the export was not audited"
    assert any(getattr(e, "action", "") == "data_export" for e in recorded)
