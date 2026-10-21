"""TDD (F1): query builder must reject SQL injection via `limit`/`offset`
and via unquoted table names in the JOIN auto-detector.

Pre-fix behaviour (audit PoC): ``POST /query/visual/execute`` with
``{"from_table": "sales", "limit": "5; DROP TABLE ..."}`` executed the DROP,
and ``/auto-join`` interpolated table names raw into ``SELECT * FROM {table}``.
"""

import duckdb
import pandas as pd
import pytest

from api.querybuilder import (
    GroupByField,
    JoinDetector,
    SQLBuilder,
    SortSpec,
    parse_query_spec,
)


@pytest.fixture()
def built_db(tmp_path):
    """Temp warehouse with an analytics schema and a `victim` table."""
    db_path = tmp_path / "builder.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA analytics")
    con.execute("CREATE TABLE analytics.sales (product VARCHAR, amount INTEGER)")
    con.execute("INSERT INTO analytics.sales VALUES ('a', 1), ('b', 2)")
    con.execute("CREATE TABLE analytics.victim (id INTEGER)")
    con.execute("INSERT INTO analytics.victim VALUES (1)")
    con.close()
    return db_path


def _victim_exists(db_path) -> bool:
    con = duckdb.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'analytics' AND table_name = 'victim'"
        ).fetchone()[0] == 1
    finally:
        con.close()


INJECTION = "5; DROP TABLE analytics.victim; --"


# ── QuerySpec coercion ────────────────────────────────────────────────────

def test_limit_string_coerced_to_int():
    spec = parse_query_spec({"from_table": "sales", "limit": "250"})
    assert spec.limit == 250
    assert isinstance(spec.limit, int)


def test_offset_string_coerced_to_int():
    spec = parse_query_spec({"from_table": "sales", "offset": "7"})
    assert spec.offset == 7


def test_integral_float_limit_coerced():
    spec = parse_query_spec({"from_table": "sales", "limit": 10.0})
    assert spec.limit == 10


def test_defaults_preserved():
    spec = parse_query_spec({"from_table": "sales"})
    assert spec.limit == 1000
    assert spec.offset == 0


@pytest.mark.parametrize(
    "bad",
    [
        INJECTION,
        "1;DROP TABLE analytics.victim",
        -1,
        "-3",
        5.7,
        "5.7",
        True,
        False,
        None,
        "",
        "abc",
        [5],
        {"v": 5},
        10 ** 9,
    ],
)
def test_invalid_limit_rejected(bad):
    with pytest.raises(ValueError):
        parse_query_spec({"from_table": "sales", "limit": bad})


@pytest.mark.parametrize("bad", [INJECTION, -1, "nope", True, None])
def test_invalid_offset_rejected(bad):
    with pytest.raises(ValueError):
        parse_query_spec({"from_table": "sales", "offset": bad})


@pytest.mark.parametrize(
    "bad",
    [
        [INJECTION],
        {"column": "x"},
        5,
        True,
    ],
)
def test_invalid_sort_column_rejected(bad):
    with pytest.raises(ValueError, match="sort column"):
        parse_query_spec({"from_table": "sales", "order_by": bad})


def test_simple_format_group_by_strings_coerced(built_db):
    spec = parse_query_spec(
        {
            "table": "sales",
            "columns": ["product"],
            "group_by": ["product"],
            "aggregates": [{"column": "amount", "function": "sum", "alias": "total"}],
        }
    )
    assert all(isinstance(g, GroupByField) for g in spec.group_by)

    builder = SQLBuilder(db_path=str(built_db), schema="analytics")
    sql, error = builder.build(spec)
    assert error is None, error
    assert "GROUP BY" in sql.upper()
    assert "SUM(" in sql.upper()

    result = builder.execute(spec)
    assert result["success"] is True, result.get("error")
    assert result["row_count"] == 2


def test_order_by_aggregate_alias_uses_bare_alias(built_db):
    spec = parse_query_spec(
        {
            "table": "sales",
            "columns": ["product"],
            "group_by": ["product"],
            "aggregates": [{"column": "amount", "function": "sum", "alias": "total"}],
            "order_by": "total",
            "order_direction": "desc",
        }
    )

    builder = SQLBuilder(db_path=str(built_db), schema="analytics")
    sql, error = builder.build(spec)
    assert error is None, error
    assert 'ORDER BY "total" DESC' in sql

    result = builder.execute(spec)
    assert result["success"] is True, result.get("error")
    assert [row["product"] for row in result["data"]] == ["b", "a"]


def test_invalid_sort_column_rejected_full_format():
    with pytest.raises(ValueError, match="sort column"):
        parse_query_spec(
            {"from_table": "sales", "sorts": [{"column": ["x"], "direction": "asc"}]}
        )


# ── Builder-level defence in depth ────────────────────────────────────────

def test_tampered_limit_blocks_build_and_execute(built_db):
    spec = parse_query_spec({"from_table": "sales", "limit": 10})
    # Simulate a caller bypassing parse_query_spec (e.g. dict mutation).
    spec.limit = INJECTION
    builder = SQLBuilder(db_path=str(built_db), schema="analytics")

    sql, error = builder.build(spec)
    assert error is not None
    assert "DROP" not in sql.upper()

    result = builder.execute(spec)
    assert result["success"] is False
    assert _victim_exists(built_db)


def test_tampered_sort_column_blocks_build_and_execute(built_db):
    spec = parse_query_spec({"from_table": "sales", "limit": 10})
    # Simulate a caller bypassing parse_query_spec (e.g. dict mutation).
    spec.sorts = [SortSpec()]
    spec.sorts[0].column = [INJECTION]
    builder = SQLBuilder(db_path=str(built_db), schema="analytics")

    sql, error = builder.build(spec)
    assert error is not None
    assert "DROP" not in sql.upper()

    result = builder.execute(spec)
    assert result["success"] is False
    assert _victim_exists(built_db)


def test_legit_query_still_executes(built_db):
    spec = parse_query_spec({"from_table": "sales", "limit": "10"})
    result = SQLBuilder(db_path=str(built_db), schema="analytics").execute(spec)
    assert result["success"] is True, result.get("error")
    assert result["row_count"] == 2


# ── JoinDetector table-name injection ─────────────────────────────────────

def test_get_sample_data_rejects_injection(built_db):
    detector = JoinDetector(db_path=str(built_db), schema="analytics")
    df = detector.get_sample_data("sales; DROP TABLE analytics.victim; --")
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert _victim_exists(built_db)


def test_get_sample_data_accepts_plain_and_qualified(built_db):
    detector = JoinDetector(db_path=str(built_db), schema="analytics")
    plain = detector.get_sample_data("sales")
    qualified = detector.get_sample_data("analytics.sales")
    assert len(plain) == 2
    assert len(qualified) == 2


def test_detect_joins_with_injection_payload(built_db):
    detector = JoinDetector(db_path=str(built_db), schema="analytics")
    candidates = detector.detect_joins(
        "sales", "victim; DROP TABLE analytics.victim; --"
    )
    assert candidates == []
    assert _victim_exists(built_db)


def test_suggest_metrics_with_injection_payload(built_db):
    detector = JoinDetector(db_path=str(built_db), schema="analytics")
    assert detector.suggest_metrics("victim; DROP TABLE analytics.victim; --") == []
    assert _victim_exists(built_db)


def test_detect_joins_legit(built_db):
    detector = JoinDetector(db_path=str(built_db), schema="analytics")
    candidates = detector.detect_joins("sales", "victim")
    assert isinstance(candidates, list)
