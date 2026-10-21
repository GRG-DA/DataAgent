"""Chat Pro live-steps UI contract (RED tests).

Source-contract checks (same pattern as tests/test_chat_visuals.py) for the
frontend pieces of the live agentic chat:

  1. `api.js askStream()` sends `stream_steps` and surfaces `{__step}` /
     `{__viz}` events to the page.
  2. `chat-pro.js` requests step events, renders a steps panel
     (`[data-cpx-steps]`), handles `__step` / `__viz` events, and NEVER lets
     non-string stream objects concatenate into the answer text.
  3. Charts concatenate across streaming events instead of being replaced.
  4. The steps panel collapses to a summary once the run finishes and is
     re-rendered from stored messages.
  5. CSS ships the panel styles.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_JS = ROOT / "web" / "js" / "api.js"
CHAT_PRO_JS = ROOT / "web" / "js" / "pages" / "chat-pro.js"
CHAT_PRO_CSS = ROOT / "web" / "css" / "chat-pro.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_api_js_streams_step_and_viz_events():
    src = _read(API_JS)
    assert "stream_steps" in src, (
        "askStream() must send the stream_steps flag so the endpoint can "
        "emit live step/chart events"
    )
    assert "data.step" in src and "__step" in src, (
        "askStream() must surface SSE step events as {__step} objects"
    )
    assert "data.viz" in src and "__viz" in src, (
        "askStream() must surface SSE viz events as {__viz} objects"
    )
    assert "streamEvents" in src, (
        "askStream() needs a streamEvents parameter (default false) so legacy "
        "callers keep the old stream shape"
    )


def test_chat_pro_requests_step_events():
    src = _read(CHAT_PRO_JS)
    m = re.search(r"askStream\(([^;]+?)\)", src, re.S)
    assert m, "askStream call not found in chat-pro.js"
    last_arg = m.group(1).split(",")[-1].strip()
    assert last_arg == "true", (
        "chat-pro must request stream_steps events (last askStream arg was "
        f"{last_arg!r})"
    )


def test_chat_pro_handles_step_and_viz_stream_objects():
    src = _read(CHAT_PRO_JS)
    assert "__step" in src and "__viz" in src, (
        "chat-pro.js must branch on {__step} / {__viz} stream objects"
    )
    assert "data-cpx-steps" in src, "steps panel must be addressable in the DOM"
    assert "steps-toggle" in src, "steps panel needs a collapse/expand toggle"
    assert "cpx-step-" in src, "per-step status classes (running/done/failed) missing"
    assert "typeof token !== 'string'" in src, (
        "non-string stream objects must never concatenate into the answer "
        "text (the old `streamed += token` corruption bug)"
    )
    assert "vizSpecs.concat(" in src, (
        "streamed charts must be concatenated (multiple __viz events), not "
        "replaced"
    )


def test_chat_pro_collapses_steps_when_finished():
    src = _read(CHAT_PRO_JS)
    assert "cpx-steps-collapsed" in src, "finished runs must collapse the panel"
    assert "stepsCollapsed" in src, "collapse state must live on the message"
    assert "_renderStepsHtml(" in src and src.count("_renderStepsHtml(") >= 2, (
        "steps must render both live (during the stream) and from stored "
        "messages on thread re-render"
    )
    assert "m.steps" in src, "stored messages must re-render their steps"


def test_css_has_steps_panel_styles():
    css = _read(CHAT_PRO_CSS)
    assert ".cpx-steps" in css, "steps panel styles missing from chat-pro.css"
    assert ".cpx-steps-collapsed" in css, "collapsed-state style missing"
    assert ".cpx-step-running" in css, "running-step style missing"
    assert ".cpx-spin" in css, "step spinner animation missing"
