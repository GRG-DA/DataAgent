# Launch remediation plan — DataAgent desktop (.exe)

**Date:** 2026-10-21 · **Baseline commit:** `3e03571` (initial import; repo had no VCS)
**Scope:** make the Windows desktop build installable, usable by a fresh user, safe to
hand to strangers, and honest about what it is.

Companion documents: `LAUNCH-READINESS.md` (concept/pricing) and `LAUNCH-TECHNICAL-REVIEW.md`
(hosted multi-tenant). Those review the **hosted SaaS** path (ADR-009). This plan addresses the
**local desktop .exe** path, and reclassifies the hosted-only findings by their real impact
on a single-user local install.

---

## 0. Why this plan exists

Two existing reviews were written against a hosted deployment. A desktop `.exe` has a
different threat model *and* a different set of hard blockers that neither document caught:

1. A fresh install **cannot create its first account** — the bootstrap-token gate that protects
   an exposed Docker port also locks out the owner of a loopback-only desktop app.
2. The JWT secret and the paid-plan grant key are written **into the Program Files bundle**,
   so they cannot be persisted — every launch mints new ones.

Neither is visible from a source read of the hosted path.

---

## 1. Findings and owners

### P0 — the `.exe` will not work for a new user

| ID | Issue | Evidence | Owner |
|---|---|---|---|
| D1 | Fresh install cannot register: first registrant must present `DATAAGENT_BOOTSTRAP_TOKEN`, which is not shipped (`.env` is stripped from the bundle) and has no UI field | `api/main.py:6951-6971`, `web/js/api.js:439`, `DataAgentShell.spec:128-145` | Agent 1 |
| D2 | JWT secret resolves from `__file__` → `<install>\_internal\data\.jwt_secret` (unwritable) → new secret every launch → every session invalidated | `api/auth.py:556-578` | Agent 2 |
| D3 | Grant key resolves from `__file__` → same problem → **paid entitlements invalidated on every restart** | `api/billing/grant_signature.py:56-59` | Agent 2 |
| D4 | Build script downloads opencode `latest` but verifies a pinned SHA256 → build aborts | `scripts/build_windows.ps1:74, 82-92` | Agent 5 |
| D5 | `signtool` not installed → unsigned installer → SmartScreen "Unknown publisher" | `scripts/build_windows.ps1:133-154` | Agent 5 (harden + document) |
| D6 | Download links point at a GitHub repo that does not exist | `website/download.html:53,64`, `shell/update.py` | Agent 7 |
| D7 | No version control at all | (no `.git`) | **done** — `3e03571` |

### P1 — security

| ID | Issue | Evidence | Owner |
|---|---|---|---|
| S1 | `/execute` sandbox escape → RCE. `_SafeModule` keeps the real module in the instance `__dict__`; Python resolves `__dict__` before `__getattr__`, so `pd._module` returns genuine pandas and every guard is bypassed. `np._module.ctypeslib.load_library()` → arbitrary native code | `api/main.py:9824-9889` | Agent 1 |
| S2 | SSRF → cloud metadata / internal pivot. Fully user-supplied URL+headers+method, no scheme check, no DNS resolution, no private-range denial, redirects followed, and `next_url` pagination takes the URL from the response body. `api/security_egress.py` does not exist | `api/main.py:3697, 3798` → `etl/extract.py:237, 265-269` | Agent 1 |
| S3 | CSV/Excel formula injection — openpyxl turns a leading `=` into a live formula; data is attacker-controlled | `export/bi_export.py` | Agent 4 |
| S4 | HTML injection — column names and cell values interpolated raw into HTML | `api/main.py:5508`, `export/bi_export.py:549-577` | Agent 1 (route) / Agent 4 (lib) |
| S5 | `POST /share` takes the owner from the request body — violates the project's own invariant | `api/main.py:2392` | Agent 1 |
| S6 | RBAC is not deny-by-default; `MUTATING_METHODS` defined and never used | `api/middleware/rbac_middleware.py:128, 172, 198-199` | Agent 3 |
| S7 | API-key auth never binds a tenant → resolves to the platform schema | `api/middleware/auth_middleware.py:329-336, 363-375` | Agent 3 |
| S8 | `query_history` schema drift → order-dependent `BinderException` | `api/user_data.py:65-74` | Agent 3 |
| S9 | `/clean` reads flat `data/raw` while upload writes the tenant dir → "File not found" | `api/routes/data.py:217` vs `:158` | Agent 3 |

### P2 — release engineering

| ID | Issue | Owner |
|---|---|---|
| R1 | `dist/` empty, `build/` stale → no reproducible release artifact | Agent 5 |
| R2 | Python version split (CI 3.11 / local 3.13; `fastparquet` gated on `<3.13`) | Agent 5 |
| R3 | Docker `config` mount is read-only but three routes write there | Agent 5 |
| R4 | CI Trivy runs with `exit-code: "0"` → the scan never gates | Agent 5 |
| R5 | No clean-room smoke test of the built installer | Agent 5 |
| R6 | Stray repo-root artefacts (`out.html`, 0-byte `ollama`, empty `lib/`, stale `installers/DataAgent-Setup-verify2.iss`) | Agent 5 |
| R7 | No desktop preflight script | Agent 5 |

### P3 — product, commercial, legal

| ID | Issue | Owner |
|---|---|---|
| C1 | Password reset is a silent no-op — the mailer logs "not configured" and returns `False`, but the endpoint answers `{"success": true}` | Agent 6 |
| C2 | Price conflict: app says Pro $6/mo, checkout says ₹499/mo | Agent 6 |
| C3 | `ai_requests = -1` (unlimited) on every plan with `tenant_ai_daily_cap: 0` → negative margin | Agent 6 |
| C4 | Feature names over-promise: "AutoML" (no model search/CV/ensembling), "Segmentation" (groupby only), "Forecasting" (polyfit, no ARIMA/ETS/Prophet) | Agent 7 |
| C5 | PDF export falls back to a reportlab stub that prints "Install Playwright for full report formatting" — and Playwright is in no requirements file | Agent 4 / Agent 7 |
| C6 | Marketing claims contradict the architecture ("100% Local / $0 / no subscription" vs ADR-009 hosted + paid plans) | Agent 7 |
| C7 | `config/legal.yaml` placeholders would be published | Agent 6 |
| C8 | `keygen_app` `DEV_PUBLIC_FINGERPRINT = ""` makes the dev-key refusal a no-op; baked public key must be regenerated before selling | Agent 6 |
| C9 | No "Sync now" button for entitlement sync; plan never syncs on a local signup | Agent 7 |
| C10 | Support/diagnostics: no way to collect a user's log | Agent 5 |

---

## 2. Rules for the parallel work

1. **Exclusive file ownership.** Each agent owns a disjoint set of files. No agent edits a file
   another agent owns. If a fix seems to need one, describe it in the report instead.
2. **TDD.** Write the failing test first, run it (RED), then implement (GREEN). Record the
   command and the output excerpt. A test that was written but never executed is not evidence.
3. **No `git commit`.** The orchestrator commits after each agent reports, so the history stays
   ordered and the index is never contended. The baseline is `3e03571`.
4. **Run only your own tests.** `python -m pytest tests/test_<yourfile>.py -q -p no:cacheprovider`.
   The full suite is run once, serially, by the orchestrator at the end. `tests/conftest.py`
   redirects the warehouse to a per-process temp dir, so concurrent runs are safe.
5. **Never weaken an existing test** to make a change pass. If an existing test encodes the old
   (wrong) behaviour, change it and say so explicitly in the report.
6. **Do not reintroduce the banned patterns** listed in `AGENTS.md`.

---

## 3. Release gates (all must be green before the `.exe` ships)

| Gate | Command |
|---|---|
| Full suite | `python -m pytest -q` |
| Coverage ≥ 74% | `python -m pytest -q --cov=. --cov-report=term` |
| Security regressions | `python -m pytest tests/test_security_regressions.py -q` |
| Packaging | `python -m pytest tests/test_packaging.py -q` |
| Clean build | `powershell -File scripts\build_windows.ps1` |
| Clean-VM smoke | install → register → upload → query → export → restart → upgrade → uninstall |

---

## 4. Explicitly out of scope for the code fix

- **Buying a code-signing certificate** (D5) — needs procurement + a hardware token or cloud HSM.
  The build script is hardened to fail loudly and print the exact `signtool` invocation.
- **Creating the public GitHub repo / publishing a release** (D6) — needs credentials the
  environment does not have. The download URLs are made configurable and the README documents
  the exact release procedure.
- **Completing a live Razorpay purchase** (C2/C3 verification) — needs KYC and a live key.
  The code path is reconciled and the checklist item stays open.
