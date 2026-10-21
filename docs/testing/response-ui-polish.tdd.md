# Response UI polish — TDD evidence

Source: UI review via Chrome MCP screenshots (spend-by-month, top-source answers).

## Defects addressed

1. Multi-row tables leaked raw snake_case headers (`group_name`, `avg_value`,
   `pct_of_total`) — only 1-row (metrics) tables were prettified.
2. Clunky phrasing: `1 visualization(s)` and `Query returned 2 row(s) × 4 column(s):`.
3. Highest/Lowest highlight lines silently disappeared: pandas 3 stores strings
   as `StringDtype`, so label detection (`dtype == object`) returned None — and
   datetime period columns were never considered labels at all.

## Contract (new)

- Table headers use `_pretty_label` in multi-row tables too.
- Header reads `**Result: N rows × M columns**` (pluralized).
- Chart footer reads `📊 **N chart(s)** — see above.`
- Highlights name the metric with its pretty label; Highest/Lowest render for
  string and datetime label columns (dates formatted `%Y-%m-%d`).

## Tests (tests/test_answer_presentation.py — 8 tests)

| # | Guarantee | Test | Result |
|---|-----------|------|--------|
| 1 | Multi-row headers are pretty; snake_case never leaks | TestPrettyTableHeaders::test_multirow_table_headers_are_pretty | PASS |
| 2 | Highlights use pretty metric labels (`Total avg value`) | TestPrettyTableHeaders::test_highlights_use_pretty_metric_label | PASS |
| 3 | Header reads "Result: 2 rows × 4 columns"; no "Query returned" | TestResultHeaderPhrasing::test_multirow_header_reads_naturally | PASS |
| 4 | Single non-empty column pluralizes ("1 column") | TestResultHeaderPhrasing::test_single_column_pluralizes_correctly | PASS |
| 5 | String labels produce Highest/Lowest lines | TestHighlightExtremes::test_extremes_name_the_label | PASS |
| 6 | Datetime labels produce Highest/Lowest with clean dates | TestHighlightExtremes::test_extremes_name_datetime_labels | PASS |
| 7 | Chart footer singular: "1 chart" | TestChartCountPhrasing::test_single_chart | PASS |
| 8 | Chart footer plural: "2 charts" | TestChartCountPhrasing::test_multiple_charts | PASS |

RED: all 6 phrasing/header tests failed pre-change; extremes tests failed pre-fix
(StringDtype / datetime). GREEN: `python -m pytest tests/test_answer_presentation.py -q` → 8 passed.

## Validation commands

- `python -m pytest tests/test_answer_presentation.py tests/test_describe_dataframe_format.py tests/test_viz_remap.py tests/test_sqlgen_alias_rules.py tests/test_chat_pro_ui.py tests/test_planner_ranking_rules.py -q` → 62 passed.
- `python -m pytest tests/test_cov_agent.py tests/test_cov_agent2.py -q` → 245 passed, 7 failed (same 7 pre-existing `test_cov_agent2` failures; unrelated).
- Updated one stale pin in `tests/test_cov_agent.py:1465` ("row(s)" → new header).
- Live verification: Chrome MCP chat → "Show me the spend by month" →
  `Result: 2 rows × 4 columns`, `Highest: 2025-04-01 = 15,959.46`,
  `📊 1 chart — see above.` — screenshot `outputs/ui_verify/chat_ui_polish_spend_by_month.png`.

## Known gaps

- `_safe_to_markdown` (LLM context dump) still tabulate-rounds; display-only, untouched.
- Extremes lines are skipped when no label column exists (e.g. all-numeric results) — intentional.

## Round 2 — full-width layout + date cells

Chrome MCP measurement showed the assistant column was shrink-to-fit:
`.cpx-col` collapsed to the ~390px table width inside an 836px row
(viewport 1536px), squeezing charts/tables into the left half.

- `web/css/chat-pro.css`: `.cpx-row-ai .cpx-col { flex: 1; ... }` (fills the
  row), `.cpx-thread-inner` / `.cpx-composer-inner` max-width 880→1200px.
- `web/js/pages/chat-pro.js`: `CSS_HREF` v=4 → v=5 (cache-buster contract
  enforced by `test_css_cache_buster_bumped`).
- `agent/orchestrator.py`: datetime table cells format via `_fmt_label`
  (midnight → `2025-04-01`, else `2025-04-01 09:30`); no `00:00:00` leak.
- Tests: `test_assistant_answers_fill_available_width` (CSS contract),
  `TestDatetimeCells` (2). RED→GREEN; 65 targeted pass, 245 pass + 7
  pre-existing failures.
- Live: column 392→1110px, table 1104px, chart 1106px; screenshot
  `outputs/ui_verify/chat_ui_fullwidth_spend_by_month.png`.
