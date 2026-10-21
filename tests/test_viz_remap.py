"""Viz x/y remap: chart requests must not fail on alias drift.

The planner picks chart axes for its own rule-SQL aliases (`value`,
`group_name`, `period`), but the SQL step is later upgraded by the LLM SQL
generator, which may return different aliases (`total_spend`, `source`, ...).
Plotly then raised "Value of 'x' is not the name of a column" and the chart
silently disappeared from the answer (2 of 5 verified answers).

Contract: `Orchestrator._remap_viz_xy` (and `_execute_viz_step` via it) must
fall back to suitable real columns instead of passing a missing name to the
chart engine.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agent.orchestrator import Orchestrator
from agent.planner import PlanStep, ToolType
from agent.tools_sql import SQLResult
from agent.tools_viz import VizResult


def _orch() -> Orchestrator:
    return Orchestrator(None, None, None, None, None)


class TestRemapVizXy:
    def test_valid_columns_unchanged(self):
        df = pd.DataFrame({"period": ["a", "b"], "value": [1, 2]})
        assert Orchestrator._remap_viz_xy(df, "line", "period", "value") == ("period", "value")

    def test_valid_x_none_y_kept_unchanged(self):
        df = pd.DataFrame({"period": ["a"], "value": [1]})
        assert Orchestrator._remap_viz_xy(df, "bar", "period", None) == ("period", None)

    def test_numeric_only_alias_drift(self):
        # trend result with count/total_value while the chart expects period/value
        df = pd.DataFrame({"count": [1], "total_value": [1]})
        x, y = Orchestrator._remap_viz_xy(df, "line", "period", "value")
        assert x == "count" and y == "total_value", (x, y)

    def test_dimension_metric_alias_drift(self):
        # top-N result: source/total_spend/max_value vs value/group_name
        df = pd.DataFrame({"source": ["Google Ads"], "total_spend": [5707.03], "max_value": [5707.03]})
        x, y = Orchestrator._remap_viz_xy(df, "bar", "value", "group_name")
        assert x == "source" and y == "total_spend", (x, y)

    def test_histogram_uses_first_numeric(self):
        df = pd.DataFrame({"spend": [1.0, 2.0]})
        assert Orchestrator._remap_viz_xy(df, "histogram", "value", None) == ("spend", None)

    def test_scatter_maps_numeric_pair(self):
        df = pd.DataFrame({"clicks": [1, 2], "spend": [1.0, 2.0]})
        x, y = Orchestrator._remap_viz_xy(df, "scatter", "x_val", "y_val")
        assert x == "clicks" and y == "spend", (x, y)

    def test_no_numeric_columns_still_picks_dimension(self):
        df = pd.DataFrame({"source": ["a", "b"]})
        x, y = Orchestrator._remap_viz_xy(df, "bar", "value", "group_name")
        assert x == "source" and y is None, (x, y)

    def test_y_never_equals_x(self):
        df = pd.DataFrame({"count": [1], "total_value": [2]})
        x, y = Orchestrator._remap_viz_xy(df, "bar", "count", "period")
        assert x == "count" and y == "total_value", (x, y)


class TestExecuteVizStepRemap:
    @pytest.mark.asyncio
    async def test_alias_drift_still_renders_chart(self):
        recorded = {}

        class StubViz:
            def create_chart(self, data, chart_type, **kw):
                recorded.update(kw)
                recorded["chart_type"] = chart_type
                return VizResult(success=True)

        o = _orch()
        o.viz_tool = StubViz()
        df = pd.DataFrame({"source": ["Google Ads"], "total_spend": [5707.03]})
        step = PlanStep(2, ToolType.VIZ, "bar chart",
                        {"chart_type": "bar", "x": "value", "y": "group_name"},
                        depends_on=[1])
        out = await o._execute_viz_step(step, {1: SQLResult(success=True, data=df)})
        assert out.success is True
        assert recorded["x"] == "source" and recorded["y"] == "total_spend", recorded

    @pytest.mark.asyncio
    async def test_valid_axes_pass_through(self):
        recorded = {}

        class StubViz:
            def create_chart(self, data, chart_type, **kw):
                recorded.update(kw)
                return VizResult(success=True)

        o = _orch()
        o.viz_tool = StubViz()
        df = pd.DataFrame({"period": ["a"], "value": [1]})
        step = PlanStep(2, ToolType.VIZ, "line chart",
                        {"chart_type": "line", "x": "period", "y": "value"},
                        depends_on=[1])
        out = await o._execute_viz_step(step, {1: SQLResult(success=True, data=df)})
        assert out.success is True
        assert recorded["x"] == "period" and recorded["y"] == "value", recorded
