# ADR-017: Chat Pro executes tasks (clean, dashboard, forecast) in-stream

- **Status:** Accepted
- **Date:** 2026-10-24

## Context

Chat Pro streamed analysis steps but could only read data. Users asking to
"clean this table and build a dashboard" got an explanation, not the work.
The side effects already existed as HTTP endpoints (`POST /clean/apply`,
`POST /dashboards`, analysis helpers), but the agent had no way to invoke
them and no UI contract for returning the created artifact.

Side effects inside `/llm/chat` are not covered by the RBAC middleware's
path gate (`/llm/chat` â†’ `query:execute`), so running them without an
in-handler check would let a `viewer` write tables and dashboards.

## Decision

1. **Planner task steps.** Add `ToolType.TASK` and a rule-based `_task_plan`
   that routes explicit action requests â€” clean (`clean|dedupe|deduplicate|
   tidy`), dashboard (build verb + `dashboard`), forecast (`forecast|predict|
   projection|next N months`) â€” before chart/intent planning. Interrogative
   data-quality questions ("how clean is this table?") stay read-only on the
   SQL path. LLM-generated plans cannot emit TASK steps (`_sanitize_llm_plan`
   accepts SQL/VIZ only), so side effects are deterministic.
2. **Executors in `agent/tools_tasks.py`.** `clean_table` reuses
   `_clean_dataframe_ops` and always writes a NEW table (`<table>_clean`,
   suffix on collision), never the source. `build_dashboard` calls the
   headless `create_dashboard_for_table`. `forecast_table` uses
   `analysis.advanced_forecast` with `method="auto"` and a bounded horizon.
3. **Fail-closed RBAC per task.** Each mutating executor calls
   `_require_permission` (`data:write` for cleaning, `dashboard:create` for
   dashboards) against the request user's role/API-key scopes. A denial
   returns a normal failed step, not a 500.
4. **Artifacts on the stream.** `AgentResponse.artifacts` collects
   `{kind: table|dashboard, ...}` from successful task results; the
   orchestrator's `step_done` progress event carries the artifact, and
   `_step_event_payload` forwards it to the SSE stream. Chat Pro renders a
   dashboard link (`#blankdash/{id}`) or a saved-table chip under the answer.
5. **Verbatim answer.** When any TASK step ran, the rule-based answer is
   returned without the LLM polish pass so the artifact link is never
   paraphrased away.

## Consequences

- A single chat message can clean, persist, dashboard, and answer in one
  streamed response with live step updates and a working deep link.
- Every task step is independently authorized; the stream cannot be used to
  escalate from `query:execute` to `data:write`/`dashboard:create`.
- Cleaning is deterministic and auditable; the source table is immutable from
  chat.
- Non-stream `/ask` does not expose artifacts yet (chat-pro uses the stream);
  adding it is additive.

## Tests

- `tests/test_tools_tasks.py` â€” planner routing, persisted cleaning, RBAC,
  dashboard artifact, forecast horizon, answer shaping.
- `tests/test_chat_tasks_stream.py` â€” SSE payload contract, per-step RBAC,
  source-step table resolution, end-to-end clean â†’ dashboard pipeline.
- Live UI verification caught one integration bug that unit fixtures masked:
  the request `User` exposes `user_id` (not `id`), so `_user_identifier`
  returned an empty owner and the saved dashboard was admin-only (deep link
  returned 404). `_user_identifier` now prefers `user_id`; covered by
  `test_user_identifier_prefers_request_user_id`.
- A second live run (clean + forecast on `support_tickets.csv`, "next 4
  periods") caught a horizon parsing gap: the regexes accepted months /
  quarters / weeks / days / years but not "periods", so the request fell back
  to the default 6. Both regexes now accept `periods?`; covered by
  `test_planner_forecast_horizon_accepts_periods_word`.
