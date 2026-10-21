"""TDD (F4): the read-only SQL paths must never persist writes.

Audit finding: `/sql`, `/execute`, `/tasks/run` validated with a
SELECT/WITH check but then handed the query to `WarehouseLoader.execute_sql`,
which runs on a read-write connection. Anything the validator misses (or a
future validator regression) wrote straight through to the warehouse.

Fix under test: those routes pass ``read_only=True`` so execution is wrapped
in BEGIN/ROLLBACK — a write that slips past validation is discarded. The
default ``execute_sql`` keeps persisting writes (ETL uses it to create
tables), and ``query`` always rolls back.
"""

import duckdb
import pandas as pd
import pytest

from etl.load import WarehouseLoader


@pytest.fixture()
def loader(tmp_path):
    db_path = tmp_path / "ro.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA analytics")
    con.execute("CREATE TABLE analytics.sales (product VARCHAR, amount INTEGER)")
    con.execute("INSERT INTO analytics.sales VALUES ('a', 1), ('b', 2)")
    con.execute("CREATE TABLE analytics.victim (id INTEGER)")
    con.execute("INSERT INTO analytics.victim VALUES (1)")
    con.close()
    return WarehouseLoader(db_path=str(db_path), schema="analytics")


def _table_exists(db_path, name: str) -> bool:
    con = duckdb.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'analytics' AND table_name = ?",
            [name],
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_execute_sql_read_only_drop_is_rolled_back(loader):
    loader.execute_sql("DROP TABLE analytics.victim", read_only=True)
    assert _table_exists(loader.db_path, "victim")


def test_execute_sql_read_only_create_is_rolled_back(loader):
    loader.execute_sql("CREATE TABLE analytics.made AS SELECT 1 AS a", read_only=True)
    assert not _table_exists(loader.db_path, "made")


def test_execute_sql_default_still_persists_writes(loader):
    """ETL contract: plain execute_sql() writes persist."""
    loader.execute_sql("CREATE TABLE analytics.etl_made AS SELECT 1 AS a")
    assert _table_exists(loader.db_path, "etl_made")


def test_query_drop_is_rolled_back(loader):
    loader.query("DROP TABLE analytics.victim")
    assert _table_exists(loader.db_path, "victim")


def test_execute_sql_select_still_returns_rows(loader):
    df = loader.execute_sql("SELECT product, amount FROM analytics.sales ORDER BY amount")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == ["product", "amount"]


def test_execute_sql_params_still_work(loader):
    df = loader.execute_sql(
        "SELECT amount FROM analytics.sales WHERE product = ?", ["a"]
    )
    assert len(df) == 1


def test_metadata_selects_still_work(loader):
    df = loader.execute_sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'analytics' ORDER BY table_name"
    )
    assert set(df["table_name"]) >= {"sales", "victim"}


def test_write_via_other_connection_visible_after_tx(loader, tmp_path):
    """After a rolled-back tx, a separate connection sees consistent data."""
    loader.execute_sql("DROP TABLE analytics.victim", read_only=True)
    con = duckdb.connect(str(loader.db_path))
    try:
        count = con.execute("SELECT COUNT(*) FROM analytics.victim").fetchone()[0]
    finally:
        con.close()
    assert count == 1
