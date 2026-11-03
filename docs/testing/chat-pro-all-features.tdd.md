# Chat Pro all-features — TDD evidence

Date: 2026-10-24
Plan: in-session approved plan ("do the recommended and do all phases, verify,
then verify by Chrome"), no `*.plan.md` file.

Scope: every sidebar feature in Chat Pro becomes reachable from a natural-language
prompt as a deterministic chat task — no LLM planning required for the operation
itself:

- Analysis: anomalies, quality snapshot, quality trend, stats (t-test/chi-square/
  correlation), compare two tables, compare periods.
- Data: lineage, merge tables, connections (fail closed), data catalog search.
- Build: export, pipeline run, workflow approvals.
- Organisation: org structure, marketplace, notifications.

## User journeys

1. As a user, I want to type "detect anomalies in sales" and get the real IQR
   result, so I do not have to leave chat for the Analysis page.
2. As a user, I want "record a quality snapshot for sales" and "show the quality
   trend for sales" to run the real quality engine.
3. As a user, I want "run a t-test on sales comparing amount by region" to run
   the real statistical test, and "compare sales and sales_after on id" /
   "compare periods of sales by month" to run the real comparison engines.
4. As a user, I want "merge A and B on id into merged" to persist a joined table
   and record its lineage, so downstream work can continue in chat.
5. As a user, I want read-only feature asks (lineage, notifications, catalog
   search) to answer without any write side effects.
6. As a viewer, I must not be able to merge tables (or any other write) from
   chat, so a read permission cannot be escalated into a write.
7. As a free-tier user, gated features (org, marketplace, workflows, pipeline,
   export) must fail closed with an upgrade message, never silently succeed.
8. As any user, "show my data connections" must never expose connection
   credentials through chat.

## Red → green

| Stage | Command | Result |
| --- | --- | --- |
| RED (planner routing + executors) | `python -m pytest tests/test_chat_tasks_all_features.py -q` | failed: `agent.tools_features` missing, no feature ops in planner, `_plan` helper produced "No tables available" (planner schema context not seeded) |
| GREEN (planner routing + executors) | same | 34 passed |
| Regression (task pipeline) | `python -m pytest tests/test_tools_tasks.py tests/test_chat_tasks_stream.py tests/test_chat_tasks_all_features.py -q` | 53 passed |
| Full suite | `python -m pytest -q --deselect tests/test_cov_agent2.py::TestVizDeep::test_matplotlib_all` | 3356 passed, 3 skipped (685s) |

Defects found and fixed during the cycle:

- Stats regex matched "test on" inside "t-test on sales", producing
  `column_a='on'`. Fixed by preferring `comparing X by Y`, then
  `(?:test|testing) X by Y`, then `comparing X`.
- `Planner()` without `set_schema_context` has no tables, so feature planning
  failed for every prompt. Tests now seed a dummy schema context per table.
- Non-streaming `/ask` steps had no `operation` in metadata, so the UI could not
  label them. `_feature_task_plan` now sets `metadata["operation"]`.

## Guarantees

| # | Guarantee | Test |
| --- | --- | --- |
| 1 | "detect anomalies in X" plans one TASK step with op `anomalies` | `test_planner_anomalies` |
| 2 | Quality snapshot / trend prompts plan `quality_snapshot` / `quality_trend` | `test_planner_quality_trend`, `test_planner_quality_snapshot` |
| 3 | "t-test … comparing amount by region" plans `stats` with the right columns | `test_planner_stats` |
| 4 | "compare A and B on id" plans `compare_tables` with both tables; one table is not enough | `test_planner_compare_two_tables`, `test_planner_compare_needs_two_tables` |
| 5 | "compare periods … by month" plans `compare_periods` | `test_planner_compare_periods` |
| 6 | Lineage, merge, notifications, marketplace, org, catalog, pipeline, workflows, export, connections each plan their op | `test_planner_lineage`, `test_planner_merge`, `test_planner_notifications`, `test_planner_marketplace`, `test_planner_org_tree`, `test_planner_catalog`, `test_planner_pipeline`, `test_planner_workflows`, `test_planner_export`, `test_planner_connections` |
| 7 | Anomaly detection returns per-column outlier counts and never mutates the source | `test_detect_anomalies` |
| 8 | Snapshot is persisted and the trend reads it back | `test_quality_snapshot_and_trend` |
| 9 | Stats returns a p-value and a human-readable interpretation | `test_run_stats` |
| 10 | Table comparison reports modified/added/removed counts | `test_compare_tables` |
| 11 | Period comparison reports change and significance counts | `test_compare_periods` |
| 12 | Lineage lookup returns recorded transforms | `test_lineage_lookup` |
| 13 | Merge infers the first shared column when the key is omitted, persists the output, and records lineage | `test_merge_tables_infers_shared_key` |
| 14 | Notifications list and mark-read work for the session user | `test_notifications` |
| 15 | Marketplace / org / export require their entitlement and fail closed | `test_marketplace_products_requires_entitlement`, `test_org_tree_requires_entitlement`, `test_export_requires_entitlement` |
| 16 | Catalog search requires `catalog:read`; sync / pipeline / workflow approvals fail closed without the entitlement | `test_catalog_search_requires_permission`, `test_catalog_sync_fails_closed`, `test_pipeline_run_fails_closed`, `test_workflow_pending_fails_closed` |
| 17 | Connections never return credentials; the op always explains the admin path | `test_connections_fail_closed` |
| 18 | A viewer cannot merge tables from chat | `test_merge_refuses_viewer` |

## Regression runs

- `tests/test_tools_tasks.py tests/test_chat_tasks_stream.py tests/test_chat_tasks_all_features.py`
  → 53 passed.
- Full suite → 3356 passed, 3 skipped, 1 deselected. The deselected test
  (`test_cov_agent2.py::TestVizDeep::test_matplotlib_all`) is a known
  pre-existing failure ("zero-size array to reduction operation fmin") on an
  untouched file.
- `node --check web/js/pages/chat-pro.js` → OK.
- Coverage gate `fail_under = 74` in `pyproject.toml` is a pre-existing failure
  (measured ~73% tree scope); not lowered for this change.

## Live UI verification (Chrome DevTools MCP)

Server `http://127.0.0.1:8002`, account `mcp_tester` (role `user`, free tier),
tenant schema `analytics_t_u_bjpxpgbd4o93ewnktdqroa_647336a4`, 11 tables.
Prompts sent in one Chat Pro conversation:

| Prompt | Observed result |
| --- | --- |
| `detect anomalies in mcp_sales_demo` | `Found 0 outlier(s) across 0 column(s) in mcp_sales_demo (iqr).` (0 outliers at IQR 1.5; count = columns with outliers) |
| `record a quality snapshot for mcp_sales_demo` | `Recorded a quality snapshot for mcp_sales_demo (score 88).` |
| `show the quality trend for mcp_sales_demo` | `mcp_sales_demo has 1 snapshot(s) in the last 30 day(s) (1 total).` |
| `run a t-test on mcp_sales_demo comparing amount by region` | `ttest: p = 2.6e-05 (… statistically significant … effect size is large.)` |
| `compare mcp_sales_demo and mcp_sales_demo_clean on order_date` | `Compared mcp_sales_demo → mcp_sales_demo_clean: 0 modified, 0 added, 0 removed.` |
| `compare periods of mcp_sales_demo by month` | `Compared 9 changes. 0 significant.` |
| `show lineage for mcp_sales_demo` | `No lineage recorded for mcp_sales_demo yet.` (correct; it was never produced by a tracked transform) |
| `merge mcp_sales_demo and mcp_sales_demo_clean on order_date into cpx_merge_demo` | `Merged … → cpx_merge_demo (20 rows).` + table artifact `cpx_merge_demo` |
| `show lineage for cpx_merge_demo` | `cpx_merge_demo has 1 lineage record(s).` API confirms `operation: merge`, `inputs: [mcp_sales_demo, mcp_sales_demo_clean]`, `join_type: inner`, `rows: 20` |
| `show my notifications` | `You have 0 notification(s).` |
| `search the data catalog for sales` | `Found 0 catalog match(es) for “sales”.` (catalog is empty for this tenant) |
| `show the org structure` | Fail closed: "The organization view requires a plan that includes this feature. Upgrade in Billing to use it from chat." |
| `show marketplace products` | Fail closed: "The data marketplace requires a plan…" |
| `show pending workflow approvals` | Fail closed: "Workflow approvals requires a plan…" |
| `run the pipeline for mcp_sales_demo` | Fail closed: "Running pipelines requires a plan…" |
| `export mcp_sales_demo as csv` | Fail closed: "Exporting tables requires a plan…" |
| `show my data connections` | Blocked by the free-tier daily AI cap (25/25) before execution; fail-closed behavior is covered by `test_connections_fail_closed` |

Console: no JS errors. Network: all `/llm/chat/stream` calls 200 except the
final 429 from the daily AI cap. The single 401 was a deliberate bare-`fetch`
auth probe.

Screenshot: `outputs/mcp-chat-pro-all-features.png` (full conversation).

## Charts for feature tasks (follow-up)

Date: 2026-10-24. Feature executors returned text only, so Chat Pro showed no
chart even though the Tools tab draws charts for the same operations. Feature
tasks now attach Plotly specs (`{"chart_type", "spec": {"data", "layout"}}`) to
`TaskResult.visualizations`; the orchestrator emits them as per-step `viz`
events and appends them to `response.visualizations`; `/ask` serializes dict
specs (and no longer assumes every step result carries a DataFrame).

| Stage | Command | Result |
| --- | --- | --- |
| RED (charts) | `python -m pytest tests/test_chat_tasks_all_features.py -k "chart or emits or skip_chart or gauge" -q` | 9 failed: `TaskResult` had no `visualizations`, `_execute_plan` emitted no `viz` event, `_build_ask_response` raised `AttributeError: 'dict' object has no attribute 'success'` |
| GREEN (charts) | same | 9 passed |
| Feature suite | `python -m pytest tests/test_chat_tasks_all_features.py -q` | 44 passed |
| Regression (stream/ask/tasks) | `python -m pytest tests/test_chat_visuals.py tests/test_cov_main_routes.py tests/test_chat_tasks_stream.py tests/test_tools_tasks.py -q` | 137 passed |
| Full suite (charts) | `python -m pytest -q --deselect tests/test_cov_agent2.py::TestVizDeep::test_matplotlib_all` | 3366 passed, 3 skipped (518s) |

Additional guarantees:

| # | Guarantee | Test |
| --- | --- | --- |
| 19 | Anomaly results emit a bar spec with one bar per outlier column and the real counts | `test_anomalies_emit_bar_chart` |
| 20 | A clean table emits no chart | `test_anomalies_skip_chart_when_clean` |
| 21 | Quality snapshot emits a 0–100 indicator gauge at the computed score | `test_quality_snapshot_emits_gauge` |
| 22 | Quality trend emits a line spec over the snapshot scores | `test_quality_trend_emits_line` |
| 23 | Stats emits a bar spec of group means/medians | `test_stats_emits_group_bar` |
| 24 | Table comparison emits modified/added/removed counts | `test_compare_tables_emits_counts_bar` |
| 25 | Period comparison emits a line spec per group | `test_compare_periods_emits_line` |
| 26 | `_execute_plan` forwards each TaskResult chart as a `viz` progress event | `test_execute_plan_emits_task_charts` |
| 27 | `/ask` serializes dict specs and tolerates task steps whose `data` is a dict | `test_build_ask_response_serializes_feature_chart`, `test_build_ask_response_handles_task_step` |
| 28 | A lineage answer lists each record's operation, timestamp, inputs → outputs, row count, and description instead of only a count | `test_lineage_lookup_message_lists_record_details` |

Live verification (server restarted, account `chartprobe`, tenant
`u_2Mf6tBgCg7YpaiLs4UOT5A`):

| Prompt | Chart observed |
| --- | --- |
| `detect anomalies in sales_probe` | Plotly bar "Outliers by column — sales_probe" (`amount`, `quantity`) |
| `record a quality snapshot for sales_probe` | Plotly indicator gauge "Quality score — sales_probe" at 91 |
| `run a t-test on sales_probe comparing amount by region` | Plotly bar "amount by group" (`west`, `east`) |
| `compare periods of sales_probe by month` | Plotly line "amount by month" across Jan–May 2026 |

Each answer also ends with `📊 1 chart — see above.`; console had no errors.
Screenshots: `outputs/chatprobe-chart-01-anomalies.png`,
`outputs/chatprobe-chart-02-quality-gauge.png`,
`outputs/chatprobe-chart-03-stats-bar.png`,
`outputs/chatprobe-chart-04-compare-periods-line.png`.

Lineage answers were also completed: `show lineage for sales_probe` now renders
`load · 2026-10-24 09:49 UTC · sales_probe.csv → sales_probe · 24 rows` plus the
description, instead of only "has 1 lineage record(s)".
Screenshot: `outputs/chatprobe-lineage-detail.png`.

## Gaps and leftovers

- The merge created a real table, `cpx_merge_demo`, in the live tenant schema.
  It is disposable; drop it with admin rights when cleaning up.
- Deterministic feature tasks still consume the free-tier daily AI request quota
  because the meter runs at the chat-stream endpoint, before planning. Worth
  revisiting if free users are expected to run many no-LLM tasks.
- `connections_list` was not exercised live (quota); unit coverage only.
- Catalog search returned 0 matches because no assets are certified for this
  tenant; the success path with results is covered by the manager's own tests.
- The anomaly summary counts columns that contain outliers, not numeric columns
  scanned; wording is accurate but can read as "0 columns analysed".
- `ruff` is not installed in this environment, so no lint run beyond
  `node --check`.
- The quality-trend line and compare-tables bar were not exercised live; they
  share the line/bar render paths verified by the period-comparison and stats
  charts and are covered by tests.
- Feature charts render through Plotly in Chat Pro; the static export path for
  a dict spec (`_viz_event_item`) was verified by unit test, not live `/ask`.
