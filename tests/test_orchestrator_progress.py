"""Orchestrator progress events + planner escalation plumbing (RED tests).

Contract under test (`agent/orchestrator.py`):

  1. `answer_question(..., progress=cb)` reports the pipeline live:
     `plan` → `step_start`/`step_done` per step → `viz` per built chart →
     `answering`. Events arrive BEFORE the final answer is built.
  2. `progress=None` (default) is a no-op — existing callers unchanged.
  3. A crashing progress callback never breaks the answer.
  4. `allow_llm_plan=True` escalates big vague questions to the planner's LLM
     through a thread with a bounded timeout; on timeout/error the rule plan
     is executed instead.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = pytest.mark.asyncio

CHART_Q = ("show me a bar chart of amount by region and a line chart of "
           "amount over time")
BIG_Q = "give me a full overview of the sales data"

SCHEMA = ("TABLE analytics.sales (3 rows):\n"
          "  - region: VARCHAR\n"
          "  - amount: DOUBLE\n"
          "  - units: DOUBLE\n"
          "  - sale_date: DATE\n")

VALID_LLM_PLAN = (
    '{"reasoning":"LLM multi-step analysis plan","steps":['
    '{"step_id":1,"tool":"sql","description":"Totals by region",'
    '"params":{"query":"SELECT region, SUM(amount) AS total_sales '
    'FROM analytics.sales GROUP BY region"},"depends_on":[]},'
    '{"step_id":2,"tool":"viz","description":"Bar chart",'
    '"params":{"chart_type":"bar","x":"region","y":"total_sales"},'
    '"depends_on":[1]}]}'
)


class SyncFakeLLM:
    """Planner-side LLM: sync `chat_sync` (planner calls it directly)."""

    def __init__(self, content: str = VALID_LLM_PLAN, delay: float = 0.0):
        from agent.llm_client import CompletionResponse, Message
        self._resp = CompletionResponse(
            message=Message(role="assistant", content=content))
        self.delay = delay
        self.calls: list = []

    async def chat(self, messages, **kwargs):
        return self._resp

    def chat_sync(self, messages, options=None):
        self.calls.append(messages)
        if self.delay:
            time.sleep(self.delay)
        return self._resp


@pytest.fixture
def orch(tmp_path):
    import duckdb
    from agent.memory import ConversationMemory
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from agent.tools_pandas import PandasTool
    from agent.tools_sql import SQLTool
    from agent.tools_viz import VizTool

    def _make(planner_llm=None):
        dbp = str(tmp_path / "prog.duckdb")
        con = duckdb.connect(dbp)
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            "CREATE TABLE analytics.sales "
            "(region VARCHAR, amount DOUBLE, units DOUBLE, sale_date DATE)")
        con.execute("DELETE FROM analytics.sales")
        con.execute(
            "INSERT INTO analytics.sales VALUES "
            "('west', 10.0, 2.0, DATE '2026-01-01'),"
            "('east', 20.0, 4.0, DATE '2026-02-01'),"
            "('west', 5.0, 1.0, DATE '2026-02-15')")
        con.close()
        planner = Planner(llm_client=planner_llm)
        planner.set_schema_context(SCHEMA)
        o = Orchestrator(
            llm_client=None,
            sql_tool=SQLTool(db_path=dbp, schema="analytics"),
            pandas_tool=PandasTool(),
            viz_tool=VizTool(output_dir=str(tmp_path / "charts")),
            planner=planner,
            timeout_seconds=15.0,
        )
        o.set_memory(ConversationMemory(), "sess-progress")
        return o

    return _make


# ─────────────────────────────────────────────────────────────
# Progress event stream
# ─────────────────────────────────────────────────────────────

async def test_progress_events_cover_plan_steps_viz_and_answering(orch):
    o = orch()
    events: list = []

    def cb(ev):
        events.append(ev)

    resp = await o.answer_question(CHART_Q, available_tables=["sales"], progress=cb)
    assert resp.success, resp.error or resp.final_answer

    kinds = [e.get("kind") for e in events]
    assert kinds, "progress callback received no events"
    assert kinds[0] == "plan"
    assert kinds[-1] == "answering"

    plan_ev = events[0]
    assert len(plan_ev["steps"]) == 4
    assert all({"id", "tool", "description"} <= set(s) for s in plan_ev["steps"])

    starts = [e["step_id"] for e in events if e["kind"] == "step_start"]
    dones = [e["step_id"] for e in events if e["kind"] == "step_done"]
    assert starts == [1, 2, 3, 4]
    assert dones == [1, 2, 3, 4]

    for e in events:
        if e["kind"] == "step_start":
            assert e.get("tool") in ("sql", "viz", "pandas", "llm")

    sql_dones = [e for e in events if e["kind"] == "step_done" and e.get("tool") == "sql"]
    assert sql_dones and all(isinstance(e.get("rows"), int) for e in sql_dones), (
        "SQL step_done events must carry a row count for the UI"
    )
    assert all(isinstance(e.get("ms"), (int, float)) for e in events
               if e["kind"] == "step_done")

    viz_events = [e for e in events if e["kind"] == "viz"]
    assert len(viz_events) == 2, f"expected one viz event per chart, got {len(viz_events)}"
    for ve in viz_events:
        result = ve["result"]
        assert result.success is True
        assert getattr(result, "html", ""), "VizResult must carry html"
        done_idx = next(i for i, e in enumerate(events)
                        if e["kind"] == "step_done" and e["step_id"] == ve["step_id"])
        assert events.index(ve) > done_idx

    assert len(resp.visualizations) >= 2


async def test_progress_none_keeps_existing_behavior(orch):
    o = orch()
    resp = await o.answer_question(CHART_Q, available_tables=["sales"])
    assert resp.success
    assert len(resp.visualizations) >= 2


async def test_crashing_progress_callback_never_breaks_the_answer(orch):
    o = orch()

    def cb(ev):
        raise RuntimeError("UI exploded")

    resp = await o.answer_question(CHART_Q, available_tables=["sales"], progress=cb)
    assert resp.success, "a broken progress callback must not fail the analysis"
    assert len(resp.visualizations) >= 2


# ─────────────────────────────────────────────────────────────
# allow_llm_plan escalation plumbing
# ─────────────────────────────────────────────────────────────

async def test_allow_llm_plan_escalates_to_planner_llm(orch):
    llm = SyncFakeLLM()
    o = orch(planner_llm=llm)
    events: list = []

    def cb(ev):
        events.append(ev)

    resp = await o.answer_question(
        BIG_Q, available_tables=["sales"], progress=cb, allow_llm_plan=True)
    assert resp.success, resp.error or resp.final_answer
    assert llm.calls, "the planner LLM should have been consulted"
    assert resp.plan is not None and len(resp.plan.steps) == 2
    assert resp.plan.reasoning == "LLM multi-step analysis plan"
    assert all(s.metadata.get("skip_llm_upgrade") for s in resp.plan.steps)

    plan_ev = next(e for e in events if e.get("kind") == "plan")
    assert len(plan_ev["steps"]) == 2


async def test_default_answer_question_does_not_escalate(orch):
    llm = SyncFakeLLM()
    o = orch(planner_llm=llm)
    resp = await o.answer_question(BIG_Q, available_tables=["sales"])
    assert resp.success
    assert llm.calls == [], "escalation must be opt-in (allow_llm_plan=False default)"


async def test_slow_llm_plan_times_out_to_rules(orch):
    llm = SyncFakeLLM(delay=1.0)
    o = orch(planner_llm=llm)
    o.plan_llm_timeout_seconds = 0.05

    started = time.monotonic()
    resp = await o.answer_question(
        BIG_Q, available_tables=["sales"], allow_llm_plan=True)
    elapsed = time.monotonic() - started

    assert resp.success, resp.error or resp.final_answer
    assert llm.calls, "the planner LLM was attempted"
    assert "Rule-based" in (resp.plan.reasoning or ""), (
        "on planner-LLM timeout the rule plan must be executed instead"
    )
    assert elapsed < 0.9, f"planning must not block on the slow LLM ({elapsed:.2f}s)"
