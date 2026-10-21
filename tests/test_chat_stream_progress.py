"""`/llm/chat/stream` live progress events behind `stream_steps` (RED tests).

Contract under test (`api/main.py` + `QuestionRequest`):

  1. `stream_steps=True` → the pipeline's step/chart progress is streamed as
     `{"step": ...}` / `{"viz": ...}` SSE events BEFORE the answer tokens, and
     the final `visualizations` batch is NOT sent (charts already streamed).
  2. Flag absent/false → byte-compatible legacy stream: tokens + one
     `visualizations` batch + `[DONE]` (legacy chat.js depends on this).
  3. Small talk routing is unaffected by the flag.
"""

from __future__ import annotations

import json

CHART_Q = ("show me a bar chart of amount by region and a line chart of "
           "amount over time")


def _parse_sse(body: str) -> list:
    out: list = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw = line[len("data:"):].strip()
        if raw == "[DONE]":
            out.append({"__done__": True})
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            pass
    return out


def _env(monkeypatch, tmp_path):
    """Real orchestrator on a tmp warehouse + stubbed endpoint externals."""
    import duckdb

    import api.main as main_mod
    from agent.memory import ConversationMemory
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from agent.tools_pandas import PandasTool
    from agent.tools_sql import SQLTool
    from agent.tools_viz import VizTool

    dbp = str(tmp_path / "stream_prog.duckdb")
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

    planner = Planner()
    planner.set_schema_context(
        "TABLE analytics.sales (3 rows):\n"
        "  - region: VARCHAR\n  - amount: DOUBLE\n"
        "  - units: DOUBLE\n  - sale_date: DATE\n")
    orch = Orchestrator(
        llm_client=None,
        sql_tool=SQLTool(db_path=dbp, schema="analytics"),
        pandas_tool=PandasTool(),
        viz_tool=VizTool(output_dir=str(tmp_path / "charts")),
        planner=planner,
    )
    orch.set_memory(ConversationMemory(), "sess-stream")

    class _Loader:
        def list_tables(self):
            return ["sales"]

    monkeypatch.setattr(main_mod, "orchestrator", orch)
    monkeypatch.setattr(main_mod, "loader", _Loader())
    # Out of scope: the daily AI cap and the real-warehouse schema refresh.
    monkeypatch.setattr(main_mod, "_meter_ai_request", lambda req: None)
    monkeypatch.setattr(main_mod, "_refresh_planner_schema", lambda *a, **k: None)
    return main_mod


def _stream(client, payload, headers):
    with client.stream("POST", "/llm/chat/stream", json=payload,
                       headers=headers, timeout=180) as r:
        assert r.status_code == 200, f"stream failed: {r.status_code} {r.read()!r}"
        return "\n".join(ln for ln in r.iter_lines() if ln)


def test_stream_steps_emits_live_step_and_viz_events(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers

    main_mod = _env(monkeypatch, tmp_path)
    client = TestClient(main_mod.app)
    body = _stream(client, {"question": CHART_Q, "stream_steps": True},
                   auth_headers())
    events = _parse_sse(body)
    assert events and events[-1].get("__done__"), f"no [DONE] terminator: {body[:300]}"

    step_events = [e["step"] for e in events if "step" in e]
    viz_events = [e["viz"] for e in events if "viz" in e]
    token_events = [e["token"] for e in events if "token" in e]

    assert token_events, "answer text must still stream"
    assert not any("visualizations" in e for e in events), (
        "stream_steps=True streams charts live — the legacy batch event "
        "must not be sent (charts would render twice)"
    )

    plans = [s for s in step_events if s.get("type") == "plan"]
    assert plans, f"expected a plan event, got: {step_events[:4]}"
    assert len(plans[0]["steps"]) == 4
    starts = [s for s in step_events if s.get("type") == "start"]
    dones = [s for s in step_events if s.get("type") == "done"]
    assert [s["id"] for s in starts] == [1, 2, 3, 4]
    assert [s["id"] for s in dones] == [1, 2, 3, 4]
    assert all("description" in s and "tool" in s for s in starts)

    assert len(viz_events) == 2, f"expected 2 live charts, got {len(viz_events)}"
    for v in viz_events:
        assert isinstance(v.get("spec"), dict) and v["spec"].get("data"), (
            "streamed chart items need a Plotly spec for the UI to render"
        )

    first_token_idx = next(i for i, e in enumerate(events) if "token" in e)
    last_progress_idx = max(i for i, e in enumerate(events)
                            if "step" in e or "viz" in e)
    assert last_progress_idx < first_token_idx, (
        "steps/charts must stream BEFORE the answer text — that is the "
        "whole point of the live pipeline"
    )


def test_default_stream_keeps_legacy_batch_contract(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers

    main_mod = _env(monkeypatch, tmp_path)
    client = TestClient(main_mod.app)
    body = _stream(client, {"question": CHART_Q}, auth_headers())
    events = _parse_sse(body)
    assert events and events[-1].get("__done__")

    assert not any("step" in e for e in events), (
        "legacy callers must not receive step events (chat.js concatenates "
        "unknown stream objects into the answer text)"
    )
    assert not any("viz" in e for e in events)

    batches = [e["visualizations"] for e in events if "visualizations" in e]
    assert len(batches) == 1
    assert len(batches[0]) == 2, "both charts must still arrive in the batch"
    assert [e["token"] for e in events if "token" in e]


def test_smalltalk_stream_ignores_stream_steps(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from tests.conftest import auth_headers

    main_mod = _env(monkeypatch, tmp_path)
    client = TestClient(main_mod.app)
    body = _stream(client, {"question": "hello", "stream_steps": True},
                   auth_headers())
    events = _parse_sse(body)
    assert events and events[-1].get("__done__")
    tokens = [e["token"] for e in events if "token" in e]
    assert tokens, "small talk must still stream its friendly reply"
    assert not any("step" in e or "viz" in e for e in events), (
        "small talk has no pipeline — no step events"
    )
