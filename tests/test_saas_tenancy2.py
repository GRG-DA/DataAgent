"""Tenancy completeness + correctness regressions (ADR-009 follow-up).

ADR-009 D1 gave every tenant its own DuckDB schema and made the SQL policy
refuse cross-tenant references.  This file guards the *remaining* gaps:

1. the data catalog is tenant-scoped — an asset never leaks to another
   tenant and registration stamps the owner,
2. retention policies and the PII inventory are tenant-scoped — a retention
   run must never delete another tenant's rows,
3. the opt-in per-tenant database mode routes the loader to a per-tenant
   file, so even a hostile DuckDB client cannot reach another tenant's data,
4. the visual query builder derives GROUP BY from the non-aggregated select
   fields instead of emitting invalid SQL,
5. an unknown hypothesis test type is an explicit error, not a silent
   success, and
6. the SQL policy refuses the system catalogs for *every* caller.
"""
from __future__ import annotations

import os

import duckdb
import pytest

from api.tenancy import (
    DEFAULT_TENANT_ID,
    tenant_database_path,
    tenant_schema,
    tenant_slug,
    use_tenant,
)

PER_TENANT_ENV = "DATAAGENT_PER_TENANT_DATABASE"


# ── helpers ────────────────────────────────────────────────────────────


def _warehouse(tmp_path, name="wh.duckdb"):
    """A warehouse file with the platform schema and two tenant schemas."""
    path = str(tmp_path / name)
    alice = tenant_schema("u_alice")
    bob = tenant_schema("u_bob")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{alice}"')
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{bob}"')
    con.close()
    return path


def _tables(path, table_name):
    """(schema, table) rows for *table_name* in the warehouse at *path*."""
    con = duckdb.connect(path)
    try:
        rows = con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_name = ?",
            [table_name],
        ).fetchall()
    finally:
        con.close()
    return rows


def _columns(path, schema, table):
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


# ── 1. the data catalog is tenant-scoped ───────────────────────────────


@pytest.fixture()
def catalog(tmp_path):
    """A catalog manager on a throwaway warehouse."""
    from api.catalog import CatalogManager

    path = _warehouse(tmp_path, "catalog.duckdb")
    return CatalogManager(db_path=path)


def test_catalog_assets_carry_a_tenant_id(catalog):
    """The catalog table has a tenant column, added by an idempotent migration."""
    assert "tenant_id" in _columns(catalog.db_path, "analytics", "catalog_assets")


def test_catalog_migration_is_idempotent(tmp_path):
    """Re-opening the manager must not fail on an existing tenant column."""
    from api.catalog import CatalogManager

    path = _warehouse(tmp_path, "catalog2.duckdb")
    CatalogManager(db_path=path)
    CatalogManager(db_path=path)
    assert "tenant_id" in _columns(path, "analytics", "catalog_assets")


def test_register_asset_stamps_the_tenant(catalog):
    """Registration records who owns the asset."""
    with use_tenant("u_alice"):
        asset = catalog.register_asset("alice_sales", name="Alice sales")
    assert asset.tenant_id == "u_alice"


def test_list_assets_returns_only_the_callers_tenant(catalog):
    """A tenant never sees another tenant's catalog entries."""
    with use_tenant("u_alice"):
        catalog.register_asset("alice_sales")
    with use_tenant("u_bob"):
        catalog.register_asset("bob_sales")

    with use_tenant("u_alice"):
        names = {a["table_name"] for a in catalog.list_assets()}
    assert "alice_sales" in names
    assert "bob_sales" not in names

    with use_tenant("u_bob"):
        names = {a["table_name"] for a in catalog.list_assets()}
    assert "bob_sales" in names
    assert "alice_sales" not in names


def test_get_asset_does_not_leak_another_tenants_asset(catalog):
    """Fetching an asset by id is scoped like the list."""
    with use_tenant("u_alice"):
        asset = catalog.register_asset("alice_sales")

    with use_tenant("u_bob"):
        assert catalog.get_asset(asset.id) is None

    with use_tenant("u_alice"):
        assert catalog.get_asset(asset.id) is not None


def test_tenant_less_assets_are_visible_to_the_operator_only(catalog):
    """A platform (tenant-less) asset is admin-only."""
    with duckdb.connect(catalog.db_path) as con:
        con.execute(
            "INSERT INTO analytics.catalog_assets (id, name, table_name, tenant_id) "
            "VALUES ('platform-1', 'Platform', 'platform_table', '')"
        )

    with use_tenant("u_alice"):
        ids = {a["id"] for a in catalog.list_assets()}
    assert "platform-1" not in ids

    # The operator tenant (the platform/`default` tenant) sees it.
    with use_tenant(DEFAULT_TENANT_ID):
        ids = {a["id"] for a in catalog.list_assets()}
    assert "platform-1" in ids


def test_a_caller_supplied_tenant_id_cannot_widen_the_scope(catalog):
    """A payload that names another tenant must be ignored."""
    with use_tenant("u_bob"):
        bob_asset = catalog.register_asset("bob_sales")

    with use_tenant("u_alice"):
        catalog.update_asset(bob_asset.id, tenant_id="u_bob", description="hijacked")

    with use_tenant("u_bob"):
        assert catalog.get_asset(bob_asset.id)["description"] != "hijacked"


def test_catalog_routes_do_not_leak_across_tenants(tmp_path, monkeypatch):
    """``GET /catalog/assets`` and ``/catalog/assets/{id}`` are tenant-scoped."""
    from fastapi.testclient import TestClient

    import api.auth as auth_mod
    import api.catalog as catalog_mod
    import api.middleware.auth_middleware as mw_mod
    from api.main import app
    from api.middleware.rate_limit import reset_rate_limits

    path = _warehouse(tmp_path, "catalog_http.duckdb")
    manager = catalog_mod.CatalogManager(db_path=path)
    monkeypatch.setattr(catalog_mod, "catalog_manager", manager)

    users = auth_mod.UserManager(auth_file=str(tmp_path / "users.json"))
    monkeypatch.setattr(auth_mod, "user_manager", users)
    monkeypatch.setattr(mw_mod, "user_manager", users)
    users.add_user("alice_cat", password="Str0ng!Passw0rd", role="user", tenant_id="u_alice")
    users.add_user("bob_cat", password="Str0ng!Passw0rd", role="user", tenant_id="u_bob")

    from api.auth import create_jwt_token

    alice = {"Authorization": f"Bearer {create_jwt_token('alice_cat', role='user')}"}
    bob = {"Authorization": f"Bearer {create_jwt_token('bob_cat', role='user')}"}

    with use_tenant("u_alice"):
        asset_id = manager.register_asset("alice_sales").id
    with use_tenant("u_bob"):
        manager.register_asset("bob_sales")

    reset_rate_limits()
    client = TestClient(app, raise_server_exceptions=False)

    listed = client.get("/catalog/assets", headers=alice)
    assert listed.status_code == 200, listed.text
    names = {a["table_name"] for a in listed.json()["assets"]}
    assert "alice_sales" in names
    assert "bob_sales" not in names

    detail = client.get(f"/catalog/assets/{asset_id}", headers=bob)
    assert detail.status_code == 200, detail.text
    assert detail.json().get("success") is False
    assert detail.json().get("asset") is None

    own = client.get(f"/catalog/assets/{asset_id}", headers=alice)
    assert own.status_code == 200, own.text
    assert own.json()["asset"]["table_name"] == "alice_sales"


# ── 2. retention and PII are tenant-scoped ─────────────────────────────


@pytest.fixture()
def compliance(tmp_path):
    """A compliance manager on a warehouse with two tenants' tables."""
    from api.compliance import ComplianceManager

    path = _warehouse(tmp_path, "compliance.duckdb")
    alice = tenant_schema("u_alice")
    bob = tenant_schema("u_bob")
    con = duckdb.connect(path)
    con.execute(f'CREATE TABLE "{alice}".events (ts DATE, label VARCHAR)')
    con.execute(f'CREATE TABLE "{bob}".events (ts DATE, label VARCHAR)')
    con.execute(f'CREATE TABLE "{alice}".alice_customers (email VARCHAR)')
    con.execute(f'CREATE TABLE "{bob}".bob_customers (email VARCHAR)')
    con.execute(
        f"INSERT INTO \"{alice}\".events VALUES (DATE '2020-01-01', 'old'), (DATE '2030-01-01', 'new')"
    )
    con.execute(
        f"INSERT INTO \"{bob}\".events VALUES (DATE '2020-01-01', 'old'), (DATE '2030-01-01', 'new')"
    )
    con.close()
    return ComplianceManager(db_path=path)


def test_retention_policies_carry_a_tenant_id(compliance):
    assert "tenant_id" in _columns(compliance.db_path, "analytics", "retention_policies")


def test_retention_policy_is_stamped_with_the_tenant(compliance):
    with use_tenant("u_alice"):
        policy = compliance.create_retention_policy("events", retention_days=30)
    assert policy.tenant_id == "u_alice"


def test_retention_only_runs_the_callers_tenant_policies(compliance):
    """A retention run must not delete another tenant's rows."""
    with use_tenant("u_alice"):
        compliance.create_retention_policy("events", retention_days=30)
    with use_tenant("u_bob"):
        compliance.create_retention_policy("events", retention_days=30)

    with use_tenant("u_alice"):
        result = compliance.execute_retention()

    executed = [r["table"] for r in result["results"] if r.get("status") == "success"]
    assert executed == ["events"], result

    alice = tenant_schema("u_alice")
    bob = tenant_schema("u_bob")
    con = duckdb.connect(compliance.db_path)
    try:
        alice_rows = con.execute(f'SELECT COUNT(*) FROM "{alice}".events').fetchone()[0]
        bob_rows = con.execute(f'SELECT COUNT(*) FROM "{bob}".events').fetchone()[0]
    finally:
        con.close()
    assert alice_rows == 1, "alice's expired row must be gone"
    assert bob_rows == 2, "bob's rows must be untouched"


def test_pii_inventory_is_tenant_scoped(compliance):
    """Only the caller's tenant tables are scanned."""
    assert "tenant_id" in _columns(compliance.db_path, "analytics", "pii_inventory")

    with use_tenant("u_alice"):
        found = compliance.scan_pii_inventory()
    tables = {item["table"] for item in found}
    assert "alice_customers" in tables
    assert "bob_customers" not in tables

    with use_tenant("u_alice"):
        inventory = compliance.get_pii_inventory()
    assert {row["table_name"] for row in inventory} == {"alice_customers"}


def test_retention_policies_are_listed_per_tenant(compliance):
    with use_tenant("u_alice"):
        compliance.create_retention_policy("events", retention_days=30)
    with use_tenant("u_bob"):
        compliance.create_retention_policy("events", retention_days=30)

    with use_tenant("u_alice"):
        alice = compliance.list_retention_policies()
    assert len(alice) == 1
    assert alice[0]["tenant_id"] == "u_alice"

    with use_tenant("u_bob"):
        bob = compliance.list_retention_policies()
    assert len(bob) == 1
    assert bob[0]["tenant_id"] == "u_bob"


def test_a_retention_update_cannot_name_another_tenant(compliance):
    """A payload that names another tenant must be ignored."""
    with use_tenant("u_bob"):
        policy = compliance.create_retention_policy("events", retention_days=30)

    with use_tenant("u_alice"):
        compliance.update_retention_policy(
            policy.id, tenant_id="u_bob", retention_days=1
        )

    with use_tenant("u_bob"):
        row = compliance.list_retention_policies()[0]
        assert row["retention_days"] == 30


# ── 3. the opt-in per-tenant database mode ────────────────────────────


def test_per_tenant_database_is_off_by_default(monkeypatch):
    """Existing installs are unchanged: the flag defaults to off."""
    from api.tenancy import per_tenant_database_enabled

    monkeypatch.delenv(PER_TENANT_ENV, raising=False)
    assert per_tenant_database_enabled() is False


def test_tenant_database_path_is_shared_when_the_mode_is_off(tmp_path, monkeypatch):
    from api.tenancy import per_tenant_database_enabled

    monkeypatch.delenv(PER_TENANT_ENV, raising=False)
    shared = str(tmp_path / "curated" / "warehouse.duckdb")
    assert per_tenant_database_enabled() is False
    assert tenant_database_path("u_alice", shared_path=shared) == shared


def test_tenant_database_path_is_per_tenant_when_the_mode_is_on(tmp_path, monkeypatch):
    from api.tenancy import per_tenant_database_enabled

    monkeypatch.setenv(PER_TENANT_ENV, "1")
    shared = str(tmp_path / "curated" / "warehouse.duckdb")
    assert per_tenant_database_enabled() is True

    path = tenant_database_path("u_alice", shared_path=shared)
    assert path == str(
        tmp_path / "tenants" / tenant_slug("u_alice") / "warehouse.duckdb"
    ), path

    # The platform tenant keeps the shared file.
    assert tenant_database_path(DEFAULT_TENANT_ID, shared_path=shared) == shared


def test_loader_writes_to_the_tenant_file_when_the_mode_is_on(tmp_path, monkeypatch):
    """The tenant's data leaves the shared warehouse entirely."""
    import pandas as pd

    from etl.load import WarehouseLoader

    monkeypatch.setenv(PER_TENANT_ENV, "1")
    shared = str(tmp_path / "curated" / "warehouse.duckdb")
    loader = WarehouseLoader(db_path=shared, schema="analytics")

    df = pd.DataFrame({"amount": [1, 2, 3]})
    with use_tenant("u_alice"):
        loader.load_dataframe(df, "sales")

    tenant_file = tenant_database_path("u_alice", shared_path=shared)
    assert os.path.exists(tenant_file), tenant_file
    assert _tables(tenant_file, "sales") == [
        (tenant_schema("u_alice"), "sales")
    ]
    # ...and it is NOT in the shared warehouse.
    assert _tables(shared, "sales") == []


def test_loader_writes_to_the_shared_file_when_the_mode_is_off(tmp_path, monkeypatch):
    """With the flag off the behaviour is exactly what it was."""
    import pandas as pd

    from etl.load import WarehouseLoader

    monkeypatch.delenv(PER_TENANT_ENV, raising=False)
    shared = str(tmp_path / "curated" / "warehouse.duckdb")
    loader = WarehouseLoader(db_path=shared, schema="analytics")

    df = pd.DataFrame({"amount": [1, 2, 3]})
    with use_tenant("u_alice"):
        loader.load_dataframe(df, "sales")

    rows = _tables(shared, "sales")
    assert rows == [(tenant_schema("u_alice"), "sales")], rows
    assert not os.path.exists(
        str(tmp_path / "tenants" / tenant_slug("u_alice") / "warehouse.duckdb")
    )


def test_per_tenant_mode_keeps_the_tenant_schema_on_the_search_path(tmp_path, monkeypatch):
    """The tenant's file holds the tenant's schema; the platform one stays put."""
    from api.tenancy import tenant_file_search_path

    monkeypatch.setenv(PER_TENANT_ENV, "1")
    assert tenant_file_search_path("u_alice") == tenant_schema("u_alice")
    assert tenant_file_search_path(DEFAULT_TENANT_ID) == "analytics"


def test_visual_query_reads_the_tenant_file_when_the_mode_is_on(tmp_path, monkeypatch):
    """The query builder follows the loader to the tenant's own file."""
    from api.querybuilder import SQLBuilder

    monkeypatch.setenv(PER_TENANT_ENV, "1")
    shared = str(tmp_path / "curated" / "warehouse.duckdb")
    tenant_file = tenant_database_path("u_alice", shared_path=shared)

    os.makedirs(os.path.dirname(tenant_file), exist_ok=True)
    schema = tenant_schema("u_alice")
    con = duckdb.connect(tenant_file)
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    con.execute(f'CREATE TABLE "{schema}".sales (region VARCHAR, amount DOUBLE)')
    con.execute(f"INSERT INTO \"{schema}\".sales VALUES ('west',10),('east',20)")
    con.close()

    with use_tenant("u_alice"):
        result = SQLBuilder(db_path=shared).execute(_mixed_spec())
    assert result["success"] is True, result.get("error")
    assert result["row_count"] == 2


# ── 4. the visual query builder derives GROUP BY ───────────────────────


def _mixed_spec():
    from api.querybuilder import AggregationType, QuerySpec, SelectField

    return QuerySpec(
        from_table="sales",
        select_fields=[
            SelectField(table="sales", column="region"),
            SelectField(table="sales", column="amount", aggregation=AggregationType.SUM),
        ],
    )


def test_visual_query_derives_group_by(tmp_path):
    """A plain column mixed with an aggregate gets a GROUP BY."""
    from api.querybuilder import SQLBuilder

    path = str(tmp_path / "qb.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute("CREATE TABLE analytics.sales (region VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO analytics.sales VALUES ('west',10),('east',20)")
    con.close()

    builder = SQLBuilder(db_path=path)
    sql, error = builder.build(_mixed_spec())
    assert error is None, error
    assert "GROUP BY" in sql, sql
    assert '"sales"."region"' in sql, sql


def test_visual_query_with_a_mixed_aggregate_executes(tmp_path):
    """The derived GROUP BY makes the statement valid SQL."""
    from api.querybuilder import SQLBuilder

    path = str(tmp_path / "qb2.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute("CREATE TABLE analytics.sales (region VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO analytics.sales VALUES ('west',10),('east',20)")
    con.close()

    result = SQLBuilder(db_path=path).execute(_mixed_spec())
    assert result["success"] is True, result.get("error")
    assert result["row_count"] == 2


def test_a_supplied_group_by_wins(tmp_path):
    """The caller's GROUP BY is never overridden by the derived one."""
    from api.querybuilder import GroupByField, SQLBuilder

    path = str(tmp_path / "qb3.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute("CREATE TABLE analytics.sales (region VARCHAR, product VARCHAR, amount DOUBLE)")
    con.close()

    spec = _mixed_spec()
    spec.group_by = [GroupByField(table="sales", column="product")]
    sql, error = SQLBuilder(db_path=path).build(spec)
    assert error is None, error
    group_clause = sql.split("GROUP BY", 1)[1]
    assert '"sales"."product"' in group_clause, sql
    assert '"sales"."region"' not in group_clause, sql


# ── 5. an unknown hypothesis test type is an error ─────────────────────


def test_unknown_hypothesis_test_type_is_an_error():
    """A typo in the test type must not read as a successful test."""
    import pandas as pd

    from analysis.statistical import is_error_result, test_hypothesis

    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, 1.0]})
    result = test_hypothesis(df, "a", "b", test_type="not_a_test")

    assert result.ok is False
    assert "Unknown test type" in result.error
    assert is_error_result(result) is True
    assert result.interpretation.startswith("Error:")


def test_a_valid_hypothesis_test_is_not_an_error():
    import pandas as pd

    from analysis.statistical import is_error_result, test_hypothesis

    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, 1.0]})
    result = test_hypothesis(df, "a", "b", test_type="ttest")

    assert result.ok is True
    assert result.error == ""
    assert is_error_result(result) is False


# ── 6. the SQL policy refuses the system catalogs for everybody ────────


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
        'SELECT * FROM "information_schema"."tables"',
        "SELECT table_name FROM information_schema.columns",
    ],
)
def test_system_catalogs_are_refused(query):
    from agent.tools_sql import SQLTool

    tool = SQLTool()
    ok, error = tool.validate_query(query)
    assert not ok, query
    assert "Access denied" in (error or ""), error


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
    ],
)
def test_system_catalogs_are_refused_for_system_table_callers(query):
    """``allow_system_tables`` must not unlock the system catalogs."""
    from agent.tools_sql import SQLTool

    tool = SQLTool()
    ok, error = tool.validate_query(query, allow_system_tables=True)
    assert not ok, query
    assert "Access denied" in (error or ""), error


def test_system_catalogs_are_refused_by_the_text_fallback(monkeypatch):
    """The fallback validator (no sqlglot) must refuse them too."""
    import agent.tools_sql as tools_sql

    monkeypatch.setattr(tools_sql, "sqlglot", None)
    tool = tools_sql.SQLTool()
    for query in (
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
    ):
        ok, error = tool.validate_query(query, allow_system_tables=True)
        assert not ok, query
        assert "Access denied" in (error or ""), error
