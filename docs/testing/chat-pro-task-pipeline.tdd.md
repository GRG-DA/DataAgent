# Chat Pro task pipeline â€” TDD evidence

Date: 2026-10-24
Plan: in-session approved plan (clean â†’ dashboard â†’ forecast side effects in
the streamed chat), no `*.plan.md` file.

## User journeys

1. As a user, I want to say "clean task_sales and build a dashboard from it"
   and get a persisted cleaned table plus a saved dashboard link, so I do not
   have to run two separate flows.
2. As a viewer, I must not be able to clean tables or save dashboards from
   chat, so `query:execute` cannot be escalated into writes.
3. As a user, I want "forecast sales for the next 6 months" to run the real
   forecast model with that horizon, not just explain how to forecast.
4. As a user, I want an interrogative data-quality question ("how clean is
   this table?") to stay read-only.

## Red â†’ green

| Stage | Command | Result |
| --- | --- | --- |
| RED (planner + executors) | `python -m pytest tests/test_tools_tasks.py -q` | 10 failed (missing `agent.tools_tasks`, missing `ToolType.TASK`; planner cases first failed on empty schema context) |
| GREEN (planner + executors) | same | 10 passed |
| RED/GREEN (stream + RBAC) | `python -m pytest tests/test_chat_tasks_stream.py -q` | 6 passed; one flawed test (monkeypatched away the permission check) rewritten to call the real executor â†’ 6 passed |
| E2E added | same file, `test_task_pipeline_end_to_end` | 7 passed (temp warehouse: clean â†’ table artifact â†’ dashboard artifact â†’ `#blankdash/` link â†’ artifact in `step_done`) |

## Guarantees

| # | Guarantee | Test |
| --- | --- | --- |
| 1 | "clean X and build a dashboard" plans TASK clean â†’ TASK dashboard with `source_step` dependency | `test_planner_clean_then_dashboard_plan` |
| 2 | Dashboard-only asks plan one TASK step; forecast horizon is parsed (6 default) | `test_planner_dashboard_only_plan`, `test_planner_forecast_plan_parses_horizon` |
| 3 | Interrogative data-quality asks stay on the read-only SQL path | `test_planner_data_quality_question_stays_read_only` |
| 4 | Cleaning persists a new table and never the source | `test_clean_table_persists_new_table` |
| 5 | Cleaning requires `data:write`; dashboard requires `dashboard:create` (fails closed) | `test_clean_table_requires_write_permission`, `test_build_dashboard_requires_permission`, `test_execute_task_step_denies_viewer` |
| 6 | Dashboard build returns `{kind, id, name, url}` artifact | `test_build_dashboard_saves_artifact` |
| 7 | Forecast returns exactly `periods` values | `test_forecast_table_returns_horizon` |
| 8 | Task message reaches the answer; polish is skipped so links survive | `test_rule_answer_includes_task_message`, `test_build_answer_keeps_task_message_unpolished` |
| 9 | SSE `done` payload forwards `artifact`; payload without one is unchanged | `test_step_event_payload_carries_artifact`, `test_step_event_payload_without_artifact_unchanged` |
| 10 | A dependent task reads the cleaned table and derives `owner_id` from the user | `test_execute_task_step_uses_cleaned_source_table` |
| 11 | Full pipeline: cleaned table exists, artifacts are `[table, dashboard]`, link in answer, artifact event emitted | `test_task_pipeline_end_to_end` |

## Regression runs

- `tests/test_dashboard_build.py tests/test_data_clean_apply.py tests/test_clean_preview_sqli.py tests/test_security_regressions.py tests/test_ai_chat_memory.py tests/test_agent_tenant_isolation.py` â†’ 59 passed.
- Planner + orchestrator progress suites â†’ 81 passed.
- Chat/stream suites (`test_chat_pro_stream_steps`, `test_chat_stream_progress`, `test_chat_visuals`, `test_ask_request_cancellable`) â†’ all passed.
- Full suite: 3305 passed, 3 skipped, 2 failed. Both failures are pre-existing and unrelated (they fail in isolation on untouched files): `test_cov_agent2.py::TestVizDeep::test_matplotlib_all` (zero-size array in `tools_viz`) and `test_cov_etl_warehouse.py::test_basic_clean_empty_strings_and_dates` (pandas 3 datetime dtype).

## Gaps

- Forecast frequency is inferred from the source series; `next 3 months` on a
  daily table means 3 inferred (daily) periods, not calendar months yet.
- Non-stream `/ask` does not expose artifacts (chat-pro uses the SSE stream).
- `ruff` is not installed in this environment, so no lint run.
- Cleaning defaults are fixed in the planner (dedupe + drop-null rows); custom
  strategies are supported by the executor but not parsed from chat yet.

## Live UI verification (Chrome DevTools MCP)

- Server: `http://127.0.0.1:8002`; account `mcp_tester` (role `user`).
- Uploaded `mcp_sales_demo.csv` (22 data rows, 2 exact duplicates) via
  Upload -> Load to Warehouse -> table `mcp_sales_demo`.
- Chat Pro message: "clean mcp_sales_demo and build a dashboard from it".
  - Clean step persisted `mcp_sales_demo_clean2` (source untouched).
  - Dashboard artifact opened `#blankdash/2f2b311b-e272-4c23-b2bb-f87a95710138`:
    name `Chat Pro — mcp_sales_demo_clean2 — 2026-10-24`, data source bound,
    6 widgets (KPI total 2,656; monthly trend; amount by notes; histogram;
    region bubble map; custom HTML header). `GET /dashboards/{id}` -> 200.
- Screenshots: `outputs/mcp-chat-task.png` (answer + artifact chips) and
  `outputs/mcp-dashboard.png` (saved dashboard).
- First live run exposed an ownership bug: `_user_identifier` read `user.id`
  but request users expose `user_id`, so the dashboard was saved with an
  empty owner and the deep link 404'd. Fixed to prefer `user_id` and added
  `test_user_identifier_prefers_request_user_id`. Re-ran the flow after a
  server restart: 200 + widgets rendered.
- Follow-up ask `forecast amount for the next 3 months` produced one TASK
  forecast step and three values (158.53 -> 163.43) from `mcp_sales_demo`.
- Re-ran `tests/test_tools_tasks.py tests/test_chat_tasks_stream.py
  tests/test_dashboard_build.py` -> 24 passed.

### Second dataset (different prompt / task mix)

- Uploaded `support_tickets.csv` (14 rows, 2 exact duplicates; monthly
  `opened_at` Jan-Dec 2025) as table `support_tickets`. The upload path runs
  the automatic cleaner, so the table held 12 rows (duplicates removed).
- Message "clean support_tickets and forecast resolution_hours for the next
  4 periods" produced 2 TASK steps (clean + forecast, no dashboard):
  `support_tickets_clean2`, 12 -> 12 rows (idempotent clean); monthly forecast
  2026-01 .. 2026-04 (8.05, 7.69, 9.75, 8.66).
- The first attempt returned 6 periods: `_TASK_FORECAST_RE` and
  `_TASK_HORIZON_RE` did not accept the word "periods", so the horizon regex
  missed and the default (6) applied. Added
  `test_planner_forecast_horizon_accepts_periods_word` (RED: `assert 6 == 4`),
  added `periods?` to both regexes, GREEN: 19 passed. Re-ran live: 4 periods.
- Follow-up read-only question "which priority has the slowest resolution on
  average?" ran a group-by + bar chart: medium 6.43h, high 6.30h, low 5.10h
  (chart + table), source `support_tickets`.
- Screenshot: `outputs/mcp-support-forecast.png`.


