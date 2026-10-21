"""RED test: Escape in edit mode must exit fullscreen.

Live verification (2026-10-17, chrome-devtools MCP against the running app)
found that with the dashboard editor in fullscreen:

- no widget selected: `_onEditKey` does not preventDefault, but Chrome's
  native Esc-exit did not fire (automation/UA dependent);
- a widget selected: `_onEditKey` deselects and calls preventDefault, which
  by fullscreen spec suppresses the native exit entirely — Esc could never
  leave fullscreen, for real users too.

Fix: the Escape branch in `_onEditKey` must explicitly exit fullscreen via
`document.exitFullscreen()` when `document.fullscreenElement` is set, before
falling back to the deselect behavior.

Contract test follows the repo's source-grep convention (see
tests/test_blankdash_viz.py, tests/test_chat_visuals.py).
Run: python -m pytest tests/test_blankdash_esc_fullscreen.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLANKDASH_JS = ROOT / "web" / "js" / "pages" / "blankdash.js"


def _src() -> str:
    return BLANKDASH_JS.read_text(encoding="utf-8")


def _edit_key_body() -> str:
    """Slice the `_onEditKey` method body (up to the next method)."""
    src = _src()
    i = src.find("_onEditKey: function")
    assert i != -1, "_onEditKey not found in blankdash.js"
    j = src.find("_onPresentKey: function", i)
    assert j != -1, "_onPresentKey not found after _onEditKey"
    return src[i:j]


def _escape_branch() -> str:
    """Slice from the Escape branch to the end of the method."""
    body = _edit_key_body()
    i = body.find('k === "Escape"')
    assert i != -1, "Escape branch not found in _onEditKey"
    return body[i:]


def test_escape_exits_fullscreen_in_edit_mode():
    """The Escape branch must explicitly exit fullscreen when active."""
    branch = _escape_branch()
    assert "document.fullscreenElement" in branch, (
        "_onEditKey's Escape handling never checks document.fullscreenElement — "
        "pressing Esc while in fullscreen cannot leave it deterministically"
    )
    assert "exitFullscreen" in branch, (
        "_onEditKey's Escape handling never calls document.exitFullscreen() — "
        "native Esc-exit is suppressed when the page preventDefaults the keydown"
    )
    assert branch.index("exitFullscreen") < branch.index("_deselect()"), (
        "exitFullscreen must run before the deselect fallback, so the first Esc "
        "leaves fullscreen and the deselect only applies outside fullscreen"
    )


def test_escape_still_deselects_without_fullscreen():
    """Regression guard: plain Esc (not fullscreen) keeps deselecting."""
    branch = _escape_branch()
    assert "_deselect()" in branch, (
        "the Escape branch must keep the deselect behavior when not fullscreen"
    )
