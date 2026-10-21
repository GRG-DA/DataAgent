"""Chat persistence follow-ups: stale serverId (404) and localStorage quota.

Bug 3 — stale serverId: when the server-side conversation is gone (DB reset,
deleted elsewhere), the local copy keeps a dead `serverId`. Rename and message
sync then 404 forever: the conversation never re-creates on the server and new
messages silently stop uploading.

Bug 4 — localStorage quota: `vizHtml`/`vizSpecs` chart payloads dominate the
`chat_conversations` store; on QuotaExceeded the bare catch drops the ENTIRE
history instead of retrying with a slimmed copy.

RED: both behaviors are absent from web/js/pages/chat.js.
GREEN: a 404 clears `conv.serverId` (message sync also kicks a re-sync), and
_save_conversations retries: full payload → viz-stripped payload → last 10
conversations.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHAT_JS = ROOT / "web" / "js" / "pages" / "chat.js"


def _chat_src() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _extract(src: str, start: str, end: str, label: str) -> str:
    s = src.find(start)
    assert s != -1, f"{label}: start marker {start!r} not found in chat.js"
    e = src.find(end, s)
    assert e != -1, f"{label}: end marker {end!r} not found in chat.js"
    return src[s:e]


def _extract_optional(src: str, start: str, end: str) -> str:
    if src.find(start) == -1:
        return ""
    return _extract(src, start, end, start)


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


# ── Bug 4: localStorage quota ─────────────────────────────────────────

def test_save_conversations_quota_persists_text_without_viz(tmp_path):
    """On QuotaExceeded, keep the chat text — drop only the chart payloads."""
    src = _chat_src()
    save = _extract(src, "_saveConversations() {",
                    "async _syncFromServer(", "_saveConversations")
    strip = _extract_optional(src, "_stripVizPayload(",
                              "_saveConversations() {")
    body = f"""
const methods = {{
{strip}
{save}
}};
const convs = [
  {{ id: 'c1', title: 'T1', serverId: 1, messages: [
      {{ role: 'user', content: 'keep this text', vizHtml: 'V'.repeat(3000),
         vizSpecs: [{{ data: [1], layout: 1 }}] }} ] }},
  {{ id: 'c2', title: 'T2', messages: [
      {{ role: 'assistant', content: 'keep that text',
         vizHtml: 'V'.repeat(3000) }} ] }},
];
// Quota = exact size of the viz-stripped payload: tier 1 (full) must fail,
// the stripped retry must fit.
const stripped = JSON.parse(JSON.stringify(convs));
for (const c of stripped) for (const m of (c.messages || [])) {{
  delete m.vizHtml; delete m.vizSpecs;
}}
const LIMIT = JSON.stringify(stripped).length;
const store = {{}};
globalThis.localStorage = {{
  setItem(k, v) {{
    if (v.length > LIMIT) {{
      const e = new Error('QuotaExceeded'); e.name = 'QuotaExceeded'; throw e;
    }}
    store[k] = v;
  }},
  getItem(k) {{ return k in store ? store[k] : null; }},
}};
const obj = {{ _STORAGE_KEY: 'chat_conversations', _conversations: convs, ...methods }};
obj._saveConversations();
const raw = localStorage.getItem('chat_conversations');
if (!raw) {{
  console.error('FAIL: nothing persisted under quota — the whole history was dropped');
  process.exit(1);
}}
const parsed = JSON.parse(raw);
const texts = JSON.stringify(parsed);
if (!texts.includes('keep this text') || !texts.includes('keep that text')) {{
  console.error('FAIL: message text lost by the quota fallback: ' + texts.slice(0, 300));
  process.exit(1);
}}
if (raw.includes('vizHtml') || raw.includes('vizSpecs')) {{
  console.error('FAIL: chart payloads still present in the persisted copy');
  process.exit(1);
}}
console.log('OK');
"""
    _assert_ok(_run_node(tmp_path, body))


# ── Bug 3: stale serverId on 404 ──────────────────────────────────────

def test_sync_messages_404_clears_server_id_and_resyncs(tmp_path):
    """A 404 from addMessage must clear the stale serverId and re-sync.

    After that the next _syncFromServer re-creates the conversation on the
    server and re-uploads the local messages; without clearing, every future
    upload keeps 404ing against a dead id.
    """
    src = _chat_src()
    sync = _extract(src, "_syncMessages(conv) {",
                    "async _newConversation(", "_syncMessages")
    body = f"""
const calls = {{ n: 0 }};
globalThis.API = {{
  addMessage: async () => {{
    calls.n++;
    const e = new Error('Not Found'); e.status = 404; throw e;
  }},
}};
const methods = {{ {sync} }};
const obj = {{
  heals: 0,
  _syncFromServer() {{ this.heals++; }},
  ...methods,
}};
const conv = {{ serverId: 42, _syncedCount: 1, messages: [
  {{ role: 'user', content: 'a' }},
  {{ role: 'assistant', content: 'b' }},
  {{ role: 'user', content: 'c' }},
] }};
obj._syncMessages(conv);
setTimeout(() => {{
  const fails = [];
  if (conv.serverId !== null) fails.push('serverId not cleared (still ' + conv.serverId + ')');
  if (calls.n !== 1) fails.push('expected upload to stop after 404, calls=' + calls.n);
  if (obj.heals !== 1) fails.push('expected one re-sync after clearing serverId, heals=' + obj.heals);
  if (fails.length) {{ console.error('FAIL: ' + fails.join('; ')); process.exit(1); }}
  console.log('OK');
}}, 150);
"""
    _assert_ok(_run_node(tmp_path, body))


def test_rename_404_clears_server_id(tmp_path):
    """A 404 from renameConversation must clear the stale serverId too."""
    src = _chat_src()
    rename = _extract(src, "_renameConversation(id, title) {",
                      "_generateTitle(text) {", "_renameConversation")
    rename_server = _extract_optional(src, "_renameServer(conv) {",
                                      "_renameConversation(")
    body = f"""
globalThis.API = {{
  renameConversation: async () => {{
    const e = new Error('Not Found'); e.status = 404; throw e;
  }},
}};
globalThis.App = {{ toast: () => {{}} }};
const methods = {{
{rename_server}
{rename}
}};
const conv = {{ id: 'c1', title: 'Old title', serverId: 7,
                messages: [{{ role: 'user', content: 'x' }}] }};
const obj = {{
  _conversations: [conv],
  _activeConversationId: 'c1',
  _STORAGE_KEY: 'chat_conversations',
  _saveConversations() {{}},
  _renderSidebar() {{}},
  _updateHeaderTitle() {{}},
  ...methods,
}};
obj._renameConversation('c1', 'New title');
setTimeout(() => {{
  const fails = [];
  if (conv.serverId !== null) fails.push('serverId not cleared (still ' + conv.serverId + ')');
  if (conv.title !== 'New title') fails.push('local rename lost: ' + conv.title);
  if (fails.length) {{ console.error('FAIL: ' + fails.join('; ')); process.exit(1); }}
  console.log('OK');
}}, 150);
"""
    _assert_ok(_run_node(tmp_path, body))


def test_update_title_uses_shared_404_safe_rename(tmp_path):
    """_updateConversationTitle must go through the same 404-safe path."""
    src = _chat_src()
    block = _extract(src, "_updateConversationTitle() {",
                     "_updateHeaderTitle(", "_updateConversationTitle")
    assert ("_renameServer(" in block) or ("status === 404" in block), (
        "_updateConversationTitle still calls API.renameConversation with a "
        "bare catch — a stale serverId 404s forever and the conversation "
        "never re-creates on the server"
    )
