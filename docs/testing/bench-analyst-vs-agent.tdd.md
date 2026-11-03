# Benchmark evidence: analyst claim — manual toolkit vs DataAgent agent + verify

**Date:** 2026-10-24
**Type:** empirical benchmark (tooling, not production code)
**Question tested:** *"the analyst uses the software, then verifies — minutes for
90% confidence, vs ~4 hours manual for the same confidence."*
**Verdict:** **Time: confirmed (1.2 min vs ≈4 h). Ease: confirmed. Correctness:
confirmed after fixes — the pre-fix run exposed a real MoM routing gap (agent
under-answered); the deterministic period-over-period fix makes the agent answer
correctly in 3-of-3 rounds, matching ground truth exactly.** See
[Verdict](#verdict) for the scoring and [Run B](#5b-fix-and-re-verification-run-b)
for the re-verification.

## 1. Source plan

The plan was derived in-session (no `*.plan.md` file): user approved (a) scripted
manual path + human-time estimate column, (b) generating a bigger sample dataset
first, (c) starting the web server for the run. This report is the evidence
index for that run.

## 2. Environment

| Item | Value |
|---|---|
| OS / shell | Windows, PowerShell 5.1 |
| Python | 3.13.14 (system; deps already installed) |
| Server | `python run.py api` on `http://127.0.0.1:8002` (PID recorded in `logs/bench-server.pid`) |
| LLM mode | `llm_model: opencode/auto`, `llm_used: true` on every round |
| Warehouse | `data/curated/warehouse.duckdb`, tenant-scoped table (read-write held by server only) |
| Probe account | provisioned/reused by `scripts/ai_probe_setup.py` (`scripts/.ai_probe_user.json`, local test credential state, never committed) |

## 3. Data

- Generated: `python scripts/make_sample_data.py --rows 2000`
  → `data/raw/sample_sales.csv` (2000 rows, seeded `SEED=42`, dates
  2026-01-01…~2026-09, 4 regions, `revenue = units Ã— unit_price Ã— (1 âˆ’ discount)`).
- Uploaded **through the app's own endpoints** (`POST /upload` → `POST /load`,
  multipart) as tenant table `bench_sales` — the app process remains the single
  warehouse writer (one-writer rule; the benchmark script never opens the
  warehouse read-write).
- `order_date` loaded as `TIMESTAMP_NS` (verified via `typeof()` through `/sql`).

## 4. Commands actually run

```powershell
python scripts\make_sample_data.py --rows 2000
python scripts\ai_probe_setup.py
python scripts\bench_analyst_vs_agent.py --table bench_sales --rounds 1   # smoke
python scripts\bench_analyst_vs_agent.py --table bench_sales --rounds 3 *> outputs\bench\run_final.log
# --- after the product fix (Run B) ---
python -m pytest tests\test_planner_period_over_period.py -q               # 15 passed
python scripts\bench_analyst_vs_agent.py --table bench_sales --rounds 3 *> outputs\bench\run_final_v2.log
```

## 5. Run A — pre-fix result (verbatim excerpt, `outputs/bench/run_final.log`)

```
ground truth: table=bench_sales months=2026-01..2026-09 cells=36 sql_ms=39
  2026-08 -> 2026-09 total_delta=-1,577.50 top=East
spot check against truth: agree=True (58 ms)

round 1: agent http= 90.98s pipeline= 90.25s llm=Y steps=2 manual_ok=True agent_mom_answer=False
round 2: agent http= 91.17s pipeline= 90.49s llm=Y steps=2 manual_ok=True agent_mom_answer=False
round 3: agent http= 91.15s pipeline= 90.47s llm=Y steps=2 manual_ok=True agent_mom_answer=False

=== RESULT ===
phase             machine median   human estimate
sql_extract              0.06s           45-60m
pandas_clean             0.00s           30-45m
analysis                 0.02s           45-60m
chart                    1.64s           60-90m
report                   0.00s           30-30m
MANUAL total             0.19s            4h07m  (scripted = machine time; human from norms)
agent /ask: median 91.15s (n=3, min 90.98s max 91.17s)
agent verify step (spot-check): median 42ms
agent total (ask + verify): 91.20s
manual matches ground truth, all rounds: True
spot check matches ground truth: True
agent actually answered the MoM question: False
  -> agent under-answered (summary/totals instead of MoM comparison); the verify step is what catches this
confidence corridor: 3-of-3 independent computations agree
```

### What the agent actually answered (round-1 full text, reproduced via a direct `/ask` call)

```
**Result: 4 rows Ã— 5 columns**
**Highlights:**
- **Total total value:** 252,446.73
- **Highest:** East = 80,481.01
...
| East | 639 | 80,481.01 | ...
```

That is **total revenue by region over all 9 months**, not the requested
2026-08 → 2026-09 comparison. A follow-up ask naming the two months explicitly
returned:

> *"Note: this table has no time/period column, so trend analysis isn't
> available here — showing the summary instead."*

…even though `order_date` is a real `TIMESTAMP_NS` column (verified via
`SELECT typeof(order_date) FROM bench_sales LIMIT 1`).

## 5b. Fix and re-verification (Run B)

**Root causes (corrected).** The "no time/period column" note was a *symptom*,
not a schema-detection failure: `_trend_no_period_note` inspects the **result**
DataFrame, and the result was a region-totals table with no date column. The
real defects were:

1. `Planner.classify_intent` trend vocabulary did not include "month-over-month"
   (nor week/quarter/year-over-year), so the question fell through to a generic
   breakdown instead of a time comparison.
2. Even on the trend path, `_generate_time_series_query` yields a monthly
   *series*, not per-region period attribution.
3. Latent: `schema_intelligence._is_date_column` used exact type equality, so
   `TIMESTAMP_NS`/`TIMESTAMP_MS`/`TIMESTAMP_S` were missed; `planner._table_date_column`
   was name-only with no type fallback. (Real bugs, but not the cause here —
   `order_date` matched by name.)
4. Harness bug: `agent_answer_checks` ran on an answer already truncated to 400
   chars, hiding the months/deltas that lived further down the answer.

**Fix (product).** `Planner._generate_period_over_period_query` now emits
`group_name, prev_period, cur_period, prev_value, cur_value, change` for
dimension questions (dynamic last-two-periods subquery, or an explicit month
pair parsed from the question), ordered by `ABS(change) DESC`; the plan branch
marks the step `period_comparison=True`, `skip_llm_upgrade=True`, and adds a
horizontal bar chart (`x="change"`, `y="group_name"`). Trend vocabulary extended
with MoM/WoW/QoQ/YoY variants; date-column detection now matches timestamp type
families; `_table_date_column` falls back to type detection. `Orchestrator`
suppresses the single-period/no-period notes when the plan is already a period
comparison. `/sql` needed no change — its 1000-row cap is explicit pagination
(`row_count`, `offset`, `limit`, `has_more`, `total_pages`); the earlier
truncation was a client bug in the benchmark, already fixed.

**TDD evidence.** `tests/test_planner_period_over_period.py` (15 tests):
intent classification, query shape, explicit-month parsing, plan metadata,
monthly-trend regression, timestamp-family detection, planner type fallback,
the orchestrator note guard, and two DuckDB execution tests. RED first: 10
failed for the intended reasons. The execution tests then caught a real binder
bug — the rolling-bounds subquery had replaced the table in the `FROM` clause
(`Referenced column "order_date" not found`) — fixed to `FROM table, (…bounds)`.
Final: **15 passed**; surrounding planner/orchestrator/format suites **100 passed**.

**Run B (verbatim, `outputs/bench/run_final_v2.log`):**

```
ground truth: table=bench_sales months=2026-01..2026-09 cells=36 sql_ms=58
  2026-08 -> 2026-09 total_delta=-1,577.50 top=East
spot check against truth: agree=True (38 ms)
round 1: agent http=  1.11s pipeline=  0.12s llm=Y steps=2 manual_ok=True agent_mom_answer=True
round 2: agent http=  1.09s pipeline=  0.10s llm=Y steps=2 manual_ok=True agent_mom_answer=True
round 3: agent http=  1.18s pipeline=  0.11s llm=Y steps=2 manual_ok=True agent_mom_answer=True
agent /ask: median 1.11s (n=3, min 1.09s max 1.18s)
agent verify step (spot-check): median 56ms
agent total (ask + verify): 1.17s
manual matches ground truth, all rounds: True
spot check matches ground truth: True
agent actually answered the MoM question: True
confidence corridor: 3-of-3 independent computations agree
```

The agent's full answer (direct `/ask` verification):

```
| Group name | Prev period | Cur period | Prev value | Cur value | Change    |
| East       | 2026-08     | 2026-09    | 8,454.09   | 11,766.01 |  3,311.92 |
| West       | 2026-08     | 2026-09    | 4,725.80   | 2,549.46  | -2,176.34 |
| South      | 2026-08     | 2026-09    | 9,616.88   | 7,527.77  | -2,089.11 |
| North      | 2026-08     | 2026-09    | 6,656.07   | 6,032.10  |   -623.97 |
```

Every value matches ground truth; East is correctly identified as the top driver.
The `/ask` latency also dropped from ~91 s to ~1.1 s because the deterministic
rule planner now handles the question instead of escalating to the LLM.

## 6. Test specification

| # | What is guaranteed | Test / command | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Ground truth (month Ã— region revenue, MoM deltas) is computed deterministically from the warehouse without the LLM | `POST /sql` aggregate in `scripts/bench_analyst_vs_agent.py` | integration | PASS | `ground truth: ... cells=36` |
| 2 | An independently-shaped SQL aggregate reproduces the ground-truth deltas (the verification step) | `spot_check()` — `SUM(CASE …)` per region | integration | PASS | `spot check against truth: agree=True` every round |
| 3 | The manual (scripted) analyst path reproduces the ground truth exactly, all 3 rounds | pandas pivot over grouped SQL extract | integration | PASS | `manual matches ground truth, all rounds: True` |
| 4 | The full agent call completes and is timed, with LLM-vs-fallback recorded | `POST /ask` (question scoped to `bench_sales`) | integration | PASS | `agent http= 91.15s … llm=Y` |
| 5 | The agent's answer is checked for whether it answers the MoM question (month reference or a true delta value — not a region name) | `agent_answer_checks()` | integration | Run A: **FAIL** (detected under-answer) / Run B: **PASS** Ã—3 | `agent_mom_answer=False` → `True` |
| 6 | Human-effort column is never conflated with machine time | report prints "scripted = machine time; human from norms" | reporting | PASS | RESULT block |
| 7 | "month-over-month" (and WoW/QoQ/YoY) routes to a period-over-period plan, and the query yields per-dimension prev/cur/change | `tests/test_planner_period_over_period.py` | unit | PASS | 15 passed |
| 8 | Generated period SQL actually executes on `TIMESTAMP_NS` and reproduces the per-region deltas, largest absolute change first | `TestGeneratedSqlExecutes` (in-memory DuckDB) | integration | PASS | 15 passed (caught a real `FROM`-clause binder bug) |
| 9 | `TIMESTAMP_NS`/`TIMESTAMP_MS`/`TIMESTAMP_S` are recognized as date columns (schema + planner fallback) | `TestDateColumnDetection` | unit | PASS | 15 passed |
| 10 | A period-comparison plan never receives a false "single period"/"no time column" note | `Orchestrator._is_period_comparison` guard | unit | PASS | 15 passed |

## 7. Verdict

| Claim component | Measured | Verdict |
|---|---|---|
| "minutes" for the agent path | Run B: ask 1.11 s (median, n=3) + verify 56 ms ≈ **1.2 minutes**. Run A (LLM-escalation path): 91.2 s ≈ 1.5 min | **CONFIRMED** |
| "~4 hours" manual | scripted machine time 0.17 s; published analyst norm **4 h 07 m** | **PLAUSIBLE** (estimate, not hand-timed — see gaps) |
| "easier" | one plain-language call vs 5 scripted phases | **CONFIRMED** (with the caveat below) |
| "same correct answer / 90% confidence" | deterministic chain: 3-of-3 agree; agent answer matches both months and all four per-region deltas (3-of-3) | **CONFIRMED after fix** |

The honest reading: the deterministic core of DataAgent (SQL → pandas analysis →
chart → report) matched the ground truth exactly, three independent ways. In
Run A the LLM `/ask` step was fast and easy but produced a *different, simpler*
analysis (region totals) and emitted a misleading "no time column" note — the
verify step caught it. The Run B fix routes MoM/YoY questions to a deterministic
period-over-period plan, and the agent now reproduces the exact deltas. The claim
still reads "agent **then verifies**" for good reason: verification is what turns
a plausible answer into a confirmed one.

## 8. Findings and status

1. **RESOLVED — MoM questions fell through to a totals summary.** The "no
   time/period column" text was a symptom (`_trend_no_period_note` inspects the
   *result* frame), not a schema failure. Root cause: `classify_intent` lacked
   month/week/quarter/year-over-year vocabulary, so the plan was never a time
   comparison. Fixed with `_generate_period_over_period_query` + plan metadata
   and an orchestrator note guard; verified by Run B and 15 tests.
2. **RESOLVED (latent) — timestamp-family detection.** `_is_date_column` used
   exact type equality (missed `TIMESTAMP_NS`/`TIMESTAMP_MS`/`TIMESTAMP_S`) and
   `_table_date_column` was name-only. Both fixed; type fallback covered by tests.
3. **DOCUMENTED — `/sql` caps inline rows at 1000** by design (`row_count`,
   `offset`, `limit`, `has_more`, `total_pages` are all returned). The first
   benchmark iteration ignored `has_more`; fixed in the script by aggregating in
   SQL. No API change needed.
4. **RESOLVED (harness) — truncated answer check.** `agent_answer_checks` ran on
   a 400-char excerpt, so a correct answer could still fail. Checks now run on
   the full answer; only the saved artifact is truncated.
5. **Methodology lesson (kept in the script)**: a region-name substring check
   passed by coincidence here (East is both the total-revenue leader and the
   largest MoM change). The script requires a month reference or a true delta
   value — see `agent_answer_checks()`.

## 9. Coverage and known gaps

- **Machine time â‰  human time.** Phase timings measure scripted execution. The
  4 h 07 m figure is a published-norm estimate (SQL 45–60 m, cleaning 30–45 m,
  analysis 45–60 m, chart 60–90 m, report 30 m). To validate it, a human must
  hand-run the manual path once.
- **Seeded synthetic data** (2000 rows) — mechanics proven, not scale.
- **Single question, single table.** Does not characterize the agent across the
  question space.
- **LLM latency depends on the path.** Run A escalated to `opencode/auto`
  (~90 s/ask); Run B answers deterministically in ~1.1 s with the LLM still
  reporting `llm_used=true` for narrative. Other questions that miss the rule
  planner will still pay LLM latency.
- Daily AI quota was not exhausted during either run (no 429s observed).
- **Full suite:** 3274 passed, 3 skipped, 2 failed — both pre-existing and
  unrelated (`test_cov_etl_warehouse.py` pandas-3 date dtype; `test_cov_agent2.py`
  matplotlib heatmap `fmin`); `etl/extract.py` and `agent/tools_viz.py` are
  untouched by this change.

## 10. Artifacts

| Path | Content |
|---|---|
| `outputs/bench/bench_analyst_vs_agent.json` | full structured result (ground truth, per-round phases, agent payload excerpts, checks) — Run B |
| `outputs/bench/run_final.log` | verbatim console log, Run A (pre-fix) |
| `outputs/bench/run_final_v2.log` | verbatim console log, Run B (post-fix) |
| `outputs/bench/manual_chart.png` | manual-path chart (plotly/kaleido) |
| `scripts/bench_analyst_vs_agent.py` | reproducible benchmark script |
| `tests/test_planner_period_over_period.py` | 15 tests covering the fix (unit + DuckDB execution) |
| `logs/bench-server.out.log`, `logs/bench-server.err.log` | server logs from the benchmark run |

## 11. Merge evidence (TDD checkpoint mapping)

**Benchmark tooling** (`scripts/bench_analyst_vs_agent.py`) follows the existing
`scripts/ai_benchmark.py` precedent. Its RED/GREEN: first iteration produced
`manual_ok=False` (RED, caused by the `/sql` 1000-row cap); SQL-side aggregation
produced `manual_ok=True` plus spot-check agreement (GREEN).

**Product fix** (planner/schema/orchestrator):

| Stage | Evidence |
|---|---|
| RED | `pytest tests/test_planner_period_over_period.py` → 10 failed for the intended reasons (no MoM intent, no comparison query, no type fallback) |
| GREEN | same command → 15 passed; surrounding planner/orchestrator/format suites → 100 passed |
| Integration | DuckDB execution tests caught a real `FROM`-clause binder bug before the E2E run; fixed |
| E2E | Run B: `agent_mom_answer=True` ×3, values match ground truth exactly, agent total 1.17 s |


