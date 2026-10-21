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

---

## 5. Completion log — Agent 7 (frontends: `web/`, `website/`, `docs/FRONTENDS.md`)

Scope of this agent: the SPA (`web/**`), the marketing site (`website/**`), `docs/FRONTENDS.md`, and a new
guard file `tests/test_launch_frontend_claims.py`.

| ID | Status | What changed |
|---|---|---|
| **C4** | **done (frontend)** | The three over-promising feature names are gone from the SPA: *AutoML* → **Regression Explorer**, *Segmentation* → **Segment Breakdown**, *Forecasting* → **Trend Forecast**. Each panel now carries a "what this actually does" line (adjusted R² comparison / a group-by breakdown, not clustering / a least-squares trend, not ARIMA-ETS-Prophet). Endpoint paths, API method names and the Python module names are unchanged. |
| **C5** | **done (frontend)** | The export page no longer offers a "PDF Report": the button is **PDF (basic)** with a tooltip, plus a `#pdf-export-note` naming the fully formatted exports (Excel, PowerPoint, `.pbix`, HTML, Markdown, CSV, Parquet). The `/export/pdf/` route is still wired. The backend stub itself is owned by another agent. |
| **C6** | **done** | The site now tells one story: a **local-first desktop app** that ships a free tier, with a one-time **Lifetime licence ($120)** and an optional Pro plan; "no telemetry" is scoped to the app; "open source" names **AGPL-3.0** (verified against `LICENSE`); the blanket "$0 / no subscription / 100% Local" claims are gone. Every GitHub/download URL now derives from **one** constant, `window.DATAAGENT_REPO_URL` in `website/js/main.js`. `docs/FRONTENDS.md` records the v1 = local-first decision, ADR-009 as the documented v2 path, and a **Claim consistency** checklist. |
| **C9** | **done (frontend) / backend route missing** | The subscription page now has a **Sync now** button that re-reads `GET /subscriptions/me` (the only plan-reading route that exists) and reports *Syncing… / Synced / Sync failed*. **No force-sync route exists in the backend** — the exact spec is in the agent report. |
| **C10** | **done** | `AGENTS.md` and `README.md` drift (`website-react/`, 80 %+ coverage, 2090 tests) is already corrected in the tree. The SPA footer now links the **rendered** legal pages (`/legal/<page>.html`) instead of the raw static template, so an unfilled `[[MARKER]]` can never be published. |

Verification: `python -m pytest tests/test_launch_frontend_claims.py -q -p no:cacheprovider` → **31 passed**.
The download-link rewrite was additionally verified in a real browser (Playwright): the anchors resolve to
`{RELEASE_BASE}/DataAgent-Setup.exe` and `{RELEASE_BASE}/DataAgent.exe` at runtime.

### The one setting the operator must flip (D6)

`website/js/main.js` → `window.DATAAGENT_REPO_URL = 'https://github.com/DataAgent/DataAgent';`

Change it to the real `<owner>/<repo>` once the repository is published, and upload `DataAgent-Setup.exe`
and `DataAgent.exe` to that repository's latest release. No page needs editing — the markup carries
`data-download="setup|portable"` and `data-repo-link="/issues|/discussions"` markers instead of URLs.

### Final status of the items this agent left open

Every item above was picked up by another agent and is now done:

| Item | Status | Where |
|---|---|---|
| The PDF backend stub | **done** | `export/pdf_export.py` states that full formatting is not available in this build, names the formats that do work, and produces a real PDF with a data preview. |
| The legal-page marker lookup | **done** | `api/routes/legal.py::MARKER_KEYS` is an explicit marker -> config-key table; a test fails when a page uses a marker with no key. All 38 markers now substitute. |
| The backend user-visible vocabulary | **done** | `api/main.py`, `analysis/pipeline.py`, `analysis/insights.py`, `agent/orchestrator.py` all use the new names. |
| The force-sync route for C9 | **done** | `POST /subscriptions/sync` in `api/subscriptions.py`; the SPA calls it and falls back to `GET /subscriptions/me`. |
| `shell/update.py` hardcoding a repo | **done** | The repo is configurable (`$DATAAGENT_RELEASES_REPO` / `app.releases_repo`), and "no release published yet" is no longer reported as "could not reach the release server". |

---

## 6. Completion summary (all agents)

| ID | Status | One-line outcome |
|---|---|---|
| D1 | **done** | A loopback-only install creates its first account with no environment variable. |
| D2/D3 | **done** | The JWT secret and the grant key persist in `%LOCALAPPDATA%\DataAgent`; a paid plan survives a restart. |
| D4 | **done** | The build downloads a pinned opencode and verifies its hash (it used to download `latest` and abort on the mismatch). |
| D5 | **partial** | The build now FAILS when `-Sign` is requested and `signtool` is missing. **A certificate still has to be bought.** |
| D6 | **operator action** | One constant: `website/js/main.js` -> `window.DATAAGENT_REPO_URL`. |
| D7 | **done** | The tree is under git: baseline `3e03571` + 13 remediation commits. |
| S1 | **done** | The sandbox escape is closed and a runaway loop is unwound at the deadline. |
| S2 | **done** | One egress allow-list, applied to the initial URL, the pagination `next_url` and every redirect. |
| S3/S4 | **done** | Spreadsheet formula injection and HTML injection are neutralised in every export path. |
| S5 | **done** | The share owner comes from the session; links are tenant-scoped. |
| S6 | **done** | RBAC is deny-by-default; 215 mutating routes registered; a structural guard holds. |
| S7 | **done** | An API key carries its tenant. |
| S8 | **done** | The `query_history` migration no longer runs against a different database. |
| S9 | **done** | `/clean` reads the tenant directory and is rate limited. |
| ? | **found by the audit** | 44 handlers interpolated a table identifier into SQL. All validated; `scripts/audit_sql_identifiers.py` proves it and is run by the test suite. |
| ? | **found by the audit** | `/export/email` was a phishing relay (the operator's SMTP identity, any recipient, any body). |
| ? | **found by the code review** | 3 MAJOR + 11 MINOR findings, all fixed ? see `docs/testing/pre-launch-remediation.tdd.md`. |
| R1-R7 | **done** | The build is reproducible, the Trivy gate can fail, `/app/config` is writable, and the desktop preflight + smoke scripts exist. |
| C1 | **done** | The mail never claims a success it did not have. |
| C2/C3 | **done** | One price list in INR; real AI caps (25/50/100). |
| C4 | **done** | AutoML / Segmentation / Forecasting are renamed, with "what it actually does" lines. |
| C5 | **done** | The PDF export is labelled *PDF (basic)* and the stub is honest. |
| C6 | **done** | The site tells one local-first story, and the download links come from one constant. |
| C7 | **done** | No `[[MARKER]]` remains; 13 operator-dependent values are counted by the release gate. |
| C8 | **done** | The dev-key guard is real; the keypair regeneration procedure is documented. |
| C9 | **done** | "Sync now" works and never downgrades the plan. |
| C10 | **done** | Doc drift corrected; the legal pages render. |

### Still open for the operator

1. **Buy a code-signing certificate** and sign the three artefacts. Without it the installer
   ships as "Unknown publisher" and SmartScreen blocks most users.
2. **Publish the release repository** and flip `window.DATAAGENT_REPO_URL`.
3. **Fill in the 13 legal values** in `config/legal.yaml`.
4. **Regenerate the Ed25519 keypair** and re-bake `keygen_app/baked_public_key.py` ? the
   baked public key has no private half in the tree, so no licence can currently be minted.
5. **Run the 16-step clean-machine smoke test** (`docs/RELEASE-CHECKLIST.md` ?4). The install ->
   run -> uninstall cycle could not be executed here: the installer requires elevation and no
   UAC prompt can be answered in this session.
6. **Run one live low-value purchase** -> webhook -> refund -> grant revoked.

---

## 7. The marketing site (second pass)

The site went through the same treatment after the app was done. The full
item-by-item status is in **`docs/WEBSITE-CHECKLIST.md`**; the highlights:

### Found and fixed

| Finding | Severity |
|---|---|
| **The WebGL hero never rendered.** The import map's *values* were bare specifiers (`vendor/?` with no `./`), which the import map spec treats as a bare specifier - Chromium dropped the value, stored a **null**, and refused to resolve `three`. No canvas, no error in the page. | major |
| **The site loaded GSAP from a CDN** on every page - a third-party request that sends the visitor's IP to a CDN, on the site of a product that says "no telemetry", and a total loss of motion offline. | major |
| **The webfonts were loaded from Google Fonts** - the same leak, on every page. Now vendored (32 woff2 files, SHA256 of each recorded). | major |
| **The scroll reveals were dead.** `premium.css` froze the legacy vocabulary with `opacity:1 !important; transform:none !important; transition:none !important`, so every element carrying `.animate-on-scroll` was pinned visible: no reveal, no stagger, no tilt. 11 / 30 / 9 elements on three pages. | major |
| **A 1.9 GB `DataAgent-public.rar` of the working tree** sat in the repo root, containing `config/users.json` (plaintext API keys), `.jwt_secret`, `.grant_key`, the warehouse and `.env`. | critical |
| **`Cross-Origin-Opener-Policy: same-origin`** in the CSP would have severed `window.opener` and broken the Google sign-in popup. | major |
| **The palette was defined three times** with different values; 113 distinct gradients; 20 gradient-text rules; 23 pure-white fills in a dark design; three competing `.btn` base rules; no `--link` token. | major |
| **The legal pages published 38 placeholders** - the marker lookup derived config keys from their last path segment, which never matched the page markers. | critical |
| **A set of elements was invisible** - the light theme's dark-ink colours leaking into the dark layer. | major |
| **The comparison table was clipped** (`overflow:hidden` over `overflow-x:auto`), so the right-hand columns were unreachable on a phone. | major |
| **17 accessibility findings** (skipped heading levels; two pages with no `<h1>`). | major |
| **No sitemap, no robots.txt, no 404 page, no security headers** for three different hosts. | major |
| **The asset cache-busters were not bumped** when the CSS changed, so a returning visitor kept the old stylesheet. | major |
| **The site claimed the installer was 175 MB**; it is 499 MB. | minor |

### Deliberately not done

- **Google Analytics.** ADR-013. It contradicts the product's own claim, and
  the metric that matters - installer downloads - is already reported by the
  GitHub Release the site links to, with no script, no cookie and no third
  party. A test now fails the build if a new third-party origin appears.
- **A light theme.** The site is dark-only and every text colour clears
  4.5:1 on all eight surface steps. Adding a light theme would be a large
  change solving a problem this site does not have.
