"""RED: /load must not report success when the cleaned frame is empty."""

import pandas as pd
import pytest

from etl.load import WarehouseLoader


def test_load_dataframe_raises_on_empty(tmp_path):
    db = tmp_path / "w.duckdb"
    loader = WarehouseLoader(db_path=db, schema="analytics")
    empty = pd.DataFrame(columns=["a", "b"])
    with pytest.raises(ValueError):
        loader.load_dataframe(empty, "should_not_create")


def test_load_dataframe_loads_rows(tmp_path):
    db = tmp_path / "w.duckdb"
    loader = WarehouseLoader(db_path=db, schema="analytics")
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    n = loader.load_dataframe(df, "ok_table")
    assert n == 2
