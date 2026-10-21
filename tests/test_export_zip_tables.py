"""RED: /export/zip must honor an optional `tables` filter.

The automation page's "Download All Tables as ZIP" passes the pipeline's
output tables, but export_all_as_zip() / GET /export/zip always pack the
entire warehouse (defect D3). Backward compatible: omitting `tables` still
exports every table in the schema.
"""

from __future__ import annotations

import io
import zipfile

import pytest


def _make_warehouse(tmp_path) -> str:
    """Temp DuckDB with analytics.sales + analytics.inventory."""
    import duckdb

    db = str(tmp_path / "wh.duckdb")
    con = duckdb.connect(db)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            "CREATE TABLE analytics.sales AS SELECT 1 AS id, 10.0 AS amount"
        )
        con.execute(
            "CREATE TABLE analytics.inventory AS SELECT 1 AS id, 5 AS qty"
        )
    finally:
        con.close()
    return db


def _zip_names(payload: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return sorted(zf.namelist())


# ── unit: export.bi_export.export_all_as_zip ─────────────────────────


def test_zip_filters_to_requested_tables(tmp_path):
    from export.bi_export import export_all_as_zip

    db = _make_warehouse(tmp_path)
    payload = export_all_as_zip(db_path=db, schema="analytics", tables=["sales"])
    assert _zip_names(payload) == ["analytics_sales.csv"]


def test_zip_without_tables_param_exports_all(tmp_path):
    """Backward compatible: no filter → every table."""
    from export.bi_export import export_all_as_zip

    db = _make_warehouse(tmp_path)
    payload = export_all_as_zip(db_path=db, schema="analytics")
    assert _zip_names(payload) == [
        "analytics_inventory.csv",
        "analytics_sales.csv",
    ]


def test_zip_unknown_table_raises_value_error(tmp_path):
    from export.bi_export import export_all_as_zip

    db = _make_warehouse(tmp_path)
    with pytest.raises(ValueError):
        export_all_as_zip(
            db_path=db, schema="analytics", tables=["sales", "nope"]
        )


# ── endpoint: GET /export/zip?tables= ────────────────────────────────


@pytest.fixture
def pro_client(tmp_path, monkeypatch):
    """TestClient with a pro-plan user (export_page entitled); the warehouse
    path is pointed at a temp file containing sales + inventory.

    Import auth helpers before patching database_path: a first-time
    `from tests.conftest import ...` re-runs conftest module body and resets
    settings.duckdb.database_path to the session temp warehouse.

    Billing reads go through the process-wide shared connection
    (api.db.get_connection), which follows settings.duckdb.database_path —
    so the billing tables are seeded into the same temp warehouse the
    export reads.
    """
    from fastapi.testclient import TestClient

    import api.state as state
    from api.dependencies import get_current_user as deps_get_current_user
    from api.main import app
    from api.middleware.auth_middleware import User
    from config import settings
    from tests.conftest import auth_headers

    db = _make_warehouse(tmp_path)
    monkeypatch.setattr(settings.duckdb, "database_path", db)
    old_loader = state.loader
    from api.billing.cache import clear_request_cache
    from api.billing.schema import init_billing_tables
    from api.billing.seed import seed_billing_data
    from api.billing.subs import ensure_billing_row

    clear_request_cache()
    init_billing_tables()
    seed_billing_data()
    ensure_billing_row(
        "user-export-zip", "pro", provider="admin", never_expires=True,
    )

    async def _pro_user():
        return User(
            user_id="user-export-zip",
            username="user-export-zip",
            role="viewer",
            email="export-zip@example.com",
            plan="pro",
            auth_method="test",
        )

    app.dependency_overrides[deps_get_current_user] = _pro_user
    try:
        yield TestClient(app, headers=auth_headers(
            "export_zip_test", "Str0ng!Passw0rd", role="admin"
        ))
    finally:
        app.dependency_overrides.pop(deps_get_current_user, None)
        state.loader = old_loader
        clear_request_cache()


def test_endpoint_tables_param_filters_zip(pro_client):
    res = pro_client.get("/export/zip", params={"tables": "sales"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/zip")
    assert _zip_names(res.content) == ["analytics_sales.csv"]


def test_endpoint_unknown_table_returns_400(pro_client):
    res = pro_client.get("/export/zip", params={"tables": "does_not_exist"})
    assert res.status_code == 400


def test_endpoint_without_tables_returns_all(pro_client):
    res = pro_client.get("/export/zip")
    assert res.status_code == 200
    names = _zip_names(res.content)
    # No filter → every table of the warehouse.  The billing tables live in
    # the same platform warehouse (one shared connection, ADR-009 scale
    # step), so they are exported with it.
    assert "analytics_sales.csv" in names
    assert "analytics_inventory.csv" in names
    assert "analytics_usage_records.csv" in names
