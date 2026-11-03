"""The /ask request must be cancellable (not merely abandoned).

Source-contract checks (same pattern as tests/test_ui_contract.py and
tests/test_chat_pro_ui.py) for the Chat Pro "Stop" button:

  1. ``web/js/api.js`` accepts an ``AbortSignal`` in the shared request
     helper and hands it to ``fetch()`` as ``{ signal }``.
  2. ``API.ask`` accepts a signal and forwards it to the request helper.
  3. ``web/js/pages/chat-pro.js`` passes the turn's AbortController signal
     into ``API.ask`` — so pressing Stop really cancels the HTTP request
     instead of leaving the server to run the whole LLM pipeline for an
     answer nobody is waiting for.
  4. The existing behaviour is preserved: the "Generation stopped." bubble,
     the "aborted turn is not persisted as an unanswered question" fix and
     the ``destroy()`` guard (leaving the page must not start a second
     server-side pipeline).

These are cheap guards, not a substitute for the browser check.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_JS = ROOT / "web" / "js" / "api.js"
CHAT_PRO_JS = ROOT / "web" / "js" / "pages" / "chat-pro.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slice(src: str, marker: str, length: int) -> str:
    """Return `length` characters of `src` starting at `marker`."""
    idx = src.find(marker)
    assert idx != -1, f"marker not found in source: {marker!r}"
    return src[idx:idx + length]


# ── web/js/api.js ────────────────────────────────────────────────────────


def test_request_helper_accepts_an_abort_signal():
    src = _read(API_JS)
    req = _slice(src, "async function _request(", 1200)
    assert re.search(r"\{[^}]*\bsignal\b[^}]*\}\s*=\s*\{\s*\}", req), (
        "_request() must destructure an optional `signal` from its options "
        "object so a caller can cancel the request"
    )


def test_request_helper_hands_the_signal_to_fetch():
    src = _read(API_JS)
    assert "config.signal = signal" in src, (
        "_request() must attach the AbortSignal to the fetch config"
    )
    assert re.search(r"await fetch\(\s*url\s*,\s*config\s*\)", src), (
        "_request() must pass the config (carrying the signal) to fetch()"
    )


def test_signal_is_only_attached_when_supplied():
    """Default behaviour must stay byte-identical for every other caller."""
    src = _read(API_JS)
    assign = _slice(src, "if (signal) {", 120)
    assert "config.signal = signal" in assign, (
        "the signal must be attached conditionally — an absent signal must "
        "leave the fetch config exactly as it was before"
    )


def test_api_ask_accepts_and_forwards_a_signal():
    src = _read(API_JS)
    ask = _slice(src, "ask: (question, opts = {}) =>", 800)
    assert re.search(r"opts\.signal", ask), (
        "API.ask must read the signal from its options object"
    )
    assert re.search(r"\{\s*signal:\s*opts\.signal\s*\}", ask), (
        "API.ask must forward the signal to the request helper"
    )
    assert "POST(\"/ask\"" in ask, "API.ask must still POST to /ask"


def test_api_ask_keeps_its_existing_payload():
    src = _read(API_JS)
    ask = _slice(src, "ask: (question, opts = {}) =>", 800)
    for key in ("question", "table:", "tables:", "session_id", "context:"):
        assert key in ask, f"API.ask must keep sending {key}"


def test_aborted_request_does_not_raise_an_error_toast():
    src = _read(API_JS)
    catch = _slice(src, "} catch (err) {", 900)
    assert "AbortError" in catch, (
        "the request helper must recognise a cancelled request (AbortError)"
    )
    toast = _slice(catch, "window.App.toast(", 60)
    guard = catch[: catch.find("window.App.toast(")]
    assert "isAbort" in guard, (
        "a cancelled request must not raise an error toast"
    )
    assert toast, "the toast call must still exist for genuine failures"


# ── web/js/pages/chat-pro.js ─────────────────────────────────────────────


def test_chat_pro_passes_the_abort_signal_to_api_ask():
    src = _read(CHAT_PRO_JS)
    helper = _slice(src, "_askWithAbort(question, tables) {", 2400)
    assert re.search(r"window\.API\.ask\(\s*question", helper), (
        "_askWithAbort must issue the request through API.ask"
    )
    assert "signal" in helper, (
        "_askWithAbort must forward an abort signal to API.ask"
    )
    assert re.search(r"signal:\s*sig", helper), (
        "the signal handed to API.ask must be the turn's AbortController "
        "signal"
    )
    assert "ctrl.signal" in helper, (
        "the signal must come from the turn's AbortController"
    )


def test_chat_pro_still_races_the_signal_and_marks_the_turn_aborted():
    src = _read(CHAT_PRO_JS)
    helper = _slice(src, "_askWithAbort(question, tables) {", 2400)
    assert "err.aborted = true" in helper, (
        "an aborted turn must be flagged with `aborted` so the caller can "
        "render the stopped bubble instead of an error"
    )
    assert "AbortError" in helper, (
        "the AbortError raised by fetch() must be normalised to the "
        "aborted shape (never surfaced as a failure)"
    )


def test_chat_pro_keeps_the_generation_stopped_path():
    src = _read(CHAT_PRO_JS)
    assert "Generation stopped." in src, (
        "the stopped turn must still render the 'Generation stopped.' bubble"
    )
    assert "askErr && askErr.aborted" in src, (
        "the /ask fallback must recognise an aborted request"
    )


def test_chat_pro_keeps_the_destroy_guard():
    src = _read(CHAT_PRO_JS)
    destroy = _slice(src, "async destroy() {", 1200)
    assert "this._gen++" in destroy, (
        "destroy() must invalidate the in-flight turn"
    )
    assert "abort()" in destroy, (
        "destroy() must abort the in-flight request"
    )
    assert "turnId !== this._gen" in src, (
        "a torn-down turn must never start a second server-side pipeline"
    )


def test_chat_pro_sse_path_still_passes_its_signal():
    src = _read(CHAT_PRO_JS)
    stream = _slice(src, "window.API.askStream(", 400)
    assert "signal" in stream, (
        "the SSE path must keep passing the abort signal to askStream"
    )


# ── web/js/pages/chat.js (the simple Chat page) ──────────────────────────


def test_simple_chat_fallback_forwards_the_turn_signal():
    """The simple Chat page has the same Stop button and the same fallback."""
    src = _read(ROOT / "web" / "js" / "pages" / "chat.js")
    fallback = _slice(src, "if (!usedStream) {", 1800)
    assert re.search(r"API\.ask\(\s*question", fallback), (
        "the non-streaming fallback must still issue API.ask"
    )
    assert re.search(r"signal", fallback), (
        "the fallback must hand the turn's AbortSignal to API.ask"
    )
    assert re.search(r"this\._abortCtrl\s*\?\s*this\._abortCtrl\.signal", fallback), (
        "the signal must come from the turn's AbortController"
    )


def test_simple_chat_fallback_handles_an_abort_without_an_error_bubble():
    src = _read(ROOT / "web" / "js" / "pages" / "chat.js")
    fallback = _slice(src, "if (!usedStream) {", 1800)
    assert "AbortError" in fallback or "askErr" in fallback, (
        "a Stop press during the fallback must be recognised as an abort, "
        "not rendered as a failure"
    )
    assert "Generation stopped." in fallback, (
        "an aborted fallback must render the same stopped bubble as the stream"
    )
