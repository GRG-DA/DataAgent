"""Cross-tenant isolation tests (ADR-009 D1).

DataAgent is a hosted multi-tenant SaaS: every tenant owns a dedicated
DuckDB schema and a request may only ever see its own tenant's data.

These tests are the guard for that promise.  They are written to FAIL if a
path forgets the tenant — which is exactly the class of bug that made the
pre-SaaS build unsafe (one global table namespace, no ``tenant_id``
anywhere, ``/upload`` and ``/load`` taking no identity).
"""
from __future__ import annotations

import os
import tempfile

import duckdb
import pytest

from api.tenancy import (
    DEFAULT_TENANT_ID,
    apply_tenant_search_path,
    assign_tenant,
    current_tenant_id,
    reset_current_tenant,
    set_current_tenant,
    tenant_schema,
    tenant_search_path,
    tenant_slug,
    use_tenant,
)


# ── Schema naming ─────────────────────────────────────────────────────────

def test_default_tenant_keeps_the_platform_schema():
    """The bootstrap admin keeps `analytics` so an existing install works."""
    assert tenant_schema(DEFAULT_TENANT_ID) == "analytics"
    assert tenant_schema(None) == "analytics"
    assert tenant_search_path(DEFAULT_TENANT_ID) == "analytics"


def test_each_tenant_gets_its_own_schema():
    a = tenant_schema("u_alice")
    b = tenant_schema("u_bob")
    assert a != b
    assert a.startswith("analytics_t_")
    assert b.startswith("analytics_t_")


def test_tenant_slug_is_sql_safe_and_collision_free():
    """A hostile tenant id must not escape into a SQL identifier."""
    for hostile in ["acme'; DROP TABLE x--", "a" * 500, "../../etc", "", "MiXeD Case"]:
        slug = tenant_slug(hostile)
        assert slug.replace("_", "").isalnum(), slug
        assert len(slug) <= 64
    # Different ids must not collapse onto the same schema.
    assert tenant_slug("acme") != tenant_slug("acme ")
    assert tenant_schema("acme") != tenant_schema("acme2")


def test_tenant_search_path_puts_the_tenant_first():
    path = tenant_search_path("u_alice")
    tenant = tenant_schema("u_alice")
    assert path.startswith(tenant + ",")
    assert path.endswith("analytics")


def test_assign_tenant_gives_the_first_account_the_default_tenant():
    assert assign_tenant("uid1", is_first_user=True) == DEFAULT_TENANT_ID
    assert assign_tenant("uid2", is_first_user=False) == "u_uid2"


# ── Request context ───────────────────────────────────────────────────────

def test_current_tenant_defaults_to_default():
    assert current_tenant_id() == DEFAULT_TENANT_ID


def test_tenant_context_is_restored_after_use():
    token = set_current_tenant("u_alice")
    try:
        assert current_tenant_id() == "u_alice"
    finally:
        reset_current_tenant(token)
    assert current_tenant_id() == DEFAULT_TENANT_ID


def test_use_tenant_is_scoped():
    with use_tenant("u_bob"):
        assert current_tenant_id() == "u_bob"
    assert current_tenant_id() == DEFAULT_TENANT_ID


# ── Real DuckDB isolation ─────────────────────────────────────────────────

@pytest.fixture
def two_tenant_db():
    """A warehouse with the same logical table name in two tenant schemas."""
    path = os.path.join(tempfile.gettempdir(), f"tenancy_{os.getpid()}.duckdb")
    for suffix in ("", ".wal"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    alice_schema = tenant_schema("u_alice")
    bob_schema = tenant_schema("u_bob")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{alice_schema}"')
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{bob_schema}"')
    con.execute(f'CREATE TABLE "{alice_schema}".sales AS SELECT 100 AS amount')
    con.execute(f'CREATE TABLE "{bob_schema}".sales AS SELECT 999 AS amount')
    con.close()
    yield path
    for suffix in ("", ".wal"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_same_table_name_coexists_per_tenant(two_tenant_db):
    """Two tenants can each own a table called `sales`."""
    alice = duckdb.connect(two_tenant_db)
    bob = duckdb.connect(two_tenant_db)
    try:
        apply_tenant_search_path(alice, "u_alice")
        apply_tenant_search_path(bob, "u_bob")
        assert alice.execute("SELECT amount FROM sales").fetchall() == [(100,)]
        assert bob.execute("SELECT amount FROM sales").fetchall() == [(999,)]
    finally:
        alice.close()
        bob.close()


def test_a_tenant_cannot_see_another_tenants_table(two_tenant_db):
    """A cross-tenant schema reference is refused at the SQL surface.

    DuckDB has no per-session schema ACLs, so an *explicitly qualified*
    reference (``other_schema.sales``) resolves regardless of the search
    path.  The enforcement point for that is the SQL policy, which refuses
    any reference to another tenant's ``analytics_t_*`` schema (see
    ``test_sql_policy_blocks_another_tenants_schema``).  The search path
    handles the unqualified case, which is what the app's own queries use.
    """
    from agent.tools_sql import SQLTool

    bob_schema = tenant_schema("u_bob")
    tool = SQLTool()
    ok, err = tool.validate_query(f"SELECT amount FROM {bob_schema}.sales")
    assert not ok, "the SQL policy allowed a cross-tenant schema reference"
    assert "Access denied" in (err or ""), err

    # And the tenant's own schema is reachable, so the policy is not
    # blocking legitimate access.
    ok, err = tool.validate_query("SELECT amount FROM sales")
    assert ok, err


def test_a_connection_without_a_search_path_sees_nothing(two_tenant_db):
    """Fail-closed: no search path means unqualified names do not resolve."""
    con = duckdb.connect(two_tenant_db)
    try:
        with pytest.raises(duckdb.CatalogException):
            con.execute("SELECT amount FROM sales").fetchall()
    finally:
        con.close()


def test_platform_tables_remain_reachable_from_a_tenant(two_tenant_db):
    """A tenant's session can still reach the platform schema."""
    con = duckdb.connect(two_tenant_db)
    try:
        con.execute("CREATE TABLE analytics.user_subscriptions (user_id VARCHAR)")
        apply_tenant_search_path(con, "u_alice")
        assert con.execute("SELECT COUNT(*) FROM user_subscriptions").fetchall() == [(0,)]
    finally:
        con.close()


# ── SQL policy ────────────────────────────────────────────────────────────

def test_sql_policy_blocks_another_tenants_schema():
    from agent.tools_sql import SQLTool

    tool = SQLTool()
    for q in [
        "SELECT * FROM analytics_t_bob.sales",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
    ]:
        ok, err = tool.validate_query(q)
        assert not ok, f"query was allowed: {q}"
        assert "Access denied" in (err or ""), err


def test_sql_policy_allows_the_callers_own_schema():
    """A qualified reference to the CALLER's own schema is safe.

    The guard exists to stop a query reaching another tenant's data. The
    caller's own schema is their own data, and the app itself used to
    hand the model such references - which then came back rejected with
    "Access denied to schema" and the user saw no rows at all.
    """
    from agent.tools_sql import SQLTool

    tool = SQLTool()
    with use_tenant("u_alice"):
        own = tenant_schema("u_alice")
        ok, err = tool.validate_query(f"SELECT amount FROM {own}.sales")
        assert ok, f"the caller's own schema was refused: {err}"
        # ... and another tenant's is still refused, from the same context.
        bob = tenant_schema("u_bob")
        ok2, err2 = tool.validate_query(f"SELECT amount FROM {bob}.sales")
        assert not ok2, "another tenant's schema was allowed"
        assert "Access denied" in (err2 or ""), err2


def test_sql_schema_context_names_tables_without_a_schema_prefix():
    """The SQL prompt must name tables the way the executor accepts them.

    The system prompt tells the model to use BARE table names (the query
    runs in the caller's workspace, where an unqualified name resolves to
    their copy of the table). When the schema context qualified the table
    with the tenant schema, the model followed the context and produced
    ``FROM analytics_t_<tenant>.table`` - which the policy refuses, so
    every AI-generated query failed with HTTP 400.
    """
    from agent import sqlgen

    captured = {}

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _Db:
        description = [("amount",), ("region",)]

        def execute(self, q, *a, **k):
            captured.setdefault("queries", []).append(q)
            head = q.strip().upper()
            if head.startswith("DESCRIBE"):
                return _Rows([("amount", "DOUBLE"), ("region", "VARCHAR")])
            if head.startswith("SELECT COUNT"):
                return _Rows([(12,)])
            if head.startswith("SELECT *"):
                return _Rows([])
            return _Rows([])

        def close(self):
            pass

    monkey = sqlgen._connect
    sqlgen._connect = lambda: _Db()
    try:
        ctx = sqlgen._sql_schema_context(["ai_probe_sales"])
    finally:
        sqlgen._connect = monkey

    assert ctx, "the schema context must not be empty"
    assert "ai_probe_sales" in ctx
    assert "analytics_t_" not in ctx, (
        "the schema context must not schema-qualify the table - the model "
        "copies it and the executor refuses a qualified tenant schema:\n" + ctx
    )


def test_sql_policy_blocks_platform_tables():
    from agent.tools_sql import SQLTool

    tool = SQLTool()
    ok, err = tool.validate_query("SELECT * FROM user_subscriptions")
    assert not ok
    assert "Access denied" in (err or "")


def test_sql_policy_blocks_duckdb_settings():
    from agent.tools_sql import SQLTool

    tool = SQLTool()
    ok, err = tool.validate_query("SELECT * FROM duckdb_settings()")
    assert not ok, "duckdb_settings() was allowed"


# ── Error messages must not leak another tenant ──────────────────────────

def test_error_message_does_not_leak_another_tenant_schema():
    """DuckDB's "did you mean" hint can name another tenant's schema."""
    from api.tenancy import sanitize_error_message

    raw = (
        'Catalog Error: Table with name alice_secret does not exist!\n'
        'Did you mean "analytics_t_u_yixgpoh7zway8onidh2bbw_c199e511.alice_secret"?\n'
        "LINE 1: SELECT * FROM alice_secret"
    )
    clean = sanitize_error_message(raw)
    assert "analytics_t_u_yixgpoh7zway8onidh2bbw_c199e511" not in clean, clean
    assert "analytics_t_" not in clean, clean
    assert "does not exist" in clean, clean


def test_error_message_keeps_ordinary_diagnostics():
    from api.tenancy import sanitize_error_message

    msg = "Parser Error: syntax error at or near \"FROM\""
    assert sanitize_error_message(msg) == msg
