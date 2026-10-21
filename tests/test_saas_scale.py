"""Scale substrate — the horizontal-scale workstream (ADR-009 follow-up).

Covers the four pieces that let a hosted deployment run more than one
replica:

* the PostgreSQL identity store (schema parity with SQLite, backend
  selection and its fallback),
* the shared session store (memory / redis / postgres selection),
* the billing connection routing (the process-wide shared connection, not
  a fresh DuckDB handle per call) and the per-tenant AI budget,
* the entitlement signing key (source precedence, owner-only file mode).

The Postgres server itself is optional: the schema parity and the SQL
construction are asserted without a server, and the server-backed tests skip
when psycopg or the database is unavailable.
"""

from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────


def _ddl_columns(ddl: str, table: str) -> tuple[str, ...]:
    """Column names of *table* inside a ``CREATE TABLE`` block.

    Deliberately a dumb parser: the point is to compare two DDL texts that
    are written in the same style, so a column added to one and forgotten
    in the other shows up as a difference.
    """
    pattern = re.compile(
        rf"CREATE TABLE IF NOT EXISTS\s+{re.escape(table)}\s*\((.*?)\n\);",
        re.S | re.I,
    )
    match = pattern.search(ddl)
    assert match, f"table {table!r} not found in the DDL"
    columns: list[str] = []
    for line in match.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        head = line.split()[0].upper()
        if head in ("PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "CHECK", "EXCLUDE"):
            continue
        columns.append(line.split()[0].strip('"'))
    return tuple(columns)


def _clear_identity_env(monkeypatch) -> None:
    for name in ("DATAAGENT_USER_STORE", "DATABASE_URL", "POSTGRES_DSN", "DATABASE_DSN"):
        monkeypatch.delenv(name, raising=False)


# ── A. PostgreSQL identity store ──────────────────────────────────────


def test_postgres_store_module_imports_without_psycopg():
    """The module must import cleanly when psycopg is not installed."""
    import api.user_store_postgres as pg

    assert pg.POSTGRES_SCHEMA_SQL
    assert pg.PostgresUserStore is not None


def test_postgres_users_table_has_the_same_columns_as_sqlite():
    from api.user_store import _SCHEMA
    from api.user_store_postgres import POSTGRES_SCHEMA_SQL

    assert _ddl_columns(POSTGRES_SCHEMA_SQL, "users") == _ddl_columns(_SCHEMA, "users")


@pytest.mark.parametrize("table", ["account_tokens", "revoked_jtis", "store_meta"])
def test_postgres_shared_tables_have_the_same_columns_as_sqlite(table):
    from api.user_store import _SCHEMA
    from api.user_store_postgres import POSTGRES_SCHEMA_SQL

    assert _ddl_columns(POSTGRES_SCHEMA_SQL, table) == _ddl_columns(_SCHEMA, table)


def test_postgres_users_table_carries_the_account_lifecycle_columns():
    from api.user_store_postgres import POSTGRES_SCHEMA_SQL

    columns = _ddl_columns(POSTGRES_SCHEMA_SQL, "users")
    for column in (
        "email_verified",
        "pending_email",
        "invited_by",
        "invite_accepted_at",
        "last_login_at",
        "must_change_password",
    ):
        assert column in columns


def test_postgres_insert_statement_uses_bound_placeholders_only():
    """Values must never be interpolated into the SQL text."""
    from api.user_store_postgres import build_insert_statement

    sql, columns = build_insert_statement()
    assert "?" not in sql
    assert "%s" in sql
    assert sql.count("%s") == len(columns)


def test_postgres_update_statement_uses_bound_placeholders_only():
    from api.user_store_postgres import build_update_statement

    sql = build_update_statement(["email", "is_active"])
    assert "?" not in sql
    assert sql.count("%s") == 3  # two columns + the username


def test_postgres_dsn_prefers_database_url_over_postgres_dsn(monkeypatch):
    from api.user_store_postgres import postgres_dsn

    monkeypatch.setenv("DATABASE_URL", "postgresql://one/db")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://two/db")
    assert postgres_dsn() == "postgresql://one/db"


def test_postgres_dsn_falls_back_to_postgres_dsn(monkeypatch):
    from api.user_store_postgres import postgres_dsn

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://two/db")
    assert postgres_dsn() == "postgresql://two/db"


# ── A. store selection ────────────────────────────────────────────────


def test_user_store_defaults_to_sqlite(monkeypatch):
    from api.user_store import SQLiteUserStore, get_user_store, reset_user_store_cache

    _clear_identity_env(monkeypatch)
    reset_user_store_cache()
    try:
        assert isinstance(get_user_store(), SQLiteUserStore)
    finally:
        reset_user_store_cache()


def test_user_store_honours_the_json_backend(monkeypatch, tmp_path):
    from api.user_store import JSONUserStore, get_user_store, reset_user_store_cache

    monkeypatch.setenv("DATAAGENT_USER_STORE", "json")
    monkeypatch.setenv("DATAAGENT_USER_JSON", str(tmp_path / "users.json"))
    reset_user_store_cache()
    try:
        assert isinstance(get_user_store(), JSONUserStore)
    finally:
        reset_user_store_cache()


def _fake_postgres_store(monkeypatch):
    """Replace the PostgreSQL store with a stub (no server needed)."""
    import api.user_store_postgres as pg

    class _FakePostgresUserStore:
        def __init__(self, *args, **kwargs):
            self.dsn = "postgresql://localhost/db"

        def close(self):
            pass

    monkeypatch.setattr(pg, "PostgresUserStore", _FakePostgresUserStore)
    return _FakePostgresUserStore


def test_user_store_selects_postgres_when_asked(monkeypatch):
    from api.user_store import get_user_store, reset_user_store_cache

    fake = _fake_postgres_store(monkeypatch)
    monkeypatch.setenv("DATAAGENT_USER_STORE", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    reset_user_store_cache()
    try:
        assert isinstance(get_user_store(), fake)
    finally:
        reset_user_store_cache()


def test_user_store_prefers_postgres_when_database_url_is_set(monkeypatch):
    from api.user_store import get_user_store, reset_user_store_cache

    fake = _fake_postgres_store(monkeypatch)
    monkeypatch.delenv("DATAAGENT_USER_STORE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    reset_user_store_cache()
    try:
        assert isinstance(get_user_store(), fake)
    finally:
        reset_user_store_cache()


def test_user_store_falls_back_to_sqlite_when_postgres_is_unreachable(monkeypatch, caplog):
    """A dead DATABASE_URL must not brick the app — and must be logged."""
    import api.user_store as us

    monkeypatch.delenv("DATAAGENT_USER_STORE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/nope")
    us.reset_user_store_cache()
    try:
        with caplog.at_level("ERROR"):
            store = us.get_user_store()
        assert isinstance(store, us.SQLiteUserStore)
        assert any("postgres" in record.message.lower() for record in caplog.records)
    finally:
        us.reset_user_store_cache()


def test_identity_db_backend_follows_the_store_selection(monkeypatch):
    from api.user_store import identity_backend

    monkeypatch.setenv("DATAAGENT_USER_STORE", "postgres")
    assert identity_backend() == "postgres"
    monkeypatch.setenv("DATAAGENT_USER_STORE", "json")
    assert identity_backend() == "json"
    monkeypatch.delenv("DATAAGENT_USER_STORE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    assert identity_backend() == "postgres"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert identity_backend() == "sqlite"


# ── B. shared sessions ────────────────────────────────────────────────


def test_session_backend_defaults_to_memory(monkeypatch):
    from api.session import select_session_backend

    monkeypatch.delenv("SESSION_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    assert select_session_backend() == "memory"


def test_session_backend_is_redis_when_redis_url_is_set(monkeypatch):
    from api.session import select_session_backend

    monkeypatch.delenv("SESSION_BACKEND", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    assert select_session_backend() == "redis"


def test_session_backend_is_postgres_when_database_url_is_set(monkeypatch):
    from api.session import select_session_backend

    monkeypatch.delenv("SESSION_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    assert select_session_backend() == "postgres"


def test_explicit_session_backend_wins_over_the_defaults(monkeypatch):
    from api.session import select_session_backend

    monkeypatch.setenv("SESSION_BACKEND", "memory")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    assert select_session_backend() == "memory"


def test_session_store_module_exposes_a_postgres_backend():
    import api.session_postgres as sp

    assert sp.PostgresSessionStore is not None
    assert "CREATE TABLE IF NOT EXISTS" in sp.POSTGRES_SCHEMA_SQL


def test_postgres_session_store_mirrors_the_redis_contract():
    from api.session import SessionStore
    from api.session_postgres import PostgresSessionStore

    for method in ("create", "get", "update", "delete", "get_user_sessions", "cleanup_expired"):
        assert callable(getattr(PostgresSessionStore, method, None)), method
    assert issubclass(PostgresSessionStore, SessionStore)


# ── B. revoked JTI shared between replicas ────────────────────────────


def test_revocation_written_through_one_store_is_seen_by_another(tmp_path):
    from api.auth import RevocationStore

    db_path = tmp_path / "identity.db"
    first = RevocationStore(db_path=db_path)
    second = RevocationStore(db_path=db_path)
    try:
        assert first.create_revocation("jti-shared", time.time() + 600) is True
        assert second.is_revoked("jti-shared") is True
    finally:
        first.close()
        second.close()


# ── C. billing connection routing ─────────────────────────────────────


def test_billing_schema_does_not_open_its_own_warehouse_connection():
    """A fresh DuckDB handle per call is the bug this routing removes."""
    import inspect

    from api.billing import schema

    source = inspect.getsource(schema)
    assert "duckdb.connect" not in source
    assert "WarehouseLoader" not in source


def test_billing_get_connection_returns_the_shared_connection(billing_db):
    from api.billing.schema import get_connection
    from api.db import get_connection as shared_connection

    with get_connection() as con:
        assert con is shared_connection()


def test_billing_writes_persist_through_the_shared_connection(billing_db):
    """A rolled-back transaction would silently break the AI cap."""
    from api.billing.schema import SCHEMA, get_connection

    with get_connection() as con:
        con.execute(
            f"INSERT INTO {SCHEMA}.usage_events (id, user_id, metric, amount) "
            "VALUES ('persisted-1', 'u1', 'ai_requests', 1)"
        )
    with get_connection() as con:
        row = con.execute(
            f"SELECT amount FROM {SCHEMA}.usage_events WHERE id = 'persisted-1'"
        ).fetchone()
    assert row is not None and int(row[0]) == 1


def test_metering_module_does_not_use_a_fresh_connection_per_call():
    import inspect

    from api.billing import metering

    source = inspect.getsource(metering)
    assert "duckdb.connect" not in source


# ── E. per-tenant AI budget ───────────────────────────────────────────


def test_usage_records_table_carries_a_tenant_id_column(billing_db):
    from api.billing.schema import SCHEMA, get_connection

    with get_connection() as con:
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = 'usage_records'",
            [SCHEMA],
        ).fetchall()
    assert "tenant_id" in {row[0] for row in rows}


def test_tenant_daily_cap_blocks_the_second_user_of_the_tenant(billing_db, monkeypatch):
    from api.billing.metering import UsageLimitError, record_usage

    monkeypatch.setenv("BILLING_TENANT_AI_DAILY_CAP", "2")
    record_usage("tenant-user-1", "ai_requests", tenant_id="acme")
    record_usage("tenant-user-2", "ai_requests", tenant_id="acme")
    with pytest.raises(UsageLimitError):
        record_usage("tenant-user-3", "ai_requests", tenant_id="acme")


def test_tenant_daily_cap_of_zero_is_unlimited(billing_db, monkeypatch):
    from api.billing.metering import get_tenant_usage, record_usage

    monkeypatch.setenv("BILLING_TENANT_AI_DAILY_CAP", "0")
    for _ in range(20):
        record_usage("tenant-user-1", "ai_requests", tenant_id="acme")
    usage = get_tenant_usage("acme")
    assert usage["limit"] == -1  # -1 = unlimited
    assert usage["remaining"] == -1


def test_tenant_daily_cap_does_not_leak_across_tenants(billing_db, monkeypatch):
    from api.billing.metering import record_usage

    monkeypatch.setenv("BILLING_TENANT_AI_DAILY_CAP", "1")
    record_usage("tenant-user-1", "ai_requests", tenant_id="acme")
    # A different tenant has its own budget.
    record_usage("tenant-user-9", "ai_requests", tenant_id="globex")


def test_get_tenant_usage_reports_the_tenant_aggregate(billing_db, monkeypatch):
    from api.billing.metering import get_tenant_usage, record_usage

    monkeypatch.setenv("BILLING_TENANT_AI_DAILY_CAP", "10")
    record_usage("tenant-user-1", "ai_requests", tenant_id="acme")
    record_usage("tenant-user-2", "ai_requests", tenant_id="acme")

    usage = get_tenant_usage("acme")
    assert usage["tenant_id"] == "acme"
    assert usage["used"] == 2
    assert usage["limit"] == 10
    assert usage["remaining"] == 8
    assert {row["user_id"] for row in usage["users"]} == {"tenant-user-1", "tenant-user-2"}


def test_get_tenant_usage_of_an_unknown_tenant_is_empty(billing_db):
    from api.billing.metering import get_tenant_usage

    usage = get_tenant_usage("nobody-here")
    assert usage["used"] == 0
    assert usage["users"] == []


def test_tenant_cap_is_read_from_the_environment(monkeypatch):
    from api.billing.metering import tenant_ai_daily_cap

    monkeypatch.setenv("BILLING_TENANT_AI_DAILY_CAP", "7")
    assert tenant_ai_daily_cap() == 7
    monkeypatch.setenv("BILLING_TENANT_AI_DAILY_CAP", "not-a-number")
    assert tenant_ai_daily_cap() == 0


# ── D. the grant signing key leaves the data host ─────────────────────


def test_grant_key_environment_value_wins(monkeypatch, tmp_path):
    from api.billing.grant_signature import load_grant_key

    monkeypatch.setenv("DATAAGENT_GRANT_KEY", "e" * 40)
    monkeypatch.setenv("DATAAGENT_GRANT_KEY_FILE", str(tmp_path / "ignored.key"))
    assert load_grant_key() == b"e" * 40


def test_grant_key_environment_file_wins_over_the_default_path(monkeypatch, tmp_path):
    from api.billing.grant_signature import load_grant_key

    key_file = tmp_path / "grant.key"
    key_file.write_bytes(b"f" * 40)
    monkeypatch.delenv("DATAAGENT_GRANT_KEY", raising=False)
    monkeypatch.setenv("DATAAGENT_GRANT_KEY_FILE", str(key_file))
    assert load_grant_key() == b"f" * 40


def test_grant_key_falls_back_to_the_legacy_data_path(monkeypatch, tmp_path):
    import api.billing.grant_signature as gs

    legacy = tmp_path / ".grant_key"
    legacy.write_bytes(b"g" * 40)
    monkeypatch.delenv("DATAAGENT_GRANT_KEY", raising=False)
    monkeypatch.delenv("DATAAGENT_GRANT_KEY_FILE", raising=False)
    monkeypatch.setattr(gs, "_key_path", lambda: str(legacy))
    assert gs.load_grant_key() == b"g" * 40


def test_grant_key_is_created_with_owner_only_permissions(monkeypatch, tmp_path):
    """The key file must never be readable by anyone but its owner."""
    import api.billing.grant_signature as gs

    target = tmp_path / ".grant_key"
    monkeypatch.delenv("DATAAGENT_GRANT_KEY", raising=False)
    monkeypatch.delenv("DATAAGENT_GRANT_KEY_FILE", raising=False)
    monkeypatch.setattr(gs, "_key_path", lambda: str(target))

    modes: list[int] = []
    real_open = os.open

    def _recording_open(path, flags, mode=0o777, **kwargs):
        modes.append(mode)
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(gs.os, "open", _recording_open)

    key = gs.load_grant_key()
    assert len(key) >= 32
    assert 0o600 in modes
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_grant_key_source_is_logged_without_the_key(monkeypatch, tmp_path, caplog):
    import api.billing.grant_signature as gs

    key_file = tmp_path / "grant.key"
    key_file.write_bytes(b"h" * 40)
    monkeypatch.delenv("DATAAGENT_GRANT_KEY", raising=False)
    monkeypatch.setenv("DATAAGENT_GRANT_KEY_FILE", str(key_file))
    with caplog.at_level("INFO"):
        gs.load_grant_key()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "h" * 40 not in messages
    assert "grant key" in messages.lower()


# ── F. configuration ──────────────────────────────────────────────────


def test_settings_expose_the_scale_substrate_keys():
    from config import settings

    assert hasattr(settings, "postgres")
    assert hasattr(settings.postgres, "url")
    assert hasattr(settings.session, "backend")
    assert hasattr(settings.billing, "tenant_ai_daily_cap")
    assert hasattr(settings.billing, "grant_key_file")
    assert hasattr(settings.tenancy, "per_tenant_database")


def test_settings_yaml_keeps_the_per_tenant_database_key():
    from config import load_settings_config

    yaml_config = load_settings_config()
    tenancy = yaml_config.get("security", {}).get("tenancy", {})
    assert "per_tenant_database" in tenancy


def test_requirements_saas_declares_psycopg():
    requirements = Path("requirements-saas.txt")
    assert requirements.exists()
    text = requirements.read_text(encoding="utf-8")
    assert "psycopg" in text
