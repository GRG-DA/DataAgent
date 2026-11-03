"""Chat Pro task pipeline: stream contract + per-step RBAC."""

import asyncio
from dataclasses import dataclass

from agent.orchestrator import Orchestrator
from agent.planner import ToolType
from agent.tools_tasks import TaskResult


@dataclass
class _User:
    role: str = "admin"
    username: str = "tester"
    id: str = "u1"
    scopes: set = None

    def __post_init__(self):
        if self.scopes is None:
            self.scopes = set()


def test_step_event_payload_carries_artifact():
    from api.main import _step_event_payload

    payload = _step_event_payload({
        "kind": "step_done",
        "step_id": 2,
        "tool": "task",
        "ok": True,
        "ms": 12.0,
        "artifact": {"kind": "dashboard", "id": "d1", "url": "#blankdash/d1"},
    })
    assert payload["artifact"]["id"] == "d1"


def test_step_event_payload_without_artifact_unchanged():
    from api.main import _step_event_payload

    payload = _step_event_payload({
        "kind": "step_done", "step_id": 1, "tool": "sql", "ok": True, "ms": 3.0,
    })
    assert "artifact" not in payload


def test_build_dashboard_requires_permission(monkeypatch):
    from agent import tools_tasks

    called = []

    def fake_create(*args, **kwargs):
        called.append(kwargs)
        return {"id": "d1", "name": "n", "url": "#blankdash/d1", "widget_count": 3}

    monkeypatch.setattr(
        "api.dashboard_build.create_dashboard_for_table", fake_create
    )
    out = tools_tasks.build_dashboard("task_sales", user=_User(role="viewer"))
    assert out.success is False
    assert "not allowed" in out.error
    assert not called


def test_execute_task_step_uses_cleaned_source_table(monkeypatch):
    from agent import tools_tasks

    seen = {}

    def fake_build(table, **kwargs):
        seen["table"] = table
        seen["owner_id"] = kwargs.get("owner_id")
        return TaskResult(success=True, data={"artifact": {"kind": "dashboard"}})

    monkeypatch.setattr(tools_tasks, "build_dashboard", fake_build)
    orch = object.__new__(Orchestrator)

    class _Step:
        params = {"operation": "build_dashboard", "source_step": 1}

    prev = TaskResult(
        success=True, data={"output_table": "task_sales_clean"}
    )
    out = asyncio.run(
        orch._execute_task_step(_Step(), {1: prev}, _User(role="admin"))
    )
    assert out.success
    assert seen["table"] == "task_sales_clean"
    assert seen["owner_id"] == "u1"


def test_execute_task_step_denies_viewer():
    orch = object.__new__(Orchestrator)

    class _Step:
        params = {"operation": "build_dashboard", "table": "task_sales"}

    out = asyncio.run(
        orch._execute_task_step(_Step(), {}, _User(role="viewer"))
    )
    assert out.success is False
    assert "not allowed" in out.error


def test_task_tool_type_is_registered():
    assert ToolType.TASK.value == "task"


def test_user_identifier_prefers_request_user_id():
    from agent.tools_tasks import _user_identifier

    class _RequestUser:
        user_id = "real-user-id"
        username = "mcp_tester"

    assert _user_identifier(_RequestUser()) == "real-user-id"


def test_task_pipeline_end_to_end(billing_db):
    import pandas as pd
    import api.state as state
    from config import settings
    from agent.planner import Planner
    from agent.tools_pandas import PandasTool
    from agent.tools_sql import SQLTool
    from agent.tools_viz import VizTool

    rows = [
        {"sold_at": f"2025-{m:02d}-01", "region": region, "amount": amount}
        for m in range(1, 13)
        for region, amount in (("west", 100.0 + m), ("east", 80.0 + m * 2))
    ]
    state.loader.load_dataframe(
        pd.DataFrame(rows), "task_sales", if_exists="replace", tenant_id="default"
    )

    planner = Planner()
    planner.set_schema_context(
        "TABLE analytics.task_sales (24 rows):\n"
        "  - sold_at: VARCHAR\n  - region: VARCHAR\n  - amount: DOUBLE\n"
    )
    orch = Orchestrator(
        llm_client=None,
        sql_tool=SQLTool(
            db_path=settings.duckdb.database_path, schema="analytics"
        ),
        pandas_tool=PandasTool(),
        viz_tool=VizTool(),
        planner=planner,
        timeout_seconds=30.0,
    )
    events = []
    resp = asyncio.run(orch.answer_question(
        "clean task_sales and build a dashboard from it",
        available_tables=["task_sales"],
        user=_User(role="admin"),
        progress=events.append,
    ))

    assert resp.success, resp.error or resp.final_answer
    assert [a["kind"] for a in resp.artifacts] == ["table", "dashboard"]
    assert "#blankdash/" in resp.final_answer
    assert state.loader.table_exists("task_sales_clean")
    done_events = [e for e in events if e.get("kind") == "step_done"]
    assert any("artifact" in e for e in done_events)
