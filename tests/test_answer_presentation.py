"""Response UI polish: human labels and phrasing in rule answers.

UI review (Chrome MCP screenshots) showed two presentation defects:

  1. Multi-row tables leak raw snake_case headers (`group_name`,
     `avg_value`, `pct_of_total`) — only 1-row results were prettified.
  2. Clunky phrasing: "1 visualization(s)" and
     "Query returned 2 row(s) × 4 column(s):".

Contract:
  * Multi-row table headers use `_pretty_label` (same as 1-row tables).
  * Highlights name the metric with its pretty label (lowercased).
  * Header reads "**Result: N rows × M columns**" (pluralized).
  * Chart footer reads "📊 **N chart(s)** — see above."
"""

from __future__ import annotations

import re

import pandas as pd

from agent.orchestrator import AgentResponse, Orchestrator, StepResult
from agent.planner import ExecutionPlan, ToolType
from agent.tools_sql import SQLResult
from agent.tools_viz import VizResult


def _orch() -> Orchestrator:
    return Orchestrator(None, None, None, None, None)


def _plan(question: str) -> ExecutionPlan:
    return ExecutionPlan(question=question, steps=[], estimated_steps=0)


def _response(df: pd.DataFrame, n_viz: int = 0) -> AgentResponse:
    return AgentResponse(
        question="q",
        plan=_plan("q"),
        step_results=[
            StepResult(step_id=1, tool=ToolType.SQL, success=True,
                       result=SQLResult(success=True, data=df, row_count=len(df)))
        ],
        final_answer="",
        visualizations=[VizResult(success=True, html="<div></div>")
                        for _ in range(n_viz)],
    )


class TestPrettyTableHeaders:
    def test_multirow_table_headers_are_pretty(self):
        df = pd.DataFrame({
            "group_name": ["Google Ads", "Meta"],
            "value": [5707.03, 5274.5],
            "count": [9, 4],
            "pct_of_total": [32.61, 30.14],
        })
        out = _orch()._describe_dataframe(df, _plan("top sources"), 1)
        flat = re.sub(r"\s+", " ", out)
        assert "| Group name | Value | Count | Pct of total |" in flat, out
        assert "group_name" not in out, "raw snake_case header must not leak"
        assert "pct_of_total" not in out, "raw snake_case header must not leak"

    def test_highlights_use_pretty_metric_label(self):
        df = pd.DataFrame({
            "period": ["2025-04", "2025-05"],
            "avg_value": [15959.46, 1539.33],
        })
        out = _orch()._describe_dataframe(df, _plan("average spend by month"), 1)
        assert "Total avg value" in out, out
        assert "avg_value" not in out, "raw snake_case metric must not leak"


class TestResultHeaderPhrasing:
    def test_multirow_header_reads_naturally(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6], "d": [7, 8]})
        out = _orch()._describe_dataframe(df, _plan("q"), 1)
        assert "**Result: 2 rows × 4 columns**" in out, out
        assert "Query returned" not in out, out

    def test_single_column_pluralizes_correctly(self):
        df = pd.DataFrame({"a": [1, 2]})
        out = _orch()._describe_dataframe(df, _plan("q"), 1)
        assert "**Result: 2 rows × 1 column**" in out, out


class TestHighlightExtremes:
    def test_extremes_name_the_label(self):
        """Highest/Lowest highlights must name the row label.

        Regression: pandas 3 stores strings as `StringDtype`, so the old
        `df[c].dtype == object` label detection returned None and the
        extremes lines silently disappeared from every answer.
        """
        df = pd.DataFrame({
            "source": ["Google Ads", "Meta"],
            "value": [5707.03, 5274.5],
        })
        out = _orch()._describe_dataframe(df, _plan("top sources"), 1)
        assert "Highest:" in out and "Google Ads = 5,707.03" in out, out
        assert "Lowest:" in out and "Meta = 5,274.50" in out, out

    def test_extremes_name_datetime_labels(self):
        """Date-grouped results (spend by month) must name the period."""
        df = pd.DataFrame({
            "period": pd.to_datetime(["2025-04-01", "2025-05-01"]),
            "value": [15959.46, 1539.33],
        })
        out = _orch()._describe_dataframe(df, _plan("spend by month"), 1)
        assert "Highest:" in out and "2025-04-01 = 15,959.46" in out, out
        assert "Lowest:" in out and "2025-05-01 = 1,539.33" in out, out


class TestDatetimeCells:
    def test_datetime_cells_render_dates_only(self):
        """Midnight timestamps must not leak `00:00:00` into table cells."""
        df = pd.DataFrame({
            "period": pd.to_datetime(["2025-04-01", "2025-05-01"]),
            "value": [15959.46, 1539.33],
        })
        out = _orch()._describe_dataframe(df, _plan("spend by month"), 1)
        assert "2025-04-01" in out, out
        assert "00:00:00" not in out, out

    def test_datetime_cells_keep_time_when_meaningful(self):
        df = pd.DataFrame({
            "ts": pd.to_datetime(["2025-04-01 09:30:00", "2025-05-01 14:45:00"]),
            "value": [10.0, 20.0],
        })
        out = _orch()._describe_dataframe(df, _plan("events over time"), 1)
        assert "2025-04-01 09:30" in out, out


class TestChartCountPhrasing:
    def test_single_chart(self):
        df = pd.DataFrame({"a": [1, 2]})
        out = _orch()._rule_answer(_response(df, n_viz=1))
        assert "**1 chart**" in out, out
        assert "visualization" not in out, out

    def test_multiple_charts(self):
        df = pd.DataFrame({"a": [1, 2]})
        out = _orch()._rule_answer(_response(df, n_viz=2))
        assert "**2 charts**" in out, out
