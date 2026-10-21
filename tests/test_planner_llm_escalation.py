"""LLM-assisted planner escalation for big/vague questions (RED tests).

Contract under test (`agent/planner.py`):

  1. `create_plan(..., allow_llm=True)` may consult the LLM planner ONLY for
     "big" analysis questions ("full overview", "analyze", "complete report")
     whose rule-based plan is thin (≤1 step, no chart).
  2. Explicit compound chart asks already produce multi SQL+VIZ rule plans and
     must NEVER pay for an LLM round-trip.
  3. Any invalid LLM plan (DML SQL, unknown chart type, dangling depends_on,
     too many steps, malformed JSON, provider error) falls back to the rule
     plan — the planner never executes unvalidated LLM output.
  4. Adopted LLM steps are marked `skip_llm_upgrade` so the orchestrator's SQL
     upgrader cannot rewrite them and break VIZ alias contracts.
"""

from __future__ import annotations

import json

from agent.llm_client import CompletionResponse, Message
from agent.planner import Planner, ToolType

SCHEMA = (
    "TABLE analytics.sales (100 rows):\n"
    "  - region: VARCHAR\n"
    "  - product: VARCHAR\n"
    "  - amount: DOUBLE\n"
    "  - units: DOUBLE\n"
    "  - sale_date: DATE\n"
)

BIG_Q = "give me a full overview of the sales data"
CHART_Q = ("show me a bar chart of amount by region and a line chart of "
           "amount over time")

VALID_SQL = ("SELECT region, SUM(amount) AS total_sales "
             "FROM analytics.sales GROUP BY region")


class RecordingLLM:
    """Minimal planner LLM: records every prompt, returns canned content."""

    def __init__(self, content: str = '{"reasoning":"r","steps":[]}', exc=None):
        self.content = content
        self.exc = exc
        self.calls: list = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return CompletionResponse(message=Message(role="assistant", content=self.content))

    def chat_sync(self, messages, options=None):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return CompletionResponse(message=Message(role="assistant", content=self.content))


def _planner(llm=None) -> Planner:
    return Planner(llm_client=llm, schema_context=SCHEMA)


def _valid_llm_plan() -> str:
    return json.dumps({
        "reasoning": "LLM multi-step analysis plan",
        "steps": [
            {"step_id": 1, "tool": "sql", "description": "Totals by region",
             "params": {"query": VALID_SQL}, "depends_on": []},
            {"step_id": 2, "tool": "viz",
             "description": "Bar chart of total sales by region",
             "params": {"chart_type": "bar", "x": "region", "y": "total_sales"},
             "depends_on": [1]},
        ],
    })


# ─────────────────────────────────────────────────────────────
# Escalation policy
# ─────────────────────────────────────────────────────────────

class TestEscalationPolicy:
    def test_big_questions_are_detected(self):
        p = _planner()
        assert p._is_big_analysis_question(BIG_Q) is True
        assert p._is_big_analysis_question("analyze the sales data") is True
        assert p._is_big_analysis_question("give me a complete report on sales") is True
        assert p._is_big_analysis_question("comprehensive breakdown of sales") is True

    def test_narrow_questions_are_not_big(self):
        p = _planner()
        assert p._is_big_analysis_question("what is the total amount?") is False
        assert p._is_big_analysis_question("show amount by region") is False
        assert p._is_big_analysis_question("how many rows are in sales") is False

    def test_thin_plan_detection(self):
        p = _planner()
        thin = p.create_plan(BIG_Q, ["sales"])
        assert p._plan_is_thin(thin) is True, (
            f"'full overview' rules plan should be thin, got {len(thin.steps)} steps"
        )
        rich = p.create_plan(CHART_Q, ["sales"])
        assert p._plan_is_thin(rich) is False, "chart plans (SQL+VIZ) are not thin"

    def test_allow_llm_false_never_consults_llm(self):
        llm = RecordingLLM(exc=AssertionError("LLM must not be called"))
        p = _planner(llm)
        plan = p.create_plan(BIG_Q, ["sales"], allow_llm=False)
        assert llm.calls == []
        assert plan.steps

    def test_default_allow_llm_is_off(self):
        llm = RecordingLLM(exc=AssertionError("LLM must not be called"))
        p = _planner(llm)
        plan = p.create_plan(BIG_Q, ["sales"])
        assert llm.calls == []
        assert plan.steps

    def test_allow_llm_without_llm_client_is_safe(self):
        p = _planner(None)
        plan = p.create_plan(BIG_Q, ["sales"], allow_llm=True)
        assert plan.steps


# ─────────────────────────────────────────────────────────────
# Escalation adoption
# ─────────────────────────────────────────────────────────────

class TestEscalationAdoption:
    def test_big_thin_question_adopts_valid_llm_plan(self):
        llm = RecordingLLM(_valid_llm_plan())
        p = _planner(llm)
        plan = p.create_plan(BIG_Q, ["sales"], allow_llm=True)

        assert llm.calls, "the LLM planner should have been consulted"
        assert plan.question == BIG_Q
        assert plan.reasoning == "LLM multi-step analysis plan"
        assert [s.tool for s in plan.steps] == [ToolType.SQL, ToolType.VIZ]
        assert plan.steps[1].params.get("chart_type") == "bar"

    def test_adopted_steps_are_marked_skip_llm_upgrade(self):
        p = _planner(RecordingLLM(_valid_llm_plan()))
        plan = p.create_plan(BIG_Q, ["sales"], allow_llm=True)
        assert plan.steps
        assert all(s.metadata.get("skip_llm_upgrade") for s in plan.steps), (
            "LLM-planned steps must not be rewritten by the orchestrator's "
            "SQL upgrader (it would break the VIZ x/y alias contract)"
        )

    def test_chart_request_never_consults_llm(self):
        llm = RecordingLLM(exc=AssertionError("LLM must not be called"))
        p = _planner(llm)
        plan = p.create_plan(CHART_Q, ["sales"], allow_llm=True)
        assert llm.calls == [], (
            "explicit multi-chart asks already have a rich rule plan — no LLM"
        )
        tools = [s.tool for s in plan.steps]
        assert tools.count(ToolType.VIZ) >= 2

    def test_llm_step_ids_are_renumbered_sequentially(self):
        llm = RecordingLLM(json.dumps({"reasoning": "r", "steps": [
            {"step_id": 7, "tool": "sql", "description": "d",
             "params": {"query": VALID_SQL}, "depends_on": []},
            {"step_id": 9, "tool": "viz", "description": "v",
             "params": {"chart_type": "bar", "x": "region", "y": "total_sales"},
             "depends_on": [7]},
        ]}))
        p = _planner(llm)
        plan = p.create_plan(BIG_Q, ["sales"], allow_llm=True)
        assert [s.step_id for s in plan.steps] == [1, 2]
        assert plan.steps[1].depends_on == [1]


# ─────────────────────────────────────────────────────────────
# Invalid LLM output → rule fallback
# ─────────────────────────────────────────────────────────────

def _adopted_plan(llm_content: str):
    p = _planner(RecordingLLM(llm_content))
    return p.create_plan(BIG_Q, ["sales"], allow_llm=True)


class TestInvalidLLMPlans:
    def test_drop_statement_rejected(self):
        plan = _adopted_plan(json.dumps({"steps": [
            {"step_id": 1, "tool": "sql", "description": "bad",
             "params": {"query": "DROP TABLE analytics.sales"}},
        ]}))
        assert "Rule-based" in plan.reasoning
        assert all("DROP" not in (s.params.get("query") or "").upper()
                   for s in plan.steps)

    def test_update_statement_rejected(self):
        plan = _adopted_plan(json.dumps({"steps": [
            {"step_id": 1, "tool": "sql", "description": "bad",
             "params": {"query": "UPDATE analytics.sales SET amount = 0"}},
        ]}))
        assert "Rule-based" in plan.reasoning
        assert all("UPDATE" not in (s.params.get("query") or "").upper()
                   for s in plan.steps)

    def test_multi_statement_sql_rejected(self):
        plan = _adopted_plan(json.dumps({"steps": [
            {"step_id": 1, "tool": "sql", "description": "bad",
             "params": {"query": "SELECT 1 AS x; DROP TABLE analytics.sales"}},
        ]}))
        assert "Rule-based" in plan.reasoning

    def test_unknown_chart_type_dropped_keeps_sql(self):
        plan = _adopted_plan(json.dumps({"reasoning": "llm", "steps": [
            {"step_id": 1, "tool": "sql", "description": "ok",
             "params": {"query": VALID_SQL}, "depends_on": []},
            {"step_id": 2, "tool": "viz", "description": "bad chart",
             "params": {"chart_type": "donut3d", "x": "region", "y": "total_sales"},
             "depends_on": [1]},
        ]}))
        assert ToolType.VIZ not in [s.tool for s in plan.steps]
        assert [s.tool for s in plan.steps] == [ToolType.SQL]

    def test_dangling_depends_on_viz_dropped(self):
        plan = _adopted_plan(json.dumps({"reasoning": "llm", "steps": [
            {"step_id": 1, "tool": "sql", "description": "ok",
             "params": {"query": VALID_SQL}, "depends_on": []},
            {"step_id": 2, "tool": "viz", "description": "orphan chart",
             "params": {"chart_type": "bar", "x": "region", "y": "total_sales"},
             "depends_on": [99]},
        ]}))
        assert ToolType.VIZ not in [s.tool for s in plan.steps]

    def test_scatter_without_y_axis_dropped(self):
        plan = _adopted_plan(json.dumps({"reasoning": "llm", "steps": [
            {"step_id": 1, "tool": "sql", "description": "ok",
             "params": {"query": VALID_SQL}, "depends_on": []},
            {"step_id": 2, "tool": "viz", "description": "scatter",
             "params": {"chart_type": "scatter", "x": "region"},
             "depends_on": [1]},
        ]}))
        assert ToolType.VIZ not in [s.tool for s in plan.steps]

    def test_too_many_steps_rejects_whole_plan(self):
        steps = [{"step_id": i, "tool": "sql", "description": f"s{i}",
                  "params": {"query": "SELECT 1 AS x"}} for i in range(1, 9)]
        plan = _adopted_plan(json.dumps({"reasoning": "llm", "steps": steps}))
        assert "Rule-based" in plan.reasoning
        assert len(plan.steps) <= 6

    def test_malformed_json_falls_back_to_rules(self):
        plan = _adopted_plan("not json at all {{{")
        assert "Rule-based" in plan.reasoning
        assert plan.steps

    def test_provider_exception_falls_back_to_rules(self):
        p = _planner(RecordingLLM(exc=RuntimeError("provider down")))
        plan = p.create_plan(BIG_Q, ["sales"], allow_llm=True)
        assert "Rule-based" in plan.reasoning
        assert plan.steps

    def test_empty_steps_fall_back_to_rules(self):
        plan = _adopted_plan(json.dumps({"reasoning": "r", "steps": []}))
        assert "Rule-based" in plan.reasoning
        assert plan.steps


# ─────────────────────────────────────────────────────────────
# Prompt teaches the LLM to emit VIZ steps
# ─────────────────────────────────────────────────────────────

class TestPromptContract:
    def test_planning_prompt_teaches_viz_steps(self):
        llm = RecordingLLM('{"reasoning":"r","steps":[]}')
        p = _planner(llm)
        p._llm_plan("ctx", BIG_Q, ["sales"])
        assert llm.calls, "_llm_plan must call chat_sync"
        prompt = llm.calls[0][0].content
        assert '"viz"' in prompt, "planning prompt must document viz steps"
        assert "chart_type" in prompt, "planning prompt must document chart_type"
        assert "analytics." in prompt

    def test_llm_plan_direct_call_contract_preserved(self):
        # tests/test_cov_agent2.py::test_llm_plan_and_factory depends on this:
        # direct _llm_plan() keeps ids/tools as-is and never sanitizes.
        p = Planner(llm_client=RecordingLLM(json.dumps({"reasoning": "r", "steps": [
            {"step_id": 9, "tool": "weird", "description": "d", "params": {}},
        ]})), use_llm_plan=True)
        out = p._llm_plan("ctx", "q")
        assert out.steps and out.steps[0].step_id == 9
