# Tools + Excel live verification — TDD evidence

Date: 2026-10-24
Scope: live verification of the Excel workbench and Tools pages for a
self-serve `user` account, with fixes for defects found during the pass.

## User journeys

1. As an analyst, I want missing values to stay NULL through upload, cleaning,
   and DuckDB load, so downstream statistics and exports never treat the
   literal string `"nan"` as data.
2. As an analyst, I want dates to remain dates after the NULL-preserving clean
   path, so time-series tools and charts work.
3. As an analyst, I want a T-Test or Mann-Whitney U with more than two groups
   to fail with an actionable message instead of silently comparing the first
   two groups.
4. As an analyst, I want the ANOVA interpretation to name the groups it
   compared and to read grammatically.
5. As an analyst, I want the Lineage page to show an honest empty state rather
   than a phantom node, and to not claim automatic tracking that does not
   exist.
6. As an analyst, I want lineage to be captured automatically for persisted
   tables, isolated per tenant, so I can trace a load or clean without a manual
   API call.
7. As an analyst, I want a lineage-recording failure to never break my load or
   clean.

## Red -> green

| Stage | Command | Result |
| --- | --- | --- |
| RED (NaN corruption) | `python -m pytest tests/test_cov_etl_warehouse.py::test_basic_clean_does_not_stringify_missing_values -q` | failed: `np.int64(1) == 3` (two missing values became `"nan"`) |
| GREEN (NaN corruption) | same | 1 passed |
| RED (date dtype regression) | `python -m pytest tests/test_cov_etl_warehouse.py::test_cleaner_infer_plan_parses_nullable_string_dates -q` | failed: `opened_at` inferred as text, not datetime |
| GREEN (date dtype regression) | same | 1 passed |
| RED (T-Test guard) | `python -m pytest tests/test_cov_analysis.py::TestHypothesis::test_ttest_rejects_more_than_two_groups -q` | failed: `assert True is False` (3 groups accepted) |
| GREEN (T-Test guard) | same | 1 passed |
| RED (lineage phantom) | `python -m pytest tests/test_cov_analysis2.py::TestLineage::test_dag_skips_empty_records -q` | failed: DAG had 2 nodes for 1 real transform |
| GREEN (lineage phantom) | same | 1 passed |
| Regression (ANOVA copy) | `python -m pytest tests/test_cov_analysis.py::TestHypothesis -q` | 93 passed |
| RED (automatic lineage) | `python -m pytest tests/test_lineage_auto.py -q` | 7 of 9 original tests failed: no automatic records; tenant stores shared; concurrent writes lost; load/clean/chat not recording; malformed payload accepted |
| GREEN (automatic lineage) | same | 10 passed (9 original + history-escape test) |
| RED (history escaping) | `python -m pytest tests/test_lineage_auto.py::test_lineage_history_escapes_inputs_and_outputs -q` | failed: history rendered raw `r.inputs.join` / `r.outputs.join` |
| GREEN (history escaping) | same | 1 passed |
| Regression (lineage routes + runtime paths) | `python -m pytest tests/test_data_clean_apply.py tests/test_cov_main_routes.py tests/test_load_endpoint_empty.py tests/test_generic_errors.py tests/test_launch_frozen_runtime_paths.py -q` | 138 passed |
| Regression (chat/tools) | `python -m pytest tests/test_lineage_auto.py tests/test_tools_tasks.py tests/test_chat_tasks_stream.py tests/test_data_clean_apply.py -q` | 37 passed |
| Full suite | `python -m pytest -q --deselect tests/test_cov_agent2.py::TestVizDeep::test_matplotlib_all` | 3322 passed, 3 skipped, 1 deselected |

The deselected test is the known pre-existing matplotlib heatmap failure
(`zero-size array to reduction operation fmin`), unrelated to this work.

## Guarantees

| # | Guarantee | Test | Type / live evidence |
| --- | --- | --- | --- |
| 1 | `_basic_clean` preserves missing values as NULL and never stringifies them | `test_basic_clean_does_not_stringify_missing_values` | upload preview `null_counts.notes = 3`; DuckDB stores NULL |
| 2 | The cleaner still infers dates from nullable string columns | `test_cleaner_infer_plan_parses_nullable_string_dates` | `support_tickets_fixed.opened_at` is `TIMESTAMP_NS` |
| 3 | T-Test rejects >2 groups with a message pointing to ANOVA | `test_ttest_rejects_more_than_two_groups` | UI: "T-Test requires exactly 2 groups in 'priority' (found 3); use ANOVA for 3 or more groups" |
| 4 | Mann-Whitney U rejects >2 groups the same way | `TestHypothesis::test_mannwhitney` | message mirrors the T-Test guard |
| 5 | ANOVA interpretation names group count and names, and grammar is correct | `TestHypothesis::test_anova` | "The one-way ANOVA comparing group means across 3 groups ('high', 'low', 'medium') is NOT statistically significant (p=0.4327 >= 0.05)…" |
| 6 | Empty/bogus lineage records do not create phantom DAG nodes | `test_dag_skips_empty_records` | Load DAG shows "No lineage data recorded yet" |
| 7 | PDF export route stays wired after the authenticated-blob download fix | `test_c5_pdf_export_still_wired_to_the_route` | `node --check`; route string retained |
| 8 | Automatic lineage is tenant-scoped; the default tenant keeps `data/lineage.json`, other tenants get `data/lineage/<slug>.json` | `test_lineage_storage_is_tenant_scoped` | unit; live record landed only in `data/lineage/u_bjpxpgbd4o93ewnktdqroa_647336a4.json` |
| 9 | Concurrent lineage writes do not lose records | `test_concurrent_records_are_not_lost` | unit |
| 10 | A successful `/load` records a load transform for the caller tenant | `test_load_records_lineage` | integration; not exercised live in this pass |
| 11 | `/clean/apply` records operation, changes, and column map | `test_clean_apply_records_lineage` | integration; live `POST /clean/apply` -> 200, `GET /lineage/lineage_live_check2` returned the clean transform |
| 12 | Chat `clean_table` records lineage in the caller's tenant | `test_chat_clean_table_records_lineage` | integration; not exercised live in this pass |
| 13 | A lineage write failure does not fail `/load` | `test_lineage_failure_does_not_fail_load` | integration |
| 14 | Malformed `/lineage/record` payloads return HTTP 400 | `test_record_endpoint_rejects_malformed_payload` | integration |
| 15 | `/lineage/record` writes to the caller's tenant, not the default tenant | `test_record_endpoint_writes_to_caller_tenant` | integration |
| 16 | Lineage SVG labels, edge operations, and history inputs/outputs are HTML-escaped | `test_lineage_history_escapes_inputs_and_outputs` + `node --check web/js/pages/lineage.js` | static; live injected `<img onerror>` label rendered as text in both the SVG and history, no `<img>` element, `window.__xss` undefined |

## Automatic lineage capture

Persistence paths now record lineage server-side; the frontend does not call
`POST /lineage/record` for automatic records.

- `/load` records `operation="load"` with the source filename and output table.
- `/clean/apply` records `operation="clean"` with inputs, output, row counts,
  cleaning changes, and the source-to-output column map.
- Chat `clean_table` records the same shape in the caller's tenant.
- `analysis.lineage` serialises writes with a process-wide `RLock`, reloads the
  tenant file under the lock, and saves via temp file + `fsync` + `os.replace`.
- Recording is best-effort: `api.lineage_service.record_persisted_transform`
  swallows storage errors and logs a warning, so lineage never fails a load.
- `POST /lineage/record` validates the payload and table identifier before
  writing; malformed requests get HTTP 400.

## Live verification

After restarting the API so the process loaded the new hooks:

- Signed in as `mcp_tester` (role `user`, tenant `u_bjPXpGbD4O93EWnKtdQrOA`).
- `POST /clean/apply` on `support_tickets_fixed` -> `lineage_live_check2`
  returned 200 (12 rows before/after, 5 columns).
- `GET /lineage/lineage_live_check2` returned one `clean` transform with the
  identity column map and row counts.
- The record was written only to
  `data/lineage/u_bjpxpgbd4o93ewnktdqroa_647336a4.json`; the default
  `data/lineage.json` was untouched.
- The lineage page showed 3 nodes / 2 edges for the clean and the matching
  transformation history entry (`outputs/mcp-lineage-auto.png`).
- XSS probe: `/lineage/record` with `<img src=x onerror=...>` as an input label
  rendered as literal text in both the DAG and the transformation history,
  created no `<img>`/`<image>` element, and left `window.__xss` undefined. The
  probe records were cleared afterwards.

## Other fixes verified in this pass

- Excel pivot grand totals, chart legend labels, and authenticated blob
  downloads were fixed earlier and re-checked live; premium export still returns
  403 for the free `user` account (auth path confirmed by 401 -> 403).
- Quick Compare `support_tickets_clean` vs `support_tickets_fixed`: 12 rows
  each, 1 column changed (`notes`, unique 2 -> 1), 6 added / 6 removed.
- Notebook SQL + Python cells still run against the tenant schema.

## Known gaps

- **Automatic lineage covers persisted warehouse tables only.** Client-side
  Excel analysis sheets are not persisted and are not recorded.
- Existing `support_tickets` and `support_tickets_clean2` tables still contain
  literal `"nan"` strings from before the fix; new loads are clean. Re-upload or
  re-clean to replace them.
- Premium export downloads cannot be end-to-end verified on the free account.
- **Coverage is below the enforced gate.** `python -m pytest -q --cov=.` measured
  73.45% (recomputed CI scope 72.68%), under `fail_under = 74`. This is the
  pre-existing uncommitted growth documented in
  `docs/testing/opencode-v1-pin-and-fast-model.tdd.md`; the new lineage modules
  measure 84.88%, so they do not lower the gate. The gate was not lowered.
- Direct DuckDB inspection during live verification is blocked by the server's
  warehouse file lock; API/UI paths were used instead.

## Screenshots

`outputs/mcp-stats-ttest-guard.png`, `outputs/mcp-stats-anova-copy.png`,
`outputs/mcp-lineage-empty.png`, `outputs/mcp-lineage-auto.png`,
`outputs/mcp-quick-compare-fixed.png`, `outputs/mcp-notebook.png`,
`outputs/mcp-excel-print-preview.png`.
