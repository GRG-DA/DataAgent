# Launch Readiness — Concept, Product & Business Review

> **Companion document:** [`LAUNCH-TECHNICAL-REVIEW.md`](LAUNCH-TECHNICAL-REVIEW.md) covers the
> implementation-level review of the **hosted multi-tenant** build — six launch-blocking defects
> (a sandbox escape, a tenant-binding gap on API keys, `/ask` reading the operator's schema, a
> cross-tenant race in the agent, an SSRF, and exports reading the platform schema) plus the
> sequenced remediation plan. This document remains the concept, viability and pricing review.

**Date:** 2026-10-20
**Scope:** Concept-level viability only. Implementation *depth* was audited to ground the
findings, but this document does **not** re-review the website/marketing front end. The
website is referenced only where its *claims* contradict the architecture (a product
decision, not a design critique).
**Method:** Source read (38K lines `api/`, 59K lines `web/`), live test run against the
warehouse, test-suite execution, and market/competitor research (May–Oct 2026 pricing data).

---

## Verdict

| Question | Answer |
|---|---|
| Useful to people? | **Yes — genuinely.** It is a real product, not a demo. |
| Can people buy a subscription? | **Mechanically yes — but today a paying customer receives no entitlement.** The last mile is broken. |
| Can you profit? | **Not with the $6/mo plan. Yes with $120 lifetime.** The pricing shape in `docs/BILLING.md` is the wrong one. |

---

## 1. What the product genuinely is

Not "an AI analytics tool." The real product is:

> **CSV → boardroom, fully offline, with a genuine Power BI handoff.**

The `.pbix` round trip is the crown jewel and it is *not* a stub:

- `export/pbix_export.py` (2,585 lines) writes a real Power BI Desktop file — `[Content_Types].xml`,
  `DataMashup` (QDEFF binary), `Report/Layout`, `DataModelSchema` (TMSL), a hybrid star schema
  with `DATATABLE(...)` calculated partitions, DAX measures derived from KPI widgets, date
  dimensions, and slicer dimensions wired many-to-one so slicers genuinely cross-filter.
- `export/verify_export.py` (475 lines) structurally validates the output against captured
  authentic samples.
- `api/pbix_spec.py` (475 lines) imports real `.pbix` files back: ZIP → `Report/Layout` →
  `sections` → `visualContainers` → `config`, mapping 30+ Power BI `visualType`s onto widget
  families, normalising boxes to 0–100 % of the page, and extracting the registered theme.

Nobody does this well. Not ChatGPT, not Julius AI, not the open-source BI tools.

**The real moat is local-first.** ChatGPT and Julius AI are cloud-only. Power BI Desktop is
local but cannot do NL → SQL → dashboard → `.pbix`. The gap is real, and it is exactly what
the code is best at: DuckDB on a local file, zero infrastructure, zero security surface.

### Supporting capability (all verified in source, not claimed)

| Area | Evidence |
|---|---|
| NL → SQL pipeline | `agent/sqlgen.py` — serious prompt, `validate_no_hallucination`, alias preservation, repair loop, semantic + execution validation. Deterministic rule-based plan stays as fallback, so `/ask` cannot regress. |
| Frontend | `web/` — 153 files, 59,021 lines app JS, 4,836 lines TS, 8,958 lines CSS, 338 API methods in `web/js/api.js`. `pages/blankdash.js` alone is 13,675 lines / 85 widget types. |
| Test suite | 2,301 passed, 3 skipped, 5m18s, **76.60 % coverage** (gate 74 %). Real DuckDB fixtures and real HTTP round-trips, not mocks. |
| Billing crypto | `api/billing/` + `keygen_app/` — Ed25519 product keys, HMAC local grant signing, fail-closed license guard, 300 s freshness windows, `hmac.compare_digest` throughout. Better engineered than most indie products. |
| Provider breadth | 9 providers (Ollama, OpenAI-compatible, LM Studio, OpenRouter, Anthropic, Gemini, xAI, DeepSeek, OpenCode free tier) + WARP egress rotation + proxy pool (`agent/warp.py`, `agent/proxifly_pool.py`). |

---

## 2. Blocker A — a paying customer gets nothing 🔴

**This is the highest-value fix on the list. ~1 hour of work.**

The chain:

- `api/main.py:6876-6880` — `RegisterRequest` is `{username, password, role, bootstrap_token}`.
  **There is no `email` field.**
- `api/auth.py:34-35` — "`email` is optional: an account without an address can sign in, it
  just cannot receive a password reset or an invitation."
- `api/premium.py:249-259` — if the signed-in account carries no email:

  ```
  HTTP 403
  "This key is registered to {buyer_email}. Sign in with that email account
   (Continue with Google) to activate it."
  ```
- The keyless subscription path resolves the plan **by email** (`docs/BILLING.md` §9,
  `api/billing/cloud_sync.py` → `GET /v1/entitlements?email=…`). No email → no plan.

**The failure scenario, end to end:**

1. User downloads the `.exe`, registers with username + password.
2. User buys a ₹10,000 Lifetime key.
3. User pastes the key into the app → **HTTP 403**.
4. User is told to sign in with Google. The local account has no email to match, so the
   Google callback creates a **different account** — the entitlement still does not attach.

**Money in, entitlement zero.** No mail server is required to fix this:
`api/premium.py:251` compares the email *string* and does **not** require `email_verified`.

**Fix:** add `email` to `RegisterRequest`, and/or expose a "set my email" route
(`api/auth.py:343 set_email` already exists at the store level, and `pending_email` /
`email_verified` fields already exist in the user record). Also add an account-linking path
so a pre-existing local account can be bound to a Google identity.

---

## 3. Blocker B — two products are being sold at once 🟠

| Surface | What it claims |
|---|---|
| `website/index.html:73-76`, `:322`, `:346` | "100% Local", "No Telemetry", "Open Source", "$0", "no subscription", "the data itself never leaves your machine" |
| `docs/decisions/ADR-009` (2026-10-20) | *"DataAgent becomes a hosted multi-tenant SaaS. The data plane moves to the server."* — explicitly withdraws the local promise |
| `docs/BILLING.md` | Pro $6/mo, Lifetime $120, external Razorpay checkout |

Both cannot be true. The first thing an enterprise buyer asks is *"where does my data live?"*
Today the honest answer depends on which document they read.

**Recommendation: keep local-first for v1.** It is the genuine moat, the code is built for
it, it eliminates the entire hosted-isolation risk class, and it costs ~$0 to serve. Keep
ADR-009 as the documented v2 path rather than the v1 strategy.

---

## 4. Blocker C — $6/mo with unlimited AI has no cost ceiling and no metering 🔴

| Fact | Evidence |
|---|---|
| Unlimited AI on **every** plan | `api/billing/seed.py:154-164` — `LIMITS` sets `ai_requests = -1` for free, pro *and* lifetime. Pro cap default is `-1` (`get_pro_ai_daily_cap`, `seed.py:31`). |
| Requests are expensive | `agent/sqlgen.py:402` — schema + relationships for up to **25 tables** are injected → 5–15K tokens per request. |
| Rough cost | Claude Sonnet ≈ $0.03–0.05/request → 600 requests/mo ≈ **$30 cost against $6 revenue**. |
| No per-tenant AI keys | `api/main.py:8643-8646` — `POST /llm/providers/update` is `require_admin`, "provider config is workspace-global", persisted to a single `config/llm_runtime.json`. |
| No cost accounting | `agent/llm_client.py` records token counts but nothing persists per-user cost. `api/billing/metering.py` counts *requests*, not tokens or cost. |
| Inference cost is on you | The whole local-first promise is that the *user* runs Ollama. Hosted, the operator pays for every query. |

**Recommendation: make $120 lifetime the primary product.** ~95 % gross margin, no
infrastructure, no metering problem, and `keygen_app/` (mint + verify + email binding +
offline grace + one-time redemption) is already built and tested for exactly that flow.
Drop or hard-cap the $6/mo tier.

---

## 5. Trust risk — three feature names promise more than the code delivers 🟠

On a self-serve download product, the review economy is the business. These gaps will
produce refunds and 1-star reviews.

| Advertised | Reality | Evidence |
|---|---|---|
| **"AutoML"** | Pearson \|r\| filter + multicollinearity pruning; `compare_regressions` fits *linear vs degree-2 polynomial* and compares adjusted R². No model search, no cross-validation, no ensembling. | `analysis/automl.py:73`, `:225`, `:347` |
| **"Segmentation"** | `df.groupby(segment_col)[metric_col].agg(["count","sum","mean","median","std","min","max"])` + `pct_of_total`. No clustering — zero `kmeans` hits in the tree. | `api/main.py:3176-3228` |
| **"Forecasting"** | `np.polyfit` linear trend + classical additive/multiplicative decomposition + changepoints. No ARIMA, no ETS, no Prophet, no Holt-Winters — `statsmodels.tsa` is never imported. | `analysis/forecasting.py:200`, `:264`, `:272-292` |
| **PDF export** | Falls back to a reportlab stub that emits *"Install Playwright for full report formatting"*. Playwright and WeasyPrint are in **no** `requirements*.txt`. | `export/pdf_export.py:84` |
| **"Tableau TDE" / "Looker" export** | TDE is a CSV with a `.tde` extension. Looker is a JSON config skeleton. | `analysis/bi_export.py` |

**No ML library is installed at all** — the complete list is `scipy`, `statsmodels`, `pandas`,
`numpy`. No `scikit-learn`, no `torch`, no `xgboost`/`lightgbm`.

**Cheapest safe fix — rename (one afternoon):**

| Rename to |
|---|
| AutoML → **Regression Explorer** |
| Segmentation → **Segment Breakdown** |
| Forecasting → **Trend Forecast** |

Also add Playwright to `requirements.txt` so PDF export is not a blank page.

---

## 6. Security — not a blocker for local, blocking for hosted 🟡

**Empirically verified** (throwaway warehouse, real `agent.tools_sql.SQLTool`):

```
SELECT * FROM analytics.sales            ->  ALLOWED   ← bootstrap admin's schema
SELECT * FROM analytics_t_u_alice.sales   ->  BLOCKED   ← other tenants (correct)
```

Chain:

- `agent/tools_sql.py:38-43` — `_BLOCKED_SCHEMAS` = {`information_schema`, `pg_catalog`,
  `main`, `temp`} plus any `analytics_t_*` prefix. **`analytics` is not blocked.**
- `api/db.py:42` — the shared connection is pinned **once** to `SET search_path = 'analytics'`.
- `agent/tools_sql.py:237-246` — `SQLTool` (the `/ask` path) yields that shared connection and
  does not override the tenant search path.
- `agent/sqlgen.py:912` — the LLM prompt **instructs** the model to write
  `analytics."table_name"`.

**Net effect on a hosted deployment:** `/ask` and `/llm/chat/stream` — the primary AI path —
resolve to the bootstrap admin's schema for every tenant.

**For the currently shipped local single-user desktop build there is no leak** (one tenant,
one schema). It becomes critical only if you go hosted.

Additional hosted-mode defects, from `docs/decisions/ADR-009` plus source review:

| # | Defect | Location |
|---|---|---|
| 1 | API keys carry no `tenant_id` → API-key auth always resolves to the admin's tenant | `api/api_keys.py:29-41`, `:435-439`; `api/middleware/auth_middleware.py:231-238` |
| 2 | `/clean` resolves `Path("data/raw")` instead of `_tenant_raw_dir()` → a non-default tenant can read the admin's uploaded file contents; also not rate-limited | `api/routes/data.py:217-218` |
| 3 | `query_history` has `CREATE TABLE IF NOT EXISTS` with no `ALTER TABLE ADD COLUMN` fallback; two test files encode contradictory schemas → order-dependent `BinderException` | `api/user_data.py:65-74`; `tests/test_account_lifecycle.py:515` vs `tests/test_saas_identity.py:125` |
| 4 | The tenancy tests only exercise helper functions in isolation — no end-to-end HTTP request proves cross-tenant invisibility, which is exactly where defects 1–3 live | `tests/test_saas_tenancy.py:1-10` |
| 5 | MCP DuckDB server opens the warehouse read-only with no `search_path` / tenant filter and enumerates `information_schema` | `mcp_servers/duckdb_mcp_server.py:49-53`, `:84` |
| 6 | `api/dashstudio.py:755-758` mutates the shared connection's `search_path` per request, leaving it pointing at whichever tenant ran last | `api/dashstudio.py:755-758` |

**Also shipped disabled by default:** RLS (`config/settings.yaml:200`), SSO (`:160`),
per-tenant database files (`:228`).

---

## 7. Market reality

### Pricing landscape (researched May–Oct 2026)

| Product | Price | Notes |
|---|---|---|
| ChatGPT Plus | $20/mo | Advanced Data Analysis does upload-CSV → Python → charts |
| ChatGPT Business | $25–30/user/mo | No-training-on-data guarantee — closes the privacy objection |
| Claude Pro | $20/mo | Strong narrative interpretation |
| Julius AI | $35–45/mo | The direct "chat with your data" competitor; DB connectors on Pro |
| Hex | $36–75/editor/mo | Collaborative SQL + Python notebooks |
| ThoughtSpot | $25/user/mo | Search-style analytics |
| Power BI Pro | $14/user/mo | ≈ $168/yr — above your $120 lifetime price |
| Power BI Copilot | $24/user/mo | Requires Fabric F64+ |
| M365 Copilot Excel | $18–30/user/mo | Excel-native |
| DataRobot | from $25K/yr | Enterprise AutoML |

### Where you win

- **Fully offline operation.** No competitor does this. It is the genuine, defensible wedge.
- **Real `.pbix` round trip.** No competitor does this well.
- **Price.** $120 once beats Power BI Pro in year one and beats a $20/mo AI subscription.
- **Named segments that will pay for privacy:** consultants and agencies (client NDA, PII),
  finance / healthcare / legal / government (data cannot leave the building), and
  **Power BI Desktop users** who want AI + a real `.pbix` without a Fabric license.

### Where you lose

- **Individuals doing one-off analysis.** ChatGPT Plus already covers it, and most people
  already pay for it. `website/comparison.html` effectively concedes this: *"Choose
  ChatGPT/Julius for quick one-off analyses."*
- The "you'll pay per month and lose control of your data" argument is **weak** — ChatGPT
  Business already offers the no-training guarantee to anyone willing to pay.
- No cloud/object-store connectors — only SQLite, PostgreSQL, MySQL (`etl/connectors.py:40-53`).
  No `boto3`, no S3/GCS/Azure, no cloud SDK in any `requirements*.txt`.
- The "agent" is a **deterministic NL→SQL compiler with an LLM-assisted SQL rewriter**, not a
  tool-calling agent loop (`agent/planner.py:1649`, `agent/orchestrator.py:310`).

---

## 8. Recommended business model

| Tier | Price | Rationale |
|---|---|---|
| **Free local app** | $0 | Acquisition funnel. The local-first story is the only real moat — do not give it up. |
| **Lifetime license** | **$120 (₹10,000)** | **Primary revenue.** ~95 % margin, no infra, offline-verifiable via the keygen path already built and tested. Undercuts Power BI Pro on year one. |
| **Hosted + AI credits** | ~$19/mo + metered credits | Optional convenience for users who will not run Ollama. **Meter by tokens.** Never "unlimited." |
| ~~Pro $6/mo~~ | **Drop** | Negative margin at unlimited; $6 signals "cheap" to buyers who would pay $120. |

The lifetime path is also the one the code supports best today.

---

## 9. Commercial readiness gaps (independent of the above)

| Gap | Evidence |
|---|---|
| Razorpay KYC not done; Subscriptions capability not enabled | `docs/BILLING.md` §6.5 — an explicit "not yet done" checklist |
| **No payment has ever been completed end-to-end** | Same checklist, item 14: run one live small-amount purchase → webhook → refund → grant revoked |
| No paid plan grant ever verified in production | Same checklist |
| Docker image never started in CI | `.github/workflows/docker-prod.yml` runs `docker build` + Trivy with `exit-code: "0"` (scan never fails) + `compose config` (YAML lint only) — a broken image passes green |
| Read-only `config` mount breaks two features | `docker-compose.yml:19` mounts `./config:/app/config:ro`, but `POST /pipeline/save` (`api/main.py:6603-6615`), `POST /pipeline/schedule` (`api/main.py:6510`) and `POST /llm/providers/update` (`api/main.py:8684-8688`) all write under `config/` — so the Settings page cannot change the LLM provider in Docker |
| Python version split | CI + Dockerfile use 3.11; the local dev environment is 3.13. `requirements.txt:33` gates `fastparquet` on `python_version < "3.13"` → different dependency sets, different code paths |
| Documentation drift | `AGENTS.md` references `website-react/` (does not exist), claims 80 %+ coverage (gate is 74 %, measured 76.60 %), and a test count of 2090 (actual: 2,303) |
| Dead / stray artefacts | `web/ts/` ~4,800 lines unused by the shipped JS tree; empty `lib/` and `dist/`; a 0-byte `ollama` file and a 28 KB `out.html` at repo root; a committed 1.4 MB `.coverage` file |
| In-process state that will not survive a replica | `_STORYTELLING_JOBS` (`api/main.py:4325`), the orchestrator singleton, `_conn` (`api/db.py:25`), `api/db._bi_query_lock`, `api/tenancy._ensured_schemas` |

---

## 10. Path to first revenue — ordered by leverage, not by effort

### Week 1 — make money *possible* (do nothing else first)

1. **Add `email` to `RegisterRequest`** + a "set my email" route. *(unblocks all revenue; ~1 h)*
2. **Razorpay KYC** + enable Subscriptions; create the monthly plan; register the webhook
   (events `payment.captured`, `payment_link.paid`, `refund.processed` + every `subscription.*`).
3. **Run one real low-value purchase** → webhook logged → refund → grant revoked. This has
   never been done.
4. **Rename** AutoML / Segmentation / Forecasting. *(one afternoon, protects the rating)*
5. **Add Playwright** to `requirements.txt` so PDF export is not a placeholder.

### Week 2 — resolve the fork

Decide local-first (recommended) vs hosted. Then make the website, the ADRs and the billing
docs say the same thing. If local-first, keep ADR-009 as the documented v2 path.

### Weeks 3–4 — sell before you build

- Talk to 20 Power BI Desktop users and 20 Indian SMB analysts / consultants.
- Manually sell 10 Lifetime licenses at ₹10,000.
- Ask each one: *"what would make you never use ChatGPT for this?"*
  If the answer is not *"my data can't leave my machine,"* there is no business — and that
  needs to be known before more code is written.

### Only if/when hosted is chosen (not before)

1. `agent/tools_sql.py` — add `analytics` to `_BLOCKED_SCHEMAS` for non-default tenants.
2. `api/db.py:42` — per-request tenant connection instead of a pinned shared one; stop
   `api/dashstudio.py:755-758` mutating the shared `search_path`.
3. `api/api_keys.py` — add `tenant_id`; thread it through `auth_middleware.py:231-238`.
4. `api/routes/data.py:217` — route `/clean` through `_tenant_raw_dir()`.
5. Add token/cost metering per user; make the LLM provider config per-tenant (BYOK).
6. Write the end-to-end HTTP tenancy test that ADR-009:157-164 already claims exists.

---

## 11. The honest summary

The product quality is genuinely **above average for a solo build** — 2,301 passing tests,
76.6 % coverage, real crypto, a working `.pbix` writer, a 59K-line frontend.

The commercial progress is **zero**: no customers, no KYC, no completed payment, and a
marketing site that contradicts the own architecture.

The risk is that the next month of effort goes into the "AutoML" module or the React
marketing site, because those *feel* like progress. They are not.

**Three days of work decides whether this makes money:**

1. Add email to signup.
2. Finish Razorpay KYC.
3. Get 10 people to pay.

**The strongest version of this business is:**
> *"The Power BI Desktop power tool that runs offline and costs ₹10,000 once."*

**The weakest version is:**
> *"A cheap hosted AI analytics SaaS at $6/mo competing with ChatGPT."*

The code is built for the first one. Do not let ADR-009 talk you out of it.
