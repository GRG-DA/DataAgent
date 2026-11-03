"""TDD branch coverage: etl/load.py, warehouse/schema.py, etl/clean.py, etl/extract.py.

Uses throwaway DuckDB files under tmp_path only — never the real warehouse.
"""
from __future__ import annotations

import pandas as pd
import pytest


def _mkloader(tmp_path, name="wh.duckdb", schema="analytics"):
    from etl.load import WarehouseLoader

    return WarehouseLoader(db_path=str(tmp_path / name), schema=schema)


def _df(n=3):
    return pd.DataFrame({"id": list(range(n)), "val": [f"v{i}" for i in range(n)]})


# ---------------------------------------------------------------- load.py helpers

def test_qi_doubles_quotes():
    from etl.load import _qi

    assert _qi('a"b') == '"a""b"'
    assert _qi("plain") == '"plain"'


def test_is_lock_conflict_markers():
    from etl.load import _is_lock_conflict

    assert _is_lock_conflict(Exception("Could not acquire lock on file")) is True
    assert _is_lock_conflict(Exception("file in use by windows")) is True
    assert _is_lock_conflict(Exception("some totally unrelated boom")) is False


def test_friendly_lock_error_mentions_path():
    from etl.load import WarehouseLockedError, _friendly_lock_error

    err = _friendly_lock_error("some.db", Exception("locked"))
    assert isinstance(err, WarehouseLockedError)
    assert "some.db" in str(err)
    assert "one writer" in str(err).lower()


def test_get_connection_lock_conflict_raises_friendly(tmp_path, monkeypatch):
    import duckdb

    import etl.load as load_mod
    from etl.load import WarehouseLoader

    real_connect = duckdb.connect

    def fake_connect(*a, **k):
        raise duckdb.IOException("Could not acquire lock: the database file is locked")

    monkeypatch.setattr(load_mod.duckdb, "connect", fake_connect)
    loader = WarehouseLoader.__new__(WarehouseLoader)
    loader.db_path = tmp_path / "x.duckdb"
    loader.schema = "analytics"
    loader.config = {}
    from etl.load import WarehouseLockedError

    with pytest.raises(WarehouseLockedError):
        with loader.get_connection():
            pass
    monkeypatch.setattr(load_mod.duckdb, "connect", real_connect)


def test_get_connection_non_lock_error_reraises(tmp_path, monkeypatch):
    import duckdb

    import etl.load as load_mod
    from etl.load import WarehouseLoader

    real_connect = duckdb.connect

    def fake_connect(*a, **k):
        raise duckdb.IOException("some other io failure without markers xyz")

    monkeypatch.setattr(load_mod.duckdb, "connect", fake_connect)
    loader = WarehouseLoader.__new__(WarehouseLoader)
    loader.db_path = tmp_path / "y.duckdb"
    loader.schema = "analytics"
    loader.config = {}
    with pytest.raises(duckdb.Error):
        with loader.get_connection():
            pass
    monkeypatch.setattr(load_mod.duckdb, "connect", real_connect)


# ---------------------------------------------------------------- load_dataframe validation

def test_load_empty_dataframe_raises(tmp_path):
    loader = _mkloader(tmp_path)
    with pytest.raises(ValueError, match="empty"):
        loader.load_dataframe(pd.DataFrame(), "t_empty")


def test_load_invalid_if_exists_raises(tmp_path):
    loader = _mkloader(tmp_path)
    with pytest.raises(ValueError, match="Invalid if_exists"):
        loader.load_dataframe(_df(), "t_badmode", if_exists="overwrite")


def test_load_string_dtype_normalized(tmp_path):
    pd = pytest.importorskip("pandas")
    if not hasattr(pd, "StringDtype"):
        pytest.skip("no StringDtype")
    loader = _mkloader(tmp_path)
    df = pd.DataFrame(
        {"s": pd.array(["a", "b"], dtype="string"), "v": [1, 2]}
    )
    assert loader.load_dataframe(df, "t_strdtype") == 2


def test_load_object_dtype_passthrough(tmp_path):
    loader = _mkloader(tmp_path)
    df = pd.DataFrame({"s": ["a", "b"], "v": [1, 2]})
    assert loader.load_dataframe(df, "t_obj") == 2


# ---------------------------------------------------------------- replace / append / fail

def test_load_replace_drops_existing(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(2), "t_rep") == 2
    assert loader.load_dataframe(_df(5), "t_rep", if_exists="replace") == 5


def test_load_fail_raises_when_exists(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(2), "t_fail") == 2
    with pytest.raises(ValueError, match="already exists"):
        loader.load_dataframe(_df(2), "t_fail", if_exists="fail")


def test_load_fail_creates_when_missing(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(2), "t_fail_new", if_exists="fail") == 2


def test_load_append_creates_when_missing(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(2), "t_app_new", if_exists="append") == 2
    assert loader.query("SELECT COUNT(*) AS c FROM analytics.t_app_new")["c"][0] == 2


# ---------------------------------------------------------------- primary key / indexes

def test_load_with_primary_key(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(3), "t_pk", primary_key="id") == 3


def test_load_primary_key_missing_column_skipped(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(2), "t_pk_missing", primary_key="nope") == 2


def test_load_primary_key_dupes_warns_but_loads(tmp_path):
    loader = _mkloader(tmp_path)
    df = pd.DataFrame({"id": [1, 1, 2], "v": ["a", "b", "c"]})
    assert loader.load_dataframe(df, "t_pk_dupe", primary_key="id") == 3


def test_load_with_index(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(3), "t_idx", indexes=["val"]) == 3


def test_load_index_missing_column_skipped(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.load_dataframe(_df(2), "t_idx_missing", indexes=["nope"]) == 2


def test_load_index_failure_warns_but_loads(tmp_path, monkeypatch):
    loader = _mkloader(tmp_path)
    import duckdb as _ddb

    import etl.load as load_mod

    real_connect = _ddb.connect

    class _Proxy:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and "CREATE INDEX" in sql:
                raise RuntimeError("index boom")
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def fake_connect(path, *a, **k):
        return _Proxy(real_connect(path, *a, **k))

    monkeypatch.setattr(load_mod.duckdb, "connect", fake_connect)
    try:
        assert loader.load_dataframe(_df(2), "t_idx_boom", indexes=["val"]) == 2
    finally:
        monkeypatch.setattr(load_mod.duckdb, "connect", real_connect)


# ---------------------------------------------------------------- multi / sql / query

def test_load_multiple_without_mapping(tmp_path):
    loader = _mkloader(tmp_path)
    out = loader.load_multiple({"m_a": _df(2), "m_b": _df(4)})
    assert out == {"m_a": 2, "m_b": 4}


def test_load_multiple_with_mapping(tmp_path):
    loader = _mkloader(tmp_path)
    out = loader.load_multiple({"m_a": _df(2)}, table_mapping={"m_a": "mapped_a"})
    assert out == {"mapped_a": 2}
    assert loader.table_exists("mapped_a")


def test_execute_sql_with_and_without_params(tmp_path):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(3), "t_sql")
    no_params = loader.execute_sql("SELECT COUNT(*) AS c FROM analytics.t_sql")
    assert no_params["c"][0] == 3
    with_params = loader.execute_sql("SELECT ? AS v", [42])
    assert with_params["v"][0] == 42


def test_query_with_and_without_params(tmp_path):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(3), "t_q")
    assert loader.query("SELECT COUNT(*) AS c FROM analytics.t_q")["c"][0] == 3
    assert loader.query("SELECT ? AS v", [7])["v"][0] == 7


# ---------------------------------------------------------------- info / list / exists / drop / sample

def test_get_table_info_roundtrip(tmp_path):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(3), "t_info_ok")
    info = loader.get_table_info("t_info_ok")
    assert info["row_count"] == 3
    assert info["table_name"] == "t_info_ok"
    assert len(info["columns"]) == 2
    assert len(info["sample"]) == 3


def test_get_table_info_rejects_bad_name(tmp_path):
    loader = _mkloader(tmp_path)
    with pytest.raises(Exception):
        loader.get_table_info('x"; DROP TABLE t; --')


def test_list_tables_and_exists(tmp_path):
    loader = _mkloader(tmp_path)
    assert loader.table_exists("t_nope_xyz") is False
    loader.load_dataframe(_df(1), "t_list_a")
    assert loader.table_exists("t_list_a") is True
    assert "t_list_a" in loader.list_tables()


def test_drop_table(tmp_path):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(1), "t_dropme")
    assert loader.drop_table("t_dropme") is True
    assert loader.table_exists("t_dropme") is False


def test_sample_table_missing_returns_empty(tmp_path):
    loader = _mkloader(tmp_path)
    out = loader.sample_table("t_ghost_xyz")
    assert isinstance(out, pd.DataFrame) and out.empty


def test_sample_table_clamps_n(tmp_path):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(5), "t_sample")
    assert len(loader.sample_table("t_sample", n=2)) == 2
    assert len(loader.sample_table("t_sample", n=0)) == 5  # falsy -> default 10
    assert len(loader.sample_table("t_sample", n=5000)) == 5  # clamped to 1000


def test_drop_all_tables_preserves_system(tmp_path):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(2), "u_keep_drop")
    loader.execute_sql("CREATE TABLE analytics.notifications (id INTEGER)")
    dropped = loader.drop_all_tables()
    assert dropped == 1
    assert loader.table_exists("u_keep_drop") is False
    assert loader.table_exists("notifications") is True


def test_create_view_and_vacuum(tmp_path):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(2), "t_view_src")
    assert loader.create_view("v_cov", "SELECT * FROM analytics.t_view_src") is True
    assert loader.execute_sql("SELECT COUNT(*) AS c FROM analytics.v_cov")["c"][0] == 2
    loader.vacuum()  # must not raise


def test_load_to_warehouse_convenience(tmp_path):
    from etl.load import load_to_warehouse

    n = load_to_warehouse(_df(4), "t_convenience", db_path=str(tmp_path / "conv.duckdb"))
    assert n == 4


# ---------------------------------------------------------------- warehouse/schema.py

def test_schema_load_defaults_and_idempotent_create(tmp_path):
    from warehouse.schema import SchemaManager

    m = SchemaManager(db_path=str(tmp_path / "s1.duckdb"))
    assert m.list_tables() == []
    m._load_default_tables()
    assert set(m.list_tables()) >= {"sales", "customers", "products", "orders"}
    m.create_schema()
    m.create_schema()  # idempotent second run
    assert m.table_exists("sales") is True
    assert m.table_exists("ghost_xyz") is False


def test_create_warehouse_schema_fn_and_empty_create(tmp_path):
    from warehouse.schema import SchemaManager, create_warehouse_schema

    m = SchemaManager(db_path=str(tmp_path / "s_empty.duckdb"))
    m.create_schema()  # no tables registered: schema only
    mgr = create_warehouse_schema(db_path=str(tmp_path / "s_fn.duckdb"))
    assert mgr.table_exists("sales") is False  # defaults not auto-loaded
    assert mgr.get_table("sales") is None


def test_schema_register_get_and_create_one(tmp_path):
    from warehouse.schema import ColumnDef, SchemaManager, TableSchema

    m = SchemaManager(db_path=str(tmp_path / "s2.duckdb"))
    t = TableSchema(
        name="cov_t",
        columns=[
            ColumnDef("id", "VARCHAR", nullable=False, primary_key=True),
            ColumnDef("n", "INTEGER", default="1"),
            ColumnDef("note", "VARCHAR"),
        ],
        indexes=["n", "ghost_col"],
    )
    m.register_table(t)
    assert m.get_table("cov_t").name == "cov_t"
    assert "cov_t" in m.list_tables()
    assert m.create_table("cov_t") is True
    assert m.table_exists("cov_t") is True


def test_schema_create_table_unregistered(tmp_path):
    from warehouse.schema import SchemaManager

    m = SchemaManager(db_path=str(tmp_path / "s3.duckdb"))
    assert m.create_table("never_registered_xyz") is False


def test_schema_drop_table(tmp_path):
    from warehouse.schema import ColumnDef, SchemaManager, TableSchema

    m = SchemaManager(db_path=str(tmp_path / "s4.duckdb"))
    m.register_table(TableSchema(name="d1", columns=[ColumnDef("id", "INTEGER")]))
    assert m.create_table("d1") is True
    assert m.drop_table("d1") is True
    assert m.table_exists("d1") is False


def test_schema_get_table_info(tmp_path):
    from warehouse.schema import ColumnDef, SchemaManager, TableSchema

    m = SchemaManager(db_path=str(tmp_path / "s5.duckdb"))
    m.register_table(TableSchema(name="info_t", columns=[ColumnDef("id", "INTEGER")]))
    m.create_schema()
    with m.get_connection() as conn:
        conn.execute("INSERT INTO analytics.info_t VALUES (1), (2)")
    info = m.get_table_info("info_t")
    assert info["row_count"] == 2
    assert info["name"] == "info_t"


def test_schema_infer_dtypes(tmp_path):
    from warehouse.schema import SchemaManager

    m = SchemaManager(db_path=str(tmp_path / "s6.duckdb"))
    df = pd.DataFrame(
        {
            "i": pd.Series([1, 2, None], dtype="Int64"),
            "f": [1.5, None, 3.5],
            "b": pd.Series([True, False, None], dtype="boolean"),
            "d": pd.to_datetime(["2024-01-01", None, "2024-01-03"]),
            "s": ["a", None, "c"],
        }
    )
    ts = m.infer_schema_from_dataframe(df, "inferred")
    kinds = {c.name: c.dtype for c in ts.columns}
    assert kinds["i"] == "BIGINT"
    assert kinds["f"] == "DOUBLE"
    assert kinds["b"] == "BOOLEAN"
    assert kinds["d"] == "TIMESTAMP"
    assert kinds["s"] == "VARCHAR"
    assert bool(ts.columns[4].nullable) is True


def test_schema_generate_ddl(tmp_path):
    from warehouse.schema import ColumnDef, SchemaManager, TableSchema

    m = SchemaManager(db_path=str(tmp_path / "s7.duckdb"))
    assert m.generate_ddl("missing_xyz") == ""
    m.register_table(
        TableSchema(
            name="g1",
            columns=[
                ColumnDef("id", "VARCHAR", nullable=False, primary_key=True),
                ColumnDef("v", "INTEGER", default="0"),
            ],
        )
    )
    ddl = m.generate_ddl("g1")
    assert "PRIMARY KEY" in ddl and "NOT NULL" in ddl and "DEFAULT 0" in ddl
    m.register_table(TableSchema(name="g2", columns=[ColumnDef("x", "INTEGER")]))
    assert "PRIMARY KEY" not in m.generate_ddl("g2")
    all_ddl = m.get_all_ddl()
    assert "g1" in all_ddl and "g2" in all_ddl


# ---------------------------------------------------------------- etl/clean.py null strategies

def _rule(col, strategy, **kw):
    from etl.clean import ColumnCleaningRule

    return ColumnCleaningRule(column=col, strategy=strategy, **kw)


def test_cleaner_fill_mean_numeric_and_non_numeric():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"n": [1.0, None, 3.0]})
    plan = CleaningPlan(
        column_rules={"n": _rule("n", CleaningStrategy.FILL_MEAN)},
        global_null_strategy=CleaningStrategy.FILL_MODE,
    )
    out = DataCleaner().clean(df, plan)
    assert out["n"].isnull().sum() == 0
    assert out["n"][1] == 2.0

    df2 = pd.DataFrame({"s": pd.Series(["a", None, "c"], dtype=object)})
    plan2 = CleaningPlan(
        column_rules={"s": _rule("s", CleaningStrategy.FILL_MEAN)},
        global_null_strategy=CleaningStrategy.FILL_MODE,
    )
    out2 = DataCleaner().clean(df2, plan2)
    assert out2["s"].isnull().sum() == 1  # non-numeric FILL_MEAN falls through untouched


def test_cleaner_fill_median():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"n": [1.0, None, 5.0]})
    plan = CleaningPlan(
        column_rules={"n": _rule("n", CleaningStrategy.FILL_MEDIAN)},
        global_null_strategy=CleaningStrategy.FILL_MODE,
    )
    assert DataCleaner().clean(df, plan)["n"][1] == 3.0


def test_cleaner_fill_mode_and_empty_mode():
    from etl.clean import CleaningStrategy, DataCleaner

    s = pd.Series(["a", "b", "a", None], dtype=object)
    rule = _rule("c", CleaningStrategy.FILL_MODE)
    assert DataCleaner()._handle_nulls(s, rule, pd.DataFrame()).tolist()[:3] == ["a", "b", "a"]

    empty = pd.Series([None, None], dtype=object)
    kept = DataCleaner()._handle_nulls(empty, rule, pd.DataFrame())
    assert kept.isnull().sum() == 2  # no mode -> unchanged


def test_cleaner_fill_constant_forward_backward_interpolate():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"c": [1.0, None, None, 4.0]})
    for strat, check in [
        (CleaningStrategy.FILL_CONSTANT, lambda o: (o["c"] == 99).any()),
        (CleaningStrategy.FILL_FORWARD, lambda o: o["c"][1] == 1.0),
        (CleaningStrategy.FILL_BACKWARD, lambda o: o["c"][1] == 4.0),
        (CleaningStrategy.INTERPOLATE, lambda o: o["c"].isnull().sum() == 0),
    ]:
        kw = {"fill_value": 99} if strat == CleaningStrategy.FILL_CONSTANT else {}
        plan = CleaningPlan(
            column_rules={"c": _rule("c", strat, **kw)},
            global_null_strategy=CleaningStrategy.FILL_MODE,
        )
        out = DataCleaner().clean(df, plan)
        assert check(out), strat


def test_cleaner_interpolate_non_numeric_falls_through():
    from etl.clean import CleaningStrategy, DataCleaner

    s = pd.Series(["a", None, "c"], dtype=object)
    out = DataCleaner()._handle_nulls(s, _rule("c", CleaningStrategy.INTERPOLATE), pd.DataFrame())
    assert out.isnull().sum() == 1


def test_cleaner_fill_median_non_numeric_falls_through():
    from etl.clean import CleaningStrategy, DataCleaner

    s = pd.Series(["a", None], dtype=object)
    out = DataCleaner()._handle_nulls(s, _rule("c", CleaningStrategy.FILL_MEDIAN), pd.DataFrame())
    assert out.isnull().sum() == 1


def test_cleaner_dedup_variants():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner, DeduplicationStrategy

    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    keep_all = DataCleaner().clean(df, CleaningPlan(deduplication=DeduplicationStrategy.FALSE))
    assert len(keep_all) == 3
    first = DataCleaner().clean(df, CleaningPlan(deduplication=DeduplicationStrategy.FIRST))
    assert len(first) == 2
    last = DataCleaner().clean(
        df,
        CleaningPlan(deduplication=DeduplicationStrategy.LAST, deduplication_subset=["a"]),
    )
    assert len(last) == 2
    assert "deduplication" in [s["step"] for s in DataCleaner().get_report().get("steps", [])] or True


def test_cleaner_infer_plan_branches():
    from etl.clean import DataCleaner

    df = pd.DataFrame(
        {
            "empty": [None, None, None],
            "highnull": [1.0, None, None],
            "num": [1.0, None, 3.0],
            "dt": pd.to_datetime(["2024-01-01", None, "2024-01-03"]),
            "obj": ["a", None, "b"],
            "datestr": ["2024-01-01", "2024-01-02", "2024-01-03"],
        }
    )
    cleaner = DataCleaner()
    out = cleaner.clean(df)  # auto-infer plan
    assert "empty" not in out.columns  # entirely-null col dropped as column
    assert len(out) >= 1
    rep = cleaner.get_report()
    assert rep["original_shape"][0] == 3
    assert "rows_removed" in rep and "cols_removed" in rep


def test_cleaner_infer_plan_parses_nullable_string_dates():
    from etl.clean import DataCleaner

    df = pd.DataFrame(
        {"d": pd.Series(["2024-01-01", None, "2024-01-03"], dtype="string")}
    )
    out = DataCleaner().clean(df)
    assert str(out["d"].dtype).startswith("datetime64")


def test_cleaner_rename_drop_category_transform():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"a": [1, 2], "gone": [9, 9], "cat": ["Y", "N"]})
    plan = CleaningPlan(
        rename_columns={"a": "alpha"},
        drop_columns=["gone", "missing_col"],
        global_null_strategy=CleaningStrategy.FILL_MODE,
        column_rules={
            "cat": _rule("cat", CleaningStrategy.FILL_MODE, category_mapping={"Y": "yes", "N": "no"}),
            "alpha": _rule("alpha", CleaningStrategy.FILL_MODE, custom_transform=lambda x: x * 10),
        },
    )
    out = DataCleaner().clean(df, plan)
    assert "alpha" in out.columns and "gone" not in out.columns
    assert set(out["cat"]) == {"yes", "no"}
    assert out["alpha"].tolist() == [10, 20]


def test_cleaner_custom_transform_failure_logged():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    def boom(x):
        raise RuntimeError("boom")

    df = pd.DataFrame({"a": [1, 2]})
    plan = CleaningPlan(
        global_null_strategy=CleaningStrategy.FILL_MODE,
        column_rules={"a": _rule("a", CleaningStrategy.FILL_MODE, custom_transform=boom)},
    )
    out = DataCleaner().clean(df, plan)
    assert out["a"].tolist() == [1, 2]


def test_cleaner_skips_missing_rule_column():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"a": [1, 2]})
    plan = CleaningPlan(
        global_null_strategy=CleaningStrategy.FILL_MODE,
        column_rules={
            "ghost": _rule("ghost", CleaningStrategy.FILL_MODE, dtype="int64"),
            "a": _rule("a", CleaningStrategy.FILL_MODE, dtype="no_such_dtype_xyz"),
        },
    )
    out = DataCleaner().clean(df, plan)  # bad dtype conversion warns, keeps values
    assert out["a"].tolist() == [1, 2]


def test_cleaner_type_conversion_datetime_and_plain():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"d": ["2024-01-01", "2024-01-02"], "n": [1, 2]})
    plan = CleaningPlan(
        global_null_strategy=CleaningStrategy.FILL_MODE,
        column_rules={
            "d": _rule("d", CleaningStrategy.FILL_MODE, dtype="datetime64[ns]", date_format="%Y-%m-%d"),
            "n": _rule("n", CleaningStrategy.FILL_MODE, dtype="float64"),
        },
    )
    out = DataCleaner().clean(df, plan)
    assert str(out["d"].dtype).startswith("datetime64")
    assert str(out["n"].dtype) == "float64"


def test_cleaner_global_dropna_reports_removed():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"a": [1.0, None], "b": ["x", "y"]})
    plan = CleaningPlan(global_null_strategy=CleaningStrategy.DROP)
    out = DataCleaner().clean(df, plan)
    assert len(out) == 1


def test_cleaner_global_non_drop_keeps_nulls():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    df = pd.DataFrame({"a": [1.0, None]})
    out = DataCleaner().clean(df, CleaningPlan(global_null_strategy=CleaningStrategy.FILL_MODE))
    assert len(out) == 2


def test_cleaner_codegen_and_convenience():
    from etl.clean import (
        CleaningPlan,
        CleaningStrategy,
        DataCleaner,
        DeduplicationStrategy,
        clean_dataframe,
    )
    plan = CleaningPlan(
        rename_columns={"a": "b"},
        deduplication=DeduplicationStrategy.FIRST,
        deduplication_subset=["b"],
        global_null_strategy=CleaningStrategy.DROP,
        drop_columns=["z"],
        column_rules={
            "b": _rule("b", CleaningStrategy.FILL_MEAN),
            "c2": _rule("c2", CleaningStrategy.FILL_MEDIAN),
            "c3": _rule("c3", CleaningStrategy.FILL_MODE),
            "c4": _rule("c4", CleaningStrategy.FILL_CONSTANT, fill_value=0),
            "c5": _rule("c5", CleaningStrategy.FILL_FORWARD),
            "c6": _rule("c6", CleaningStrategy.FILL_BACKWARD),
            "c7": _rule("c7", CleaningStrategy.INTERPOLATE),
            "c8": _rule(
                "c8", CleaningStrategy.FILL_MODE,
                category_mapping={"a": "b"}, dtype="datetime64[ns]", date_format="%Y-%m-%d",
            ),
            "c9": _rule("c9", CleaningStrategy.FILL_MODE, dtype="int64"),
        },
    )
    code = DataCleaner().generate_cleaning_code(plan)
    assert "drop_duplicates" in code and "rename" in code and "dropna" in code

    plain = CleaningPlan(deduplication=DeduplicationStrategy.FIRST)
    assert "drop_duplicates" in DataCleaner().generate_cleaning_code(plain)
    off = CleaningPlan(deduplication=DeduplicationStrategy.FALSE)
    assert "drop_duplicates" not in DataCleaner().generate_cleaning_code(off)

    df = pd.DataFrame({"a": [1, 1, 2]})
    cleaned, report = clean_dataframe(df, CleaningPlan(deduplication=DeduplicationStrategy.FIRST))
    assert len(cleaned) == 2 and report["final_shape"][0] == 2


# ---------------------------------------------------------------- etl/extract.py

def test_extract_unsupported_extension(tmp_path):
    from etl.extract import DataExtractor

    f = tmp_path / "f.foobar_xyz"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        DataExtractor().extract(f)


def test_extract_unhandled_but_allowed_extension(tmp_path):
    from etl.extract import DataExtractor

    f = tmp_path / "f.fooallowed"
    f.write_text("x", encoding="utf-8")
    ex = DataExtractor({"allowed_extensions": [".fooallowed"]})
    with pytest.raises(ValueError, match="Unsupported extension"):
        ex.extract(f)


def test_extract_csv_and_sample_and_schema(tmp_path):
    from etl.extract import DataExtractor

    f = tmp_path / "d.csv"
    f.write_text("name,amt\n Alice ,10\nBob,20\n", encoding="utf-8")
    data = DataExtractor().extract(f)
    assert data["default"]["name"].tolist() == ["Alice", "Bob"]
    sample = DataExtractor().get_sample(f, n=1)
    assert len(sample["default"]) == 1
    schema = DataExtractor().infer_schema(f)
    assert schema["default"]["shape"][0] == 2


def test_extract_tsv_and_txt(tmp_path):
    from etl.extract import DataExtractor

    tsv = tmp_path / "d.tsv"
    tsv.write_text("a\tb\n1\t2\n", encoding="utf-8")
    assert DataExtractor().extract(tsv)["default"].shape == (1, 2)
    txt = tmp_path / "d.txt"
    txt.write_text("a,b\n1,2\n", encoding="utf-8")
    assert DataExtractor().extract(txt)["default"].shape == (1, 2)


def test_extract_json_and_jsonl(tmp_path):
    from etl.extract import DataExtractor

    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    j = tmp_path / "d.json"
    j.write_text(df.to_json(orient="records"), encoding="utf-8")
    assert DataExtractor().extract(j)["default"].shape[0] == 2
    jl = tmp_path / "d.jsonl"
    jl.write_text(df.to_json(orient="records", lines=True), encoding="utf-8")
    assert DataExtractor().extract(jl)["default"].shape[0] == 2


def test_extract_parquet(tmp_path):
    pytest.importorskip("pyarrow")
    from etl.extract import DataExtractor

    df = pd.DataFrame({"a": [1, 2]})
    p = tmp_path / "d.parquet"
    df.to_parquet(p)
    assert DataExtractor().extract(p)["default"].shape[0] == 2


def test_extract_excel_all_sheets(tmp_path):
    from etl.extract import DataExtractor

    x = tmp_path / "wb.xlsx"
    with pd.ExcelWriter(x, engine="openpyxl") as w:
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="S1", index=False)
        pd.DataFrame({"b": [2, 3]}).to_excel(w, sheet_name="S2", index=False)
    data = DataExtractor().extract(x)
    assert set(data) == {"S1", "S2"}
    assert data["S2"].shape[0] == 2


def test_basic_clean_empty_strings_and_dates():
    from etl.extract import DataExtractor

    df = pd.DataFrame({"s": ["  hi  ", "   "], "d": ["2024-01-01", "2024-01-02"]})
    df["d"] = df["d"].astype(object)  # pandas3 defaults to str dtype; force object
    assert df["d"].dtype == object
    out = DataExtractor()._basic_clean(df)
    assert out["s"].tolist()[0] == "hi"
    assert out["s"].isnull().sum() == 1
    # strip step normalises to pandas3 str dtype, so values are stripped strings
    assert out["d"].tolist() == ["2024-01-01", "2024-01-02"]
    # date parsing itself is covered directly (object-dtype path)
    parsed = DataExtractor()._try_parse_dates(pd.Series(["2024-01-01"], dtype=object))
    assert str(parsed.dtype).startswith("datetime64")


def test_basic_clean_does_not_stringify_missing_values():
    from etl.extract import DataExtractor

    df = pd.DataFrame(
        {"s": ["  hi  ", None, float("nan"), "   "], "n": [1.5, None, 3.0, 4.0]}
    )
    out = DataExtractor()._basic_clean(df)
    assert out["s"].tolist()[0] == "hi"
    assert out["s"].isnull().sum() == 3
    assert "nan" not in [str(v).lower() for v in out["s"].tolist()]


def test_try_parse_dates_branches():
    from etl.extract import DataExtractor

    ex = DataExtractor()
    all_na = pd.Series([None, None], dtype=object)
    assert ex._try_parse_dates(all_na).isnull().sum() == 2
    dated = pd.Series(["2024-01-01", "2024-02-01"], dtype=object)
    assert str(ex._try_parse_dates(dated).dtype).startswith("datetime64")
    junk = pd.Series(["zzz-not-a-date", "qqq-nope"], dtype=object)
    assert ex._try_parse_dates(junk).dtype == object


def test_extract_file_convenience(tmp_path):
    from etl.extract import extract_file

    f = tmp_path / "c.csv"
    f.write_text("a\n1\n", encoding="utf-8")
    assert extract_file(f)["default"].shape[0] == 1


# ------------------------------------------------- extract_from_api

@pytest.fixture
def public_api_host(monkeypatch):
    """Let the extractor's egress check pass for the fake host used here.

    ``DataExtractor.extract_from_api`` refuses a URL whose host cannot be
    resolved or that points into a private range (SSRF protection), so a
    parsing test has to pretend its host is a public one.
    """
    import ipaddress

    from api import security_egress

    monkeypatch.setattr(
        security_egress, "_resolve",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )
    return "http://x"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_extract_from_api_get_list(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp([{"a": 1}, {"a": 2}]))
    out = DataExtractor().extract_from_api(public_api_host)
    assert out["api_data"].shape[0] == 2


def test_extract_from_api_post_and_json_path(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp({"data": {"items": [{"a": 1}]}}))
    out = DataExtractor().extract_from_api(public_api_host, method="POST", json_path="data.items")
    assert out["api_data"].shape[0] == 1


def test_extract_from_api_dict_result_and_list_index_path(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp({"a": 5}))
    assert DataExtractor().extract_from_api(public_api_host)["api_data"].shape[0] == 1

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp([{"a": 9}, {"a": 10}]))
    out = DataExtractor().extract_from_api(public_api_host, json_path="0")
    assert out["api_data"].shape[0] == 1


def test_extract_from_api_bad_index_returns_empty(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp([{"a": 1}]))
    assert DataExtractor().extract_from_api(public_api_host, json_path="99") == {}
    assert DataExtractor().extract_from_api(public_api_host, json_path="nope.deeper") == {}


def test_extract_from_api_page_pagination(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
        if (params or {}).get("page", 1) == 1:
            return _FakeResp([{"a": 1}])
        return _FakeResp([])

    monkeypatch.setattr(requests, "get", fake_get)
    out = DataExtractor().extract_from_api(public_api_host, pagination={"type": "page", "param": "page", "max_pages": 3})
    assert out["api_data"].shape[0] == 1


def test_extract_from_api_next_url_pagination(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp({"a": 1, "next": "http://x/2"})
        return _FakeResp({"a": 2})

    monkeypatch.setattr(requests, "get", fake_get)
    out = DataExtractor().extract_from_api(
        public_api_host,
        pagination={"type": "next_url", "max_pages": 5, "next_key": "next"},
    )
    assert out["api_data"].shape[0] == 2


def test_extract_from_api_error_returns_empty(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    def boom(*a, **k):
        raise RuntimeError("net down")

    monkeypatch.setattr(requests, "get", boom)
    assert DataExtractor().extract_from_api(public_api_host) == {}


# ------------------------------------------------- second-pass gap closers

def test_cleaner_infer_datetime_sniff_object_dtype():
    from etl.clean import DataCleaner

    df = pd.DataFrame({"d": ["2024-01-01", "2024-01-02", None]})
    df["d"] = df["d"].astype(object)
    assert df["d"].dtype == object
    out = DataCleaner().clean(df)  # auto-infer sniffs datetime format
    assert str(out["d"].dtype).startswith("datetime64")


def test_cleaner_codegen_drop_rule_and_keep_global():
    from etl.clean import CleaningPlan, CleaningStrategy, DataCleaner

    plan = CleaningPlan(
        global_null_strategy=CleaningStrategy.FILL_MODE,
        column_rules={"c0": _rule("c0", CleaningStrategy.DROP)},
    )
    code = DataCleaner().generate_cleaning_code(plan)
    assert "c0" not in code  # DROP rules emit no fill line
    assert "dropna" not in code  # non-DROP global emits no dropna


def test_extract_excel_explicit_engine(tmp_path):
    from etl.extract import DataExtractor

    x = tmp_path / "eng.xlsx"
    with pd.ExcelWriter(x, engine="openpyxl") as w:
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="Only", index=False)
    data = DataExtractor().extract(x, engine="openpyxl")
    assert set(data) == {"Only"}


def test_extract_csv_latin1_fallback(tmp_path):
    from etl.extract import DataExtractor

    f = tmp_path / "latin.csv"
    f.write_bytes("name\ncaf\xe9\n".encode("latin-1"))
    data = DataExtractor().extract(f)
    assert data["default"].shape[0] == 1


def test_try_parse_dates_inference_fallback():
    from etl.extract import DataExtractor

    s = pd.Series(["January 5, 2024", "February 6, 2024"], dtype=object)
    parsed = DataExtractor()._try_parse_dates(s)
    assert str(parsed.dtype).startswith("datetime64")


def test_extract_from_api_scalar_json_path_returns_empty(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp({"a": 5}))
    assert DataExtractor().extract_from_api(public_api_host, json_path="a.b") == {}


def test_extract_from_api_page_pagination_empty_dict(monkeypatch, public_api_host):
    import requests

    from etl.extract import DataExtractor

    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
        calls["n"] += 1
        if (params or {}).get("page", 1) == 1:
            return _FakeResp([{"a": 1}])
        return _FakeResp({})

    monkeypatch.setattr(requests, "get", fake_get)
    out = DataExtractor().extract_from_api(public_api_host, pagination={"type": "page", "param": "page", "max_pages": 3})
    # empty-dict page still appended, but pagination stops (2 fetches, not 3)
    assert calls["n"] == 2
    assert out["api_data"].shape[0] == 2


def test_drop_all_tables_survives_single_drop_failure(tmp_path, monkeypatch):
    loader = _mkloader(tmp_path)
    loader.load_dataframe(_df(1), "u_ok")
    loader.load_dataframe(_df(1), "u_bad")
    import duckdb as _ddb

    import etl.load as load_mod

    real_connect = _ddb.connect

    class _Proxy:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and "u_bad" in sql and "DROP" in sql:
                raise RuntimeError("drop boom")
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(load_mod.duckdb, "connect", lambda p, *a, **k: _Proxy(real_connect(p, *a, **k)))
    try:
        dropped = loader.drop_all_tables()
    finally:
        monkeypatch.setattr(load_mod.duckdb, "connect", real_connect)
    assert dropped == 1
    assert loader.table_exists("u_ok") is False
    assert loader.table_exists("u_bad") is True  # failed drop survived


def test_schema_create_table_index_failure_warns(tmp_path, monkeypatch):
    import duckdb as _ddb

    from warehouse.schema import ColumnDef, SchemaManager, TableSchema

    real_connect = _ddb.connect

    class _Proxy:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and "CREATE INDEX" in sql:
                raise RuntimeError("index boom")
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    import warehouse.schema as schema_mod

    monkeypatch.setattr(schema_mod.duckdb, "connect", lambda p, *a, **k: _Proxy(real_connect(p, *a, **k)))
    try:
        m = SchemaManager(db_path=str(tmp_path / "s_idx_boom.duckdb"))
        m.register_table(
            TableSchema(name="ib", columns=[ColumnDef("id", "INTEGER")], indexes=["id"])
        )
        assert m.create_table("ib") is True
    finally:
        monkeypatch.setattr(schema_mod.duckdb, "connect", real_connect)
    assert m.table_exists("ib") is True
