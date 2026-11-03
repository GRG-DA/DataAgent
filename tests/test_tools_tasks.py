"""TDD: Chat Pro task pipeline (clean / dashboard / forecast)."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest


class _User:
    def __init__(self, role: str = "admin", username: str = "tester", uid: str = "u1"):
        self.role = role
        self.username = username
        self.id = uid
        self.scopes = set()


def _seed(loader, name: str = "task_sales"):
    dates = pd.date_range("2024-01-01", periods=24, freq="MS").repeat(2)
    df = pd.DataFrame(
        {
            "sold_at": dates,
            "region": ["east", "west"] * 24,
            "amount": [100.0 + i * 5 for i in range(48)],
        }
    )
    loader.load_dataframe(df, name, if_exists="replace", tenant_id="default")
    return df


# ── Planner routing ──────────────────────────────────────────

def _planner():
    from agent.planner import Planner

    planner = Planner()
    planner.set_schema_context(
        "TABLE analytics.task_sales (48 rows):\n"
        "  - sold_at: TIMESTAMP\n"
        "  - region: VARCHAR\n"
        "  - amount: DOUBLE\n"
    )
    return planner


def test_planner_clean_then_dashboard_plan():
    from agent.planner import ToolType

    plan = _planner().create_plan(
        "clean the task_sales table and build a dashboard from it",
        available_tables=["task_sales"],
    )
    assert [s.tool for s in plan.steps] == [ToolType.TASK, ToolType.TASK]
    assert plan.steps[0].params["operation"] == "clean_table"
    assert plan.steps[0].params["table"] == "task_sales"
    assert plan.steps[1].params["operation"] == "build_dashboard"
    assert plan.steps[1].params["source_step"] == plan.steps[0].step_id


def test_planner_dashboard_only_plan():
    from agent.planner import ToolType

    plan = _planner().create_plan(
        "build a dashboard for task_sales", available_tables=["task_sales"]
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].tool == ToolType.TASK
    assert plan.steps[0].params["operation"] == "build_dashboard"


def test_planner_forecast_plan_parses_horizon():
    from agent.planner import ToolType

    plan = _planner().create_plan(
        "forecast task_sales for the next 6 months", available_tables=["task_sales"]
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].tool == ToolType.TASK
    assert plan.steps[0].params["operation"] == "forecast"
    assert plan.steps[0].params["periods"] == 6


def test_planner_forecast_horizon_accepts_periods_word():
    from agent.planner import ToolType

    plan = _planner().create_plan(
        "forecast task_sales for the next 4 periods", available_tables=["task_sales"]
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].tool == ToolType.TASK
    assert plan.steps[0].params["operation"] == "forecast"
    assert plan.steps[0].params["periods"] == 4


def test_planner_data_quality_question_stays_read_only():
    from agent.planner import ToolType

    plan = _planner().create_plan(
        "how clean is the task_sales data?", available_tables=["task_sales"]
    )
    assert plan.steps
    assert all(s.tool == ToolType.SQL for s in plan.steps)


# ── Executors ────────────────────────────────────────────────

def test_clean_table_persists_new_table(billing_db):
    import api.state as state
    from agent.tools_tasks import clean_table

    _seed(state.loader)
    result = clean_table("task_sales", user=_User())
    assert result.success, result.error
    assert result.data["output_table"] == "task_sales_clean"
    assert state.loader.table_exists("task_sales_clean")
    assert state.loader.table_exists("task_sales")
    assert result.message and "task_sales_clean" in result.message
    assert result.row_count == 48


def test_clean_table_requires_write_permission(billing_db):
    import api.state as state
    from agent.tools_tasks import clean_table

    _seed(state.loader)
    result = clean_table("task_sales", user=_User(role="viewer"))
    assert result.success is False
    assert "not allowed" in (result.error or "")
    assert not state.loader.table_exists("task_sales_clean")


def test_build_dashboard_saves_artifact(billing_db):
    import api.state as state
    from agent.tools_tasks import build_dashboard
    from api.dashboards import DashboardManager

    _seed(state.loader)
    result = build_dashboard("task_sales", user=_User(), owner_id="", name="Task dash")
    assert result.success, result.error
    art = result.data["artifact"]
    assert art["kind"] == "dashboard"
    assert art["url"] == f"#blankdash/{art['id']}"
    dash = DashboardManager().get(art["id"], allow_all=True)
    assert dash["layout"]["_blankdash"]["table"] == "task_sales"


def test_forecast_table_returns_horizon(billing_db):
    import api.state as state
    from agent.tools_tasks import forecast_table

    _seed(state.loader)
    result = forecast_table("task_sales", user=_User(), periods=6)
    assert result.success, result.error
    assert len(result.data["forecast_values"]) == 6
    assert "forecast" in result.message.lower()


# ── Orchestrator wiring ──────────────────────────────────────

def test_rule_answer_includes_task_message():
    from agent.orchestrator import Orchestrator, AgentResponse, StepResult
    from agent.planner import ExecutionPlan, ToolType
    from agent.tools_tasks import TaskResult

    orch = object.__new__(Orchestrator)
    resp = AgentResponse(
        question="q",
        plan=ExecutionPlan(question="q", steps=[], estimated_steps=0),
        step_results=[
            StepResult(
                step_id=1,
                tool=ToolType.TASK,
                success=True,
                result=TaskResult(success=True, message="Cleaned a -> b"),
            )
        ],
        final_answer="",
    )
    out = orch._rule_answer(resp)
    assert "Cleaned a -> b" in out


def test_build_answer_keeps_task_message_unpolished():
    from agent.orchestrator import Orchestrator, AgentResponse, StepResult
    from agent.planner import ExecutionPlan, ToolType
    from agent.tools_tasks import TaskResult

    orch = object.__new__(Orchestrator)
    orch.llm_client = object()
    resp = AgentResponse(
        question="q",
        plan=ExecutionPlan(question="q", steps=[], estimated_steps=0),
        step_results=[
            StepResult(
                step_id=1,
                tool=ToolType.TASK,
                success=True,
                result=TaskResult(success=True, message="Saved dashboard"),
            )
        ],
        final_answer="",
    )
    assert asyncio.run(orch._build_answer(resp)) == "Saved dashboard"
