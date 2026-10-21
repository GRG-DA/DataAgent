"""SQL identifier injection: interpolated table/column names must be rejected.

Guards every route that interpolates identifiers into DESCRIBE/SELECT/UPDATE
(DuckDB executes multi-statement strings, so `sales;DROP TABLE ...` must never
reach the engine). Values stay parameterized; this covers identifiers.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


@pytest.mark.parametrize("bad", [
    "sales;DROP TABLE users",
    "sales --",
    "sales/*",
    '"sales"',
    "sales sales",
    "analytics.sales",
    "",
    "a" * 64,
    "sales\nDROP",
])
def test_validate_table_name_rejects_injection(bad):
    from api.security_paths import validate_table_name

    with pytest.raises(HTTPException) as exc:
        validate_table_name(bad)
    assert exc.value.status_code == 400


def test_validate_table_name_accepts_plain():
    from api.security_paths import validate_table_name

    assert validate_table_name("chocolate_sales") == "chocolate_sales"
    assert validate_table_name("_tmp1") == "_tmp1"


def _ensure_analytics_schema():
    """Module-level managers create the analytics schema at import.

    The session temp warehouse starts empty, so create it first.
    """
    from config import settings as _settings

    import duckdb

    con = duckdb.connect(str(_settings.duckdb.database_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    finally:
        con.close()


def test_masking_scan_rejects_injection():
    _ensure_analytics_schema()
    from api.masking import MaskingManager

    mgr = MaskingManager.__new__(MaskingManager)
    with pytest.raises(HTTPException) as exc:
        mgr.scan_for_pii_columns("sales;DROP TABLE users")
    assert exc.value.status_code == 400


def test_catalog_register_rejects_injection(billing_db):
    _ensure_analytics_schema()
    from api.catalog import CatalogManager

    mgr = CatalogManager.__new__(CatalogManager)
    mgr.db_path = ":memory:"
    mgr.schema = "analytics"
    # Bypass __init__ table creation; register_asset must validate first.
    with pytest.raises(HTTPException) as exc:
        CatalogManager.register_asset(mgr, "sales;DROP TABLE users")
    assert exc.value.status_code == 400
