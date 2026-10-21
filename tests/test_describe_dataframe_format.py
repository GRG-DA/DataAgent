"""Answer formatting: 1-row results render as a Claude-style metrics table.

The old 1-row branch emitted a terse comma sentence:

    **Result:** **count**: 1, **total_value**: 0.

which reads poorly for wide aggregates (raw snake_case keys, no structure) —
this is what users saw for "Show me the key trends in this dataset".

New contract for `Orchestrator._describe_dataframe`:

  1. Single scalar value  -> keep the conversational one-liner (unchanged).
  2. Multiple values      -> "**Result**" + 2-column markdown table
                             (Metric | Value) with pretty labels
                             (`total_value` -> "Total value").
  3. Trend-style question whose result only covers one period -> append a
     short italic note explaining there is no multi-period history yet.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agent.orchestrator import Orchestrator
from agent.planner import ExecutionPlan


def _orch() -> Orchestrator:
    return Orchestrator(None, None, None, None, None)


def _plan(question: str) -> ExecutionPlan:
    return ExecutionPlan(question=question, steps=[], estimated_steps=0)


TREND_Q = "Show me the key trends in this dataset"


class TestSingleRowFormatting:
    def test_single_scalar_keeps_sentence(self):
        df = pd.DataFrame({"count": [42]})
        out = _orch()._describe_dataframe(df, _plan(TREND_Q), 1)
        assert out == "**Result:** **Count**: 42."

    def test_single_scalar_uses_pretty_label(self):
        df = pd.DataFrame({"total_spend": [17498.79]})
        out = _orch()._describe_dataframe(df, _plan("total spend?"), 1)
        assert out == "**Result:** **Total spend**: 17,498.79."

    def test_multi_value_row_becomes_metrics_table(self):
        df = pd.DataFrame({
            "period": ["2026-10-01"],
            "count": [1],
            "unique_users": [1],
            "total_value": [0],
        })
        out = _orch()._describe_dataframe(df, _plan("how are we doing?"), 1)
        assert "| Metric | Value |" in out, out
        assert "| --- | --- |" in out or "|---|---|" in out, out
        assert "Total value" in out, "snake_case key must be prettified"
        assert "Unique users" in out, out
        assert "total_value" not in out, "raw snake_case key must not leak"

    def test_row_values_are_formatted(self):
        df = pd.DataFrame({"total_value": [1234567.0], "rate": [0.5]})
        out = _orch()._describe_dataframe(df, _plan("numbers?"), 1)
        assert "| Total value | 1,234,567 |" in out, out
        assert "| Rate | 0.5 |" in out, out

    def test_nan_values_are_dropped(self):
        # One valid value left after dropping NaN → conversational sentence.
        df = pd.DataFrame({"count": [1], "missing": [float("nan")]})
        out = _orch()._describe_dataframe(df, _plan("numbers?"), 1)
        assert "Missing" not in out, out
        assert "nan" not in out.lower(), out

    def test_nan_dropped_from_multi_value_table(self):
        df = pd.DataFrame({"count": [1], "rate": [0.5], "missing": [float("nan")]})
        out = _orch()._describe_dataframe(df, _plan("numbers?"), 1)
        assert "| Metric | Value |" in out, out
        assert "Missing" not in out, out
        assert "| Count | 1 |" in out and "| Rate | 0.5 |" in out, out

    def test_all_none_row_still_renders(self):
        df = pd.DataFrame({"a": [None]})
        out = _orch()._describe_dataframe(df, _plan("numbers?"), 1)
        assert out.strip() != ""
        assert "Result" in out

    def test_compat_text_result_contains_result(self):
        # Existing regression (tests/test_cov_agent.py) requires "Result".
        assert "Result" in _orch()._describe_dataframe(pd.DataFrame({"x": [1]}), None, 1)


class TestSinglePeriodNote:
    def test_trend_question_with_period_column_gets_note(self):
        df = pd.DataFrame({"period": ["2026-10-01"], "count": [1], "total_value": [0]})
        out = _orch()._describe_dataframe(df, _plan(TREND_Q), 1)
        assert "single period" in out.lower(), out

    def test_plain_question_with_period_column_gets_no_note(self):
        df = pd.DataFrame({"period": ["2026-10-01"], "count": [1], "total_value": [0]})
        out = _orch()._describe_dataframe(df, _plan("how many redemptions?"), 1)
        assert "single period" not in out.lower(), out

    def test_trend_question_without_period_column_gets_no_note(self):
        df = pd.DataFrame({"count": [1], "total_value": [0]})
        out = _orch()._describe_dataframe(df, _plan(TREND_Q), 1)
        assert "single period" not in out.lower(), out

    def test_none_plan_is_safe(self):
        df = pd.DataFrame({"period": ["2026-10-01"], "count": [1]})
        out = _orch()._describe_dataframe(df, None, 1)
        assert "single period" not in out.lower(), out


class TestTrendNoPeriodCaveat:
    """Trend question whose result has NO time column at all (e.g. the table
    has no date column) must say so instead of silently returning a bare
    summary ("Count 1 / Total value 1")."""

    def test_trend_question_without_time_column_gets_caveat(self):
        df = pd.DataFrame({"count": [1], "total_value": [1]})
        out = _orch()._describe_dataframe(df, _plan(TREND_Q), 1)
        assert "no time/period column" in out.lower(), out

    def test_trend_question_with_time_column_gets_only_single_period_note(self):
        df = pd.DataFrame({"period": ["2026-10-01"], "count": [1], "total_value": [1]})
        out = _orch()._describe_dataframe(df, _plan(TREND_Q), 1)
        assert "single period" in out.lower(), out
        assert "no time/period column" not in out.lower(), out

    def test_plain_question_without_time_column_gets_no_caveat(self):
        df = pd.DataFrame({"count": [1], "total_value": [1]})
        out = _orch()._describe_dataframe(df, _plan("how many redemptions?"), 1)
        assert "no time/period column" not in out.lower(), out

    def test_none_plan_gets_no_caveat(self):
        df = pd.DataFrame({"count": [1], "total_value": [1]})
        out = _orch()._describe_dataframe(df, None, 1)
        assert "no time/period column" not in out.lower(), out

    def test_multirow_result_unaffected(self):
        df = pd.DataFrame({"source": ["a", "b"], "value": [1, 2]})
        out = _orch()._describe_dataframe(df, _plan(TREND_Q), 1)
        assert "no time/period column" not in out.lower(), out


class TestLargeNumberPrecision:
    """Live defect: "Show me the spend by month" rendered the April value as
    `15959.5` (to_markdown truncates to 6 significant digits) and the
    highlights total as `17,499` (_num rounded >=1000 values to integers).
    Both must keep cents."""

    def test_highlights_keep_cents_for_large_totals(self):
        df = pd.DataFrame({"period": ["2025-04", "2025-05"],
                           "value": [15959.460000000001, 1539.33]})
        out = _orch()._describe_dataframe(df, _plan("spend by month"), 1)
        assert "17,498.79" in out, out
        assert "17,499" not in out, out

    def test_multirow_table_keeps_cents(self):
        df = pd.DataFrame({"period": ["2025-04", "2025-05"],
                           "value": [15959.460000000001, 1539.33]})
        out = _orch()._describe_dataframe(df, _plan("spend by month"), 1)
        assert "15,959.46" in out, out
        assert "15959.5" not in out, out

    def test_num_still_integer_formats_whole_thousands(self):
        assert Orchestrator._num(1500) == "1,500"
        assert Orchestrator._num(17498.79) == "17,498.79"


class TestHelpers:
    def test_pretty_label_snake_case(self):
        assert Orchestrator._pretty_label("total_value") == "Total value"
        assert Orchestrator._pretty_label("unique_users") == "Unique users"
        assert Orchestrator._pretty_label("count") == "Count"

    def test_fmt_scalar_matches_legacy_fmt_value(self):
        o = _orch()
        assert o._fmt_scalar(5) == "5"
        assert o._fmt_scalar(0.5) == "0.5"
        assert o._fmt_scalar(2.5) == "2.50"
        assert o._fmt_scalar("str") == "str"

    def test_fmt_value_unchanged_contract(self):
        o = _orch()
        assert "**x**" in o._fmt_value("x", 5) and "5" in o._fmt_value("x", 5)
        assert "x" in o._fmt_value("x", "str")
