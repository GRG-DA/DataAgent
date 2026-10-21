"""Regression tests for the chat-pro QA sweep fixes.

Each test pins a behavior that failed in the 25-question QA run:

  - nonsense chart asks ("chart of unicorn sightings") decline instead of
    charting a raw text column
  - singular superlatives ("what is the highest revenue order") label the
    aggregate honestly and skip the bogus ranking bar chart
  - comparison/distribution/trend fallbacks (raw SELECT *) get no chart
  - scatter charts honor "X vs Y" axis naming
  - introspection plans skip the LLM SQL upgrade (schema filters preserved)
  - long text blobs (license keys, JSON) are clamped in answers
  - the step-summary duration hides sub-100 ms noise
"""
import re
from pathlib import Path

import pandas as pd

from agent.orchestrator import Orchestrator
from agent.planner import ExecutionPlan, Planner, ToolType
from agent.tools_viz import VizTool


def _planner():
    p = Planner()
    p.set_schema_context(
        "TABLE analytics.smoke_sales (8 rows):\n"
        "  - order_id: INTEGER\n"
        "  - region: VARCHAR\n"
        "  - product: VARCHAR\n"
        "  - revenue: DOUBLE\n"
        "  - units: INTEGER\n"
        "  - order_date: DATE\n"
        "TABLE analytics.key_redemptions (1 rows):\n"
        "  - key_hash: VARCHAR\n"
        "  - user_id: INTEGER\n"
        "  - email: VARCHAR\n"
        "  - activated_at: TIMESTAMP\n"
    )
    return p


class _Plan:
    question = "show license keys"
    steps = []


class TestMeaninglessQuestionGuard:
    def test_all_unknown_words_still_flagged(self):
        p = _planner()
        assert p._is_meaningless_question("what is the xyzzy blorp?") is True

    def test_chart_of_nonsense_is_flagged(self):
        p = _planner()
        assert p._is_meaningless_question(
            "show me a chart of unicorn sightings by rainbow color") is True

    def test_format_only_ask_is_flagged(self):
        p = _planner()
        assert p._is_meaningless_question("show me a line chart") is True

    def test_real_chart_question_passes(self):
        p = _planner()
        assert p._is_meaningless_question("bar chart of revenue by region") is False


class TestChartDeadEndDecline:
    def test_chart_of_unicorn_declines(self):
        p = _planner()
        plan = p._rule_based_plan(
            "show me a chart of unicorn sightings by rainbow color",
            ["key_redemptions"], preferred_table="key_redemptions")
        assert plan.steps == []
        assert "no chartable" in plan.reasoning

    def test_named_text_column_chart_declines(self):
        # "bar chart of key_hash" names a text column, but the generated
        # query is a raw SELECT * fallback — no chart may be built from it.
        p = _planner()
        steps = p._chart_steps_for("bar", {}, "show a bar chart of key_hash",
                                   "key_redemptions")
        assert steps == []

    def test_real_chart_still_builds(self):
        p = _planner()
        plan = p._rule_based_plan("bar chart of revenue by region",
                                  ["smoke_sales"], preferred_table="smoke_sales")
        tools = [s.tool for s in plan.steps]
        assert ToolType.SQL in tools
        assert ToolType.VIZ in tools

    def test_line_chart_over_categories_still_builds(self):
        p = _planner()
        steps = p._chart_steps_for("line", {}, "line chart of orders", "smoke_sales")
        assert steps
        assert steps[0].tool == ToolType.SQL

    def test_bar_chart_by_month_still_builds(self):
        p = _planner()
        steps = p._chart_steps_for("bar", {}, "bar chart of revenue by month",
                                   "smoke_sales")
        descs = [s.description for s in steps]
        assert "Time series for bar chart" in descs
        assert any(s.tool == ToolType.VIZ for s in steps)


class TestRankingScalar:
    def test_highest_revenue_is_scalar_labeled_no_chart(self):
        p = _planner()
        plan = p._rule_based_plan(
            "What is the highest revenue order in smoke_sales?",
            ["smoke_sales"], preferred_table="smoke_sales")
        descs = [s.description for s in plan.steps]
        assert any(d.startswith("Compute aggregate for:") for d in descs)
        assert not any(s.tool == ToolType.VIZ for s in plan.steps)

    def test_grouped_ranking_keeps_chart(self):
        p = _planner()
        plan = p._rule_based_plan(
            "top 3 products by revenue", ["smoke_sales"],
            preferred_table="smoke_sales")
        assert any(s.tool == ToolType.VIZ for s in plan.steps)


class TestComparisonFallbackNoChart:
    def test_select_star_comparison_gets_no_chart(self):
        p = _planner()
        plan = p._rule_based_plan(
            "summarize key insights across all tables",
            ["key_redemptions"], preferred_table="key_redemptions")
        sql_steps = [s for s in plan.steps if s.tool == ToolType.SQL]
        assert sql_steps
        assert not any(s.tool == ToolType.VIZ for s in plan.steps)


class TestScatterAxes:
    def test_vs_columns_resolve_to_named_axes(self):
        p = _planner()
        steps = p._chart_steps_for("scatter", {},
                                   "scatter of units vs revenue", "smoke_sales")
        assert len(steps) == 2
        query = steps[0].params["query"]
        assert "units AS x_val" in query
        assert "revenue AS y_val" in query
        assert steps[1].params["x"] == "x_val"
        assert steps[1].params["y"] == "y_val"


class TestIntrospectionSkipUpgrade:
    def test_plan_one_marks_skip_llm_upgrade(self):
        p = _planner()
        plan = p._introspection_plan(
            "what tables are available in the warehouse?",
            ["smoke_sales", "key_redemptions"])
        assert plan is not None and plan.steps
        assert plan.steps[0].metadata.get("skip_llm_upgrade") is True


class TestLongTextClamping:
    def test_fmt_scalar_clamps_long_text(self):
        out = Orchestrator._fmt_scalar("x" * 500)
        assert len(out) == 118
        assert out.endswith("\u2026")

    def test_fmt_scalar_keeps_numbers_and_short_text(self):
        assert Orchestrator._fmt_scalar(5) == "5"
        assert Orchestrator._fmt_scalar("str") == "str"

    def test_describe_dataframe_clamps_long_cells_and_labels(self):
        o = Orchestrator.__new__(Orchestrator)
        long = "x" * 500
        df = pd.DataFrame({"license_key": [long, "short"], "n": [1, 2]})
        md = o._describe_dataframe(df, _Plan(), 1)
        assert long not in md
        assert "\u2026" in md


class TestPieSingleSliceFallback:
    def test_single_slice_pie_degrades_to_bar(self):
        t = VizTool()
        df = pd.DataFrame({"stage": ["only"], "count": [5]})
        r = t.create_chart(df, "pie", x="stage", y="count")
        assert r.success is True
        assert r.figure is not None
        assert r.figure.data[0].type == "bar"

    def test_multi_slice_pie_stays_pie(self):
        t = VizTool()
        df = pd.DataFrame({"stage": ["a", "b"], "count": [5, 3]})
        r = t.create_chart(df, "pie", x="stage", y="count")
        assert r.success is True
        assert r.figure.data[0].type == "pie"


class TestChartDurationFloor:
    def test_duration_hidden_below_100ms(self):
        src = Path("web/js/pages/chat-pro.js").read_text(encoding="utf-8")
        assert "if (totalMs >= 100)" in src
