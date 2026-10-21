# ADR-006 — Live agentic chat for Chat Pro (`stream_steps` + LLM plan escalation)

Date: 2026-10-17
Status: Implemented (opencode chat plan; phased TDD; full suite re-run green
modulo known pre-existing failures)

## Context

Chat Pro (`#chat-pro`, `web/js/pages/chat-pro.js`) streamed only answer text
and one final `visualizations` batch after the whole pipeline finished. Two
product gaps drove this change:

1. **No visible process.** A multi-step question (SQL → chart → SQL → chart)
   showed a typing indicator for the entire run, then text and charts at once —
   no sense of what the agent was doing or that it was still alive.
2. **Thin planning on big asks.** The rule-based planner produces one good
   step for explicit chart requests, but broad asks ("give me a full overview
   of the sales data") ended in a single generic SQL step. The existing
   `Planner.use_llm_plan` escape hatch was all-or-nothing, had no validation,
   and was off for the chat endpoint.

Hard constraints:

- **Legacy chat byte-compatibility.** `web/js/pages/chat.js:999-1014`
  concatenates any non-`__visualizations` stream object into the answer text.
  New SSE event shapes must be opt-in.
- **`_llm_plan` is a parsed-contract seam.** `tests/test_cov_agent2.py:722-738`
  asserts its raw output shape (weird tools kept, original step ids). It must
  stay raw; all sanitization happens in the new escalation path.
- One writer on the DuckDB warehouse; SSE endpoint already bounded (180s).

## Decision

1. **`stream_steps` opt-in flag on `/llm/chat/stream`** (`QuestionRequest`,
   api/main.py). When true the endpoint runs the pipeline in a task, relays
   orchestrator progress as `{"step": ...}` / `{"viz": ...}` SSE events before
   the tokens, and **skips** the final `visualizations` batch (charts already
   streamed — sending both renders every chart twice). Flag absent/false keeps
   the legacy byte-identical contract. `api.js askStream()` surfaces the events
   as `{__step}` / `{__viz}`; only `chat-pro.js` requests them (last arg
   `true`).
2. **Orchestrator progress callback** (`answer_question(..., progress=cb,
   allow_llm_plan=False)`). Events: `plan`, `step_start`, `step_done`
   (ms/rows), `viz` (VizResult), `answering`. Sync or async callbacks; callback
   exceptions are swallowed so a broken UI never breaks an answer. Default
   (`progress=None`) is byte-identical to before.
3. **Opt-in LLM plan escalation in `Planner.create_plan(..., allow_llm=False)`**
   — escalate to the LLM only when `allow_llm` **and** an `llm_client` exists
   **and** the rule plan is thin (≤1 step, no chart) **and** the question is a
   big-analysis ask (`overview|full|complete|analyze|report|dashboard|audit|
   deep-dive…`). The rules plan is the fallback on any LLM failure, timeout
   (`Orchestrator.plan_llm_timeout_seconds`, default 30s, thread-wrapped), or
   invalid output.
4. **`_sanitize_llm_plan` validates escalated plans**: SQL read-only
   (`check_read_only`), chart type allow-list, x/y contracts, surviving-SQL
   requirement, ≤6 steps, sequential renumbering with `depends_on` remap and
   auto-attach of a dep-less chart to the last SQL step. Surviving steps carry
   `{skip_llm_upgrade: true, llm_planned: true}` metadata. `_llm_plan` itself
   is untouched.
5. **Chat Pro UI**: live steps panel (`.cpx-steps`, `data-cpx-steps`) above
   the charts, one row per step with running spinner/done/failed states; charts
   append via `vizSpecs.concat(...)` (never replace — replacing innerHTML wipes
   existing Plotly canvases); the panel collapses to a `✓ N steps · M charts ·
   X.Xs` summary at the end; stored messages re-render their `steps` collapsed
   with a toggle. Non-string stream objects can never concatenate into the
   answer text (`typeof token !== 'string'` guard).

## Consequences

- Legacy `#chat` and every other stream consumer are unaffected (flag off by
  default; legacy batch still emitted).
- Chat Pro costs one extra LLM call only for thin-plans-on-big-asks; the
  merged plan is sanitized so a hallucinated chart type or unsafe SQL degrades
  to the rules plan instead of erroring.
- Step payloads use `type: plan|start|done|answering` and `id` (mapped from
  the orchestrator's `kind`/`step_id`) — the SSE wire shape is now a small,
  documented translation layer (`_step_event_payload`, `_viz_event_item`).
- Known trade-off: while `stream_steps` is on, the client cannot double-render
  from a batch; any new chart source must go through `{viz}` events there.

## Verification

- New tests: `tests/test_planner_llm_escalation.py` (22), 
  `tests/test_orchestrator_progress.py` (6 — sync/async callbacks, timeout,
  crash-safety), `tests/test_chat_stream_progress.py` (3 — live events, legacy
  batch, small talk), `tests/test_chat_pro_stream_steps.py` (5 static FE
  contract), cache-buster bump in `tests/test_chat_pro_ui.py`.
- Debugging notes: async progress callbacks are scheduled tasks — tests use
  sync list-appenders for determinism; the endpoint must `queue.put_nowait`
  directly (a `call_soon_threadsafe` hand-off loses events after the
  synchronous pipeline's `None` terminator).
- Fixed en route: `_is_project_help_question` treated any question containing
  "overview" as project help, swallowing data asks ("full overview of the
  sales data") — now requires a project-context word or absence of data words.
- Live E2E (Playwright, real server + warehouse, rule-planned flow): a
  two-chart question streamed `plan` → 4 step rows flipping
  pending → running (spinner) → done with per-step ms/rows, 2 Plotly charts
  rendered (bar + line, append-only across questions), panel auto-collapsed
  to `✓ 4 steps · 2 charts · 0.3s`; manual toggle re-expands and survives
  re-renders. DOM mutation timeline captured
  (`0 of 2 steps` → `1:running` → `1:done/2:running` → `✓ 2 steps · 1 chart`)
  including the transient spinner states. Note: first SQL-LLM upgrade call
  on a cold session took ~40s before step 1 started (pre-existing sqlgen
  upgrade path, unrelated to this change; warm calls ~15s).
- Full suite: 1878 passed; 22 failures + 1 error all pre-existing
  environment/platform issues (7 `test_cov_agent2` numpy/pandas/warp/llama/
  network; 13 `test_cov_export2` stale `_build_plan_from_spec` re-export
  failing since 2026-10-06 per `logs/audit.log` + Unix-only `fcntl`; pydantic
  CORS probe; billing-db artifact; subscription page drift).
