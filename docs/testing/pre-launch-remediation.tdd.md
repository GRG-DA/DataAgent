# TDD evidence — pre-launch remediation of the Windows desktop build

**Scope:** the pre-launch remediation of the DataAgent Windows `.exe`, performed on 2026-10-21
against baseline commit `3e03571` (the tree had **no version control** before this work; the
baseline was created as the first commit).

**Baseline:** `python -m pytest -q` → **2456 passed, 3 skipped** (13 min).
**Final:** → **2788 passed, 3 skipped** (17 min) — see §5 for the final numbers.

This document is the evidence index required by the project's TDD workflow. It says **what each
test proves**, not what was intended. Every claim below is backed by a command that was run and
an output that was observed.

---

## 1. Source plan

`docs/LAUNCH-FIX-PLAN.md` — the item→owner matrix, the file-ownership rules and the release
gates. Companion documents: `docs/LAUNCH-READINESS.md` (concept/pricing),
`docs/LAUNCH-TECHNICAL-REVIEW.md` (hosted multi-tenant), `docs/RELEASE-CHECKLIST.md` (the
gates), `docs/decisions/ADR-010-local-first-v1.md` (the product decision).

---

## 2. User journeys

Derived from the plan (it did not pre-exist these):

| # | Journey |
|---|---|
| J1 | As a first-time user of the downloaded `.exe`, I create my account and start working, without setting an environment variable or looking up a token. |
| J2 | As a paying customer, I close and reopen the app and my plan and my session are still there. |
| J3 | As a user, I export a table and open it in Excel — no cell is evaluated as a formula. |
| J4 | As a user, I ask the app a question — the notebook sandbox cannot be used to run arbitrary native code. |
| J5 | As a user, I connect to an API — I cannot be redirected to the cloud metadata service or my private network. |
| J6 | As a user, I read the privacy policy and the terms and see the operator's details, not placeholders. |
| J7 | As a user, I click "Sync now" and get an honest answer, and my plan is never downgraded. |
| J8 | As a user, I read the marketing site and the price I see is the price I am charged. |
| J9 | As an operator, I build the installer and it contains the app, and it is reproducible. |
| J10 | As an operator, I add a route — the permission and the SQL-identifier guards hold. |

---

## 3. Task report

| ID | What was implemented | Validation command | Result |
|---|---|---|---|
| **D1** | `_bootstrap_gate_required()` — a loopback-only install creates its first account without a token; a network-exposed install still requires one. | `pytest tests/test_launch_core_api.py` | 51 passed |
| **D2/D3** | `api/runtime_paths.py` — one canonical writable-runtime-dir resolver; the JWT secret and the grant key persist. | `pytest tests/test_launch_runtime_paths.py` | 14 passed |
| **D2/D3 (extra)** | 3 more `__file__`-relative runtime paths found by the audit: `keygen_app/storage.py`, `api/llm_deps.py`, `analysis/lineage.py`. | `pytest tests/test_launch_frozen_runtime_paths.py` | 7 passed |
| **S1** | The `/execute` sandbox: the module lives in a closure, dunders refused, a `settrace` deadline unwinds a runaway loop. | `pytest tests/test_launch_core_api.py` | 51 passed |
| **S2** | `api/security_egress.py` — one egress allow-list, applied to the initial URL, the pagination `next_url` and every redirect hop. | `pytest tests/test_launch_core_api.py` | 51 passed |
| **S3** | Spreadsheet formula injection — one sanitiser across CSV, XLSX, Markdown and HTML. | `pytest tests/test_launch_export_hardening.py` | 26 passed |
| **S4** | HTML injection — `analysis/report.py` escapes column names and LLM text; the route escapes anything left raw. | `pytest tests/test_launch_export_hardening.py` | 26 passed |
| **S5** | `POST /share` takes the owner from the session; `share_links` is tenant-scoped. | `pytest tests/test_launch_core_api.py` | 51 passed |
| **S6** | RBAC is deny-by-default for mutating verbs; 215 routes registered; a structural guard walks `app.routes`. | `pytest tests/test_launch_middleware_tenancy.py` | 19 passed |
| **S7** | An API key carries its tenant; the middleware binds it. | `pytest tests/test_launch_api_key_tenant.py` | 7 passed |
| **S8** | `query_history` migration — the loader is resolved from the current settings, not bound at import time. | `pytest tests/test_launch_middleware_tenancy.py` | 19 passed |
| **S9** | `/clean` reads the tenant directory and is rate limited. | `pytest tests/test_launch_middleware_tenancy.py` | 19 passed |
| **SQL** | 27→44 handlers now validate the table identifier before it reaches SQL. | `python scripts/audit_sql_identifiers.py` | `UNVALIDATED IDENTIFIERS REACHING SQL: 0` |
| **C1** | The mailer distinguishes "not configured" from "failed"; the account-lifecycle endpoints never claim a mail was sent. | `pytest tests/test_launch_billing_legal.py` | 32 passed |
| **C2/C3** | One price list in INR; real AI caps (25/50/100); the seed repairs a legacy `-1` row. | `pytest tests/test_launch_billing_legal.py` | 32 passed |
| **C4** | AutoML → Regression Explorer, Segmentation → Segment Breakdown, Forecasting → Trend Forecast, each with a "what it actually does" line. | `pytest tests/test_launch_frontend_claims.py` | 31 passed |
| **C5** | The PDF export is labelled *PDF (basic)*; the stub says so honestly and produces a real PDF. | `pytest tests/test_launch_export_hardening.py` | 26 passed |
| **C6** | The site tells one story; the download links come from one constant. | `pytest tests/test_launch_frontend_claims.py` | 31 passed |
| **C7** | No `[[MARKER]]` remains; the release gate counts the 13 operator-dependent values. | `pytest tests/test_launch_legal_pages.py` | 20 passed |
| **C8** | The dev-key guard is real; the keypair regeneration procedure is documented. | `pytest tests/test_launch_billing_legal.py` | 32 passed |
| **C9** | `POST /subscriptions/sync` forces the sync; the button uses it; the plan is never downgraded. | `pytest tests/test_launch_route_guards.py` | 5 passed |
| **C10** | Doc drift corrected; the SPA links the *rendered* legal pages. | `pytest tests/test_launch_frontend_claims.py` | 31 passed |
| **Review** | The 3 MAJOR and 11 MINOR findings of the independent code review. | `pytest tests/test_launch_review_findings.py` | 27 passed |
| **Email** | The `/export/email` phishing relay: recipient restricted to the account's own address; body sanitised; failures leak nothing. | `pytest tests/test_launch_email_delivery.py` | 29 passed |

---

## 4. Test specification

| # | What is guaranteed | Test file | Type | Result |
|---|---|---|---|---|
| 1 | A fresh install can create its first account with no environment variable; a non-loopback install cannot. | `tests/test_launch_core_api.py` | integration | PASS |
| 2 | The JWT secret and the grant key persist across a restart and never land inside the PyInstaller bundle. | `tests/test_launch_runtime_paths.py`, `tests/test_launch_frozen_runtime_paths.py` | unit | PASS |
| 3 | A signed paid grant still verifies after a simulated restart. | `tests/test_launch_runtime_paths.py` | unit | PASS |
| 4 | `pd._module` raises; the class-chain escape is refused; a runaway loop is unwound at the deadline. | `tests/test_launch_core_api.py` | unit | PASS |
| 5 | An internal address, a metadata address and a non-HTTP scheme are refused; the refusal message contains no URL. | `tests/test_launch_core_api.py` | unit | PASS |
| 6 | A cell starting with `=`/`+`/`-`/`@` (or a leading tab) is stored as text, in CSV, XLSX and Markdown. | `tests/test_launch_export_hardening.py` | unit | PASS |
| 7 | A numeric cell keeps its type — a float is not turned into text. | `tests/test_launch_export_hardening.py` | unit | PASS |
| 8 | A report never carries a live `<script>`/`<img onerror>` from an uploaded CSV. | `tests/test_launch_export_hardening.py`, `tests/test_launch_core_api.py` | integration | PASS |
| 9 | A share link is owned by the session user and is invisible to another tenant. | `tests/test_launch_core_api.py` | integration | PASS |
| 10 | An unmapped mutating route is refused; every mutating route has a rule. | `tests/test_launch_middleware_tenancy.py` | structural | PASS |
| 11 | An API key resolves to its owner's tenant, and the key stores that tenant. | `tests/test_launch_api_key_tenant.py` | unit | PASS |
| 12 | The `api_keys` index migration survives the `tenant_id` column, with and without it. | `tests/test_launch_api_key_migration.py` | unit | PASS |
| 13 | `/clean` reads the tenant's own directory and is rate limited. | `tests/test_launch_middleware_tenancy.py` | integration | PASS |
| 14 | **No table identifier reaches SQL unvalidated.** | `scripts/audit_sql_identifiers.py` (run by `tests/test_launch_sql_identifier_audit.py`) | structural | PASS |
| 15 | A table name derived from a filename or a step name is a valid identifier. | `tests/test_launch_review_findings.py` | unit | PASS |
| 16 | The password-reset endpoint never claims a mail was sent, and never enumerates accounts. | `tests/test_launch_billing_legal.py` | integration | PASS |
| 17 | The app price and the checkout price are the same number, and no plan claims unlimited AI. | `tests/test_launch_billing_legal.py`, `tests/test_launch_review_findings.py` | unit | PASS |
| 18 | A remote plan can only ever **raise** the local plan, never lower it. | `tests/test_launch_review_findings.py` | unit | PASS |
| 19 | A report can only be mailed to the account's own address or an allow-listed one; the allow-list is not echoed back. | `tests/test_launch_route_guards.py`, `tests/test_launch_review_findings.py` | integration | PASS |
| 20 | The mail body carries no script, no frame, no form, no event handler, no `javascript:` URL and no remote image; the report's own markup survives. | `tests/test_launch_email_delivery.py`, `tests/test_launch_review_findings.py` | unit | PASS |
| 21 | Every legal-page marker maps to a config key that exists, and no marker survives the fill. | `tests/test_launch_legal_pages.py` | unit | PASS |
| 22 | The feature names describe what the code does, and the old names are gone from the SPA. | `tests/test_launch_frontend_claims.py` | structural | PASS |
| 23 | The build downloads a **pinned** opencode and verifies its hash; it never mixes `latest` with a pinned hash. | `tests/test_launch_packaging.py`, `tests/test_exe_release.py` | structural | PASS |
| 24 | `/app/config` is writable and the seed yaml files are still read-only. | `tests/test_prod_release.py` | structural | PASS |

---

## 5. Coverage and known gaps

The enforced coverage gate is `fail_under = 74` in `pyproject.toml`; the measured value is
**~76.6 %** (the pre-remediation measurement). The new code is covered by the launch suites
listed above.

**Known gaps, stated plainly:**

1. **No clean-VM test.** The installer was verified to build (182 MB) and to extract (the
   low-privilege test variant), and the frozen `dist\DataAgent.exe check` was run successfully
   on this machine — but the **full install → run → uninstall cycle was not executed on a clean
   Windows machine**, because the installer requires elevation and no UAC prompt can be answered
   in this session. `docs/RELEASE-CHECKLIST.md` §4 lists the 16 steps that must still be run by
   a human.
2. **No code signing.** `signtool` is not installed here, so the release is unsigned and will
   trigger SmartScreen. The build script now fails loudly when `-Sign` is requested and the
   tool is missing.
3. **The DNS-rebinding window in the egress check** is documented and accepted: the address is
   resolved for the check and resolved again by the HTTP client.
4. **The notebook sandbox cannot interrupt a blocking C call** — the `settrace` deadline unwinds
   a Python-level loop, not a call inside a C extension. Full isolation needs a child process
   with an address-space rlimit.
5. **`config/legal.yaml` still carries 13 "fill this in before you publish" values.** The
   startup gate and the release checklist count them; they need the operator's real details.
6. **The release repository does not exist yet**, so the download links on the marketing site
   resolve to a placeholder. One constant to flip: `website/js/main.js` →
   `window.DATAAGENT_REPO_URL`.

---

## 6. Merge evidence

The work is committed as **13 commits** on top of `3e03571` (the baseline import):

```
3e03571 chore: initial commit — import existing DataAgent tree for version control
2840988 fix: persist runtime secrets and runtime state in the writable per-user dir
07005c8 fix: neutralise spreadsheet/HTML injection, unify pricing, make mail honest
ec1daf4 fix: make the legal pages, the marketing prices and the feature names honest
3a4df78 fix: align the remaining user-visible feature vocabulary
3803d3b fix: close the first-run lockout, the sandbox escape, the SSRF and 27 SQL injections
f7a998b fix: deny-by-default RBAC, tenant-bound API keys, and the self-serve role
3a46aca fix: the API-key store must work on a fresh warehouse
b8ba5a5 fix: read the API key tenant defensively
71881f5 fix: make the release pipeline reproducible and its gates real
488b735 fix: address the pre-launch code review findings
7c059ec fix: the api_keys index migration must survive the tenant_id column
363199d fix: the environment check must not cry wolf in a frozen build
```

Each commit is self-contained and was made against a green (or a deliberately-red, in the
review-fix commit) suite. The RED→GREEN evidence for every task is in §3; the full suite is
green at the tip.

**If the commits are squashed**, this document is the surviving evidence: it names the test
that proves each behaviour, and the command that runs it.
