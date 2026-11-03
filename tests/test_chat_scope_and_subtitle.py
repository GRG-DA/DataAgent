"""Chat query scope (table picker) and conversation-list subtitles.

Two defects in the AI Chat page (``web/js/pages/chat.js``):

1. **The table scope was silently dropped on a cold load.** ``render()``
   pruned the stored scope against ``App.state.tables`` and ``destroy()``
   persisted the result unconditionally. ``App.refreshTables()`` swallows its
   own errors and ``App.state.tables`` starts out as ``{}``, so on a cold load
   where the table fetch had not finished (or failed) the stored scope was
   pruned to ``[]`` and the empty scope was written back over the user's
   selection — gone, silently. The pruning must only run against a table
   list that is known to be loaded, and an empty list must never clear a
   stored selection.

2. **The conversation list showed the wrong subtitle.** Every row previewed
   the last *assistant* answer (a whole result table such as ``"Result
   Metric Value --- --- Group name North Value 816.95 Co…"``), which made the
   sidebar unreadable. The subtitle must be the user's last question.

Source-contract checks plus a node harness that runs the real methods taken
out of ``web/js/pages/chat.js`` (the same pattern as
``tests/test_chat_persistence.py``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT_JS = ROOT / "web" / "js" / "pages" / "chat.js"

TABLES_KEY = "chat_selected_tables_v2"


# ── helpers ────────────────────────────────────────────────────────────

def _chat_src() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _extract(src: str, start: str, end: str, label: str) -> str:
    s = src.find(start)
    assert s != -1, f"{label}: start marker {start!r} not found in chat.js"
    e = src.find(end, s)
    assert e != -1, f"{label}: end marker {end!r} not found in chat.js"
    return src[s:e]


def _run_node(tmp_path: Path, body: str) -> str:
    script = tmp_path / "case.js"
    script.write_text(body, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30,
        cwd=str(ROOT),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"node harness failed:\n{out}"
    return out


def _assert_ok(out: str) -> None:
    assert "OK" in out, f"node harness reported failure:\n{out}"


def _scope_methods() -> str:
    """The scope helpers as they are written in chat.js."""
    src = _chat_src()
    return "\n".join([
        _extract(src, "_allTableNames() {", "_toggleTablePicker() {", "_allTableNames"),
        _extract(src, "_persistTables() {", "_readStoredTables() {", "_persistTables"),
        _extract(src, "_readStoredTables() {", "_persistScopeOnExit() {", "_readStoredTables"),
        _extract(src, "_persistScopeOnExit() {", "_tableListLoaded() {", "_persistScopeOnExit"),
        _extract(src, "_tableListLoaded() {", "_pruneSelectedTables() {", "_tableListLoaded"),
        _extract(src, "_pruneSelectedTables() {", "_updateTableUI() {", "_pruneSelectedTables"),
    ])


def _subtitle_method() -> str:
    src = _chat_src()
    return _extract(src, "_conversationSubtitle(conv) {", "_startRename(id) {",
                    "_conversationSubtitle")


# ── Defect 1: the scope must survive a cold load ───────────────────────

def test_scope_pruning_goes_through_the_gated_helper():
    """render() must not prune the scope inline against a maybe-empty list."""
    src = _chat_src()
    render = _extract(src, "async render() {", "_renderSidebar() {", "render")
    assert "_pruneSelectedTables()" in render, (
        "render() must prune the stored scope through _pruneSelectedTables(), "
        "which is gated on the table list being loaded"
    )
    assert "known.has(t.toLowerCase())" not in render, (
        "render() still prunes the scope inline — an empty (not yet loaded) "
        "table list wipes the user's selection"
    )
    assert "_pruneSelectedTables() {" in src, (
        "chat.js must expose _pruneSelectedTables()"
    )


def test_pruning_is_gated_on_a_loaded_table_list():
    """The gate must exist and must be consulted before anything is dropped."""
    src = _chat_src()
    prune = _extract(src, "_pruneSelectedTables() {", "_updateTableUI() {",
                     "_pruneSelectedTables")
    assert "_tableListLoaded()" in prune, (
        "_pruneSelectedTables() must check that the table list is loaded "
        "before it drops anything"
    )
    loaded = _extract(src, "_tableListLoaded() {", "_pruneSelectedTables() {",
                      "_tableListLoaded")
    assert "_allTableNames()" in loaded, (
        "_tableListLoaded() must derive the answer from the table list "
        "(App.state.tables)"
    )
    # The gate has to come first — otherwise the filter runs before the check.
    assert prune.index("_tableListLoaded()") < prune.index("filter("), (
        "the loaded check must guard the filter, not follow it"
    )


def test_empty_table_list_does_not_clear_a_stored_selection(tmp_path):
    """A table list that is empty (not loaded / failed) must keep the scope."""
    body = """
const methods = { __METHODS__ };
const store = {};
globalThis.sessionStorage = {
  setItem(k, v) { store[k] = String(v); },
  getItem(k) { return k in store ? store[k] : null; },
};
globalThis.App = { state: { tables: {} } };
const obj = {
  _TABLES_KEY: '__KEY__',
  _selectedTables: ['ai_probe_sales'],
  ...methods,
};
const fails = [];
for (const empty of [{}, null, []]) {
  globalThis.App.state.tables = empty;
  sessionStorage.setItem(obj._TABLES_KEY, JSON.stringify(['ai_probe_sales']));
  obj._selectedTables = ['ai_probe_sales'];
  const changed = obj._pruneSelectedTables();
  if (changed !== false) fails.push('prune reported a change for ' + JSON.stringify(empty));
  if (JSON.stringify(obj._selectedTables) !== '["ai_probe_sales"]') {
    fails.push('scope cleared by an empty table list: ' + JSON.stringify(obj._selectedTables));
  }
  if (sessionStorage.getItem(obj._TABLES_KEY) !== '["ai_probe_sales"]') {
    fails.push('stored scope overwritten by an empty table list: '
               + sessionStorage.getItem(obj._TABLES_KEY));
  }
}
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _scope_methods()).replace("__KEY__", TABLES_KEY)
    _assert_ok(_run_node(tmp_path, body))


def test_a_table_that_really_disappeared_is_still_dropped(tmp_path):
    """With a loaded list, a table that is gone is pruned (and persisted)."""
    body = """
const methods = { __METHODS__ };
const store = {};
globalThis.sessionStorage = {
  setItem(k, v) { store[k] = String(v); },
  getItem(k) { return k in store ? store[k] : null; },
};
globalThis.App = { state: { tables: { other_table: { row_count: 3 } } } };
const obj = {
  _TABLES_KEY: '__KEY__',
  _selectedTables: ['ai_probe_sales'],
  ...methods,
};
const fails = [];
sessionStorage.setItem(obj._TABLES_KEY, JSON.stringify(['ai_probe_sales']));
const changed = obj._pruneSelectedTables();
if (changed !== true) fails.push('prune did not report the change');
if (obj._selectedTables.length !== 0) {
  fails.push('the dropped table survived the prune: ' + JSON.stringify(obj._selectedTables));
}
if (sessionStorage.getItem(obj._TABLES_KEY) !== '[]') {
  fails.push('the pruned scope was not persisted: ' + sessionStorage.getItem(obj._TABLES_KEY));
}
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _scope_methods()).replace("__KEY__", TABLES_KEY)
    _assert_ok(_run_node(tmp_path, body))


def test_a_surviving_table_is_left_alone(tmp_path):
    """A selected table that is still there must not be dropped/re-persisted."""
    body = """
const methods = { __METHODS__ };
const store = {};
globalThis.sessionStorage = {
  setItem(k, v) { store[k] = String(v); },
  getItem(k) { return k in store ? store[k] : null; },
};
globalThis.App = { state: { tables: { ai_probe_sales: { row_count: 12 } } } };
const obj = {
  _TABLES_KEY: '__KEY__',
  _selectedTables: ['ai_probe_sales'],
  ...methods,
};
const fails = [];
sessionStorage.setItem(obj._TABLES_KEY, JSON.stringify(['ai_probe_sales']));
const changed = obj._pruneSelectedTables();
if (changed !== false) fails.push('prune reported a change for a table that still exists');
if (JSON.stringify(obj._selectedTables) !== '["ai_probe_sales"]') {
  fails.push('the scope changed: ' + JSON.stringify(obj._selectedTables));
}
if (sessionStorage.getItem(obj._TABLES_KEY) !== '["ai_probe_sales"]') {
  fails.push('the stored scope changed: ' + sessionStorage.getItem(obj._TABLES_KEY));
}
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _scope_methods()).replace("__KEY__", TABLES_KEY)
    _assert_ok(_run_node(tmp_path, body))


def test_teardown_never_overwrites_a_stored_scope_with_an_empty_one(tmp_path):
    """destroy() must not persist an empty scope over a stored one."""
    body = """
const methods = { __METHODS__ };
const store = {};
globalThis.sessionStorage = {
  setItem(k, v) { store[k] = String(v); },
  getItem(k) { return k in store ? store[k] : null; },
};
globalThis.App = { state: { tables: {} } };
const obj = {
  _TABLES_KEY: '__KEY__',
  _selectedTables: [],
  ...methods,
};
const fails = [];
// The table list never loaded: the scope is empty in memory, but a scope is
// stored — it must survive the teardown.
sessionStorage.setItem(obj._TABLES_KEY, JSON.stringify(['ai_probe_sales']));
obj._selectedTables = [];
obj._persistScopeOnExit();
if (sessionStorage.getItem(obj._TABLES_KEY) !== '["ai_probe_sales"]') {
  fails.push('teardown wiped the stored scope: ' + sessionStorage.getItem(obj._TABLES_KEY));
}
// A real scope is persisted on the way out.
obj._selectedTables = ['ai_probe_sales'];
obj._persistScopeOnExit();
if (sessionStorage.getItem(obj._TABLES_KEY) !== '["ai_probe_sales"]') {
  fails.push('the scope was not persisted on teardown: ' + sessionStorage.getItem(obj._TABLES_KEY));
}
// The user cleared the scope (the picker persists that immediately) — the
// teardown must keep it cleared.
sessionStorage.setItem(obj._TABLES_KEY, '[]');
obj._selectedTables = [];
obj._persistScopeOnExit();
if (sessionStorage.getItem(obj._TABLES_KEY) !== '[]') {
  fails.push('a deliberately cleared scope came back: ' + sessionStorage.getItem(obj._TABLES_KEY));
}
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _scope_methods()).replace("__KEY__", TABLES_KEY)
    _assert_ok(_run_node(tmp_path, body))


def test_the_scope_is_restored_before_the_first_await():
    """init() must restore the scope before it can be torn down."""
    src = _chat_src()
    init = _extract(src, "async init() {", "async destroy() {", "init")
    restore = init.index("_readStoredTables()")
    first_await = init.index("await ")
    assert restore < first_await, (
        "init() must restore the stored scope before its first await — the "
        "router can call destroy() while init() is still awaiting, and "
        "destroy() persists the scope"
    )


def test_destroy_persists_the_scope_through_the_guarded_helper():
    """destroy() must use the guarded persist, not the raw one."""
    src = _chat_src()
    destroy = _extract(src, "async destroy() {", "_loadConversations() {", "destroy")
    assert "_persistScopeOnExit()" in destroy, (
        "destroy() must persist the scope through _persistScopeOnExit()"
    )
    assert "this._persistTables();" not in destroy, (
        "destroy() persists the scope unconditionally — an empty scope (the "
        "table list was not ready) overwrites the stored selection"
    )


# ── Defect 2: the subtitle is the user's question ─────────────────────

def test_sidebar_preview_uses_the_question_not_the_last_message():
    """_renderSidebar must preview the question, not the assistant answer."""
    src = _chat_src()
    sidebar = _extract(src, "_renderSidebar() {", "_startRename(id) {", "_renderSidebar")
    assert "_conversationSubtitle(" in sidebar, (
        "the conversation list must build its subtitle with "
        "_conversationSubtitle() (the user's last question)"
    )
    assert "find(m => m.content)" not in sidebar, (
        "the conversation list still previews the last message of any role — "
        "that is the assistant's (long, noisy) answer"
    )


def test_sidebar_subtitle_is_the_users_question(tmp_path):
    """The subtitle must be the user's last question."""
    body = """
const methods = { __METHODS__ };
const obj = { ...methods };
const conv = {
  id: 'c1', title: 'Row count',
  messages: [
    { role: 'user', content: 'How many rows are in the table?' },
    { role: 'assistant', content: 'Result Metric Value --- --- Group name North Value 816.95 Co' },
  ],
};
const fails = [];
const sub = obj._conversationSubtitle(conv);
if (sub !== 'How many rows are in the table?') {
  fails.push('subtitle is not the question: ' + JSON.stringify(sub));
}
if (/Result|Metric Value/.test(sub)) {
  fails.push('subtitle uses the assistant answer: ' + JSON.stringify(sub));
}
const conv2 = {
  id: 'c2', title: 'T',
  messages: [
    { role: 'user', content: 'first question' },
    { role: 'assistant', content: 'first answer' },
    { role: 'user', content: 'second question' },
    { role: 'assistant', content: 'second answer' },
  ],
};
const sub2 = obj._conversationSubtitle(conv2);
if (sub2 !== 'second question') {
  fails.push('subtitle is not the LAST question: ' + JSON.stringify(sub2));
}
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _subtitle_method())
    _assert_ok(_run_node(tmp_path, body))


def test_sidebar_subtitle_is_truncated(tmp_path):
    """A long question must be cut down to one readable line."""
    body = """
const methods = { __METHODS__ };
const obj = { ...methods };
const long = 'x'.repeat(400);
const conv = { id: 'c1', title: 'T', messages: [{ role: 'user', content: long }] };
const fails = [];
const sub = obj._conversationSubtitle(conv);
if (sub.length > 61) fails.push('subtitle is not truncated: ' + sub.length + ' chars');
if (!sub.endsWith('\\u2026')) fails.push('a truncated subtitle must end with an ellipsis');
if (!sub.startsWith('xxx')) fails.push('truncation dropped the start of the question');
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _subtitle_method())
    _assert_ok(_run_node(tmp_path, body))


def test_sidebar_subtitle_falls_back_without_a_user_message(tmp_path):
    """No user message yet → a sensible placeholder, never the answer."""
    body = """
const methods = { __METHODS__ };
const obj = { ...methods };
const fails = [];
const conv = { id: 'c1', title: 'T', messages: [
  { role: 'assistant', content: 'Result Metric Value --- --- Group name North Value 816.95 Co' },
] };
const sub = obj._conversationSubtitle(conv);
if (!sub) fails.push('the subtitle must not be empty');
if (/Result|Metric Value/.test(sub)) fails.push('the fallback repeats the assistant answer');
const empty = { id: 'c2', title: 'New chat', messages: [] };
const sub2 = obj._conversationSubtitle(empty);
if (!sub2) fails.push('the subtitle must not be empty for an empty conversation');
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _subtitle_method())
    _assert_ok(_run_node(tmp_path, body))


def test_sidebar_subtitle_keeps_the_question_readable(tmp_path):
    """Markdown in a question must not leak into the subtitle."""
    body = """
const methods = { __METHODS__ };
const obj = { ...methods };
const fails = [];
const conv = { id: 'c1', title: 'T', messages: [
  { role: 'user', content: '**total** | `sales` for the [north](https://x.test) region' },
] };
const sub = obj._conversationSubtitle(conv);
if (/\\*|\\||`/.test(sub)) fails.push('markdown leaked into the subtitle: ' + JSON.stringify(sub));
if (!/total/.test(sub)) fails.push('the question text was lost: ' + JSON.stringify(sub));
if (!/north/.test(sub)) fails.push('the link text was lost: ' + JSON.stringify(sub));
if (fails.length) { console.error('FAIL: ' + fails.join('; ')); process.exit(1); }
console.log('OK');
""".replace("__METHODS__", _subtitle_method())
    _assert_ok(_run_node(tmp_path, body))
