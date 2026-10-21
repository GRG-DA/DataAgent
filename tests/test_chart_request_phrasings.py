"""Chart-request phrasing coverage.

Follow-up bug: "chart of boxes by product" produced a SQL-only plan
(intent=general, 0 VIZ steps). Root cause: _CHART_KIND_RE only matched
type-anchored phrasings ("bar chart of X", "line graph of X"), so a bare
"chart of X by Y" / imperative "plot X" / "visualize X" never reached
_chart_request_plan.

RED: these tests fail against the current pattern list.
GREEN: after adding a generic chart-ask fallback pattern (ordered last so
specific phrasings keep winning).
"""
from agent.planner import Planner

SCHEMA = (
    "TABLE analytics.chocolate_sales (4 rows):\n"
    "  - region: VARCHAR\n"
    "  - product: VARCHAR\n"
    "  - amount: DOUBLE\n"
    "  - boxes: INTEGER\n"
)
TABLES = ["chocolate_sales"]


def plan_for(question: str):
    p = Planner()
    p.set_schema_context(SCHEMA)
    return p.create_plan(question, TABLES)


def tools(plan):
    return [str(s.tool) for s in plan.steps]


def test_chart_of_measure_by_dimension_gets_sql_and_bar_viz():
    plan = plan_for("chart of boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.reasoning.startswith("Chart-request plan")
    sql, viz = plan.steps
    assert "product" in sql.params["query"].lower()
    assert viz.depends_on == [sql.step_id]
    assert viz.params["chart_type"] == "bar"
    assert viz.params["x"] == "group_name"
    assert viz.params["y"] == "total_value"


def test_show_a_chart_of_still_gets_viz():
    plan = plan_for("show a chart of boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.reasoning.startswith("Chart-request plan")


def test_graph_of_measure_by_dimension_gets_viz():
    plan = plan_for("graph of boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.reasoning.startswith("Chart-request plan")


def test_plot_imperative_gets_viz():
    plan = plan_for("plot boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.reasoning.startswith("Chart-request plan")


def test_visualize_imperative_gets_viz():
    plan = plan_for("visualize boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.reasoning.startswith("Chart-request plan")


def test_type_first_phrasing_still_wins_bar():
    plan = plan_for("bar chart of boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.reasoning.startswith("Chart-request plan")
    assert plan.steps[1].params["chart_type"] == "bar"


def test_type_first_phrasing_still_wins_line():
    plan = plan_for("line chart of boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.steps[1].params["chart_type"] == "line"


def test_non_chart_ask_gets_no_chart_plan():
    plan = plan_for("what is the total boxes")
    assert tools(plan) == ["ToolType.SQL"]
    assert not plan.reasoning.startswith("Chart-request plan")


def test_compare_phrasing_unchanged():
    plan = plan_for("compare boxes by product")
    assert tools(plan) == ["ToolType.SQL", "ToolType.VIZ"]
    assert plan.reasoning.startswith("Rule-based plan: intent=comparison")
