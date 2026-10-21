"""Core pipeline coverage: extract → clean → warehouse schema/load.

These paths had no direct tests (only endpoint-level load tests). Guards
the upload → warehouse spine against regressions.
"""
from __future__ import annotations


def test_extract_csv_and_infer_schema(tmp_path):
    from etl.extract import DataExtractor

    csv = tmp_path / "sales.csv"
    csv.write_text("region,amount\nwest,10\neast,20\n", encoding="utf-8")
    ex = DataExtractor()
    data = ex.extract(csv)
    assert "default" in data
    assert list(data["default"].columns) == ["region", "amount"]
    schema = ex.infer_schema(csv)
    assert schema, "schema inference must not be empty"


def test_cleaner_dedups_and_reports():
    import pandas as pd

    from etl.clean import DataCleaner

    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    cleaner = DataCleaner()
    cleaned = cleaner.clean(df)
    assert len(cleaned) == 2
    report = cleaner.get_report()
    assert report is not None


def test_warehouse_schema_and_load_roundtrip(tmp_path):
    import pandas as pd

    from etl.load import WarehouseLoader
    from warehouse.schema import create_warehouse_schema

    db = tmp_path / "wh.duckdb"
    create_warehouse_schema(db_path=str(db), schema="analytics")
    loader = WarehouseLoader(db_path=str(db), schema="analytics")
    df = pd.DataFrame({"product": ["a", "b"], "amount": [1.0, 2.0]})
    assert loader.load_dataframe(df, "smoke_t") == 2
    assert loader.table_exists("smoke_t")
    assert "smoke_t" in loader.list_tables()
    info = loader.get_table_info("smoke_t")
    assert info["row_count"] == 2


def test_system_table_classification():
    from warehouse.system_tables import is_system_table, user_tables

    assert is_system_table("api_keys")
    assert is_system_table("subscriptions")
    assert not is_system_table("sales_data")
    assert user_tables(["sales_data", "api_keys", "orders"]) == ["sales_data", "orders"]


def test_load_quotes_malicious_table_name(tmp_path):
    """A hostile table name becomes a literal table, never SQL."""
    import pandas as pd

    from etl.load import WarehouseLoader

    loader = WarehouseLoader(db_path=str(tmp_path / "wh2.duckdb"), schema="analytics")
    df = pd.DataFrame({"a": [1]})
    loader.load_dataframe(df, "smoke_t")
    evil = 'x"; DROP TABLE smoke_t; --'
    loader.load_dataframe(df, evil)
    assert loader.table_exists("smoke_t")  # DROP never ran
    assert loader.table_exists(evil)  # stored literally


def test_load_endpoint_rejects_bad_table_name():
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    r = client.post("/load",
                    json={"filename": "nope.csv", "table_name": 'x"; DROP TABLE t; --'},
                    headers=auth_headers())
    assert r.status_code in (400, 404)
