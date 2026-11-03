"""Period-over-period (month-over-month) questions must compare the last two
periods per dimension — not fall through to a region-totals summary.

Live defect (benchmark, 2026-10-24): "Which region drove the month-over-month
revenue change in bench_sales?" produced total revenue by region over all
months, and the answer claimed the table had no time column even though
`order_date` is TIMESTAMP_NS. Root causes covered here:

* `classify_intent` had no vocabulary for "month-over-month" / "year-over-year"
  / "period-over-period", so the question fell through to a generic summary.
* `_table_date_column` resolved date columns by name only, so a type-based
  TIMESTAMP_NS column without a "date"/"time" name was invisible.
* `agent.schema_intelligence._is_date_column` matched type names exactly and
  missed DuckDB's TIMESTAMP_NS / TIMESTAMP_MS / TIMESTAMP_S.
"""

from __future__ import annotations

import re

from agent.planner import Planner
from agent.schema_intelligence import classify_schema

SCHEMA = (
    "TABLE analytics.bench_sales (2000 rows):\n"
    "  - order_id: VARCHAR\n"
    "  - order_date: TIMESTAMP_NS\n"
    "  - region: VARCHAR\n"
    "  - product: VARCHAR\n"
    "  - units: BIGINT\n"
    "  - unit_price: DOUBLE\n"
    "  - discount: DOUBLE\n"
    "  - revenue: DOUBLE\n"
)

MOM_Q = "Which region drove the month-over-month revenue change in bench_sales?"


def _planner() -> Planner:
    p = Planner()
    p.set_schema_context(SCHEMA)
    return p


class TestIntentClassification:
    def test_month_over_month_is_trend(self):
        assert _planner().classify_intent(MOM_Q) == "trend"

    def test_year_over_year_is_trend(self):
        q = "How did revenue change year-over-year in bench_sales?"
        assert _planner().classify_intent(q) == "trend"


class TestPeriodOverPeriodQuery:
    def test_mom_query_compares_two_periods_per_region(self):
        sql = _planner()._generate_period_over_period_query(MOM_Q, "bench_sales")
        assert sql, "month-over-month question must produce a comparison query"
        up = sql.upper()
        assert "GROUP BY" in up, sql
        assert re.search(r'\bregion\b', sql), sql
        assert "PREV_VALUE" in up and "CUR_VALUE" in up and "CHANGE" in up, sql
        assert "ORDER BY" in up, sql
        assert "SELECT *" not in up, sql

    def test_mom_query_uses_the_last_two_periods(self):
        sql = _planner()._generate_period_over_period_query(MOM_Q, "bench_sales")
        up = sql.upper()
        assert "LIMIT 2" in up, sql
        assert "DATE_TRUNC('MONTH'" in up, sql

    def test_explicit_months_are_used_verbatim(self):
        q = ("Compare revenue by region between August and September 2026 "
             "in bench_sales")
        sql = _planner()._generate_period_over_period_query(q, "bench_sales")
        assert sql, q
        assert "'2026-08'" in sql and "'2026-09'" in sql, sql
        assert "DATE_PART('MONTH'" in sql.upper(), sql

    def test_non_mom_question_returns_none(self):
        assert _planner()._generate_period_over_period_query(
            "Show the monthly revenue trend in bench_sales", "bench_sales") is None


class TestPlanRouting:
    def test_mom_question_routes_to_comparison_step(self):
        p = _planner()
        plan = p.create_plan(MOM_Q, ["bench_sales"])
        assert plan.steps, "MoM question must produce a plan"
        query = plan.steps[0].params.get("query", "")
        assert "prev_value" in query.lower() and "change" in query.lower(), query
        assert plan.steps[0].metadata.get("period_comparison") is True

    def test_monthly_trend_still_uses_time_series(self):
        p = _planner()
        plan = p.create_plan("Show the monthly revenue trend in bench_sales",
                             ["bench_sales"])
        query = plan.steps[0].params.get("query", "")
        up = query.upper()
        assert "AS PERIOD" in up, query
        assert "PREV_VALUE" not in up, query


class TestDateColumnDetection:
    def test_timestamp_ns_column_is_a_date_column(self):
        profile = classify_schema("t", {"event_ts": "TIMESTAMP_NS",
                                        "amount": "DOUBLE"}, row_count=10)
        assert "event_ts" in profile.date_cols, profile.date_cols

    def test_timestamp_ms_and_s_are_date_columns(self):
        profile = classify_schema("t", {"a_ts": "TIMESTAMP_MS",
                                        "b_ts": "TIMESTAMP_S"}, row_count=10)
        assert {"a_ts", "b_ts"} <= set(profile.date_cols), profile.date_cols

    def test_planner_falls_back_to_type_for_date_column(self):
        p = Planner()
        p.set_schema_context(
            "TABLE events (10 rows):\n"
            "  - event_ts: TIMESTAMP_NS\n"
            "  - amount: DOUBLE\n"
        )
        assert p._table_date_column("events") == "event_ts"


class TestPeriodNotesGuard:
    """A period-comparison plan must never get a false 'no time column' note."""

    def test_comparison_plan_suppresses_period_notes(self):
        import pandas as pd

        from agent.orchestrator import Orchestrator

        plan = _planner().create_plan(MOM_Q, ["bench_sales"])
        assert Orchestrator._is_period_comparison(plan)
        result = pd.DataFrame({"group_name": ["East"], "change": [1.0]})
        assert Orchestrator._trend_no_period_note(result, plan) == ""
        assert Orchestrator._single_period_note(result, plan) == ""

    def test_plain_plan_does_not_count_as_comparison(self):
        from agent.orchestrator import Orchestrator

        plan = _planner().create_plan("Show the monthly revenue trend in bench_sales",
                                      ["bench_sales"])
        assert not Orchestrator._is_period_comparison(plan)


class TestGeneratedSqlExecutes:
    """The generated SQL must run on the real DuckDB types, not just look right."""

    @staticmethod
    def _db():
        import duckdb

        con = duckdb.connect(":memory:")
        con.execute(
            'CREATE TABLE bench_sales (order_id VARCHAR, order_date TIMESTAMP_NS, '
            'region VARCHAR, product VARCHAR, units BIGINT, unit_price DOUBLE, '
            'discount DOUBLE, revenue DOUBLE)'
        )
        rows = [
            ("o1", "2026-08-03", "East", "A", 2, 10.0, 0.0, 20.0),
            ("o2", "2026-08-04", "West", "A", 1, 10.0, 0.0, 10.0),
            ("o3", "2026-08-05", "North", "B", 3, 10.0, 0.0, 30.0),
            ("o4", "2026-09-01", "East", "A", 5, 10.0, 0.0, 50.0),
            ("o5", "2026-09-02", "West", "A", 1, 10.0, 0.0, 10.0),
            ("o6", "2026-09-03", "North", "B", 2, 10.0, 0.0, 20.0),
        ]
        con.executemany(
            "INSERT INTO bench_sales VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        return con

    def test_dynamic_last_two_periods_sql_runs(self):
        sql = _planner()._generate_period_over_period_query(MOM_Q, "bench_sales")
        con = self._db()
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        by_region = {r["group_name"]: r for r in rows}
        assert by_region["East"]["prev_period"] == "2026-08"
        assert by_region["East"]["cur_period"] == "2026-09"
        assert by_region["East"]["change"] == 30.0
        assert by_region["West"]["change"] == 0.0
        assert by_region["North"]["change"] == -10.0
        # Largest absolute change first.
        assert rows[0]["group_name"] == "East"

    def test_explicit_month_sql_runs(self):
        q = ("Compare revenue by region between August and September 2026 "
             "in bench_sales")
        sql = _planner()._generate_period_over_period_query(q, "bench_sales")
        con = self._db()
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        by_region = {r["group_name"]: r for r in rows}
        assert by_region["East"]["prev_period"] == "2026-08"
        assert by_region["East"]["change"] == 30.0

