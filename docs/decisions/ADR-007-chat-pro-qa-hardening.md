# ADR-007 — Chat Pro QA hardening (bounded LLM calls, honest plan gates, clamped output)

Date: 2026-10-17
Status: Implemented (25-question QA sweep findings; TDD; live-verified)

## Context

A 25-question QA sweep against the live Chat Pro UI (`#chat-pro`, real
warehouse, `e2e_probe_user` account) surfaced 14 defects across the
planner → orchestrator → sqlgen → API → UI pipeline:

1. Nonsense asks ("chart of unicorn sightings") were charted via the LLM
   instead of declined.
2. Singular superlatives ("highest revenue order") were mislabeled as
   rankings and drew a bogus bar chart.
3. Comparison/trend/distribution fallbacks (`SELECT * … LIMIT 100`) were
   charted as if grouped data.
4. A single LLM call could hang unbounded (>3 min observed) — the license-key
   question never returned inside the 180s stream timeout, which then produced
   an empty error body.
5. Cold LLM calls take 40–54s each; multi-call plans exceeded 180s.
6. Sub-100 ms step durations rendered as "0.0s".
7. 500+ char text (license keys, embedded JSON) leaked unbounded into answers
   (`_highlights`, table cells, `_fmt_scalar`).
8. A single-category pie rendered a meaningless full-circle chart.
9. Introspection plans ("list tables") were LLM-rewritten, dropping the
   schema filter.
10. Scatter charts ignored named "X vs Y" columns.

## Decision

1. **Bound every LLM call.** `agent/sqlgen.py` wraps coroutine chats in
   `asyncio.wait_for(..., _LLM_CALL_TIMEOUT_S=90.0)`; plan escalation timeout
   `plan_llm_timeout_seconds` 30→90s; stream `_run_pipeline` 180→240s and the
   direct path wrapped with the same budget. On timeout the API returns a
   clean user-facing message ("The analysis timed out after 4 minutes…");
   any LLM failure falls back to the rules plan / rule answer.
2. **Meaningless-question guard.** `_is_meaningless_question` requires at
   least one known content word and treats format-only asks
   (`_FORMAT_ONLY_WORDS`) as non-data. A chart request on a table with no
   chartable columns declines with reasoning instead of charting raw text.
3. **Honest step labels.** `MAX/MIN` scalar queries are labeled
   "Compute aggregate for: …" and draw no ranking chart; `_numeric_value_column`
   returns `None` when no numeric column matches (no silent fallback).
4. **Chart gates on real grouped SQL.** Comparison charts require `GROUP BY`;
   `_chart_steps_for` returns `[]` when it cannot build a grouped or
   time-series query; trend viz skips `SELECT *` fallbacks; distribution viz
   requires `GROUP BY`. No chart is ever drawn from raw-row SQL.
5. **Scatter honors named axes** — "units vs revenue" resolves singular/plural
   column forms to `x_val`/`y_val`; unnamed falls back to the first two
   numeric columns.
6. **Introspection plans carry `skip_llm_upgrade: true`** so "list tables"
   stays a single schema-aware step.
7. **Clamp all display text.** Table cells and `_fmt_scalar` at 120 chars,
   `_fmt_label` at 60 chars; `_safe_to_markdown` no longer raises on
   non-DataFrame input. Answers never embed multi-KB blobs.
8. **Single-slice pie degrades to a bar** (zero-information circle avoided).
9. **UI:** step-summary duration is hidden below 100 ms (no "0.0s").

## Consequences

- Worst-case question latency is now bounded (240s stream/direct, 90s per LLM
  call) with a user-understandable timeout message; no more zombie runs.
- Nonsense and unchartable asks cost zero LLM calls and decline instantly.
- Legacy `#chat` remains byte-compatible; all changes are additive guards.
- Pre-existing behavior kept where charts are legitimate: grouped comparisons,
  time series over dates, distribution over dimensions, scatter with named or
  fallback axes.

## Verification

- New regression suite `tests/test_qa_fix_regressions.py` (20 tests): meaningless
  guard, unicorn decline, named-text-column chart decline, real chart intact,
  ranking scalar label, grouped ranking keeps chart, SELECT * comparison no
  chart, scatter named axes, introspection skip metadata, scalar/cell/label
  clamping, pie fallback, duration floor.
- Full suite: 1897 passed; 21 failures + 1 error — the same pre-existing
  environment set as ADR-006 minus `TestOrchDeep::test_build_answer_variants`
  (fixed by the `_safe_to_markdown` hardening); no new failures.
- Live E2E on restarted server (PID 14412, `APP_PORT=8002`, clean error log),
  `e2e_probe_user2` session:
  - "chart of unicorn sightings…" → declined in 402 ms, no step.
  - "bar chart of key_hash" → 1 SQL step, 0 charts, actual row returned.
  - "bar chart of revenue by region" → 2 steps, 1 chart, total 757.62 (matches
    warehouse).
  - "scatter chart of units vs revenue" → `x_val`/`y_val` axes, 8 rows,
    1 chart.
  - "what tables are available…" → 1 step, 61 rows, no LLM rewrite.
  - "What is the highest revenue order in smoke_sales?" → scalar answer
    (99.95), honest "Compute aggregate for:" label, 0 charts, completed.
  - "pie chart of email distribution in key_redemptions" (1 row) → single
    bar rendered in the Plotly DOM (`barlayer` point, 0 pie slices).
- `python -m py_compile` clean on all five touched Python modules.
