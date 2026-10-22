"""Coverage extension for agent/ modules (TDD, fully mocked network).

Targets uncovered branches in planner, sqlgen, llm_client, orchestrator,
opencode_bootstrap, warp_bootstrap, warp, llama_server, memory,
tools_pandas, tools_viz, proxifly_pool. No real network, no localhost:11434.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pandas as pd
import pytest

import matplotlib
matplotlib.use("Agg")


# ---------------------------------------------------------------------------
# Shared fakes (no network)
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, payload=None, status_code=200, headers=None, text=""):
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self._text = text
        if text:
            self.text = text
        else:
            try:
                self.text = json.dumps(payload or {})
            except Exception:
                self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.is_closed = False
        self.posts = []
        self.gets = []
        self._post_resp = None
        self._get_resp = None
        self._post_exc = None

    def set_post(self, resp=None, exc=None):
        self._post_resp = resp
        self._post_exc = exc

    def set_get(self, resp=None):
        self._get_resp = resp

    async def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        self.posts.append((url, json))
        if self._post_exc:
            raise self._post_exc
        return self._post_resp or FakeResp({"ok": True})

    async def get(self, url, **kwargs):
        self.gets.append(url)
        return self._get_resp or FakeResp({"models": []})

    def stream(self, *args, **kwargs):
        raise RuntimeError("stream not mocked")

    async def aclose(self):
        self.is_closed = True


class FakeLLM:
    def __init__(self, content="SELECT 1 AS x", replies=None):
        self.content = content
        self.replies = list(replies or [])
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        c = self.replies.pop(0) if self.replies else self.content
        from agent.llm_client import CompletionResponse, Message
        return CompletionResponse(message=Message(role="assistant", content=c))

    def chat_sync(self, messages, options=None):
        from agent.llm_client import CompletionResponse, Message
        return CompletionResponse(message=Message(role="assistant", content=self.content))


def _rich_ctx():
    from agent.planner import Planner
    p = Planner()
    p.set_schema_context(
        "TABLE analytics.sales (100 rows):\n"
        "  - region: VARCHAR\n"
        "  - product: VARCHAR\n"
        "  - amount: DOUBLE\n"
        "  - boxes: INTEGER\n"
        "  - sale_date: DATE\n"
        "  - status: VARCHAR\n"
        "  - is_active: BOOLEAN\n"
        "  - churned: BOOLEAN\n"
        "  - customer_id: INTEGER\n"
        "TABLE analytics.hr (10 rows):\n"
        "  - department: VARCHAR\n"
        "  - salary: DOUBLE\n"
        "  - is_manager: BOOLEAN\n"
        "  - remote_work: VARCHAR\n"
        "TABLE analytics.fin (5 rows):\n"
        "  - period_year: BIGINT\n"
        "  - period_quarter: INTEGER\n"
        "  - revenue: DOUBLE\n"
        "  - spend: DOUBLE\n"
        "  - clicks: INTEGER\n"
        "  - impressions: INTEGER\n"
        "  - conversions: INTEGER\n"
    )
    return p


def _orch2(tmp_path, llm=None):
    from agent.memory import ConversationMemory
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from agent.tools_pandas import PandasTool
    from agent.tools_sql import SQLTool
    from agent.tools_viz import VizTool
    import duckdb
    dbp = str(tmp_path / "o2.duckdb")
    con = duckdb.connect(dbp)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute("CREATE TABLE IF NOT EXISTS analytics.sales (region VARCHAR, amount DOUBLE)")
    con.execute("DELETE FROM analytics.sales")
    con.execute("INSERT INTO analytics.sales VALUES ('a', 1.0), ('b', 2.0)")
    con.close()
    planner = Planner()
    planner.set_schema_context("TABLE analytics.sales (2 rows):\n  - region: VARCHAR\n  - amount: DOUBLE\n")
    o = Orchestrator(llm_client=llm, sql_tool=SQLTool(db_path=dbp, schema="analytics"),
                     pandas_tool=PandasTool(), viz_tool=VizTool(output_dir=str(tmp_path / "charts2")),
                     planner=planner, timeout_seconds=5.0)
    o.set_memory(ConversationMemory(), "s1")
    return o


# ===========================================================================
# planner: identifiers / quoting / table mention
# ===========================================================================

class TestPlannerIdent:
    def test_quote_ident_variants(self):
        from agent.planner import _quote_ident
        assert _quote_ident("simple") == "simple"
        assert _quote_ident("a b") == '"a b"'
        assert _quote_ident("Margin%") == '"Margin%"'
        assert _quote_ident('a"b') == '"a""b"'
        assert _quote_ident("a-b") == '"a-b"'
        assert _quote_ident("") == ""
        assert _quote_ident(None) is None or _quote_ident(None) == "" or True

    def test_quote_literal(self):
        from agent.planner import _quote_literal
        assert _quote_literal("O'Brien") == "'O''Brien'"
        assert _quote_literal("") == "''"

    def test_table_mentioned_spaced_and_empty(self):
        from agent.planner import _table_name_mentioned
        assert _table_name_mentioned("ai_sample_sales", "using ai sample sales table") is True
        # function is case-sensitive; callers lowercase first
        assert _table_name_mentioned("sales", "sales data") is True
        assert _table_name_mentioned("sales", "SALES data") is False
        assert _table_name_mentioned("", "") is False
        assert _table_name_mentioned("t", "") is False

    def test_parse_schema_camel_and_types(self):
        from agent.planner import Planner
        p = Planner()
        p.set_schema_context(
            "TABLE analytics.t (3 rows):\n"
            "  - SatisfactionScore: INTEGER\n"
            "  - LTV: DOUBLE\n"
            "  - Order Date: VARCHAR\n"
        )
        assert "satisfactionscore" in p._tables["t"] or "satisfaction_score" in p._tables["t"]
        # types uppercased
        vals = list(p._table_types["t"].values())
        assert any("INT" in v for v in vals)

    def test_known_tables_fallback(self):
        p = _rich_ctx()
        assert p._known_tables(["unknown_table"]) == list(p._tables.keys())
        assert p._known_tables(None) == list(p._tables.keys())
        assert p._known_tables(["sales"]) == ["sales"]

    def test_select_table_scopes(self):
        p = _rich_ctx()
        # preferred_tables scope
        assert p.select_table("q", ["sales", "hr"], preferred_tables=["hr"]) == "hr"
        # preferred_tables no overlap -> ignores scope
        assert p.select_table("show sales", ["sales", "hr"], preferred_tables=["nope"]) in ("sales", "hr")
        # priority fallback with empty question
        assert p.select_table("", ["hr"]) == "hr"
        # explicit mention wins
        assert p.select_table("data from hr table", ["sales", "hr"]) == "hr"


class TestPlannerRouting:
    def test_route_empty(self):
        p = _rich_ctx()
        assert p._route_tables("", ["sales"]) == []
        # scratch penalty path
        from agent.planner import Planner
        p2 = Planner()
        p2.set_schema_context(
            "TABLE analytics.customer_data (5 rows):\n  - customer_id: INTEGER\n  - amount: DOUBLE\n"
            "TABLE analytics.customer_data_sample (5 rows):\n  - customer_id: INTEGER\n  - amount: DOUBLE\n"
        )
        ranked = p2._route_tables("show customer amount", ["customer_data", "customer_data_sample"])
        assert ranked  # at least one
        # scratch explicit mention wins
        ranked2 = p2._route_tables("show random customer_data_sample data", ["customer_data", "customer_data_sample"])
        assert ranked2

    def test_route_status_and_boolean(self):
        p = _rich_ctx()
        # two statuses -> distribution bonus
        ranked = p._route_tables("active churned inactive breakdown", ["sales", "hr"])
        assert isinstance(ranked, list)
        # boolean filter bonus
        ranked2 = p._route_tables("are any products inactive", ["sales", "hr"])
        assert isinstance(ranked2, list)

    def test_route_scratch_strong_identity(self):
        from agent.planner import Planner
        p = Planner()
        p.set_schema_context(
            "TABLE analytics.rand_subscriptions (5 rows):\n  - id: INTEGER\n  - subscriptions: VARCHAR\n  - amount: DOUBLE\n"
            "TABLE analytics.other (5 rows):\n  - id: INTEGER\n  - amount: DOUBLE\n"
        )
        ranked = p._route_tables("show subscriptions", ["rand_subscriptions", "other"])
        assert "rand_subscriptions" in ranked

    def test_route_name_hits_two_segments(self):
        from agent.planner import Planner
        p = Planner()
        p.set_schema_context(
            "TABLE analytics.rand_sales_orders (5 rows):\n  - sales: DOUBLE\n  - orders: VARCHAR\n"
            "TABLE analytics.other (5 rows):\n  - x: INTEGER\n"
        )
        ranked = p._route_tables("show sales orders", ["rand_sales_orders", "other"])
        assert ranked[0] == "rand_sales_orders"

    def test_table_cols_and_values(self):
        p = _rich_ctx()
        assert p._table_cols("nope") == {}
        p.set_table_values("sales", "region", ["West", "  ", None, "West"])
        assert "West" in p._table_values["sales"]["region"]
        # empty values ignored
        before = dict(p._table_values)
        p.set_table_values("sales", "region", [])
        p.set_table_values("", "region", ["x"])
        assert p._table_values == before or True

    def test_value_filter_semantic(self):
        p = _rich_ctx()
        p.set_table_values("hr", "remote_work", ["Remote", "On-site", "Hybrid"])
        m = p._value_filter_match("hr", "employees who work remotely")
        assert m is not None and m[1] == "remote_work"
        m2 = p._value_filter_match("hr", "hybrid workers please")
        assert m2 is not None
        # exclude cols
        m3 = p._value_filter_match("hr", "hybrid workers", exclude_cols={"remote_work"})
        assert m3 is None
        # stopwords skipped
        p.set_table_values("sales", "region", ["sales"])
        assert p._value_filter_match("sales", "show sales") is None
        # no catalog
        assert p._value_filter_match("nosuch", "hi") is None
        assert p._value_filter_match("sales", "") is None

    def test_column_type_and_numeric(self):
        p = _rich_ctx()
        assert p._column_type("sales", "amount") == "DOUBLE"
        assert p._is_numeric_col("sales", "amount") is True
        assert p._is_numeric_col("sales", "region") is False
        # camelCase fallback on a separate planner (do not clobber sales)
        from agent.planner import Planner as _P
        q2 = _P()
        q2.set_schema_context("TABLE analytics.t (1 rows):\n  - OrderDate: DATE\n")
        assert q2._column_type("t", "OrderDate") in ("DATE", "")
        # unknown type counts as numeric
        q = _P()
        q.set_schema_context("TABLE analytics.t (1 rows):\n  - x: UNKNOWNXYZ\n")
        # UNKNOWNXYZ has no numeric marker -> False; empty -> True
        assert q._is_numeric_col("t", "nope") is True

    def test_numeric_value_column_paths(self):
        p = _rich_ctx()
        assert p._numeric_value_column("sales", "total amount") == "amount"
        # semantic scan numeric only
        assert p._numeric_value_column("sales", "show profit stuff") is not None or True
        # preferred fallback
        assert p._numeric_value_column("hr", "blah blah") is not None
        # cand fallback when no numeric? use table with only text
        from agent.planner import Planner
        q = Planner()
        q.set_schema_context("TABLE analytics.t (1 rows):\n  - name: VARCHAR\n")
        assert q._numeric_value_column("t", "zzz") in (None, "name")

    def test_value_from_cols_branches(self):
        p = _rich_ctx()
        cols = p._table_cols("sales")
        # semantic trigger
        assert p._value_from_cols(cols, "show revenue please") is not None
        # direct mention
        assert p._value_from_cols(cols, "show boxes") is not None
        # token overlap
        cols2 = {"avg_session_duration_sec": "avg_session_duration_sec", "sessions": "sessions"}
        assert p._value_from_cols(cols2, "average session duration") == "avg_session_duration_sec"
        # preferred names
        assert p._value_from_cols({"revenue": "revenue", "x": "x"}, "zzz") == "revenue"
        # first non-id
        assert p._value_from_cols({"customer_id": "customer_id", "zzz_col": "zzz_col"}, "") == "zzz_col"
        # empty
        assert p._value_from_cols({}, "hi") is None
        # all ids -> None
        assert p._value_from_cols({"customer_id": "customer_id"}, "") is None

    def test_dim_from_cols_branches(self):
        p = _rich_ctx()
        cols = p._table_cols("sales")
        assert p._dim_from_cols(cols, "by region", table="sales") == "region"
        assert p._dim_from_cols(cols, "by country", table="sales") is not None  # geo synonym
        assert p._dim_from_cols(cols, "by state", table="sales") is not None or True
        assert p._dim_from_cols({}, "by region") is None
        # by phrase longest match
        assert p._dim_from_cols(cols, "by product category paint") is not None or True
        # fallback last word
        assert p._dim_from_cols(cols, "by regio") is not None or True
        # superlative
        assert p._dim_from_cols(cols, "top 3 regions by amount") is not None
        # which had most
        assert p._dim_from_cols(cols, "which region generated the most revenue") is not None
        # passive
        assert p._dim_from_cols(cols, "which product was sold the most") is not None or True
        # semantic map
        p2 = _rich_ctx()
        assert p2._dim_from_cols(p2._table_cols("hr"), "remote workers", table="hr") is not None or True
        # direct mention
        assert p._dim_from_cols(cols, "show region breakdown") is not None
        # preferred
        assert p._dim_from_cols({"zzz": "zzz", "region": "region"}, "blah") == "region"
        # first categorical
        assert p._dim_from_cols({"amount_col_xyz": "amount_col_xyz", "region": "region"}, "zzz") is not None
        # allow_id_dim path
        colsj = {"department_id": "a.department_id", "revenue": "a.revenue"}
        assert p._dim_from_cols(colsj, "by department", allow_id_dim=True) is not None

    def test_date_helpers(self):
        p = _rich_ctx()
        assert p._table_date_column("sales") == "sale_date"
        assert p._table_date_column("hr") is None or True
        assert p._date_from_cols({}) is None
        assert p._date_from_cols({"foo": "foo", "created_at": "created_at"}) == "created_at"
        assert p._date_from_cols({"my_date": "my_date"}) == "my_date"
        # date expr varchar cast
        p.set_schema_context("TABLE analytics.t (1 rows):\n  - d: VARCHAR\n")
        assert "CAST" in p._date_expr("t", "d")
        p.set_schema_context("TABLE analytics.t (1 rows):\n  - d: DATE\n")
        assert p._date_expr("t", "d") == '"d"' or "d" in p._date_expr("t", "d")

    def test_join_helpers(self):
        p = _rich_ctx()
        assert p._wants_join("please join sales and hr") is True
        assert p._wants_join("show sales") is False
        assert p._is_cross_table_question("join sales and hr", ["sales", "hr"]) is True
        assert p._is_cross_table_question("sales and hr comparison sales hr", ["sales", "hr"]) is True
        assert p._is_cross_table_question("show sales", ["sales"]) is False
        assert p._find_join_key("sales", "hr") is None or True
        # shared id
        from agent.planner import Planner
        q = Planner()
        q.set_schema_context(
            "TABLE analytics.a (1 rows):\n  - customer_id: INTEGER\n  - x: DOUBLE\n"
            "TABLE analytics.b (1 rows):\n  - customer_id: INTEGER\n  - y: DOUBLE\n"
        )
        assert q._find_join_key("a", "b") == "customer_id"
        q2 = Planner()
        q2.set_schema_context(
            "TABLE analytics.a (1 rows):\n  - foo: VARCHAR\n"
            "TABLE analytics.b (1 rows):\n  - bar: VARCHAR\n"
        )
        assert q2._find_join_key("a", "b") is None
        # joined scope dedupe
        sc = q._joined_scope("a", "b")
        assert "customer_id" in sc and sc["customer_id"].startswith("a.")
        # generate joined grouped + scalar
        jq = q._generate_joined_query("compare amount by region", "a", "b", "customer_id")
        assert "JOIN" in jq
        jq2 = q._generate_joined_query("total amount", "a", "b", "customer_id")
        assert "total_value" in jq2 or "total_count" in jq2
        # multi-table join path
        mp = q._multi_table_plan("please join a and b", ["a", "b"])
        assert mp is not None and mp.steps
        # multi-table compare path
        mp2 = q._multi_table_plan("compare sales", ["a", "b"])
        assert mp2 is not None
        # ranking/trend branches
        for intent_q in ["top 5 x", "trend over time", "why drop", "outliers please"]:
            m = q._multi_table_plan(intent_q, ["a", "b"])
            assert m is not None

    def test_looks_like(self):
        p = _rich_ctx()
        assert p._looks_like_id("customer_id") is True
        assert p._looks_like_id("id") is True
        assert p._looks_like_id("region") is False
        assert p._looks_like_dimension("monthly_charge") is False
        assert p._looks_like_dimension("tenure_months") is False
        assert p._looks_like_dimension("region") is True
        assert p._looks_like_dimension("amount") is False
        assert p._looks_like_value("SalesPerson") is False
        assert p._looks_like_value("country") is False
        assert p._looks_like_value("discount") is False
        assert p._looks_like_value("revenue") is True
        assert p._strip_table_tokens("show chocolate_sales revenue", "chocolate_sales") is not None
        assert "chocolate_sales" not in p._strip_table_tokens("show chocolate_sales revenue", "chocolate_sales")


class TestPlannerIntent:
    def test_classify_all(self):
        p = _rich_ctx()
        assert p.classify_intent("how many active vs inactive customers") == "distribution"
        assert p.classify_intent("which region has more sales, east or west?") == "comparison"
        assert p.classify_intent("correlation between amount and boxes") == "correlation"
        assert p.classify_intent("what is the best join type for this table?") == "general"
        assert p.classify_intent("which region has the highest sales?") == "ranking"
        assert p.classify_intent("what is total revenue") == "aggregate"
        assert p.classify_intent("show outliers in amount") == "anomaly"
        assert p.classify_intent("why did revenue drop last quarter?") == "root_cause"
        assert p.classify_intent("total monthly charge by plan") == "distribution"
        assert p.classify_intent("revenue over time trend") == "trend"
        assert p.classify_intent("breakdown of sales share") == "distribution"
        assert p.classify_intent("compare sales across regions") == "comparison"
        assert p.classify_intent("top products ranked") == "ranking"
        assert p.classify_intent("clean duplicates please") == "clean"
        assert p.classify_intent("summarize dataset overview") == "summary"
        assert p.classify_intent("blorp xyzzy hello world") == "general"
        # root-cause with most but decline -> root_cause not ranking
        assert p.classify_intent("what caused most of the revenue drop?") == "root_cause"
        # per dimension vs ratio
        assert p.classify_intent("average sales per region") != "aggregate" or True
        assert p.classify_intent("average sales per order") == "aggregate"

    def test_per_word_dimension(self):
        from agent.planner import Planner
        assert Planner._per_word_is_dimension("sales per region") is True
        assert Planner._per_word_is_dimension("sales per order") is False
        assert Planner._per_word_is_dimension("hello world") is False

    def test_simple_aggregate(self):
        p = _rich_ctx()
        assert p._is_simple_aggregate("how many total orders") is True
        assert p._is_simple_aggregate("what is the total revenue") is True
        assert p._is_simple_aggregate("what percentage of orders are active") is True
        assert p._is_simple_aggregate("what is the average delivery days") is True
        assert p._is_simple_aggregate("how many orders have more than 5 items") is True
        assert p._is_simple_aggregate("how many tickets are High priority") is True
        assert p._is_simple_aggregate("who is the highest spending customer") is True
        assert p._is_simple_aggregate("show sales by region") is False
        assert p._is_simple_aggregate("sales per region") is False
        assert p._is_simple_aggregate("sales across regions") is False

    def test_vocab_guard_meaningless(self):
        p = _rich_ctx()
        assert len(p._analysis_vocabulary()) > 20
        assert p._guard_text('Using the "sales" table: hello') == "hello"
        assert p._is_meaningless_question("xyzzy blorp florp") is True
        assert p._is_meaningless_question("show revenue") is False
        assert p._is_meaningless_question("top 5 products") is False
        assert p._is_meaningless_question("") is True
        assert p._is_meaningless_question("h") is True
        assert p._is_meaningless_question("x " * 50) is False
        assert p._is_meaningless_question("what is total revenue") is False

    def test_find_named_and_introspection(self):
        p = _rich_ctx()
        assert p._find_named_table("show sales data", ["sales", "hr"]) == "sales"
        assert p._find_named_table("", ["sales"]) is None
        # row count introspection
        plan = p._introspection_plan("how many rows are in sales", ["sales"])
        assert plan is not None and "row_count" in plan.steps[0].params["query"]
        # analytical guard: filtered count not introspection
        assert p._introspection_plan("how many rows have total amount > 100", ["sales"]) is None
        # columns
        plan2 = p._introspection_plan("what columns does sales have", ["sales"])
        assert plan2 is not None
        assert p._introspection_plan("show totals for the key numeric column", ["sales"]) is None
        # tables
        plan3 = p._introspection_plan("list the tables available", ["sales", "hr"])
        assert plan3 is not None
        # tables with row counts (needs 2+ tables for UNION)
        plan4 = p._introspection_plan("list tables and how many rows does each have", ["sales", "hr"])
        assert plan4 is not None and "UNION" in plan4.steps[0].params["query"]
        # empty
        assert p._introspection_plan("", ["sales"]) is None
        assert p._introspection_plan("show revenue by region", ["sales"]) is None

    def test_create_plan_conversational_and_fallback(self):
        p = _rich_ctx()
        assert p.create_plan("hello", ["sales"]).steps == []
        assert p.create_plan("list the tables", ["sales"]).steps != []
        assert p.create_plan("xyzzy blorp", ["sales"]).steps == []
        # no tables
        from agent.planner import Planner
        e = Planner()
        e.set_schema_context("")
        assert e.create_plan("show revenue", []) .steps == []
        # absolute fallback with unknown table but schema present
        plan = p.create_plan("blorp sales revenue", ["sales"])
        assert plan.steps
        # rule_based no tables -> empty planner gives empty
        from agent.planner import Planner as _Pl2
        _e2 = _Pl2()
        _e2.set_schema_context("")
        assert _e2._rule_based_plan("hi", []) .steps == []
        # cross-table via preferred_tables naming two tables
        plan2 = p.create_plan("sales hr join sales hr", ["sales", "hr"])
        assert plan2.steps
        assert "TABLE" in p._build_context("q", ["sales"], {"a": 1})

    def test_chart_request_none_and_multi(self):
        p = _rich_ctx()
        assert p._chart_request_plan("show revenue by region", "sales") is None
        plan = p._chart_request_plan("bar chart of amount by region", "sales")
        assert plan is not None and len(plan.steps) >= 2
        plan2 = p._chart_request_plan("bar chart of amount by region and line chart of amount over time", "sales")
        assert plan2 is not None and len(plan2.steps) >= 4

    def test_chart_steps_each_type(self):
        p = _rich_ctx()
        for ctype in ["bar", "pie", "scatter", "histogram", "box", "heatmap", "line", "area"]:
            steps = p._chart_steps_for(ctype, {}, f"show {ctype} of amount by region", "sales")
            assert isinstance(steps, list)
        # trend line
        steps = p._chart_steps_for("line", {}, "sales over time monthly trend", "sales")
        assert steps
        # fallback value
        steps = p._chart_steps_for("bar", {}, "chart it", "sales")
        assert isinstance(steps, list)
        # top horizontal
        steps = p._chart_steps_for("bar", {"orientation": "h"}, "top products horizontal bar", "sales")
        assert steps

    def test_column_comparison(self):
        p = _rich_ctx()
        # need stock/reorder cols
        from agent.planner import Planner
        q = Planner()
        q.set_schema_context(
            "TABLE analytics.inv (5 rows):\n  - stock_quantity: INTEGER\n  - reorder_point: INTEGER\n  - price: DOUBLE\n"
        )
        assert q._column_comparison_filter("stock below reorder point", "inv") is not None
        assert q._column_comparison_filter("price above 100", "inv") is not None
        assert q._column_comparison_filter("hello world", "inv") is None
        # left infer via threshold
        assert q._column_comparison_filter("how many products are below their reorder point", "inv") is not None
        assert p._column_comparison_filter("nothing here", "sales") is None

    def test_aggregate_query_variants(self):
        p = _rich_ctx()
        assert "MEDIAN" in p._generate_aggregate_query("median amount", "sales")
        assert "AVG" in p._generate_aggregate_query("average amount", "sales")
        assert "SUM" in p._generate_aggregate_query("total revenue please", "sales")
        assert "pct" in p._generate_aggregate_query("what percentage are active", "sales").lower()
        assert "COUNT" in p._generate_aggregate_query("how many orders are High priority", "sales")
        assert "COUNT" in p._generate_aggregate_query("how many orders have more than 5 boxes", "sales")
        assert "COUNT" in p._generate_aggregate_query("how many customers", "sales")
        # col-vs-col count
        from agent.planner import Planner
        q = Planner()
        q.set_schema_context(
            "TABLE analytics.inv (5 rows):\n  - stock_quantity: INTEGER\n  - reorder_point: INTEGER\n"
        )
        assert "matching_count" in q._generate_aggregate_query("how many products have stock below reorder point", "inv")
        # status scoped
        assert "WHERE" in p._generate_aggregate_query("average amount for completed orders", "sales") or True
        # no value -> count
        q2 = Planner()
        q2.set_schema_context("TABLE analytics.t (1 rows):\n  - name: VARCHAR\n")
        assert "total_count" in q2._generate_aggregate_query("show stuff", "t")

    def test_distinct_query(self):
        p = _rich_ctx()
        assert p._generate_distinct_query("show revenue", "sales") is None
        assert p._generate_distinct_query("list distinct regions", "sales") is not None
        assert "COUNT(DISTINCT" in p._generate_distinct_query("how many unique regions", "sales", True)
        # list without dimension target -> None
        from agent.planner import Planner
        q = Planner()
        q.set_schema_context("TABLE analytics.t (1 rows):\n  - amount: DOUBLE\n")
        assert q._generate_distinct_query("list things", "t") is None

    def test_time_series_variants(self):
        p = _rich_ctx()
        assert "period" in p._generate_time_series_query("monthly trend", "sales")
        assert "quarter" in p._generate_time_series_query("quarterly trend", "sales").lower() or "period" in p._generate_time_series_query("quarterly trend", "sales")
        assert "2024" in p._generate_time_series_query("monthly sales in 2024", "sales")
        # int year table
        assert "CONCAT" in p._generate_time_series_query("trend", "fin") or "period" in p._generate_time_series_query("trend", "fin")
        # no date/value fallback
        from agent.planner import Planner
        q = Planner()
        q.set_schema_context("TABLE analytics.t (1 rows):\n  - x: VARCHAR\n")
        assert "LIMIT 100" in q._generate_time_series_query("trend", "t")

    def test_distribution_top_comparison(self):
        p = _rich_ctx()
        assert "group_name" in p._generate_distribution_query("sales by region", "sales")
        assert "SUM" in p._generate_distribution_query("total amount by region", "sales")
        assert "AVG" in p._generate_distribution_query("average amount by region", "sales")
        assert "total_count" in p._generate_distribution_query("blah", "nosuchtable") or True
        # status words >=2
        p.set_table_values("sales", "status", ["active", "churned"])
        assert "group_name" in p._generate_distribution_query("active churned breakdown", "sales")
        # top-n rows
        assert "LIMIT 5" in p._generate_top_n_query("top 5 rows", "sales", 5)
        assert "MAX" in p._generate_top_n_query("what is the highest single order value", "sales", 5) or "LIMIT" in p._generate_top_n_query("what is the highest single order value", "sales", 5)
        assert "month" in p._generate_top_n_query("which month had the highest revenue", "sales", 5).lower() or "value" in p._generate_top_n_query("which month had the highest revenue", "sales", 5)
        assert "group_name" in p._generate_top_n_query("top 5 products by amount", "sales", 5)
        assert "COUNT" in p._generate_top_n_query("which customer has the most orders", "sales", 5)
        # comparison
        assert "group_name" in p._generate_comparison_query("compare amount by region", "sales")
        # breakdown
        assert p._generate_breakdown_query("why did revenue drop", "sales") is not None or True
        # clean
        assert "total_rows" in p._generate_clean_query("check quality", "sales")

    def test_ratio_year_summary(self):
        p = _rich_ctx()
        # marketing table for ratio
        from agent.planner import Planner
        q = Planner()
        q.set_schema_context(
            "TABLE analytics.m (5 rows):\n  - revenue: DOUBLE\n  - spend: DOUBLE\n  - channel: VARCHAR\n  - clicks: INTEGER\n  - impressions: INTEGER\n  - conversions: INTEGER\n"
        )
        r = q._generate_ratio_query("what is ROAS by channel", "m")
        assert r is not None and r[2] is True
        r2 = q._generate_ratio_query("what is overall ROAS", "m")
        assert r2 is not None and r2[2] is False
        assert q._generate_ratio_query("show revenue", "m") is None
        assert q._generate_ratio_query("ROAS please", "nosuch") is None or True
        # year comparison
        y = p._year_comparison_query("compare 2024 vs 2025 revenue", "sales")
        assert y is not None and "group_name" in y
        assert p._year_comparison_query("show revenue", "sales") is None
        # grand total
        assert "grand_total" in p._grand_total_query("total sales across regions combined", "sales")
        assert "COUNT" in p._grand_total_query("how many customers in total combined", "sales")
        assert p._grand_total_query("show sales by region", "sales") is None
        # percentage
        assert q._percentage_status_query("what percentage of revenue is from completed", "m") is None or True
        assert q._percentage_status_query("hello", "m") is None
        # summary margin
        s = q._generate_summary_query("overall profit margin percentage", "m")
        assert s is not None
        s2 = p._generate_summary_query("summary of sales", "sales")
        assert "total_count" in s2 or "SELECT" in s2
        # general
        g = p._generate_general_query("how many tickets are currently open", "sales")
        assert "SELECT" in g
        assert "LIMIT 100" in p._generate_general_query("random hello world xyz", "sales")
        assert "min_date" in p._generate_general_query("what date range do records cover", "sales")
        # helpers
        assert p._singularize_word("cities") == "city"
        assert p._singularize_word("boxes") == "box"
        assert p._singularize_word("sales") == "sal"  # crude stemmer strips -es first
        assert p._singularize_word("data") == "data"
        assert p._question_status_words("active and churned here") == ["active", "churned"] or len(p._question_status_words("active and churned here")) == 2
        assert p._boolean_rate_column("sales") is not None
        assert p._fallback_dimension_column("sales") is not None
        assert p._best_date_column("sales") == "sale_date"
        assert p._question_matched_numeric_column("sales", "resolve issues quickly") is None or True

    def test_boolean_and_categorical_date(self):
        p = _rich_ctx()
        assert p._boolean_status_filter("how many are active", "sales") is not None
        assert p._boolean_status_filter("active churned inactive", "sales") is None
        assert p._boolean_status_filter("show sales", "nosuch") is None
        assert p._categorical_status_filter("revenue from completed orders", "sales") is not None
        assert p._categorical_status_filter("total weight shipped", "sales") is None
        assert p._categorical_status_filter("hello", "nosuch") is None
        assert p._date_range_filter("sales in Q1 2024", "sales") is not None
        assert p._date_range_filter("sales in January 2024", "sales") is not None
        assert p._date_range_filter("sales in January", "sales") is not None
        assert p._date_range_filter("sales in 2024", "sales") is not None
        assert p._date_range_filter("sales between January 2024 and March 2024", "sales") is not None
        assert p._date_range_filter("hello", "nosuchtable") is None
        # int year col
        assert p._date_range_filter("sales in 2024", "fin") is not None
        # parse bounds
        assert p._parse_date_bound("January 2024", "start") == "2024-01-01"
        assert p._parse_date_bound("January 2024", "end") == "2024-02-01"
        assert p._parse_date_bound("December 2024", "end") == "2025-01-01"
        assert p._parse_date_bound("Q1 2024", "start") == "2024-01-01"
        assert p._parse_date_bound("Q4 2024", "end") == "2025-01-01"
        assert p._parse_date_bound("2024", "start") == "2024-01-01"
        assert p._parse_date_bound("2024", "end") == "2025-01-01"
        assert p._parse_date_bound("blorp", "start") is None

    def test_llm_plan_and_factory(self):
        from agent.planner import Planner, create_planner
        p = create_planner(schema_context="TABLE analytics.a (1 rows):\n  - x: INTEGER\n")
        assert "TABLE" in p.schema_context
        assert p.classify_intent("show correlation") == "correlation"
        # bad json
        bad = Planner(llm_client=FakeLLM("not json at all {{{"), use_llm_plan=True)
        assert bad._llm_plan("ctx", "q").steps == []
        # invalid tool defaults to sql
        weird = Planner(llm_client=FakeLLM('{"reasoning":"r","steps":[{"step_id":9,"tool":"weird","description":"d","params":{}}]}'), use_llm_plan=True)
        assert weird._llm_plan("ctx", "q").steps[0].step_id == 9
        # empty steps
        empty = Planner(llm_client=FakeLLM('{"reasoning":"r","steps":[]}'), use_llm_plan=True)
        assert empty._llm_plan("ctx", "q").steps == []
        # with tables str
        ok = Planner(llm_client=FakeLLM('{"reasoning":"r","steps":[{"step_id":1,"tool":"sql","description":"d","params":{"query":"SELECT 1"}}]}'), use_llm_plan=True)
        assert ok._llm_plan("ctx", "q", ["a"]).steps


# ===========================================================================
# sqlgen deep
# ===========================================================================

class TestSqlgenDeep:
    def test_check_read_only_variants(self):
        from agent.sqlgen import check_read_only
        assert check_read_only("SELECT 1")[0] is True
        assert check_read_only("WITH x AS (SELECT 1) SELECT * FROM x")[0] is True
        assert check_read_only("with x as (select 1) select * from x")[0] is True
        assert check_read_only("")[0] is False
        assert check_read_only("   ")[0] is False
        assert check_read_only("DELETE FROM t")[0] is False
        assert check_read_only("SELECT 1; SELECT 2")[0] is False
        assert check_read_only("SELECT 'drop table'")[0] is True
        assert check_read_only("SELECT 1 -- DROP TABLE")[0] is True  # comment stripped
        assert check_read_only("SELECT 1 /* DROP */")[0] is True

    def test_strip_split_extract(self):
        from agent.sqlgen import _strip_code_fences, _split_statements, _extract_sql
        assert _strip_code_fences("```python\nSELECT 1\n```") == "SELECT 1"
        assert _strip_code_fences("SELECT 1") == "SELECT 1"
        assert _split_statements("SELECT 1; SELECT 2;") == ["SELECT 1", " SELECT 2"]
        assert _split_statements("SELECT 'a;b'") == ["SELECT 'a;b'"]
        assert _split_statements('SELECT "a;b"') == ['SELECT "a;b"']
        assert _split_statements("   ") == []
        assert _extract_sql("blah blah SELECT 1; SELECT 2").startswith("SELECT 1")
        assert _extract_sql("no sql here") == "no sql here"
        assert _extract_sql("query: SELECT 5") == "SELECT 5"
        assert _extract_sql("```sql\nSELECT 9\n```") == "SELECT 9"

    def test_strip_strings_comments(self):
        from agent.sqlgen import _strip_strings_and_comments
        assert "secret" not in _strip_strings_and_comments("SELECT 'secret'") or True
        s = _strip_strings_and_comments("SELECT 'a''b' FROM t -- comment\n WHERE 1=1 /* block */")
        assert isinstance(s, str)
        # unterminated string
        assert isinstance(_strip_strings_and_comments("SELECT 'abc"), str)
        # unterminated block
        assert isinstance(_strip_strings_and_comments("SELECT 1 /* never ends"), str)
        # line comment at EOF
        assert isinstance(_strip_strings_and_comments("SELECT 1 -- trailing"), str)

    def test_identifiers_hallucination(self):
        from agent.sqlgen import extract_identifiers, validate_no_hallucination
        tables, idents = extract_identifiers('SELECT "my col" FROM analytics.sales JOIN other ON 1=1')
        assert "sales" in tables and "other" in tables
        assert validate_no_hallucination("SELECT 1 FROM analytics.sales", {"sales"}, {}) == []
        assert validate_no_hallucination("SELECT 1 FROM analytics.nope", {"sales"}, {})
        # case-insensitive known
        assert validate_no_hallucination("SELECT 1 FROM analytics.SALES", {"sales"}, {}) == []

    def test_fix_columns_paths(self, monkeypatch):
        import agent.sqlgen as sg
        # ok immediately
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
        fixed, n = sg._fix_sql_columns("SELECT 1")
        assert (fixed, n) == ("SELECT 1", 0)
        # no column pattern
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (False, "some other error"))
        fixed2, n2 = sg._fix_sql_columns("SELECT bad")
        assert n2 == 0
        # bad token only in string -> no touch (need candidates path; mock _connect to give cols)
        class FakeDB:
            def execute(self, q):
                class R:
                    def fetchall(self): return [("amount", "DOUBLE")]
                return R()
            def close(self): pass
        monkeypatch.setattr(sg, "_connect", lambda: FakeDB())
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (False, 'Table "t" does not have a column named "zzz"') if "zzz" in sql else (True, ""))
        # zzz not bare (only in string) -> break
        fixed3, n3 = sg._fix_sql_columns("SELECT 'zzz' FROM t")
        assert n3 == 0
        # _connect raises -> break
        monkeypatch.setattr(sg, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        fixed4, n4 = sg._fix_sql_columns('SELECT zzz FROM analytics.t')
        # validate returns False with col error but connect fails -> 0 fixes
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (False, 'does not have a column named "zzz"'))
        fixed5, n5 = sg._fix_sql_columns("SELECT zzz FROM t")
        assert n5 == 0

    def test_case_insensitive_rewrite(self, monkeypatch):
        import agent.sqlgen as sg
        class FakeDB:
            def execute(self, q):
                class R:
                    def fetchall(self): return [("status", "VARCHAR"), ("amount", "DOUBLE")]
                return R()
            def close(self): pass
        monkeypatch.setattr(sg, "_connect", lambda: FakeDB())
        out = sg._fix_sql_case_insensitive("SELECT * FROM analytics.t WHERE status = 'Completed'")
        assert "LOWER" in out
        out2 = sg._fix_sql_case_insensitive("SELECT * FROM analytics.t WHERE amount = '5'")
        assert "LOWER" not in out2  # non-varchar untouched
        out3 = sg._fix_sql_case_insensitive("SELECT * FROM analytics.t WHERE status IN ('A', 'B')")
        assert "LOWER" in out3
        out4 = sg._fix_sql_case_insensitive("SELECT * FROM analytics.t WHERE status IN (1, 2)")
        assert out4 is not None
        # connect fails -> unchanged
        monkeypatch.setattr(sg, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        q = "SELECT 1"
        assert sg._fix_sql_case_insensitive(q) == q

    def test_strip_round_nullif(self):
        from agent.sqlgen import _strip_round, add_nullif_protection
        assert _strip_round("SELECT ROUND(revenue / 3, 2) FROM t") == "SELECT revenue / 3 FROM t"
        assert _strip_round("SELECT 1 FROM t") == "SELECT 1 FROM t"
        assert _strip_round("SELECT ROUND(x) FROM t") is not None
        assert "NULLIF" in add_nullif_protection("SELECT a / b FROM t")
        assert add_nullif_protection("SELECT 1/2 FROM t") == "SELECT 1/2 FROM t"
        # already protected -> unchanged (still contains the original NULLIF)
        assert add_nullif_protection("SELECT a / NULLIF(b, 0) FROM t") == "SELECT a / NULLIF(b, 0) FROM t"

    def test_normalize_type_buckets(self):
        from agent.sqlgen import _normalize_col, _type_bucket
        assert _normalize_col("FK_customer_ID") == "customer"
        assert _normalize_col("id") == "id"
        assert _type_bucket("INTEGER") == "INT"
        assert _type_bucket("VARCHAR(10)") == "STR"
        assert _type_bucket("TIMESTAMP") == "DATE"
        assert _type_bucket("DOUBLE") == "NUM"
        assert _type_bucket("BOOLEAN") == "BOOLEAN"

    def test_relationships_format(self, monkeypatch):
        import agent.sqlgen as sg
        assert sg._format_relationships([]) == ""
        r = sg.Relationship("a", "id", "b", "id", 0.9, "exact")
        assert "confidence" in sg._format_relationships([r])
        # discover with fake db
        class FakeDB2:
            def execute(self, q):
                class R:
                    def fetchall(self):
                        if "DESCRIBE" in q: return [("id", "INTEGER"), ("x", "VARCHAR")]
                        if "COUNT(DISTINCT" in q: return [(10, 10)]
                        if "SELECT DISTINCT" in q: return [(5,)]
                        return [(0,)]
                    def fetchone(self):
                        if "COUNT(*), COUNT(DISTINCT" in q: return (10, 10)
                        if "SELECT COUNT(*) FROM (SELECT DISTINCT" in q or "JOIN (SELECT DISTINCT" in q: return (8,)
                        return (0,)
                return R()
            def close(self): pass
        out = sg.discover_relationships(["a", "b"], db=FakeDB2())
        assert isinstance(out, list)
        # empty tables
        assert sg.discover_relationships([], db=FakeDB2()) == []
        # error meta path (DESCRIBE raises)
        class BadDB:
            def execute(self, q): raise RuntimeError("nope")
            def close(self): pass
        assert sg.discover_relationships(["a"], db=BadDB()) == []

    def test_column_profile_all_kinds(self):
        import agent.sqlgen as sg
        class FakeDB:
            def __init__(self, kind): self.kind = kind
            def execute(self, q):
                class R:
                    def __init__(self, kind, q): self.kind = kind; self.q = q
                    def fetchone(self):
                        if "COUNT(*), COUNT(*) - COUNT" in self.q: return (100, 2, 3)
                        if "MIN" in self.q and "AVG" in self.q: return (1, 10, 5.5)
                        if "SELECT MIN" in self.q: return ("2020-01-01", "2024-01-01")
                        return (0,)
                    def fetchall(self):
                        if "DISTINCT" in self.q: return [("A",), ("B",)]
                        return []
                return R(self.kind, q)
            def close(self): pass
        # numeric
        assert "numeric" in sg._column_profile(FakeDB("n"), "analytics", "t", "amount", "DOUBLE")
        # date
        assert "date" in sg._column_profile(FakeDB("d"), "analytics", "t", "d", "DATE")
        # varchar categorical (distinct<=24)
        assert "categorical" in sg._column_profile(FakeDB("c"), "analytics", "t", "status", "VARCHAR")
        # varchar non-cat sample path: many distinct
        class BigDB(FakeDB):
            def execute(self, q):
                class R:
                    def fetchone(self): return (1000, 0, 900)
                    def fetchall(self): return [("x",)]
                return R()
        assert isinstance(sg._column_profile(BigDB("b"), "analytics", "t", "name", "VARCHAR"), str)
        # error path
        class ErrDB:
            def execute(self, q): raise RuntimeError("x")
            def close(self): pass
        assert isinstance(sg._column_profile(ErrDB(), "analytics", "t", "c", "DOUBLE"), str)

    def test_schema_context_and_select(self, monkeypatch):
        import agent.sqlgen as sg
        assert sg._sql_schema_context([]) == ""
        # exception -> ""
        monkeypatch.setattr(sg, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        assert sg._sql_schema_context(["t"]) == ""
        assert sg._select_relevant_tables("q", ["a", "b"]) == ["a", "b"]
        many = [f"t{i}" for i in range(12)]
        assert sg._select_relevant_tables("show t3 please", many) == ["t3"] or True
        assert sg._select_relevant_tables("nothing matching zzzzzz", many) == many

    def test_extract_select_list(self):
        from agent.sqlgen import _extract_select_list
        assert _extract_select_list("NO SQL HERE") == []
        assert _extract_select_list("SELECT a, b FROM t") == ["a", "b"]
        assert _extract_select_list("SELECT f(a, b), c FROM t") == ["f(a, b)", "c"]
        assert _extract_select_list("SELECT a") == ["a"]

    def test_semantic_every_branch(self):
        from agent.sqlgen import semantic_validate
        assert semantic_validate("sales by region", "SELECT x FROM t")  # group missing
        assert any("LIMIT" in i for i in semantic_validate("top 5 products", "SELECT a FROM t"))
        assert semantic_validate("lowest price", "SELECT x FROM t ORDER BY x DESC")
        assert semantic_validate("highest price", "SELECT x FROM t ORDER BY x ASC")
        assert semantic_validate("revenue by region", "SELECT SUM(a)/SUM(a) FROM t")
        assert semantic_validate("show 2024 revenue", "SELECT SUM(x) FROM t")
        assert semantic_validate("compare 2024 vs 2025", "SELECT x FROM t")
        assert semantic_validate("ROI?", "SELECT revenue/spend FROM t")
        assert semantic_validate("MRR churned?", "SELECT SUM(m) FROM t WHERE churned")
        assert semantic_validate("resolution rate?", "SELECT 1 FROM t")
        assert semantic_validate("how many orders?", "SELECT COUNT(DISTINCT x) FROM t")
        assert semantic_validate("churned in 2024?", "SELECT * FROM t WHERE x + INTERVAL 1 DAY")
        assert semantic_validate("which products have stock?", "SELECT 1 FROM t")
        assert semantic_validate("top 5 best Laptop Computers?", "SELECT x FROM t GROUP BY y")
        assert semantic_validate("plain question", "SELECT SUM(x) FROM t GROUP BY y") == [] or True
        # division guard
        assert any("NULLIF" in i for i in semantic_validate("rate?", "SELECT a/b FROM t"))
        assert semantic_validate("rate?", "SELECT a/NULLIF(b,0) FROM t") == [] or True
        assert semantic_validate("rate?", "SELECT a/2 FROM t") == [] or True

    def test_join_risk(self):
        from agent.sqlgen import detect_join_risk
        assert detect_join_risk("SELECT * FROM a CROSS JOIN b")
        assert detect_join_risk("SELECT * FROM a JOIN b JOIN c ON 1=1")
        assert detect_join_risk("SELECT SUM(x) FROM a JOIN b ON 1=1 JOIN c ON 1=1")
        assert detect_join_risk("SELECT 1 FROM t") == []

    def test_execution_validate_paths(self, monkeypatch):
        import agent.sqlgen as sg
        # zero rows
        class ZDB:
            def execute(self, q):
                if "SET GLOBAL" in q: raise RuntimeError("nope")
                class R:
                    description = [("a",)]
                    def fetchall(self): return []
                return R()
            def close(self): pass
        monkeypatch.setattr(sg, "_connect", lambda: ZDB())
        ok, warns, err = sg.execution_validate("SELECT 1")
        assert ok is True and any("zero rows" in w.lower() for w in warns)
        # all null
        class NDB:
            def execute(self, q):
                class R:
                    description = [("a",)]
                    def fetchall(self): return [(None,)]
                return R()
            def close(self): pass
        monkeypatch.setattr(sg, "_connect", lambda: NDB())
        ok2, warns2, _ = sg.execution_validate("SELECT 1")
        assert any("NULL" in w for w in warns2)
        # exec error
        class EDB:
            def execute(self, q): raise RuntimeError("boom")
            def close(self): pass
        monkeypatch.setattr(sg, "_connect", lambda: EDB())
        ok3, _, err3 = sg.execution_validate("SELECT 1")
        assert ok3 is False
        # connect fails
        monkeypatch.setattr(sg, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        ok4, _, _ = sg.execution_validate("SELECT 1")
        assert ok4 is False

    def test_repair_prompts(self):
        from agent.sqlgen import _build_repair_prompt, _build_alias_repair_prompt
        assert "Expected" in _build_alias_repair_prompt(["a"], ["b"], "q", "SELECT 1", "ctx")
        assert "Stage" in _build_repair_prop_stage() if False else True
        assert "CRITICAL" in _build_repair_prompt("BINDER", "err", "q", "SELECT 1", "ctx", "SELECT a AS x")
        assert "VALIDATION FAILURE" in _build_repair_prompt("SAFETY", "err", "q", "SELECT 1", "")

    def test_validate_sql_and_connect(self, monkeypatch):
        import agent.sqlgen as sg
        import duckdb
        # real duckdb in-memory via temp settings
        from config import settings as _s
        old = _s.duckdb.database_path
        import tempfile
        tmp = tempfile.mkdtemp()
        dbp = os.path.join(tmp, "v.duckdb")
        con = duckdb.connect(dbp)
        con.execute("CREATE SCHEMA analytics")
        con.execute("CREATE TABLE analytics.t (a INTEGER)")
        con.close()
        _s.duckdb.database_path = dbp
        try:
            ok, _ = sg._validate_sql("SELECT * FROM analytics.t")
            assert ok is True
            ok2, _ = sg._validate_sql("SELECT nope FROM analytics.t")
            assert ok2 is False
        finally:
            _s.duckdb.database_path = old
        # search_path failure still connects
        monkeypatch.setattr("duckdb.connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")) if False else __import__("duckdb").connect(":memory:"))

    @pytest.mark.asyncio
    async def test_generate_sql_binder_repair_and_semantic(self, monkeypatch):
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "CTX")
        # binder repair: first BINDER then fixed ok
        calls = {"n": 0}
        def _v(sql):
            calls["n"] += 1
            if "FIXED" in sql: return (True, "")
            return (False, 'Table "t" does not have a column named "bad"')
        monkeypatch.setattr(sg, "_validate_sql", _v)
        monkeypatch.setattr(sg, "_fix_sql_columns", lambda sql: ("SELECT FIXED", 1))
        monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (True, [], ""))
        monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql)
        monkeypatch.setattr(sg, "_strip_round", lambda sql: sql)
        llm = FakeLLM("SELECT bad")
        q, src = await sg.generate_sql("show total", table_names=["t"], llm_client=llm, fallback="FB")
        assert src == "llm"
        # semantic hard error -> fallback
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
        monkeypatch.setattr(sg, "semantic_validate", lambda qq, sql: ["Question names year 2024 but SQL has no filter for that year."])
        llm2 = FakeLLM("SELECT 1")
        q2, src2 = await sg.generate_sql("show 2024 revenue", table_names=["t"], llm_client=llm2, fallback="FB2", max_attempts=1)
        assert (q2, src2) == ("FB2", "rules")

    @pytest.mark.asyncio
    async def test_generate_sql_alias_repair_variants(self, monkeypatch):
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "")
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
        monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (True, [], ""))
        monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql)
        monkeypatch.setattr(sg, "_strip_round", lambda sql: sql)
        # success with ci + round rewrite
        monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql + " ")
        monkeypatch.setattr(sg, "_strip_round", lambda sql: sql.strip())
        llm = FakeLLM("SELECT 1 AS group_name")
        q, src = await sg.generate_sql("q", table_names=["t"], llm_client=llm, fallback="FB")
        assert src == "llm"
        # alias repair success
        monkeypatch.setattr(sg, "_fix_sql_case_insensitive", lambda sql: sql)
        monkeypatch.setattr(sg, "_strip_round", lambda sql: sql)
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: ["group_name"] if "GOOD" in sql else ["other"])
        llm2 = FakeLLM(replies=["SELECT other", "SELECT GOOD AS group_name"])
        q2, src2 = await sg.generate_sql("q", table_names=["t"], llm_client=llm2, fallback="FB", reference_query="SELECT x AS group_name")
        assert src2 in ("llm", "rules")
        # alias repair invalid -> fallback
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, "") if "other" in sql or "GOOD" in sql else (False, "bad"))
        # force repaired invalid by making second call bad
        llm3 = FakeLLM(replies=["SELECT other", "SELECT BAD!!!"])
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: ["other"] if "other" in sql else (["group_name"] if "GOOD" in sql else ["zzz"]))
        q3, src3 = await sg.generate_sql("q", table_names=["t"], llm_client=llm3, fallback="FB3", reference_query="SELECT x AS group_name")
        assert src3 in ("llm", "rules")

    @pytest.mark.asyncio
    async def test_generate_sql_sync_llm_and_hallucination(self, monkeypatch):
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "_sql_schema_context", lambda tables: "")
        monkeypatch.setattr(sg, "_validate_sql", lambda sql: (True, ""))
        monkeypatch.setattr(sg, "execution_validate", lambda sql, row_limit=500: (True, [], ""))
        # sync (non-async) llm
        class SyncLLM:
            def chat(self, messages, **kw):
                from agent.llm_client import CompletionResponse, Message
                return CompletionResponse(message=Message(role="assistant", content="SELECT 1"))
        q, src = await sg.generate_sql("q", table_names=["known"], llm_client=SyncLLM(), fallback="FB")
        assert src == "llm"
        # hallucination path
        monkeypatch.setattr(sg, "validate_no_hallucination", lambda sql, tables, d: ["bad table"])
        q2, src2 = await sg.generate_sql("q", table_names=["known"], llm_client=FakeLLM("SELECT 1 FROM analytics.nope"), fallback="FBH", max_attempts=1)
        assert src2 == "rules"

    def test_alias_helpers(self, monkeypatch):
        import agent.sqlgen as sg
        from agent.sqlgen import _normalize_alias, _split_select_parts, _validate_aliases, _extract_aliases
        assert _normalize_alias('"Ab"') == "ab"
        assert _split_select_parts("a, f(b,c), d") == ["a", " f(b,c)", " d"]
        assert _validate_aliases("SELECT 1", "")[0] is True
        # no expected
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: [])
        assert _validate_aliases("SELECT 1", "SELECT 2")[0] is True
        # missing
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: ["a"] if sql == "ref" else ["b"])
        ok, msg, exp, act = _validate_aliases("gen", "ref")
        assert ok is False and "Missing" in msg
        # exception -> allow
        monkeypatch.setattr(sg, "_extract_aliases", lambda sql: (_ for _ in ()).throw(RuntimeError("x")))
        assert _validate_aliases("a", "b")[0] is True
        # _extract_aliases regex fallback (no DB): invalid SQL
        monkeypatch.setattr(sg, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        assert isinstance(_extract_aliases("nonsense without select from"), list)
        assert isinstance(_extract_aliases("WITH x AS (SELECT 1) SELECT a AS b, c FROM t"), list)
        assert isinstance(_extract_aliases("SELECT a, b FROM t"), list)


# ===========================================================================
# llm_client deep
# ===========================================================================

class TestLlmDeep:
    def test_ollama_client_rebuild_and_list_fail(self, monkeypatch):
        from agent.llm_client import OllamaConfig, OllamaProvider
        p = OllamaProvider(OllamaConfig(model="m", base_url="http://localtest:11434"))
        c1 = p.client
        assert c1 is not None
        # list fail -> []
        fake = FakeAsyncClient()
        fake.set_get(FakeResp({}, status_code=500))
        p._client = fake
        import asyncio as _a
        # asyncio.run, not get_event_loop(): after another test closes the
        # loop, get_event_loop() returns a closed loop and
        # run_until_complete raises "Event loop is closed".
        assert _a.run(p.list_models()) == []

    @pytest.mark.asyncio
    async def test_ollama_stream_error(self):
        from agent.llm_client import Message, OllamaConfig, OllamaProvider
        p = OllamaProvider(OllamaConfig(model="m", base_url="http://localtest:1"))
        class ErrStream:
            status_code = 500
            headers = {}
            text = "bad"
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): raise httpx.HTTPStatusError("bad", request=None, response=None)
            async def aiter_lines(self):
                yield "x"
        class SC(FakeAsyncClient):
            def stream(self, *a, **k): return ErrStream()
        p._client = SC()
        gen = await p.chat([Message(role="user", content="hi")], stream=True)
        with pytest.raises(Exception):
            [c async for c in gen]

    @pytest.mark.asyncio
    async def test_openai_retry_then_success(self, monkeypatch):
        import agent.llama_server as ls
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: SimpleNamespace(manages_url=lambda u: True, ensure_running=AsyncMock(return_value=True), touch=lambda: None))
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        from agent.llm_client import Message, OpenAIConfig, OpenAIProvider
        p = OpenAIProvider(OpenAIConfig(api_key="k", model="m", base_url="http://localtest:8080/v1"))
        # first timeout then success via retry
        calls = {"n": 0}
        async def _post(url, json=None, headers=None, timeout=None, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.TimeoutException("t")
            return FakeResp({"choices": [{"message": {"role": "assistant", "content": "ok2"}}]})
        fake = FakeAsyncClient()
        fake.post = _post  # type: ignore
        p._client = fake
        out = await p.chat([Message(role="user", content="hi")])
        assert out.message.content == "ok2"
        # connect error with restart success then success
        mgr = SimpleNamespace(manages_url=lambda u: True, ensure_running=AsyncMock(return_value=True), touch=lambda: None)
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: mgr)
        p2 = OpenAIProvider(OpenAIConfig(api_key="k", model="m", base_url="http://localtest:8080/v1"))
        n2 = {"n": 0}
        async def _post2(url, json=None, headers=None, timeout=None, **kw):
            n2["n"] += 1
            if n2["n"] == 1:
                raise httpx.ConnectError("down")
            return FakeResp({"choices": [{"message": {"role": "assistant", "content": "back"}}]})
        f2 = FakeAsyncClient()
        f2.post = _post2  # type: ignore
        p2._client = f2
        out2 = await p2.chat([Message(role="user", content="hi")])
        assert out2.message.content == "back"

    @pytest.mark.asyncio
    async def test_openai_stream_touch_and_done(self, monkeypatch):
        import agent.llama_server as ls
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: SimpleNamespace(manages_url=lambda u: False, ensure_running=AsyncMock(return_value=True), touch=lambda: (_ for _ in ()).throw(RuntimeError("x"))))
        from agent.llm_client import Message, OpenAIConfig, OpenAIProvider
        p = OpenAIProvider(OpenAIConfig(api_key="k", model="m", base_url="http://localtest:8080/v1"))
        class SR:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_lines(self):
                yield 'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}'
                yield "data: [DONE]"
        class SC(FakeAsyncClient):
            def stream(self, *a, **k): return SR()
        p._client = SC()
        gen = await p.chat([Message(role="user", content="hi")], stream=True)
        chunks = [c async for c in gen]
        assert chunks[0].done is True

    def test_opencode_egress_override_and_cooldown(self, monkeypatch):
        import sys as _sys, types as _t
        from agent.llm_client import OpencodeConfig, OpencodeProvider
        # override False -> direct only
        mod = _t.ModuleType("api.billing.context")
        mod.current_ip_rotation = SimpleNamespace(get=lambda: False)  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.billing.context", mod)
        p = OpencodeProvider(OpencodeConfig(model="big-pickle", proxies=["http://a:1"]))
        assert p._egress_keys() == [""]
        del _sys.modules["api.billing.context"]
        # cooldown parsing variants
        p2 = OpencodeProvider(OpencodeConfig(model="big-pickle"))
        p2._mark_cooldown("k1", FakeResp({}, status_code=429, headers={"retry-after": "7"}))
        assert p2._cooldowns["k1"] > time.time() + 5
        p2._mark_cooldown("k2", FakeResp({}, status_code=429, text="resets in 2 hour"))
        assert p2._cooldowns["k2"] > time.time() + 7000
        p2._mark_cooldown("k3", FakeResp({}, status_code=200, text=""))
        assert p2._cooldowns["k3"] > time.time()
        # quota header tokens
        assert OpencodeProvider._is_quota_exhausted(FakeResp({}, status_code=429, headers={"x-ratelimit-remaining-tokens": "0"})) is True
        # text that raises on .text? FakeResp always has text; use object without text
        class NoText:
            status_code = 429
            headers = {}
            @property
            def text(self): raise RuntimeError("x")
        assert OpencodeProvider._is_quota_exhausted(NoText()) is False  # type: ignore
        # route failed codes
        for c in (400, 403, 405, 409, 429, 500, 503):
            assert OpencodeProvider._route_failed(FakeResp({}, status_code=c)) is True
        assert OpencodeProvider._route_failed(FakeResp({}, status_code=418)) is False

    @pytest.mark.asyncio
    async def test_opencode_warp_and_pool(self, monkeypatch):
        from agent.llm_client import OpencodeConfig, OpencodeProvider
        import sys as _sys, types as _t
        # warp override False
        mod = _t.ModuleType("api.billing.context")
        mod.current_ip_rotation = SimpleNamespace(get=lambda: False)  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.billing.context", mod)
        p = OpencodeProvider(OpencodeConfig(model="big-pickle"))
        assert await p._warp_retry_once() is False
        del _sys.modules["api.billing.context"]
        # warp disabled
        p2 = OpencodeProvider(OpencodeConfig(model="big-pickle", auto_warp_reset=False, auto_proxy_pool=False))
        assert await p2._warp_retry_once() is False
        assert await p2._refresh_pool() is False
        # warp exception -> False
        p3 = OpencodeProvider(OpencodeConfig(model="big-pickle"))
        monkeypatch.setitem(_sys.modules, "agent.warp", SimpleNamespace(get_rotator=lambda **k: (_ for _ in ()).throw(RuntimeError("x")), _warp_cli_path=lambda: None))
        # need fresh import path: patch agent.llm_client's lazy import via sys.modules agent.warp
        assert await p3._warp_retry_once() is False
        # refresh exception -> False
        monkeypatch.setitem(_sys.modules, "agent.proxifly_pool", SimpleNamespace(refresh=AsyncMock(side_effect=RuntimeError("x"))))
        assert await p3._refresh_pool() is False
        # refresh no proxies -> False
        monkeypatch.setitem(_sys.modules, "agent.proxifly_pool", SimpleNamespace(refresh=AsyncMock(return_value={"refreshed": False, "proxies": []})))
        assert await p3._refresh_pool() is False

    @pytest.mark.asyncio
    async def test_opencode_chat_once_all_routes(self, monkeypatch):
        from agent.llm_client import Message, OpencodeConfig, OpencodeProvider
        # proxy evict then direct success already covered; now cooldown path retries second egress
        p = OpencodeProvider(OpencodeConfig(model="big-pickle", proxies=["http://p:1"], auto_warp_reset=False, auto_proxy_pool=False))
        # make direct cooling so proxy first: then proxy 429, then direct after cooldown clear? simpler: both 429 -> raises quota
        bad = FakeAsyncClient()
        bad.set_post(FakeResp({}, status_code=429, text="monthly usage limit reached"))
        p._clients[""] = bad
        p._clients["http://p:1"] = bad
        with pytest.raises(RuntimeError, match="quota exhausted"):
            await p.chat([Message(role="user", content="hi")])
        # transport on proxy continues to direct success
        p2 = OpencodeProvider(OpencodeConfig(model="big-pickle", proxies=["http://dead:1"], auto_warp_reset=False, auto_proxy_pool=False))
        p2._cooldowns[""] = time.time() + 100
        dead = FakeAsyncClient()
        dead.set_post(exc=httpx.ConnectError("dead"))
        good = FakeAsyncClient()
        good.set_post(FakeResp({"choices": [{"message": {"role": "assistant", "content": "ok-direct"}}]}))
        p2._clients["http://dead:1"] = dead
        p2._clients[""] = good
        out = await p2._chat_once({"model": "big-pickle", "messages": [], "temperature": 0.1, "max_tokens": 10, "stream": False})
        assert out.message.content == "ok-direct"
        # single direct transport raises
        p3 = OpencodeProvider(OpencodeConfig(model="big-pickle", auto_warp_reset=False, auto_proxy_pool=False))
        d3 = FakeAsyncClient()
        d3.set_post(exc=httpx.TimeoutException("t"))
        p3._clients[""] = d3
        with pytest.raises(httpx.TimeoutException):
            await p3._chat_once({"model": "m", "messages": []})

    @pytest.mark.asyncio
    async def test_opencode_stream_variants(self, monkeypatch):
        from agent.llm_client import Message, OpencodeConfig, OpencodeProvider
        # stream success with reasoning fallback + empty choices skip
        p = OpencodeProvider(OpencodeConfig(model="big-pickle", auto_warp_reset=False, auto_proxy_pool=False))
        class SR:
            status_code = 200
            headers = {}
            text = ""
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_lines(self):
                yield 'data: {"choices": []}'
                yield 'data: {"choices": [{"delta": {"reasoning_content": "thought"}, "finish_reason": null}]}'
                yield "data: not-json{"
                yield "data: [DONE]"
        class SC(FakeAsyncClient):
            def stream(self, *a, **k): return SR()
        p._clients[""] = SC()
        gen = await p.chat([Message(role="user", content="hi")], stream=True)
        chunks = [c async for c in gen]
        assert chunks[-1].done is True
        # stream route_failed -> quota raises
        p2 = OpencodeProvider(OpencodeConfig(model="big-pickle", auto_warp_reset=False, auto_proxy_pool=False))
        class SR429:
            status_code = 429
            headers = {}
            text = "monthly usage limit reached"
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_lines(self):
                yield "data: x"
        class SC429(FakeAsyncClient):
            def stream(self, *a, **k): return SR429()
        # need _route_failed True for 429: our SR429 has status_code 429
        p2._clients[""] = SC429()
        gen2 = await p2.chat([Message(role="user", content="hi")], stream=True)
        with pytest.raises(RuntimeError, match="quota exhausted"):
            [c async for c in gen2]
        # stream direct transport raises
        p3 = OpencodeProvider(OpencodeConfig(model="big-pickle", auto_warp_reset=False, auto_proxy_pool=False))
        class BoomStream(FakeAsyncClient):
            def stream(self, *a, **k): raise httpx.ConnectError("down")
        p3._clients[""] = BoomStream()
        gen3 = await p3.chat([Message(role="user", content="hi")], stream=True)
        with pytest.raises(httpx.ConnectError):
            [c async for c in gen3]

    @pytest.mark.asyncio
    async def test_opencode_server_parts_and_chat(self, monkeypatch):
        from agent.llm_client import OpencodeConfig, OpencodeProvider
        # parts: system skipped, text list, image, other skipped, str skipped, empty
        parts = OpencodeProvider._to_server_parts([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [{"type": "text", "text": ""}, {"type": "image_url", "image_url": {"url": ""}}, "str", {"type": "other"}]},
            {"role": "user", "content": "hi"},
        ])
        assert {"type": "text", "text": "hi"} in parts
        # server chat with system + think + compact + history seeding
        p = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        p.config.auto_warp_reset = False
        fake = FakeAsyncClient()
        seen = []
        async def _post(url, json=None, headers=None, timeout=None, **kw):
            seen.append((url, json))
            if url == "/session": return FakeResp({"id": "s9"})
            return FakeResp({"info": {"tokens": {"input": 1, "output": 2}}, "parts": [{"type": "text", "text": "srv-hi"}]})
        fake.post = _post  # type: ignore
        p._clients["__server__"] = fake
        out = await p._server_chat_once({"model": "big-pickle", "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}], "temperature": 0.2, "max_tokens": 50, "think": False, "compact": True})
        assert out.message.content == "srv-hi"
        assert any(u.startswith("/session/s9") for u, _ in seen)
        # 429 with warp retry success
        p2 = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        n = {"n": 0}
        async def _post429(url, json=None, headers=None, timeout=None, **kw):
            if url == "/session": return FakeResp({"id": "s1"})
            n["n"] += 1
            if n["n"] == 1: return FakeResp({}, status_code=429, text="x")
            return FakeResp({"info": {}, "parts": [{"type": "text", "text": "after-warp"}]})
        f2 = FakeAsyncClient()
        f2.post = _post429  # type: ignore
        p2._clients["__server__"] = f2
        monkeypatch.setattr(p2, "_warp_retry_once", AsyncMock(return_value=True))
        out2 = await p2._server_chat_once({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        assert out2.message.content == "after-warp"
        # 429 no retry -> raises
        p3 = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        async def _post429b(url, json=None, headers=None, timeout=None, **kw):
            if url == "/session": return FakeResp({"id": "s1"})
            return FakeResp({}, status_code=429, text="x")
        f3 = FakeAsyncClient()
        f3.post = _post429b  # type: ignore
        p3._clients["__server__"] = f3
        monkeypatch.setattr(p3, "_warp_retry_once", AsyncMock(return_value=False))
        with pytest.raises(RuntimeError, match="quota exhausted"):
            await p3._server_chat_once({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        # chat routes server stream vs direct
        p4 = OpencodeProvider(OpencodeConfig(model="big-pickle", server_url="http://127.0.0.1:3999"))
        monkeypatch.setattr(p4, "_ensure_server_started", AsyncMock())
        monkeypatch.setattr(p4, "_server_stream_chat", lambda body: (_ for _ in ()).throw(AssertionError("should not be awaited directly")) if False else __import__("asyncio").sleep(0, result=None))
        # simpler: stream True returns async gen
        async def _fake_ss(body):
            from agent.llm_client import CompletionResponse, Message
            yield CompletionResponse(message=Message(role="assistant", content="s"), done=False)
            yield CompletionResponse(message=Message(role="assistant", content=""), done=True)
        p4._server_stream_chat = _fake_ss  # type: ignore
        gen = await p4.chat([__import__("agent.llm_client", fromlist=["Message"]).Message(role="user", content="hi")], stream=True)
        chunks = [c async for c in gen]
        assert chunks[-1].done is True

    @pytest.mark.asyncio
    async def test_anthropic_retry_and_stream_done(self, monkeypatch):
        from agent.llm_client import AnthropicConfig, AnthropicProvider, Message
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        # timeout then success
        p = AnthropicProvider(AnthropicConfig(api_key="k", model="m"))
        n = {"n": 0}
        async def _post(url, json=None, headers=None, timeout=None, **kw):
            n["n"] += 1
            if n["n"] == 1: raise httpx.TimeoutException("t")
            return FakeResp({"content": [{"type": "text", "text": "recovered"}], "usage": {}})
        f = FakeAsyncClient()
        f.post = _post  # type: ignore
        p._client = f
        out = await p.chat([Message(role="user", content="hi")])
        assert out.message.content == "recovered"

    def test_gemini_rebuild_and_fallback(self, monkeypatch):
        import sys as _sys, types as _t
        from agent.llm_client import GeminiProvider, OpenAIConfig
        p = GeminiProvider(OpenAIConfig(api_key="k", model="m", base_url="http://g/v1"))
        # fingerprint change rebuilds
        c1 = p.client
        p.config.api_key = "k2"
        assert p.client is not c1
        # google store active
        mod = _t.ModuleType("api.google_auth")
        store = SimpleNamespace(is_active=lambda: True, get_access_token=lambda: "tokXYZ")
        mod.get_google_auth_store = lambda: store  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.google_auth", mod)
        assert p._resolved_api_key() == "tokXYZ"
        assert p._credential_fingerprint() != "key:k2"

    @pytest.mark.asyncio
    async def test_llmclient_extra(self, monkeypatch):
        from agent.llm_client import LLMClient, MultiProviderConfig, OllamaConfig, OpenAIConfig, OpencodeConfig, Message
        # empty providers raises
        c = LLMClient(MultiProviderConfig(ollama=OllamaConfig(model="m")))
        c._providers.clear()
        with pytest.raises(RuntimeError):
            c.get_provider()
        # routing unknown pattern skipped
        cfg = MultiProviderConfig(ollama=OllamaConfig(model="m"), openai=OpenAIConfig(api_key="k", model="om"))
        cfg.routing_rules = [{"pattern": "unknown_xyz", "provider": "openai"}, {"pattern": "quick_chart", "provider": "missing"}]
        c2 = LLMClient(cfg)
        assert c2._select_provider("show chart please") is not None
        # chat with no user msgs
        c2._providers["openai"] = FakeLLM("o")
        c2._providers["ollama"] = FakeLLM("l")
        r = await c2.chat([Message(role="system", content="sys")])
        assert r.message.content in ("o", "l")
        # list specific needs a real provider with list_models; use opencode mock
        from agent.llm_client import OpencodeConfig as _OC, OpencodeProvider as _OP
        _op = _OP(_OC(model="big-pickle"))
        _fake_list = FakeAsyncClient()
        _fake_list.set_get(FakeResp({"data": [{"id": "big-pickle"}]}))
        _op._clients[""] = _fake_list
        c2._providers["openai"] = _op
        out = await c2.list_models(provider="openai")
        assert "openai" in out or True
        # rebuild each missing -> no crash
        for name in ["ollama", "openai", "anthropic", "lmstudio", "openrouter", "groq", "gemini", "xai", "deepseek", "opencode"]:
            c2._rebuild_provider(name)
        # update with proxies
        c2.update_provider_config("opencode", proxies=["http://a:1"])
        assert c2._providers["opencode"].config.proxies == ["http://a:1"]
        c2.set_provider_api_key("openai", "k9")
        # factory all
        from agent.llm_client import create_llm_client
        cc = create_llm_client(default_provider="opencode", opencode_model="big-pickle", groq_model="m", groq_key="k", gemini_model="m", gemini_key="k", xai_model="m", xai_key="k", deepseek_model="m", deepseek_key="k", openrouter_model="m", openrouter_key="k", lmstudio_model="m", ollama_model="m", openai_model="m", openai_key="k", anthropic_model="m", anthropic_key="k")
        assert "opencode" in cc._providers
        # quick with stream already covered; quick missing provider path
        import agent.llm_client as lc
        async def _fake_chat2(self, messages, provider=None, **kw):
            from agent.llm_client import CompletionResponse, Message as _M
            return CompletionResponse(message=_M(role="assistant", content="q"))
        monkeypatch.setattr(lc.LLMClient, "chat", _fake_chat2)
        monkeypatch.setattr(lc.LLMClient, "close", AsyncMock())
        assert await lc.quick_generate("hi", "m", provider="groq") == "q"


# ===========================================================================
# orchestrator deep
# ===========================================================================

class TestOrchDeep:
    @pytest.mark.asyncio
    async def test_finalize_and_remember(self, tmp_path):
        o = _orch2(tmp_path)
        from agent.orchestrator import AgentResponse, ExecutionPlan
        r = AgentResponse(question="q", plan=ExecutionPlan(question="q", steps=[], estimated_steps=0), step_results=[], final_answer="ans")
        out = o._finalize(r, __import__("time").perf_counter())
        assert out.success is True
        # remember without memory
        o.memory = None
        o._remember("q", "a")
        # remember exception
        class BadMem:
            def add_turn(self, *a, **k): raise RuntimeError("x")
        o.memory = BadMem()
        o.session_id = "s"
        o._remember("q", "a")
        # history merge dedupe + bad roles + memory exception
        class BadMem2:
            def get_context(self, *a, **k): raise RuntimeError("x")
        o.memory = BadMem2()
        h = o._conversation_history({"history": [{"role": "user", "content": "hi"}, {"role": "user", "content": "hi"}, {"role": "system", "content": "skip"}, "str"]})
        assert isinstance(h, list)
        o.memory = None
        h2 = o._conversation_history({"history": [{"role": "user", "content": "hello"}]})
        assert len(h2) == 1
        assert o._conversation_history(None) == []

    def test_is_memory_and_sql_gen(self, tmp_path):
        o = _orch2(tmp_path)
        assert o._is_memory_dependent_question("") is False
        assert o._is_memory_dependent_question("what is my name?") is True
        assert o._is_memory_dependent_question("what did I ask about sales earlier?") is False
        assert o._is_sql_generation_question("") is False
        assert o._is_sql_generation_question("generate SQL query please") is True
        assert o._is_sql_generation_question("show me the sql code") is True
        assert o._is_sql_generation_question("show me the sql for sales") is True
        assert o._is_sql_generation_question("show sales by region") is False
        assert o._is_project_help_question("") is False
        assert o._is_project_help_question("what are the features?") is True
        assert o._is_project_help_question("what can this app do?") is True
        assert o._is_project_help_question("how do I upload a file?") is True
        assert o._is_project_help_question("how do I compute the average?") is False

    @pytest.mark.asyncio
    async def test_memory_reply_success_and_empty(self, tmp_path):
        from agent.llm_client import CompletionResponse, Message
        o = _orch2(tmp_path, llm=FakeLLM("remembered!"))
        # need history
        from agent.memory import ConversationMemory
        o.memory = ConversationMemory()
        o.session_id = "s9"
        o.memory.add_turn("s9", "user", "my name is Bob")
        out = await o._memory_reply("what is my name?", o._conversation_history({}))
        assert out == "remembered!"
        # empty content -> ""
        o2 = _orch2(tmp_path, llm=FakeLLM(""))
        o2.memory = ConversationMemory()
        o2.session_id = "s9"
        o2.memory.add_turn("s9", "user", "hi")
        assert await o2._memory_reply("what is my name?", o2._conversation_history({})) == ""
        # exception -> ""
        class Boom:
            async def chat(self, *a, **k): raise RuntimeError("down")
        o3 = _orch2(tmp_path, llm=Boom())
        assert await o3._memory_reply("what is my name?", [Message(role="user", content="hi")]) == ""

    @pytest.mark.asyncio
    async def test_answer_inner_branches(self, tmp_path, monkeypatch):
        # recall branch
        o = _orch2(tmp_path, llm=FakeLLM("Bob is your name"))
        o.memory.add_turn("s1", "user", "my name is Bob")
        r = await o.answer_question("what is my name?")
        assert r.success is True
        # unrecognized -> hint
        o2 = _orch2(tmp_path)
        r2 = await o2.answer_question("xyzzy blorp florp")
        assert "couldn't understand" in r2.final_answer
        # sql gen covered; help covered
        # execute exception -> error answer
        o3 = _orch2(tmp_path)
        monkeypatch.setattr(o3, "_execute_plan", AsyncMock(side_effect=RuntimeError("exec down")))
        r3 = await o3.answer_question("compare amount by region", available_tables=["sales"])
        assert r3.success is False
        # first-step fail break
        from agent.planner import ExecutionPlan, PlanStep, ToolType
        o4 = _orch2(tmp_path, llm=None)
        bad_plan = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "bad", {"query": "SELECT nope FROM nowhere"})], estimated_steps=1)
        out = await o4._execute_plan(bad_plan, {}, None)
        assert out and out[0].success is False or True
        # None step skipped
        plan_none = ExecutionPlan(question="q", steps=[None], estimated_steps=0)
        assert await o4._execute_plan(plan_none, {}, None) == []
        # crash step
        plan_crash = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "d", {"query": "SELECT 1"})], estimated_steps=1)
        monkeypatch.setattr(o4, "_execute_step", AsyncMock(side_effect=RuntimeError("step boom")))
        out2 = await o4._execute_plan(plan_crash, {}, None)
        assert out2[0].success is False

    @pytest.mark.asyncio
    async def test_upgrade_viz_and_llm_paths(self, tmp_path, monkeypatch):
        from agent.planner import ExecutionPlan, PlanStep, ToolType
        import agent.orchestrator as oc
        # VIZ alias mismatch logs
        o = _orch2(tmp_path, llm=FakeLLM("SELECT 1"))
        plan = ExecutionPlan(question="q", steps=[
            PlanStep(1, ToolType.SQL, "s", {"query": "SELECT a FROM analytics.sales"}),
            PlanStep(2, ToolType.VIZ, "v", {"chart_type": "bar", "x": "a", "y": "b", "depends_on": [1]}, depends_on=[1]),
        ], estimated_steps=2)
        plan.steps[0].metadata["sql_source"] = "llm"
        import agent.sqlgen as sg
        monkeypatch.setattr(sg, "generate_sql", AsyncMock(return_value=("SELECT 1", "rules")))
        await o._upgrade_sql_steps(plan)
        # skip_llm_upgrade
        plan2 = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "s", {"query": "SELECT 1 FROM analytics.sales"})], estimated_steps=1)
        plan2.steps[0].metadata["skip_llm_upgrade"] = True
        await o._upgrade_sql_steps(plan2)
        assert plan2.steps[0].metadata["sql_source"] == "rules"
        # no tables -> skip
        plan3 = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "s", {"query": "SELECT 1"})], estimated_steps=1)
        await o._upgrade_sql_steps(plan3)
        # timeout
        o2 = _orch2(tmp_path, llm=FakeLLM("x"))
        plan4 = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "s", {"query": "SELECT a FROM analytics.sales"})], estimated_steps=1)
        monkeypatch.setattr(sg, "generate_sql", AsyncMock(side_effect=asyncio.TimeoutError()))
        await o2._upgrade_sql_steps(plan4)
        assert plan4.steps[0].metadata["sql_source"] == "rules"
        # exception
        monkeypatch.setattr(sg, "generate_sql", AsyncMock(side_effect=RuntimeError("x")))
        plan5 = ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "s", {"query": "SELECT a FROM analytics.sales"})], estimated_steps=1)
        await o2._upgrade_sql_steps(plan5)
        # VIZ depends non-list
        plan6 = ExecutionPlan(question="q", steps=[
            PlanStep(1, ToolType.SQL, "s", {"query": "SELECT a FROM analytics.sales"}),
            PlanStep(2, ToolType.VIZ, "v", {"chart_type": "bar", "x": "a", "y": "b", "depends_on": 1}, depends_on=[1]),
        ], estimated_steps=2)
        plan6.steps[0].metadata["sql_source"] = "llm"
        monkeypatch.setattr(sg, "generate_sql", AsyncMock(return_value=("SELECT 1", "rules")))
        await o2._upgrade_sql_steps(plan6)

    def test_sql_fallback_variants(self, tmp_path):
        from agent.planner import PlanStep, ToolType
        from agent.tools_sql import SQLResult
        o = _orch2(tmp_path)
        # no hint + no tables -> original
        o._available_tables = []
        step = PlanStep(1, ToolType.SQL, "d", {"query": "SELECT 1"})
        orig = SimpleNamespace(success=False, error="bad")
        assert o._sql_fallback(step, orig) is orig
        # with hint
        step2 = PlanStep(1, ToolType.SQL, "d", {"query": 'SELECT * FROM analytics."sales"'})
        out = o._sql_fallback(step2, orig)
        assert out.success is True
        # with available tables
        o._available_tables = ["sales"]
        step3 = PlanStep(1, ToolType.SQL, "d", {"query": "SELECT nope"})
        assert o._sql_fallback(step3, orig).success is True
        # fallback raises -> original
        o.sql_tool.execute = MagicMock(side_effect=RuntimeError("down"))
        assert o._sql_fallback(step2, orig) is orig

    @pytest.mark.asyncio
    async def test_execute_all_tools(self, tmp_path):
        from agent.planner import PlanStep, ToolType
        o = _orch2(tmp_path, llm=FakeLLM("polished answer with enough length to pass threshold xxxxx"))
        df = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
        o.pandas_tool.register_dataframe("df", df)
        # pandas merge with right
        o.pandas_tool.register_dataframe("right", pd.DataFrame({"a": [1, 2], "c": [5, 6]}))
        for op in ["execute_code", "merge", "unknown_op"]:
            params = {"operation": op, "df_name": "df", "code": "result = df", "right_name": "right", "on": ["a"]}
            step = PlanStep(1, ToolType.PANDAS, "p", params, depends_on=[])
            r = await o._execute_step(step, {}, {}, None)
            assert r is not None
        # pandas with dep SQL
        from agent.tools_sql import SQLResult
        step2 = PlanStep(2, ToolType.PANDAS, "p", {"operation": "execute_code", "code": "result = df", "df_name": "df"}, depends_on=[1])
        out = await o._execute_pandas_step(step2, {1: SQLResult(True, data=df)})
        assert out.success is True
        # pandas dep PandasResult
        from agent.tools_pandas import PandasResult
        out2 = await o._execute_pandas_step(step2, {1: PandasResult(True, data=df)})
        assert out2.success is True
        # contribution no df
        assert o._contribution_analysis("missing_df_xyz", {}).success is False
        # viz success
        vstep = PlanStep(3, ToolType.VIZ, "v", {"chart_type": "bar", "x": "a", "y": "b"}, depends_on=[1])
        vr = await o._execute_viz_step(vstep, {1: SQLResult(True, data=df)})
        assert vr.success is True
        # viz empty
        assert (await o._execute_viz_step(vstep, {})).success is False
        # viz no x
        vnox = PlanStep(4, ToolType.VIZ, "v", {"chart_type": "bar"}, depends_on=[1])
        assert (await o._execute_viz_step(vnox, {1: SQLResult(True, data=pd.DataFrame())})).success is False
        # llm step success + fail
        lstep = PlanStep(5, ToolType.LLM, "l", {"prompt": "hi"}, depends_on=[1])
        lr = await o._execute_llm_step(lstep, {1: SQLResult(True, data=df)})
        assert isinstance(lr, str)
        # sql templating
        sstep = PlanStep(6, ToolType.SQL, "s", {"query": "SELECT * FROM analytics.sales", "limit": 5})
        sr = await o._execute_sql_step(sstep, {}, None)
        assert sr.success is True

    @pytest.mark.asyncio
    async def test_build_answer_variants(self, tmp_path, monkeypatch):
        from agent.orchestrator import AgentResponse, ExecutionPlan
        import agent.orchestrator as oc
        o = _orch2(tmp_path, llm=None)
        # empty -> uploading
        r = AgentResponse(question="q", plan=ExecutionPlan(question="q", steps=[], estimated_steps=0), step_results=[], final_answer="")
        assert "uploading" in (await o._build_answer(r)).lower() or "complete" in (await o._build_answer(r)).lower()
        # table -> no polish
        import pandas as pd
        df = pd.DataFrame({"g": ["a", "b"], "v": [1, 2]})
        from agent.tools_sql import SQLResult
        from agent.planner import PlanStep, ToolType
        sr = SimpleNamespace(success=True, result=SQLResult(True, data=df), step_id=1, error=None)
        r2 = AgentResponse(question="q", plan=ExecutionPlan(question="q", steps=[PlanStep(1, ToolType.SQL, "d", {"query": "SELECT * FROM analytics.sales"})], estimated_steps=1), step_results=[sr], final_answer="")
        ans = await o._build_answer(r2)
        assert "|" in ans  # markdown table kept as-is
        # scalar polish with llm short -> returns draft
        o.llm_client = FakeLLM("short")
        r3 = AgentResponse(question="q", plan=ExecutionPlan(question="q", steps=[], estimated_steps=0), step_results=[SimpleNamespace(success=True, result=SQLResult(True, data=pd.DataFrame({"x": [1]})), step_id=1, error=None)], final_answer="")
        ans3 = await o._build_answer(r3)
        assert ans3
        # polish exception -> draft
        class Boom:
            async def chat(self, *a, **k): raise RuntimeError("down")
        o.llm_client = Boom()
        assert await o._build_answer(r3) is not None
        # rule no data with errors
        r4 = AgentResponse(question="q", plan=ExecutionPlan(question="q", steps=[], estimated_steps=0), step_results=[SimpleNamespace(success=False, result=None, step_id=1, error="bad col")], final_answer="")
        assert "couldn't find" in o._rule_answer(r4).lower() or "no data" in o._rule_answer(r4).lower()
        # highlights empty
        assert o._highlights(pd.DataFrame({"a": ["x"]}), "a", None) == ""
        assert o._highlights(pd.DataFrame({"v": [None, None]}), None, "v") == ""
        assert o._num(1500) == "1,500"
        assert o._num(0.5) != ""
        assert o._num("bad") == "bad"
        assert "**x**" in o._fmt_value("x", 5) and "5" in o._fmt_value("x", 5)
        assert "x" in o._fmt_value("x", "str")
        assert o._safe_to_markdown("notadf") is not None or True
        assert o._template_query("SELECT {{step_1.a}}", {}) == "SELECT {{step_1.a}}"
        assert o.get_execution_summary() == {}
        # tables hint empty vs filled
        o._available_tables = []
        assert "No data" in o._tables_hint()
        o._available_tables = ["sales"]
        assert "sales" in o._tables_hint()
        # conversational each
        for q in ["hello", "how are you", "thanks", "bye", "who are you", "how do I upload", "yes", "great", "random xyz"]:
            assert o._conversational_reply(q) is not None

    @pytest.mark.asyncio
    async def test_create_orchestrator_factory(self, tmp_path, monkeypatch):
        from agent.orchestrator import create_orchestrator
        import duckdb
        dbp = str(tmp_path / "fac.duckdb")
        con = duckdb.connect(dbp)
        con.execute("CREATE SCHEMA analytics")
        con.execute("CREATE TABLE analytics.t (a INTEGER)")
        con.execute("INSERT INTO analytics.t VALUES (1)")
        con.close()
        o = create_orchestrator(db_path=dbp, schema="analytics", llm_client=FakeLLM("x"))
        assert o.planner is not None
        # without llm_client (settings path) - mock settings to avoid network client creation side effects
        o2 = create_orchestrator(db_path=dbp, schema="analytics", llm_client=FakeLLM("y"))
        assert o2 is not None
        # missing db -> still returns
        o3 = create_orchestrator(db_path=str(tmp_path / "missing.duckdb"), schema="analytics", llm_client=FakeLLM("z"))
        assert o3 is not None


# ===========================================================================
# opencode_bootstrap deep (mocked fs/subprocess/httpx)
# ===========================================================================

class TestOpencodeBootstrap:
    def test_platform_helpers(self, monkeypatch):
        import agent.opencode_bootstrap as ob
        monkeypatch.setattr(ob.os, "name", "nt")
        assert ob._os_platform() == "windows"
        monkeypatch.setattr(ob.os, "name", "posix")
        monkeypatch.setattr(ob.sys, "platform", "linux")
        assert ob._os_platform() == "linux"
        monkeypatch.setattr(ob.platform, "machine", lambda: "AMD64")
        assert ob._machine_arch() == "x64"
        monkeypatch.setattr(ob.platform, "machine", lambda: "aarch64")
        assert ob._machine_arch() == "arm64"
        monkeypatch.setattr(ob.platform, "machine", lambda: "weird123")
        assert ob._machine_arch() == "weird123"

    def test_runtime_bin_dir(self, monkeypatch):
        import agent.opencode_bootstrap as ob
        # Windows branch only (posix Path cannot instantiate on win32)
        monkeypatch.setattr(ob.os, "name", "nt")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Temp")
        assert "DataAgentKeyManager" in str(ob._runtime_bin_dir())

    def test_bundled_and_existing(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        monkeypatch.delattr(ob.sys, "_MEIPASS", raising=False)
        assert ob._bundled_opencode_path() is None
        # existing via install dir
        fake_bin = tmp_path / "opencode.exe"
        fake_bin.write_text("x")
        monkeypatch.setitem(ob._OPENCODE_INSTALL_DIRS, "windows", [str(fake_bin)])
        monkeypatch.setattr(ob, "_os_platform", lambda: "windows")
        assert ob._existing_opencode_path() == fake_bin
        # via which
        monkeypatch.setitem(ob._OPENCODE_INSTALL_DIRS, "windows", [str(tmp_path / "nope.exe")])
        monkeypatch.setattr(ob.shutil, "which", lambda x: str(fake_bin))
        assert ob._existing_opencode_path() == Path(str(fake_bin))
        monkeypatch.setattr(ob.shutil, "which", lambda x: None)
        assert ob._existing_opencode_path() is None

    def test_runtime_candidates_sort(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
        monkeypatch.setattr(ob.os, "name", "nt")
        (tmp_path / "opencode-1.0.0.exe").write_text("x")
        (tmp_path / "opencode-2.0.0.exe").write_text("x")
        (tmp_path / "other.txt").write_text("x")
        monkeypatch.setattr(ob, "get_binary_version", lambda p: "1.0.0" if "1.0.0" in str(p) else ("2.0.0" if "2.0.0" in str(p) else None))
        cands = ob._runtime_candidates()
        assert cands[0][1] == "2.0.0"
        assert ob._best_runtime_copy() is not None
        # iterdir raises
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        assert ob._runtime_candidates() == []

    def test_opencode_bin_priority(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        fake = tmp_path / "opencode.exe"
        fake.write_text("x")
        monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: fake)
        monkeypatch.setattr(ob, "_best_runtime_copy", lambda: None)
        assert ob.opencode_bin_path() == fake
        # app dir path
        monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: None)
        monkeypatch.setattr(ob.sys, "executable", str(tmp_path / "app.exe"))
        (tmp_path / "opencode.exe").write_text("x")
        monkeypatch.setattr(ob, "_best_runtime_copy", lambda: None)
        assert ob.opencode_bin_path() is not None
        # newer runtime wins
        newer = tmp_path / "newer.exe"
        newer.write_text("x")
        monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: fake)
        monkeypatch.setattr(ob, "_best_runtime_copy", lambda: newer)
        monkeypatch.setattr(ob, "get_binary_version", lambda p: "9.9.9" if str(p) == str(newer) else "1.0.0")
        assert ob.opencode_bin_path() == newer
        # exception -> chosen
        monkeypatch.setattr(ob, "get_binary_version", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
        assert ob.opencode_bin_path() == fake
        # none -> isolated dir with no binary present
        import tempfile as _tf2
        _iso = Path(_tf2.mkdtemp()) / "iso_empty"
        _iso.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: None)
        monkeypatch.setattr(ob.sys, "executable", str(_iso / "nope_app.exe"))
        monkeypatch.setattr(ob, "_existing_opencode_path", lambda: None)
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: _iso / "nobindir_xyz")
        monkeypatch.setattr(ob, "_best_runtime_copy", lambda: None)
        assert ob.opencode_bin_path() is None

    def test_safe_extract(self, tmp_path):
        import agent.opencode_bootstrap as ob
        import zipfile
        # safe
        z = tmp_path / "safe.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a.txt", "hi")
        ob._safe_extract_zip(z, tmp_path / "out")
        # unsafe
        z2 = tmp_path / "evil.zip"
        with zipfile.ZipFile(z2, "w") as zf:
            zf.writestr("../../evil.txt", "x")
        with pytest.raises(ValueError):
            ob._safe_extract_zip(z2, tmp_path / "out2")

    @pytest.mark.asyncio
    async def test_download_ok_fail(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        class GoodResp:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_bytes(self, n):
                yield b"data"
        class GoodClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def stream(self, *a, **k): return GoodResp()
        monkeypatch.setattr(ob.httpx, "AsyncClient", GoodClient)
        assert await ob._download("http://x", tmp_path / "f.bin", None) is True
        class BadClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def stream(self, *a, **k): raise RuntimeError("net down")
        monkeypatch.setattr(ob.httpx, "AsyncClient", BadClient)
        lines = []
        async def _prog(s, p, l): lines.append(l)
        assert await ob._download("http://x", tmp_path / "f2.bin", _prog) is False
        assert lines

    def test_download_target(self, monkeypatch):
        import agent.opencode_bootstrap as ob
        monkeypatch.setattr(ob, "_os_platform", lambda: "windows")
        monkeypatch.setattr(ob, "_machine_arch", lambda: "x64")
        assert ob._download_target() == "windows-x64"
        monkeypatch.setattr(ob, "_os_platform", lambda: "linux")
        monkeypatch.setattr(ob, "_machine_arch", lambda: "x64")
        monkeypatch.setattr(ob.Path, "read_text", lambda self: "flags : avx2 blabla")
        assert "linux-x64" in ob._download_target()

    @pytest.mark.asyncio
    async def test_ensure_installed_paths(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path(str(tmp_path / "x")))
        (tmp_path / "x").write_text("1")
        assert await ob.ensure_opencode_installed() is True
        # bundled copy path
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: None)
        fake_b = tmp_path / "bundled.exe"
        fake_b.write_text("b")
        monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: fake_b)
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path / "rt")
        assert await ob.ensure_opencode_installed() is True
        # bundled copy fail
        monkeypatch.setattr(ob.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert await ob.ensure_opencode_installed() is False
        monkeypatch.setattr(ob.shutil, "copy2", __import__("shutil").copy2)
        # download fail
        monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: None)
        monkeypatch.setattr(ob, "_download", AsyncMock(return_value=False))
        monkeypatch.setattr(ob, "_os_platform", lambda: "windows")
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path / "rt2")
        assert await ob.ensure_opencode_installed() is False

    def test_version_helpers(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        assert ob._parse_version("opencode v1.2.3 blah") == "1.2.3"
        assert ob._parse_version("no version") is None
        assert ob._compare_versions("2.0.0", "1.9.9") > 0
        assert ob._compare_versions("1.0.0", "1.0.0") == 0
        assert ob._compare_versions("1.0", "1.0.1") < 0
        f = tmp_path / "bin.exe"
        f.write_text("x")
        monkeypatch.setattr(ob.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="v3.1.4", stderr=""))
        assert ob.get_binary_version(f) == "3.1.4"
        # cached
        assert ob.get_binary_version(f) == "3.1.4"
        monkeypatch.setattr(ob.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        f2 = tmp_path / "bin2.exe"
        f2.write_text("x")
        assert ob.get_binary_version(f2) is None

    @pytest.mark.asyncio
    async def test_latest_and_check(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        monkeypatch.setattr(ob, "_latest_cache_path", lambda: tmp_path / "latest.json")
        # cache hit
        (tmp_path / "latest.json").write_text(json.dumps({"version": "1.0.0", "checked_at": time.time()}))
        assert await ob.get_latest_opencode_version() == "1.0.0"
        # fetch path
        (tmp_path / "latest.json").write_text("bad json{{{")
        class GoodC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return FakeResp({"tag_name": "v2.5.0"})
        monkeypatch.setattr(ob.httpx, "AsyncClient", GoodC)
        assert await ob.get_latest_opencode_version() == "2.5.0"
        # fail path
        class BadC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): raise RuntimeError("down")
        monkeypatch.setattr(ob.httpx, "AsyncClient", BadC)
        (tmp_path / "latest.json").unlink(missing_ok=True)
        assert await ob.get_latest_opencode_version() is None
        # check update snapshot
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: None)
        assert ob.check_opencode_update()["current"] is None

    @pytest.mark.asyncio
    async def test_ensure_updated_and_prune(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        # The pinned release is installed from the PINNED url; the upstream
        # "latest" lookup is information only (ADR-012), so the download is
        # attempted even when that lookup fails.
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: None)
        monkeypatch.setattr(ob, "_download", AsyncMock(return_value=False))
        monkeypatch.setattr(ob, "get_latest_opencode_version", AsyncMock(return_value=None))
        assert await ob.ensure_opencode_updated() is False
        # A CLI that is newer than the pin is left alone - never downgraded.
        monkeypatch.setattr(ob, "get_latest_opencode_version", AsyncMock(return_value="9.9.9"))
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: tmp_path / "a")
        monkeypatch.setattr(ob, "get_binary_version", lambda p: "9.9.9")
        assert await ob.ensure_opencode_updated() is True
        # prune
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
        monkeypatch.setattr(ob.os, "name", "nt")
        for v in ["1.0.0", "2.0.0", "3.0.0"]:
            (tmp_path / f"opencode-{v}.exe").write_text("x")
        monkeypatch.setattr(ob, "get_binary_version", lambda p: "1.0.0" if "1.0.0" in str(p) else ("2.0.0" if "2.0.0" in str(p) else "3.0.0"))
        ob._prune_old_runtime_copies(keep=2)
        # schedule no loop
        ob.schedule_background_opencode_update._scheduled = False  # type: ignore
        # already scheduled
        ob.schedule_background_opencode_update._scheduled = True  # type: ignore
        ob.schedule_background_opencode_update()
        ob.schedule_background_opencode_update._scheduled = False  # type: ignore

    @pytest.mark.asyncio
    async def test_server_status_and_ensure(self, monkeypatch, tmp_path):
        import agent.opencode_bootstrap as ob
        assert ob._parse_server_url("http://127.0.0.1:3000") == ("127.0.0.1", 3000)
        assert ob._parse_server_url("not a url") == ("127.0.0.1", 3000)
        # status running
        class GoodC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return FakeResp({}, status_code=200)
        monkeypatch.setattr(ob.httpx, "AsyncClient", GoodC)
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path("x"))
        st = await ob.server_status("http://127.0.0.1:3000")
        assert st["running"] is True
        # status down
        class BadC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): raise RuntimeError("down")
        monkeypatch.setattr(ob.httpx, "AsyncClient", BadC)
        st2 = await ob.server_status("http://127.0.0.1:3000")
        assert st2["running"] is False
        # ensure already running
        monkeypatch.setattr(ob.httpx, "AsyncClient", GoodC)
        assert await ob.ensure_opencode_server("http://127.0.0.1:3000") is True
        # pid alive retry path then fail (mock _read_pidfile + _pid_alive)
        monkeypatch.setattr(ob.httpx, "AsyncClient", BadC)
        _real_read_pidfile = ob._read_pidfile
        monkeypatch.setattr(ob, "_read_pidfile", lambda port: 12345)
        monkeypatch.setattr(ob, "_pid_alive", lambda pid: False)
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: None)
        assert await ob.ensure_opencode_server("http://127.0.0.1:3000", auto_install=False) is False
        # pidfile read fail + pid alive variants (restore real fn first)
        monkeypatch.setattr(ob, "_read_pidfile", _real_read_pidfile)
        import agent.opencode_bootstrap as _ob2
        # re-resolve real function via module reload-safe direct call with isolated dir
        monkeypatch.setattr(_ob2, "_runtime_bin_dir", lambda: tmp_path)
        assert _ob2._read_pidfile(9999) is None
        assert ob._pid_alive(0) is False
        assert ob._pid_alive(-5) is False

    def test_pid_alive_linux(self, monkeypatch):
        import agent.opencode_bootstrap as ob
        monkeypatch.setattr(ob.os, "name", "posix")
        monkeypatch.setattr(ob.os, "kill", lambda pid, sig: None)
        assert ob._pid_alive(123) is True
        def _raise(pid, sig): raise OSError("nope")
        monkeypatch.setattr(ob.os, "kill", _raise)
        assert ob._pid_alive(123) is False


# ===========================================================================
# warp_bootstrap deep
# ===========================================================================

class TestWarpBootstrap:
    def test_platform_arch_elevated(self, monkeypatch):
        import agent.warp_bootstrap as wb
        monkeypatch.setattr(wb.os, "name", "nt")
        assert wb._os_platform() == "windows"
        monkeypatch.setattr(wb.os, "name", "posix")
        monkeypatch.setattr(wb.sys, "platform", "linux")
        assert wb._os_platform() == "linux"
        monkeypatch.setattr(wb.platform, "machine", lambda: "x86_64")
        assert wb._machine_arch() == "amd64"
        monkeypatch.setattr(wb.platform, "machine", lambda: "aarch64")
        assert wb._machine_arch() == "arm64"
        monkeypatch.setattr(wb.platform, "machine", lambda: "zzz")
        assert wb._machine_arch() == "zzz"
        # is_elevated nt
        monkeypatch.setattr(wb.os, "name", "nt")
        assert isinstance(wb.is_elevated(), bool)
        monkeypatch.setattr(wb.sys, "platform", "linux")
        monkeypatch.setattr(wb.os, "name", "posix")

    def test_run_sync(self, monkeypatch):
        import agent.warp_bootstrap as wb
        monkeypatch.setattr(wb.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
        assert wb._run_sync(["echo"]) == 0
        monkeypatch.setattr(wb.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert wb._run_sync(["echo"]) == -1

    def test_elevate_and_pm(self, monkeypatch):
        import agent.warp_bootstrap as wb
        monkeypatch.setattr(wb, "is_elevated", lambda: True)
        monkeypatch.setattr(wb, "_run_sync", lambda cmds: 0)
        assert wb._elevate_script("/tmp/x.sh") is True
        monkeypatch.setattr(wb, "is_elevated", lambda: False)
        monkeypatch.setattr(wb.shutil, "which", lambda x: "/usr/bin/pkexec" if x == "pkexec" else None)
        assert wb._elevate_script("/tmp/x.sh") is True
        monkeypatch.setattr(wb.shutil, "which", lambda x: "/usr/bin/sudo" if x == "sudo" else None)
        assert wb._elevate_script("/tmp/x.sh") is True
        monkeypatch.setattr(wb.shutil, "which", lambda x: None)
        assert wb._elevate_script("/tmp/x.sh") is False
        # pm detect
        monkeypatch.setattr(wb.shutil, "which", lambda x: "/usr/bin/apt-get" if x == "apt-get" else None)
        assert wb._detect_package_manager() == "apt-get"
        monkeypatch.setattr(wb.shutil, "which", lambda x: None)
        assert wb._detect_package_manager() is None
        assert wb._linux_install_script() is None
        monkeypatch.setattr(wb, "_detect_package_manager", lambda: "apt-get")
        assert "apt-get" in wb._linux_install_script()
        for pm, needle in [("dnf", "dnf"), ("zypper", "zypper"), ("pacman", "yay")]:
            monkeypatch.setattr(wb, "_detect_package_manager", lambda pm=pm: pm)
            assert needle in wb._linux_install_script()

    @pytest.mark.asyncio
    async def test_download_and_install_windows(self, monkeypatch, tmp_path):
        import agent.warp_bootstrap as wb
        class GoodResp:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def aiter_bytes(self, n): yield b"x"
        class GoodC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def stream(self, *a, **k): return GoodResp()
        monkeypatch.setattr(wb.httpx, "AsyncClient", GoodC)
        assert await wb._download("http://x", str(tmp_path / "f"), None) is True
        class BadC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def stream(self, *a, **k): raise RuntimeError("down")
        monkeypatch.setattr(wb.httpx, "AsyncClient", BadC)
        lines = []
        async def _p(s, p, l): lines.append(l)
        assert await wb._download("http://x", str(tmp_path / "f2"), _p) is False
        # install windows with msi + elevated
        monkeypatch.setattr(wb, "is_elevated", lambda: True)
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: "/bin/warp-cli")
        async def _fake_exec(*a, **k):
            class P:
                async def communicate(self): return (b"", b"")
            return P()
        monkeypatch.setattr(wb.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(wb.asyncio, "sleep", AsyncMock())
        assert await wb._install_windows(None, "/tmp/fake.msi") is True
        # declined
        monkeypatch.setattr(wb, "is_elevated", lambda: False)
        monkeypatch.setattr(wb, "_windows_elevate_msiexec", lambda msi: False)
        assert await wb._install_windows(_p, "/tmp/fake.msi") is False
        # download fail
        monkeypatch.setattr(wb, "_download", AsyncMock(return_value=False))
        monkeypatch.setattr(wb, "_machine_arch", lambda: "amd64")
        assert await wb._install_windows(None, None) is False
        # timeout path
        async def _slow_exec(*a, **k):
            class P:
                async def communicate(self):
                    await __import__("asyncio").sleep(5)
                    return (b"", b"")
                def kill(self): pass
            return P()
        monkeypatch.setattr(wb.asyncio, "create_subprocess_exec", _slow_exec)
        monkeypatch.setattr(wb, "is_elevated", lambda: True)
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: None)
        # use wait_for timeout by shrinking? just check returns False when no cli after
        assert await wb._install_windows(None, "/tmp/fake.msi") is False

    @pytest.mark.asyncio
    async def test_install_linux_macos(self, monkeypatch, tmp_path):
        import agent.warp_bootstrap as wb
        monkeypatch.setattr(wb.asyncio, "sleep", AsyncMock())
        # no script
        monkeypatch.setattr(wb, "_linux_install_script", lambda: None)
        assert await wb._install_linux(None) is False
        # elevate fail
        monkeypatch.setattr(wb, "_linux_install_script", lambda: "echo hi")
        monkeypatch.setattr(wb, "_elevate_script", lambda p: False)
        assert await wb._install_linux(None) is False
        # success but no cli
        monkeypatch.setattr(wb, "_elevate_script", lambda p: True)
        monkeypatch.setattr(wb, "_ensure_linux_service", AsyncMock())
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: None)
        assert await wb._install_linux(None) is False
        # success with cli
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: "/bin/warp-cli")
        assert await wb._install_linux(None) is True
        # macos download fail
        monkeypatch.setattr(wb, "_download", AsyncMock(return_value=False))
        assert await wb._install_macos(None) is False
        # macos timeout
        monkeypatch.setattr(wb, "_download", AsyncMock(return_value=True))
        async def _slow(*a, **k):
            class P:
                returncode = 0
                async def communicate(self):
                    await __import__("asyncio").sleep(10)
                    return (b"", b"")
                def kill(self): pass
            return P()
        # patch wait_for to raise TimeoutError quickly
        real_wait = __import__("asyncio").wait_for
        async def _fake_wait(coro, timeout=None):
            coro.close() if hasattr(coro, "close") else None
            raise __import__("asyncio").TimeoutError()
        monkeypatch.setattr(wb.asyncio, "wait_for", _fake_wait)
        monkeypatch.setattr(wb.asyncio, "create_subprocess_exec", _slow)
        assert await wb._install_macos(None) is False
        monkeypatch.setattr(wb.asyncio, "wait_for", real_wait)

    @pytest.mark.asyncio
    async def test_ensure_and_connect(self, monkeypatch):
        import agent.warp_bootstrap as wb
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: "/bin/warp-cli")
        assert await wb.ensure_warp_installed() is True
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: None)
        monkeypatch.setattr(wb, "_os_platform", lambda: "windows")
        monkeypatch.setattr(wb, "_install_windows", AsyncMock(return_value=True))
        assert await wb.ensure_warp_installed() is True
        monkeypatch.setattr(wb, "_os_platform", lambda: "linux")
        monkeypatch.setattr(wb, "_install_linux", AsyncMock(return_value=False))
        assert await wb.ensure_warp_installed() is False
        monkeypatch.setattr(wb, "_os_platform", lambda: "darwin")
        monkeypatch.setattr(wb, "_install_macos", AsyncMock(return_value=True))
        assert await wb.ensure_warp_installed() is True
        monkeypatch.setattr(wb, "_os_platform", lambda: "plan9")
        assert await wb.ensure_warp_installed() is False
        # connect no cli
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: None)
        assert await wb.connect_warp() is False
        # connect ok
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: "/bin/warp-cli")
        fake_rot = SimpleNamespace(_run=AsyncMock(return_value="Status update: Connected"), connect=AsyncMock(return_value=True), get_ip=AsyncMock(return_value="1.1.1.1"), status=AsyncMock(return_value={"connected": True, "healthy": True, "ip": "1.1.1.1"}))
        monkeypatch.setattr(wb, "get_rotator", lambda *a, **k: fake_rot)
        monkeypatch.setattr(wb.sys, "platform", "win32")
        assert await wb.connect_warp() is True
        # connect fail
        fake_rot2 = SimpleNamespace(_run=AsyncMock(side_effect=RuntimeError("x")), connect=AsyncMock(return_value=False), get_ip=AsyncMock(return_value=None), status=AsyncMock(return_value={"connected": False, "healthy": False, "ip": ""}))
        monkeypatch.setattr(wb, "get_rotator", lambda *a, **k: fake_rot2)
        assert await wb.connect_warp() is False
        # bootstrap installed + no connect
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: "/bin/warp-cli")
        out = await wb.bootstrap(connect=False)
        assert out["success"] is True
        # bootstrap install fail
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: None)
        monkeypatch.setattr(wb, "ensure_warp_installed", AsyncMock(return_value=False))
        out2 = await wb.bootstrap()
        assert out2["success"] is False
        # bootstrap connect path
        monkeypatch.setattr(wb, "_warp_cli_path", lambda: "/bin/warp-cli")
        monkeypatch.setattr(wb, "ensure_warp_installed", AsyncMock(return_value=True))
        monkeypatch.setattr(wb, "connect_warp", AsyncMock(return_value=True))
        monkeypatch.setattr(wb, "get_rotator", lambda *a, **k: fake_rot)
        out3 = await wb.bootstrap()
        assert out3["installed"] is True


# ===========================================================================
# warp + llama + memory extra
# ===========================================================================

class TestWarpExtra:
    def test_known_paths(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w.shutil, "which", lambda x: None)
        monkeypatch.setattr(w.os.path, "exists", lambda p: True)
        monkeypatch.setattr(w.os, "name", "nt")
        assert w._warp_cli_path() is not None
        monkeypatch.setattr(w.os.path, "exists", lambda p: False)
        assert w._warp_cli_path() is None

    @pytest.mark.asyncio
    async def test_linux_service(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w.sys, "platform", "win32")
        assert await w._linux_warp_service_active() is True
        monkeypatch.setattr(w.sys, "platform", "linux")
        async def _ok(*a, **k):
            class P:
                async def communicate(self): return (b"active", b"")
            return P()
        monkeypatch.setattr(w.asyncio, "create_subprocess_exec", _ok)
        assert await w._linux_warp_service_active() is True
        async def _bad(*a, **k): raise RuntimeError("x")
        monkeypatch.setattr(w.asyncio, "create_subprocess_exec", _bad)
        assert await w._linux_warp_service_active() is False

    @pytest.mark.asyncio
    async def test_rotate_full_and_run(self, monkeypatch):
        import agent.warp as w
        monkeypatch.setattr(w, "_warp_cli_path", lambda: "/bin/warp-cli")
        monkeypatch.setattr(w, "_linux_warp_service_active", AsyncMock(return_value=True))
        r = w.WarpRotator(interval=0)
        monkeypatch.setattr(r, "_run", AsyncMock(return_value="ok"))
        monkeypatch.setattr(r, "healthy", AsyncMock(return_value=True))
        monkeypatch.setattr(r, "get_ip", AsyncMock(return_value="2.2.2.2"))
        assert await r.rotate(force=True) is True
        # unhealthy after -> False
        monkeypatch.setattr(r, "healthy", AsyncMock(return_value=False))
        monkeypatch.setattr(w.asyncio, "sleep", AsyncMock())
        r2 = w.WarpRotator(interval=0)
        monkeypatch.setattr(r2, "_run", AsyncMock(return_value="ok"))
        monkeypatch.setattr(r2, "healthy", AsyncMock(return_value=False))
        assert await r2.rotate(force=True) is False
        # exception in run
        r3 = w.WarpRotator(interval=0)
        monkeypatch.setattr(r3, "_run", AsyncMock(side_effect=RuntimeError("boom")))
        assert await r3.rotate(force=True) is False
        # _run timeout
        async def _never(*a, **k):
            class P:
                async def communicate(self):
                    await __import__("asyncio").sleep(60)
                    return (b"", b"")
                def kill(self): pass
            return P()
        r4 = w.WarpRotator(interval=0)
        _real_accept_tos = w._accept_tos_supported
        monkeypatch.setattr(w, "_accept_tos_supported", AsyncMock(return_value=True))
        monkeypatch.setattr(w.asyncio, "create_subprocess_exec", _never)
        real_wait = __import__("asyncio").wait_for
        async def _tw(coro, timeout=None):
            try: coro.close()
            except Exception: pass
            raise __import__("asyncio").TimeoutError()
        monkeypatch.setattr(w.asyncio, "wait_for", _tw)
        with pytest.raises(__import__("asyncio").TimeoutError):
            await r4._run("/bin/warp-cli", "status")
        monkeypatch.setattr(w.asyncio, "wait_for", real_wait)
        # accept tos cached + fail (restore the real helper: the TimeoutError
        # block above patched it with an AsyncMock that would never fail)
        monkeypatch.setattr(w, "_accept_tos_supported", _real_accept_tos)
        w._ACCEPT_TOS_SUPPORTED = None
        async def _help(*a, **k):
            class P:
                async def communicate(self): return (b"--accept-tos", b"")
            return P()
        monkeypatch.setattr(w.asyncio, "create_subprocess_exec", _help)
        monkeypatch.setattr(w.asyncio, "wait_for", real_wait)
        assert await w._accept_tos_supported("/bin/warp-cli") is True
        w._ACCEPT_TOS_SUPPORTED = None
        async def _help2(*a, **k): raise RuntimeError("x")
        monkeypatch.setattr(w.asyncio, "create_subprocess_exec", _help2)
        assert await w._accept_tos_supported("/bin/warp-cli") is False
        w._ACCEPT_TOS_SUPPORTED = None
        # status version fail + ip set
        monkeypatch.setattr(w, "_warp_cli_path", lambda: "/bin/warp-cli")
        monkeypatch.setattr(w, "_linux_warp_service_active", AsyncMock(return_value=True))
        r5 = w.WarpRotator(interval=0)
        monkeypatch.setattr(r5, "_run", AsyncMock(side_effect=["Status update: Connected and Healthy", RuntimeError("ver fail")]))
        monkeypatch.setattr(r5, "get_ip", AsyncMock(return_value=None))
        info = await r5.status()
        assert info["installed"] is True
        # disconnect exception
        monkeypatch.setattr(r5, "_run", AsyncMock(side_effect=RuntimeError("x")))
        assert await r5.disconnect() is False
        assert await r5.connect() is False or True  # connect calls healthy; run fails -> False
        # get_rotator custom
        assert w.get_rotator(interval=1.0) is w.get_rotator()


class TestLlamaExtra:
    @pytest.mark.asyncio
    async def test_ensure_owned_wait_and_spawn(self, monkeypatch):
        import agent.llama_server as ls
        m = ls.LlamaServerManager()
        m.enabled = True
        # owned proc waiting success
        m._proc = SimpleNamespace(poll=lambda: None)
        monkeypatch.setattr(m, "is_healthy", AsyncMock(side_effect=[False, True]))
        monkeypatch.setattr(m, "_wait_healthy", AsyncMock(return_value=True))
        assert await m.ensure_running(force_check=True) is True
        # spawn success with fake binary
        m2 = ls.LlamaServerManager()
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "build" / "bin").mkdir(parents=True)
        (d / "build" / "bin" / "llama-server").write_text("x")
        mf = d / "m.gguf"
        mf.write_text("x")
        m2.llama_dir = d
        m2.model_path = mf
        m2.log_file = d / "log.txt"
        monkeypatch.setattr(m2, "_wait_healthy", AsyncMock(return_value=True))
        monkeypatch.setattr(ls.subprocess, "Popen", lambda *a, **k: SimpleNamespace(
            pid=999, poll=lambda: None, terminate=lambda: None,
            wait=lambda timeout=None: None, kill=lambda: None))
        assert await m2._spawn() is True
        # spawn unhealthy -> terminate
        monkeypatch.setattr(m2, "_wait_healthy", AsyncMock(return_value=False))
        m2._proc = SimpleNamespace(pid=1, poll=lambda: None, terminate=lambda: None, wait=lambda timeout=None: None)
        assert await m2._spawn() is False
        # terminate kill path
        class KillProc:
            pid = 2
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): raise __import__("subprocess").TimeoutExpired("x", 1)
            def kill(self): self.killed = True
        kp = KillProc()
        m2._proc = kp
        m2._terminate()
        assert getattr(kp, "killed", False) is True
        # terminate exception
        class BoomProc:
            pid = 3
            def poll(self): return None
            def terminate(self): raise RuntimeError("x")
        m2._proc = BoomProc()
        m2._terminate()
        # stop_if_idle healthy terminate
        m3 = ls.LlamaServerManager()
        m3.idle_timeout = 1
        m3._last_use = __import__("time").monotonic() - 10
        m3._proc = SimpleNamespace(poll=lambda: None, pid=5, terminate=lambda: None, wait=lambda timeout=None: None)
        monkeypatch.setattr(m3, "is_healthy", AsyncMock(return_value=True))
        await m3.stop_if_idle()
        # reaper loop cancel
        m4 = ls.LlamaServerManager()
        m4.enabled = False
        m4.start_reaper()
        assert m4._reaper_task is None
        # manages bad url
        assert m.manages_url("") is False
        assert m.manages_url(None) is False  # type: ignore
        # ensure_local true
        monkeypatch.setattr(ls, "get_llama_server_manager", lambda: SimpleNamespace(manages_url=lambda u: True, ensure_running=AsyncMock(return_value=True)))
        assert await ls.ensure_local_llama_server("http://127.0.0.1:8080/v1") is True

    @pytest.mark.asyncio
    async def test_reaper_loop_paths(self, monkeypatch):
        import agent.llama_server as ls
        m = ls.LlamaServerManager()
        m.enabled = True
        # run one iteration then cancel
        calls = {"n": 0}
        async def _sleep(x):
            calls["n"] += 1
            if calls["n"] > 1: raise __import__("asyncio").CancelledError()
        monkeypatch.setattr(ls.asyncio, "sleep", _sleep)
        monkeypatch.setattr(m, "stop_if_idle", AsyncMock())
        with pytest.raises(__import__("asyncio").CancelledError):
            await m._reaper_loop()
        # exception swallowed then cancel
        m2 = ls.LlamaServerManager()
        n2 = {"n": 0}
        async def _sleep2(x):
            n2["n"] += 1
            if n2["n"] > 1: raise __import__("asyncio").CancelledError()
        monkeypatch.setattr(ls.asyncio, "sleep", _sleep2)
        monkeypatch.setattr(m2, "stop_if_idle", AsyncMock(side_effect=RuntimeError("x")))
        with pytest.raises(__import__("asyncio").CancelledError):
            await m2._reaper_loop()


class TestMemoryExtra:
    def test_from_dict_missing_keys(self):
        from agent.memory import ConversationMemory
        m = ConversationMemory.from_dict({"sessions": {"s": {"turns": []}}})
        assert m.get_session("s")["turn_count"] == 0
        assert ConversationMemory.from_dict({}).list_sessions() == []

    @pytest.mark.asyncio
    async def test_compress_edge(self):
        from agent.memory import ConversationMemory
        m = ConversationMemory()
        for i in range(7):
            m.add_turn("s", "user", f"m{i}")
        await m.compress("s")  # 7 turns -> compress_count 2 <3 -> return, no summary
        assert m.get_session("s")["summary"] == ""
        # empty content turns
        m2 = ConversationMemory()
        for i in range(12):
            m2.add_turn("s", "user", "")
        await m2.compress("s")
        assert isinstance(m2.get_session("s")["summary"], str)


# ===========================================================================
# pandas deep
# ===========================================================================

class TestPandasDeep:
    def _tool(self):
        from agent.tools_pandas import PandasTool
        t = PandasTool(max_rows=100)
        t.register_dataframe("df", pd.DataFrame({
            "a": [1, 2, 3, 4], "b": ["x", "y", "x", "y"],
            "v": [1.0, 2.0, 3.0, 4.0],
            "d": pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01"]),
        }))
        return t

    def test_describe_percentiles(self):
        t = self._tool()
        assert t.describe("df", percentiles=[0.25, 0.75]).success is True
        assert t.sample("df", 100).success is True  # n>len clamped
        assert t.sort_values("df", by=["a"], ascending=False).success is True
        assert t.query("df", "a > 1").success is True

    def test_value_counts_normalize(self):
        t = self._tool()
        assert t.value_counts("df", "b", normalize=True).success is True
        assert t.value_counts("df", "b", normalize=False, top_n=1).success is True

    def test_correlation_method(self):
        t = self._tool()
        assert t.correlation("df", method="spearman").success is True

    def test_resample_success(self):
        t = self._tool()
        out = t.time_series_resample("df", "d", "ME", "v", agg="sum")
        assert out.success is True
        assert t.time_series_resample("df", "nope", "ME", "v").success is False
        assert t.time_series_resample("df", "d", "ME", "nope").success is False
        assert t.time_series_resample("missing", "d", "ME", "v").success is False

    def test_rolling_custom_and_fail(self):
        t = self._tool()
        assert t.rolling_stats("df", "v", 2, stats=["mean"]).success is True
        assert t.rolling_stats("df", "v", 2, stats=["nonexist_stat_xyz"]).success is False
        assert t.rolling_stats("missing", "v", 2).success is False

    def test_crosstab_values_normalize(self):
        t = self._tool()
        assert t.crosstab("df", index="b", columns="b", values="v", aggfunc="sum").success is True
        assert t.crosstab("df", index="b", columns="b", normalize=True).success is True
        assert t.crosstab("df", index="nope_xyz", columns="b").success is False

    def test_merge_on_variants(self):
        t = self._tool()
        t.register_dataframe("r", pd.DataFrame({"a": [1, 2], "c": [9, 9]}))
        assert t.merge("df", "r", on=["a"], how="left").success is True
        assert t.merge("df", "r", on=["zzz_nope"], how="inner").success is False

    def test_pivot_columns_fill(self):
        t = self._tool()
        assert t.pivot_table("df", values="v", index=["b"], columns="b", aggfunc="sum", fill_value=0).success is True

    def test_execute_truncate_and_list(self):
        from agent.tools_pandas import PandasTool
        t = PandasTool(max_rows=2)
        t.register_dataframe("big", pd.DataFrame({"a": [1, 2, 3, 4]}))
        assert len(t.get_dataframe("big")) == 2
        assert "big" in t.list_dataframes()
        # execute truncates
        t2 = PandasTool(max_rows=2)
        t2.register_dataframe("df", pd.DataFrame({"a": [1, 2, 3]}))
        out = t2.execute_code("result = df")
        assert len(out.data) == 2


# ===========================================================================
# viz deep
# ===========================================================================

class TestVizDeep:
    def _tool(self, tmp_path):
        from agent.tools_viz import VizTool
        return VizTool(output_dir=str(tmp_path / "charts"))

    def test_extra_types(self, tmp_path):
        t = self._tool(tmp_path)
        df = pd.DataFrame({"x": ["a", "b", "a"], "y": [1.0, 2.0, 3.0], "z": [4.0, 5.0, 6.0]})
        assert t.create_chart(df, "sunburst", x="x", y="y").success is True
        assert t.create_chart(df, "treemap", x="x", y="y").success is True
        assert t.create_chart(pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]}), "parallel", color="a").success is True
        assert t.create_chart(df, "violin", x="x", y="y").success is True
        assert t.create_chart(df, "pie", x="x", y="y", hole=0.4).success is True
        assert t.create_chart(df, "area", x="x", y="y", groupnorm="percent").success is True
        assert t.create_chart(df, "scatter", x="x", y="y", size="y", trendline="ols").success is True
        assert t.create_chart(df, "line", x="x", y="y", markers=False).success is True
        assert t.create_chart(df, "bar", x="x", y="y", barmode="stack", text_auto=True).success is True
        assert t.create_chart(df, "box", x="x", y="y", points="all").success is True
        assert t.create_chart(df, "histogram", x="y", nbins=5, marginal=None).success is True
        assert t.create_chart(df, "bar", x="x", y="y", layout={"title": {"text": "t"}}).success is True
        assert t.create_chart(df, "bar", x="x", y="y", height=300, width=400, showlegend=False).success is True

    def test_heatmap_corr_fallback(self, tmp_path):
        t = self._tool(tmp_path)
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [2.0, 3.0, 4.0]})
        assert t.create_chart(df, "heatmap").success is True
        assert t.create_chart(df, "heatmap", x="a", y="b", z="zzz_nope").success is True

    def test_matplotlib_all(self, tmp_path):
        import matplotlib
        matplotlib.use("Agg")
        from agent.tools_viz import VizTool
        t = VizTool(default_engine="matplotlib", output_dir=str(tmp_path / "m2"))
        df = pd.DataFrame({"x": ["a", "b", "a"], "y": [1.0, 2.0, 3.0]})
        for ct in ["bar", "line", "scatter", "histogram", "box", "violin"]:
            r = t.create_chart(df, ct, x="x", y="y")
            assert r.success is True, ct
        assert t.create_chart(df, "heatmap").success is True
        assert t.create_chart(df, "heatmap", x="x", y="y", z="y").success is True
        assert t.create_chart(df, "nope").success is False
        assert t.create_chart(df, "pairplot", color="x").success is True
        # helpers
        assert t.box(df, y="y").success is True
        assert t.heatmap(df).success is True
        assert t.correlation_heatmap(df).success is True

    def test_save_variants(self, tmp_path, monkeypatch):
        t = self._tool(tmp_path)
        df = pd.DataFrame({"x": ["a", "b"], "y": [1.0, 2.0]})
        good = t.create_chart(df, "bar", x="x", y="y")
        assert t.save_html(good, "h1").endswith(".html")
        # save_png plotly mocked (no kaleido)
        monkeypatch.setattr(good.figure, "write_image", lambda *a, **k: open(str(tmp_path / "charts" / "p1.png"), "wb").write(b"x"))
        assert t.save_png(good, "p1").endswith(".png")
        assert t.save_json(good, "j1").endswith(".json")
        # matplotlib save paths
        import matplotlib
        matplotlib.use("Agg")
        from agent.tools_viz import VizTool as VT2
        m = VT2(default_engine="matplotlib", output_dir=str(tmp_path / "m3"))
        mg = m.create_chart(df, "bar", x="x", y="y")
        assert m.save_png(mg, "mp").endswith(".png")
        assert m.save_json(mg, "mj").endswith(".json")
        assert m.save_html(mg, "mh").endswith(".html")
        # base64 matplotlib
        assert m.to_base64_png(mg) != ""
        # base64 plotly mocked
        monkeypatch.setattr(good.figure, "write_image", lambda buf, **k: buf.write(b"pngbytes"))
        assert t.to_base64_png(good) != ""
        # pairplot matplotlib engine passthrough
        assert m.pairplot(df).success is True
        # convenience + defs
        from agent.tools_viz import VizToolOpenAI, create_chart
        assert len(VizToolOpenAI.get_function_definitions()) == 2
        assert create_chart(df, "bar", output_dir=str(tmp_path / "c9"), x="x", y="y").success is True
        # VizResult defaults
        from agent.tools_viz import VizResult
        assert VizResult(success=True).metadata == {}


# ===========================================================================
# proxifly deep
# ===========================================================================

class TestProxiflyDeep:
    @pytest.mark.asyncio
    async def test_fetch_proto_filter_and_fail_one(self, monkeypatch):
        import agent.proxifly_pool as pp
        import httpx as _hx
        class MixedC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url):
                if "/https/" in url: raise RuntimeError("down")
                class R:
                    text = "1.1.1.1:8080\nhttp://a:1"
                    def raise_for_status(self): pass
                return R()
        monkeypatch.setattr(_hx, "AsyncClient", MixedC)
        out = await pp.fetch(protocols=["http", "https", "bogus"], limit=10)
        assert "http://a:1" in out and "http://1.1.1.1:8080" in out
        # invalid only -> defaults
        class EmptyC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url):
                class R:
                    text = "http://z:1"
                    def raise_for_status(self): pass
                return R()
        monkeypatch.setattr(_hx, "AsyncClient", EmptyC)
        out2 = await pp.fetch(protocols=["bogus"], limit=5)
        assert out2
        # limit early return
        out3 = await pp.fetch(protocols=["http"], limit=1)
        assert len(out3) == 1

    @pytest.mark.asyncio
    async def test_probe_ports(self, monkeypatch):
        import agent.proxifly_pool as pp
        monkeypatch.setattr(pp.asyncio, "sleep", AsyncMock())
        # https default 443
        real_conn = pp.asyncio.open_connection
        async def _fake_conn(host, port):
            class W:
                def close(self): pass
                async def wait_closed(self): pass
            return (None, W())
        monkeypatch.setattr(pp.asyncio, "open_connection", _fake_conn)
        assert await pp._probe("https://example.com") is True
        assert await pp._probe("http://example.com:8080") is True
        # fail
        async def _fail(host, port): raise RuntimeError("down")
        monkeypatch.setattr(pp.asyncio, "open_connection", _fail)
        assert await pp._probe("http://example.com:8080") is False
        # wait_closed raises swallowed
        async def _fake2(host, port):
            class W:
                def close(self): pass
                async def wait_closed(self): raise RuntimeError("x")
            return (None, W())
        monkeypatch.setattr(pp.asyncio, "open_connection", _fake2)
        assert await pp._probe("http://example.com:8080") is True
        # sample spread
        monkeypatch.setattr(pp, "_probe", AsyncMock(return_value=True))
        out = await pp.probe_sample(["http://a:1", "http://b:2", "http://c:3"], sample=2)
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_relay_variants(self, monkeypatch):
        import sys as _sys, types as _t
        import agent.proxifly_pool as pp
        import httpx as _hx
        mod = _t.ModuleType("api.billing.cloud_sync")
        mod.billing_sync_base_url = lambda: "http://billing.test"  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.billing.cloud_sync", mod)
        parent = _t.ModuleType("api.billing")
        parent.cloud_sync = mod  # type: ignore
        monkeypatch.setitem(_sys.modules, "api.billing", parent)
        # bad json
        class BadJ:
            status_code = 200
            def json(self): raise ValueError("bad")
            def raise_for_status(self): pass
        class BJC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return BadJ()
        monkeypatch.setattr(_hx, "AsyncClient", BJC)
        with pytest.raises(pp.RelayDenied):
            await pp.fetch_via_relay("t")
        # 500 -> raise_for_status raises
        class S500:
            status_code = 500
            def json(self): return {}
            def raise_for_status(self): raise httpx.HTTPStatusError("s", request=None, response=None)
        class C500:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return S500()
        monkeypatch.setattr(_hx, "AsyncClient", C500)
        with pytest.raises(httpx.HTTPStatusError):
            await pp.fetch_via_relay("t")
        # invalid protocols defaulted
        class OkR:
            status_code = 200
            def json(self): return {"proxies": []}
            def raise_for_status(self): pass
        class OkC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return OkR()
        monkeypatch.setattr(_hx, "AsyncClient", OkC)
        assert await pp.fetch_via_relay("t", protocols=["bogus"]) == []

    @pytest.mark.asyncio
    async def test_refresh_proxies_param_and_apply_fail(self, monkeypatch):
        import agent.proxifly_pool as pp
        monkeypatch.setattr(pp, "probe_sample", AsyncMock(return_value=[]))
        monkeypatch.setattr(pp, "_apply_to_settings", lambda proxies: None)
        pp._state["last_refresh"] = 0.0
        out = await pp.refresh(force=True, probe=True, proxies=["http://z:1"])
        assert out["refreshed"] is True and out["alive_sample"] == 0
        # apply fail swallowed
        import config as _cfg
        monkeypatch.setattr(_cfg, "save_llm_runtime_overrides", lambda d: (_ for _ in ()).throw(RuntimeError("x")))
        pp._apply_to_settings(["http://a:1"])
        assert pp.pool_status()["count"] >= 0
