"""Multi-turn chat: conversation history must reach the analytical pipeline.

Issue: `ConversationMemory.get_context()` had zero call sites — turns were
written by `_remember()` but never read back, and the client-supplied
`context["history"]` was only consumed on the small-talk branch of
`/llm/chat/stream`. So a follow-up like "What is my name?" was planned as a
data question and answered with a sales table instead of recalling the prior
turn.

Guarantees:
  1. A recall-style question with history is answered from the conversation.
  2. A genuine data question with history still goes down the analytical path.
  3. History merges server-side memory with the client-supplied history.
  4. The answer polish pass receives the history.
"""

from __future__ import annotations

import pytest

from agent.llm_client import CompletionResponse, Message
from agent.memory import ConversationMemory
from agent.orchestrator import Orchestrator
from agent.planner import Planner
from agent.tools_pandas import PandasTool
from agent.tools_sql import SQLTool
from agent.tools_viz import VizTool


class RecordingLLM:
    """Fake LLM: records every request, replies with a canned string."""

    def __init__(self, reply: str = "OK"):
        self.calls: list[list[Message]] = []
        self.reply = reply

    async def chat(self, messages, **kwargs):
        self.calls.append([
            m if isinstance(m, Message) else Message(**m) for m in messages
        ])
        return CompletionResponse(
            message=Message(role="assistant", content=self.reply)
        )

    @property
    def system_prompts(self) -> list[str]:
        return [
            m.content
            for call in self.calls
            for m in call
            if m.role == "system"
        ]


@pytest.fixture
def orch(tmp_path):
    planner = Planner()
    # Hermetic copy of what `_refresh_planner_schema` builds from the real
    # warehouse, so data questions get planned without touching DuckDB.
    planner.set_schema_context(
        "TABLE analytics.chocolate_sales (900 rows):\n"
        "  - region: VARCHAR\n"
        "  - product: VARCHAR\n"
        "  - amount: DOUBLE\n"
        "  - boxes: INTEGER\n"
    )
    o = Orchestrator(
        llm_client=None,
        sql_tool=SQLTool(db_path=str(tmp_path / "t.duckdb"), schema="analytics"),
        pandas_tool=PandasTool(),
        viz_tool=VizTool(),
        planner=planner,
    )
    o.set_memory(ConversationMemory(), "sess-1")
    return o


HISTORY = [
    {"role": "user", "content": "My name is Priya and I work with chocolate sales."},
    {"role": "assistant", "content": "Nice to meet you, Priya!"},
]


# ─────────────────────────────────────────────────────────────
# 1. Helper: history merge
# ─────────────────────────────────────────────────────────────

def test_conversation_history_merges_client_and_memory(orch):
    orch.memory.add_turn("sess-1", "user", "server-side turn")
    msgs = orch._conversation_history({"history": HISTORY})

    contents = [m.content for m in msgs]
    assert "server-side turn" in contents, "server memory must be included"
    assert any("Priya" in c for c in contents), "client history must be included"
    assert all(isinstance(m, Message) for m in msgs)


def test_conversation_history_empty_without_history(orch):
    assert orch._conversation_history(None) == []
    assert orch._conversation_history({}) == []
    assert orch._conversation_history({"history": []}) == []


def test_conversation_history_skips_system_and_non_dicts(orch):
    msgs = orch._conversation_history({
        "history": [
            {"role": "system", "content": "ignore me"},
            {"role": "user", "content": "hello"},
            "not-a-dict",
            {"role": "user"},           # no content
        ]
    })
    assert [m.content for m in msgs] == ["hello"]


# ─────────────────────────────────────────────────────────────
# 2. Helper: recall-question detection
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "What is my name?",
    "Who am I?",
    "What did I ask earlier?",
    "Do you remember what I told you?",
    "What did we discuss about my company?",
    "You said I work with chocolate — can you repeat that?",
])
def test_detects_recall_questions(q):
    assert Orchestrator._is_memory_dependent_question(q) is True


@pytest.mark.parametrize("q", [
    "Which region had the highest total sales?",
    "Show revenue by region",
    "Compare with the previous month",
    "What are the top 5 products?",
    "How many rows are in the table?",
    "Trend of profit earlier this year",
])
def test_does_not_hijack_data_questions(q):
    assert Orchestrator._is_memory_dependent_question(q) is False


# ─────────────────────────────────────────────────────────────
# 3. Integration: recall question answered from history
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_question_answered_from_history(orch):
    llm = RecordingLLM(reply="You told me your name is Priya.")
    orch.llm_client = llm

    resp = await orch.answer_question(
        "What is my name?",
        available_tables=["chocolate_sales"],
        context={"history": HISTORY, "session_id": "sess-1"},
    )

    assert resp.success
    assert "Priya" in resp.final_answer, (
        "answer must recall the name from the prior turn"
    )
    assert resp.plan.steps == [], (
        "recall questions must short-circuit before analytical planning"
    )
    # The history must actually be sent to the model.
    sent = " ".join(c for call in llm.calls for m in call for c in [m.content])
    assert "Priya" in sent, "history must be passed to the LLM"


@pytest.mark.asyncio
async def test_recall_question_uses_server_memory_when_no_client_history(orch):
    llm = RecordingLLM(reply="You told me your name is Priya.")
    orch.llm_client = llm
    orch.memory.add_turn("sess-1", "user", "My name is Priya")
    orch.memory.add_turn("sess-1", "assistant", "Nice to meet you, Priya!")

    resp = await orch.answer_question(
        "What is my name?", available_tables=["chocolate_sales"]
    )

    assert "Priya" in resp.final_answer


# ─────────────────────────────────────────────────────────────
# 4. Integration: data questions keep the analytical pipeline
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_question_keeps_analytical_pipeline(orch, monkeypatch):
    llm = RecordingLLM(reply="polished narrative")
    orch.llm_client = llm

    async def _no_exec(plan, context, user):
        return []

    monkeypatch.setattr(orch, "_execute_plan", _no_exec)

    resp = await orch.answer_question(
        "Which region had the highest total sales?",
        available_tables=["chocolate_sales"],
        context={"history": HISTORY, "session_id": "sess-1"},
    )

    assert resp.plan.steps, "data questions must still be planned analytically"
    assert not any(
        "conversation history" in p.lower() for p in llm.system_prompts
    ), "data questions must not be routed to the memory-reply branch"


@pytest.mark.asyncio
async def test_recall_question_without_history_falls_back_to_pipeline(orch, monkeypatch):
    llm = RecordingLLM(reply="polished narrative")
    orch.llm_client = llm

    async def _no_exec(plan, context, user):
        return []

    monkeypatch.setattr(orch, "_execute_plan", _no_exec)

    resp = await orch.answer_question(
        "What is my name?", available_tables=["chocolate_sales"]
    )

    # Without history there is nothing to recall, so the memory branch must
    # stay dark (its system prompt is the only place the marker appears).
    assert not any(
        "conversation history" in p.lower() for p in llm.system_prompts
    ), "no history -> no memory reply"
    assert resp.success


# ─────────────────────────────────────────────────────────────
# 5. Polish pass receives the history
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_polish_receives_history(orch):
    llm = RecordingLLM(reply="A polished narrative without tables.")
    orch.llm_client = llm

    from agent.orchestrator import AgentResponse, ExecutionPlan
    response = AgentResponse(
        question="What did I ask about?",
        plan=ExecutionPlan(question="q", steps=[], estimated_steps=0),
        step_results=[],
        final_answer="",
    )

    out = await orch._polish_with_llm("draft answer", response, HISTORY)
    assert out

    sent = " ".join(m.content for call in llm.calls for m in call)
    assert "Priya" in sent, "polish prompt must carry the conversation history"


# ─────────────────────────────────────────────────────────────
# 6. The chat (SSE) endpoint attaches the session's memory
# ─────────────────────────────────────────────────────────────

def test_chat_stream_endpoint_wires_session_memory(monkeypatch, tmp_path):
    """`/llm/chat/stream` takes a `session_id` from the UI but used to ignore it."""
    import api.main as main_mod
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    planner = Planner()
    planner.set_schema_context(
        "TABLE analytics.chocolate_sales (900 rows):\n  - region: VARCHAR\n"
    )
    stream_orch = Orchestrator(
        llm_client=None,
        sql_tool=SQLTool(db_path=str(tmp_path / "s.duckdb"), schema="analytics"),
        pandas_tool=PandasTool(),
        viz_tool=VizTool(),
        planner=planner,
    )
    mem = ConversationMemory()
    mem.add_turn("sess-rt", "user", "My name is Priya")
    stream_orch._memory = mem

    monkeypatch.setattr(main_mod, "orchestrator", stream_orch)
    # Out of scope here: the daily-AI-cap gate (covered by test_ai_metering).
    monkeypatch.setattr(main_mod, "_meter_ai_request", lambda req: None)

    captured: dict = {}

    async def fake_answer(question, available_tables=None, context=None, **kw):
        captured["session_id"] = stream_orch.session_id
        captured["history"] = stream_orch._conversation_history(context)
        from agent.orchestrator import AgentResponse, ExecutionPlan
        return AgentResponse(
            question=question,
            plan=ExecutionPlan(question=question, steps=[], estimated_steps=0),
            step_results=[],
            final_answer="Your name is Priya.",
            success=True,
        )

    monkeypatch.setattr(stream_orch, "answer_question", fake_answer)

    client = TestClient(main_mod.app)
    with client.stream(
        "POST", "/llm/chat/stream",
        json={"question": "What is my name?",
              "context": {"session_id": "sess-rt"}},   # no client history
        headers=auth_headers(), timeout=60,
    ) as r:
        body = "\n".join(ln for ln in r.iter_lines() if ln)

    assert captured.get("session_id") == "sess-rt", (
        "the endpoint must attach the session id the UI sent"
    )
    assert any("Priya" in m.content for m in captured.get("history", [])), (
        "server-side turns must be readable for recall"
    )
    assert "Priya" in body
