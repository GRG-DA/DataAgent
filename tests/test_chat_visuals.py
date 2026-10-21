"""Chat visualizations must render end-to-end ("visual not render" bug).

Two production bugs were confirmed in the browser:

1. `web/js/api.js` → `askStream()` POSTed `/llm/chat/stream` WITHOUT the
   Authorization header, so every message got 401 and silently fell back to
   the non-streaming `/ask` path (streaming never worked).
2. That fallback dropped `result.visualizations` on the floor — the answer
   text said "see chart above" with no chart — and `/ask` never returned a
   Plotly `spec` (only CDN-based `html`, which `innerHTML` will not execute).

Guarantees:
  1. `/llm/chat/stream` rejects unauthenticated callers → the client MUST
     attach the JWT, and the endpoint works when it does.
  2. The frontend's `askStream()` attaches the Bearer token.
  3. `/ask` visualizations carry a renderable Plotly `spec`.
  4. The chat `/ask` fallback path renders `result.visualizations`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_JS = ROOT / "web" / "js" / "api.js"
CHAT_JS = ROOT / "web" / "js" / "pages" / "chat.js"


def test_chat_stream_endpoint_requires_auth():
    """Unauthenticated POST /llm/chat/stream must 401 (frontend must send JWT)."""
    from fastapi.testclient import TestClient

    import api.main as main_mod

    client = TestClient(main_mod.app)
    r = client.post("/llm/chat/stream", json={"question": "hello"})
    assert r.status_code == 401, (
        "if this is no longer 401 the askStream auth contract changed"
    )


def test_ask_stream_endpoint_accepts_auth(monkeypatch, tmp_path):
    """With a valid JWT the SSE endpoint streams tokens (no auth=401 only)."""
    from fastapi.testclient import TestClient

    import api.main as main_mod
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from agent.tools_pandas import PandasTool
    from agent.tools_sql import SQLTool
    from agent.tools_viz import VizTool
    from tests.conftest import auth_headers

    # Out of scope here: the daily-AI-cap gate (covered by test_ai_metering).
    monkeypatch.setattr(main_mod, "_meter_ai_request", lambda req: None)
    # TestClient skips startup events, so the module-level orchestrator is None.
    monkeypatch.setattr(main_mod, "orchestrator", Orchestrator(
        llm_client=None,
        sql_tool=SQLTool(db_path=str(tmp_path / "stream.duckdb"), schema="analytics"),
        pandas_tool=PandasTool(),
        viz_tool=VizTool(),
        planner=Planner(),
    ))

    client = TestClient(main_mod.app)
    with client.stream(
        "POST", "/llm/chat/stream",
        json={"question": "hi"}, headers=auth_headers(), timeout=60,
    ) as r:
        assert r.status_code == 200, (
            "authenticated streaming must work — this is what the frontend "
            "relies on once askStream sends the header"
        )
        body = "\n".join(ln for ln in r.iter_lines() if ln)
    assert "data:" in body, "expected an SSE stream, got: " + body[:200]


def _ask_stream_body() -> str:
    src = API_JS.read_text(encoding="utf-8")
    start = src.find("askStream:")
    assert start != -1, "askStream not found in web/js/api.js"
    end = src.find("_request,", start)
    assert end != -1, "could not locate end of askStream in web/js/api.js"
    return src[start:end]


def test_ask_stream_attaches_bearer_token():
    """Regression guard: the raw SSE fetch must carry the JWT like _request does."""
    body = _ask_stream_body()
    assert "Authorization" in body and "Bearer" in body, (
        "askStream() posts /llm/chat/stream without an Authorization header, "
        "so AuthMiddleware 401s every stream and the chat silently degrades "
        "to /ask (no streaming, no chart render)"
    )


def test_ask_response_includes_plotly_spec():
    """/ask visualizations must include a renderable `spec` (not only HTML).

    The chat fallback renders with the vendored Plotly via `spec`; the CDN
    `<script>` inside `html` never executes through `innerHTML`.
    """
    import plotly.graph_objects as go

    import api.main as main_mod
    from agent.orchestrator import AgentResponse, ExecutionPlan
    from agent.tools_viz import VizResult

    fig = go.Figure(data=[go.Bar(x=["west", "east"], y=[10.0, 20.0])])
    viz = VizResult(
        success=True,
        figure=fig,
        html="<div>cdn-only html</div>",
        config={"chart_type": "bar"},
    )
    resp = AgentResponse(
        question="show a chart of sales by region",
        plan=ExecutionPlan(question="show a chart of sales by region",
                           steps=[], estimated_steps=0),
        step_results=[],
        final_answer="Here is the chart.",
        visualizations=[viz],
    )
    req = main_mod.QuestionRequest(question="show a chart of sales by region")

    built = main_mod._build_ask_response(req, resp, "sess-1")
    items = built.get("visualizations") or []
    assert items, "expected /ask to return at least one visualization"
    spec = items[0].get("spec")
    assert isinstance(spec, dict) and spec.get("data") and spec.get("layout"), (
        "/ask visualization is missing a Plotly `spec` — the chat fallback "
        "cannot render it (got keys: %s)" % sorted(items[0].keys())
    )


def _chat_fallback_block() -> str:
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.find("if (!usedStream)")
    assert start != -1, "fallback block not found in web/js/pages/chat.js"
    return src[start:start + 900]


def test_ask_fallback_renders_visualizations():
    """The non-streaming /ask fallback must render the returned visualizations."""
    block = _chat_fallback_block()
    assert "result.visualizations" in block, (
        "the /ask fallback discards result.visualizations — the chart the "
        "backend returned is never shown"
    )
    assert "_buildViz" in block, "the /ask fallback must build viz HTML/specs"
    assert "_addMessage('assistant'" in block and "built" in block, (
        "the /ask fallback must pass the built viz into the assistant message"
    )
    # The message itself must render those specs (once — not also in the caller).
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.find("_addMessage(role, content, viz)")
    assert start != -1, "_addMessage must accept an optional viz argument"
    am_block = src[start:start + 900]
    assert "_renderVizSpecs" in am_block, (
        "_addMessage must render the viz specs it stores on the message"
    )
