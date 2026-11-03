"""Chat Pro UI upgrades: Claude-style rendering + jump-to-bottom affordance.

These are source-contract checks (same pattern as tests/test_chat_visuals.py)
guarding the frontend pieces that have no other automated coverage:

  1. Markdown tables are wrapped in a scrollable card container.
  2. The thread has a jump-to-bottom pill wired to real scroll state.
  3. Assistant answers render flat (no chat card) while errors keep a card.
  4. The stylesheet cache-buster is bumped whenever the CSS changes.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT_PRO_JS = ROOT / "web" / "js" / "pages" / "chat-pro.js"
CHAT_PRO_CSS = ROOT / "web" / "css" / "chat-pro.css"


def test_css_cache_buster_bumped():
    src = CHAT_PRO_JS.read_text(encoding="utf-8")
    assert "css/chat-pro.css?v=7" in src, (
        "chat-pro.css changed in this release — bump CSS_HREF in chat-pro.js "
        "so browsers reload the stylesheet"
    )


def test_assistant_answers_fill_available_width():
    """Chrome MCP UI review: answers rendered in a ~390px shrink-to-fit
    column while the row was 836px — charts and tables squeezed into the
    left half with dead space to the right. The assistant column must fill
    the row, and the thread/composer canvas must be wide enough for data.
    """
    css = CHAT_PRO_CSS.read_text(encoding="utf-8")

    block_start = css.find(".cpx-row-ai .cpx-col {")
    assert block_start != -1, ".cpx-row-ai .cpx-col rule missing"
    block = css[block_start:block_start + 140]
    assert "flex: 1" in block, (
        "assistant column must fill the row (flex: 1), not shrink to fit "
        "the widest child"
    )
    assert "max-width: calc(100% - 46px)" in block, (
        "assistant column must still reserve room for the avatar column"
    )

    inner_start = css.find(".cpx-thread-inner {")
    assert inner_start != -1, ".cpx-thread-inner rule missing"
    inner = css[inner_start:inner_start + 140]
    assert "max-width: 1200px" in inner, (
        "thread canvas must be wide enough for full-width data answers"
    )

    composer_start = css.find(".cpx-composer-inner {")
    assert composer_start != -1, ".cpx-composer-inner rule missing"
    composer = css[composer_start:composer_start + 120]
    assert "max-width: 1200px" in composer, (
        "composer must stay aligned with the widened thread"
    )


def test_markdown_tables_are_wrapped_for_horizontal_scroll():
    src = CHAT_PRO_JS.read_text(encoding="utf-8")
    assert 'cpx-table-scroll"><table>' in src, (
        "markdown tables must be wrapped in .cpx-table-scroll so wide data "
        "tables scroll horizontally instead of stretching the bubble"
    )
    css = CHAT_PRO_CSS.read_text(encoding="utf-8")
    assert ".cpx-table-scroll" in css and "overflow-x: auto" in css, (
        ".cpx-table-scroll must enable horizontal overflow"
    )


def test_jump_to_bottom_pill_is_wired():
    src = CHAT_PRO_JS.read_text(encoding="utf-8")
    assert 'data-cpx-jump' in src, "thread must render a [data-cpx-jump] control"
    assert "case 'jump-bottom':" in src, "jump pill must have a click handler"
    assert "_updateJumpBtn()" in src, "jump pill visibility must track scroll state"
    assert "addEventListener('scroll'" in src, (
        "thread must listen to scroll events to toggle the jump pill"
    )
    css = CHAT_PRO_CSS.read_text(encoding="utf-8")
    assert ".cpx-jump.cpx-show" in css, "jump pill needs a visible state class"


def test_assistant_bubble_is_flat_but_errors_keep_card():
    css = CHAT_PRO_CSS.read_text(encoding="utf-8")
    block_start = css.find(".cpx-bubble-ai {")
    assert block_start != -1
    block = css[block_start:block_start + 260]
    assert "background: transparent" in block, (
        "assistant answers must render flat (Claude-style), not inside a card"
    )
    assert ".cpx-bubble-ai.cpx-error {" in css, (
        "error answers must keep a visible card so failures stand out"
    )
