# Test & coverage plan

How to run the DataAgent test suite, what the coverage configuration is, what the measured coverage actually is, where the gaps are, how the suite stays isolated from real data, and a prioritised plan for closing the gap. Written for contributors adding tests and for reviewers deciding what "done" means for coverage.

---

## 1. Running the tests

```powershell
python -m pytest -q                                        # whole suite
python -m pytest tests/test_billing_entitlements.py -q     # one file
python -m pytest -q -k "grant or license"                  # filter by name
python -m pytest -q -k "not slow"                          # exclude
python -m pytest -q --cov=. --cov-report=term-missing      # coverage (full report)
python -m pytest -q --cov=api --cov-report=term            # coverage (package only)
```

`pytest.ini` and `pyproject.toml` both set `testpaths = ["tests"]` and `asyncio_default_fixture_loop_scope = "function"`. `pyproject.toml` adds `addopts = "-q -rs"` (short tracebacks, a summary of skipped tests) and a `filterwarnings` list that silences known-noisy deprecations (pyparsing, `datetime.utcnow`, `maxsplit` as a positional, etc.).

The full suite is currently **2090 tests, all passing** (measured 2026-10-20).

### The coverage gate

Coverage is configured in `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["api", "agent", "etl", "warehouse", "analysis", "billing_server"]
omit  = ["tests/*", "web/vendor/*", "*/__pycache__/*"]
branch = true

[tool.coverage.report]
fail_under   = 60
show_missing = true
skip_covered = false
```

- `source` — the six first-party packages the gate measures.
- `branch = true` — branch coverage is on, so partial branches count as misses.
- `show_missing = true` — the report lists the uncovered line numbers.
- `skip_covered = false` — fully-covered files stay in the report (easier to diff between runs).
- `fail_under = 60` — the gate. The comment in `pyproject.toml` records the rationale: "Target: 80%. Gate enforced at the measured baseline … raise toward 80 as coverage grows, **never lower it to land a change**."

---

## 2. The measured coverage

```powershell
python -m pytest -q --cov=. --cov-report=term-missing
```

**Result on this tree (2026-10-20):**

```
TOTAL                                35927   7915  11674   1517    76%
Required test coverage of 60.0% reached. Total coverage: 76.13%
2090 passed, 1094 warnings in 489.34s (0:08:09)
```

**Total coverage: 76.13%** — produced by the command above. (An earlier measurement of this tree gave 76.29%; the difference is normal run-to-run drift from environment-dependent tests. The number to quote is the one from the most recent run, and the command above is how to reproduce it.)

Note that `--cov=.` measures **more** than the configured `source` list — it also picks up `config/`, `export/`, `keygen_app/`, `shell/`, `mcp_servers/` and `run.py`. The `fail_under = 60` gate applies to whatever `coverage` measured in that run, so the 76.13% above is a whole-tree figure, not a `source`-only one.

The project target is **80%**; the current gate is 60%.

---

## 3. Where the coverage is

### Well covered

The data / statistics / warehouse core is in good shape:

| Package / file | Coverage |
|---|---|
| `warehouse/schema.py` | **100%** |
| `warehouse/queries.py` | 97% |
| `etl/load.py` | 99% |
| `etl/clean.py` | 98% |
| `etl/extract.py` | 97% |
| `etl/connectors.py` | **100%** |
| `etl/watcher.py` | 98% |
| `analysis/forecasting.py` | 99% |
| `analysis/statistical.py` | 95% |
| `analysis/quality.py` | 97% |
| `analysis/bayesian.py` | 99% |
| `analysis/cohort.py` | 97% |
| `export/pbix_export.py` | 89% |
| `export/dashboard_export.py` | 93% |
| `agent/orchestrator.py` | 92% |
| `agent/tools_viz.py` | 99% |
| `agent/schema_intelligence.py` | 94% |
| `billing_server/main.py` | 85% |
| `billing_server/store.py` | 94% |

The whole `analysis/` family sits at 85–100%, and `etl/` / `warehouse/` are effectively saturated.

### Thin — and the thin parts are the security-critical ones

| File | Coverage | Why it matters |
|---|---|---|
| `api/llm_deps.py` | **30%** | Ollama/LM Studio install, start, pull and job polling — 652 statements, the largest untested module. |
| `api/marketplace.py` | **31%** | The marketplace product/request flow. |
| `api/main.py` | **39%** | **10,127 lines / 5,708 statements** — the monolith that holds most of the core routes. One third of it is unexercised. |
| `api/security_uploads.py` | **38%** | **Upload content sniffing** — the magic-byte check that stops a ZIP renamed to `.csv`. |
| `api/google_auth.py` | **41%** | Google OAuth for the Gemini provider. |
| `api/routes/proxies.py` | **49%** | The proxy relay surface. |
| `api/google_login.py` | 51% | Google **login** (the app's sign-in path). |
| `api/premium.py` | 59% | `/premium/*` — key activation and license status. |
| `api/dashboard_store.py` | 60% | Dashboard persistence. |
| `api/workflows.py` | 60% | Workflow definitions and instances. |
| `api/api_keys.py` | 62% | The legacy API-key manager. |
| `api/billing/subs.py` | **65%** | The canonical subscription layer: period advance, cancel, past-due, revoke, legacy sync. |
| `api/compliance.py` | 66% | Compliance / privacy requests. |
| `api/billing/entitlement_token.py` | **69%** | Ed25519 entitlement-token verification — every failure branch (bad signature, wrong product, wrong plan, email mismatch, expiry) is a grant path. |
| `api/billing/feature_ticket.py` | **70%** | Per-request authorization for paid actions. |
| `api/billing/license_guard.py` | **72%** | The keygen / cloud_sync / legacy re-verification guard. |
| `api/auth.py` | 73% | User manager, JWT minting/verification, revoked-JTI persistence. |
| `api/billing/schema.py` | **74%** | Billing DDL — and specifically the one-time grant migration (`_migrate_unsigned_grants`, lines 395–433) is **not covered at all**. |
| `api/collaboration.py` | 75% | Comments / sharing. |
| `api/billing/entitlements.py` | **77%** | `_verify_effective` — the grant-source switch — and the entitlement overlay. |
| `api/billing/grant_signature.py` | **77%** | Signed local grants: key creation, the file-backed path, and the persistence-failure fallback (lines 62, 70–84) are uncovered. |
| `api/middleware/audit_middleware.py` | 78% | The audit middleware's dispatch paths. |
| `api/middleware/rate_limit.py` | 78% | Rate limiting, including the trusted-proxy logic. |

Several modules that are easy to assume are thin are in fact well covered, and the ones that are thin are thin for a reason:

| File | Coverage | Note |
|---|---|---|
| `api/billing/gate.py` | **100%** | The `require_feature` dependency — allow, deny, the dev backdoor and the frozen-build refusal are all exercised. |
| `api/rbac.py` | 99% | |
| `api/audit.py` | 93% | |
| `api/routes/api_keys.py` | **100%** | |
| `api/routes/enterprise.py` | 71% | The weakest of this group — the CRUD success paths are covered, the error branches are not. |
| `api/security_paths.py` | **100%** | The SQL write-keyword deny list. |
| `api/billing/seed.py`, `api/billing/redemption.py`, `api/billing/cache.py` | **100%** | |

### The short version

The pattern is consistent and uncomfortable: **the data pipeline is thoroughly tested and the security boundary is not.** `api/main.py` at 39% is the headline — it is a 10k-line file holding the core upload/ask/SQL/analytics routes and every premium-gated endpoint.

---

## 4. The isolation model

`tests/conftest.py` makes sure the suite can never touch real data.

### The warehouse is redirected before anything can open it

At **import time** — before `api.billing` can run `init_billing_tables()` against the real `data/curated/warehouse.duckdb`:

```python
_TEST_WH_DIR = Path(tempfile.mkdtemp(prefix="dataagent_test_wh_"))
_TEST_WH = str(_TEST_WH_DIR / "warehouse.duckdb")

from config import settings as _settings

_path_now = _settings.duckdb.database_path.replace("\\", "/")
if "dataagent_test_wh" not in _path_now and "curated" in _path_now:
    _settings.duckdb.database_path = _TEST_WH
```

The `if` guard matters: `tests.conftest` can be re-imported under a different module name (e.g. `from tests.conftest import auth_headers`), and re-running it must not clobber a path a fixture has already monkeypatched — otherwise later requests would silently reconnect to the session warehouse.

### The credential store is redirected per test

```python
@pytest.fixture(autouse=True)
def isolated_user_store(tmp_path, monkeypatch):
    """Never write test users into config/users.json (runtime credential state)."""
    from api.auth import user_manager as um
    monkeypatch.setattr(um, "auth_file", tmp_path / "users.json")
    yield um
```

It is **autouse**, so every test in the suite writes its users to a per-test temp file. `UserManager._save_users()` dumps the whole in-memory map to `auth_file`; without this, every test would pollute the real `config/users.json`.

### The fixtures

| Fixture | Scope | What it gives you |
|---|---|---|
| `isolated_user_store` | autouse, function | Redirects `user_manager.auth_file` to `tmp_path/users.json` and yields the user manager. |
| `billing_db` | function | A fresh DuckDB warehouse with the billing tables created and seeded. Monkeypatches `settings.duckdb.database_path` **and** swaps `api.state.loader` so the billing helpers only touch the temp file. Clears the request cache on both setup and teardown. |
| `billing_env` | function | An isolated `billing_server.config`: temp SQLite path, a fresh Ed25519 keypair (private PEM written to `tmp_path`), and stubbed Razorpay / sync / email / admin secrets. Yields `{cfg, keymint, public_pem, private}`. |
| `store_env` | function | The billing store pointed at a temp SQLite file, Turso env cleared, `store._initialized_targets` reset. |
| `fake_turso` | function | A fake `libsql` module installed into `sys.modules`; every `connect()` lands on one shared in-memory SQLite DB. Records connect calls and executed `CREATE` statements for assertions. |
| `tcon` | function | A temp DuckDB connection with an `analytics.sales` table (4 rows). |
| `sample_plan` / `sample_plan_copy` / `sample_kpi_plan` | function | Validated dashboard plans with executable zones (kpi / bar / table) mirroring `_build_plan_from_spec`. |
| `auth_headers` | **helper** (not a fixture) | Bearer JWT headers for `TestClient` requests. Creates the user if missing (`ensure_user`) and mints a real token. Local auto-admin was removed in the Phase 1 auth hardening, so tests hitting protected routes must present real credentials. |
| `sync_env` | function — `tests/test_cloud_entitlement_sync.py` | Points cloud sync at a fake billing host: `BILLING_SYNC_URL=https://billing.example.test`, `BILLING_SYNC_SECRET=unit_secret`. Yields the `api.billing.cloud_sync` module. |
| `dash_conn` | function — `tests/test_cov_main_routes.py` | Stubs `api.dashboards.get_connection` to a temp DuckDB file and yields the connection. |

Anything that needs the real warehouse path is a bug — the point of the model is that no test can reach `data/curated/warehouse.duckdb` or `config/users.json`.

---

## 5. Known-flaky / environment-dependent tests

### The event-loop problem (fixed)

Two provider tests used to call `asyncio.get_event_loop().run_until_complete(...)`. Because another test in the same module had already closed the loop, `get_event_loop()` returned a **closed** loop and the call raised `RuntimeError: Event loop is closed` — making the two tests **order-dependent**: they passed in isolation and failed when another test ran first.

Both now use `asyncio.run(...)`:

- `tests/test_cov_agent.py::TestOpenAIProvider::test_client_rebuilds_when_closed`
- `tests/test_cov_agent2.py::TestLlmDeep::test_ollama_client_rebuild_and_list_fail`

```python
# asyncio.run, not get_event_loop(): after another test closes the
# loop, get_event_loop() returns a closed loop and
# run_until_complete raises "Event loop is closed".
assert _a.run(p.list_models()) == []
```

**Rule for new tests:** never call `asyncio.get_event_loop().run_until_complete(...)`. Use `asyncio.run(...)`, or mark the test `@pytest.mark.asyncio`.

### Environment-dependent (not flaky per se, but machine-dependent)

These need real libraries, a GPU, or network access, and can fail on a bare Windows box:

- **`test_cov_agent2`** — numpy/pandas version drift, WARP (Cloudflare), the llama.cpp server, and anything that reaches the network.
- **`test_cov_export2`** — the stale `_build_plan_from_spec` re-export path, and `fcntl` (Unix-only) used by the file-locking helper.
- **Pydantic CORS probe** — depends on the installed pydantic version's CORS model behaviour.
- **Billing-DB artifact** — a leftover `billing.sqlite3` in the working directory changes what the store fixture picks up.
- **Subscription page drift** — a frontend string change breaks a static-content assertion.

As recorded in `docs/decisions/ADR-006-live-agentic-chat-stream-steps.md` (2026-10-17), a full run then showed "1878 passed; 22 failures + 1 error, all pre-existing environment/platform issues". **The current tree is green — 2090 passed, 0 failures** — so those issues have since been fixed or the affected tests have been made environment-tolerant. Keep it that way: a new test that needs a GPU, a network, or a specific numpy version must skip explicitly rather than fail.

### Windows-specific hazards

- `fcntl` does not exist on Windows — `shell/single_instance.py` and the export file-lock helper both need a Windows branch (they have one).
- Opening `warehouse.duckdb` read-write from two processes fails with "file in use" on Windows but often succeeds on Linux. Tests must never open the real warehouse.
- `ollama.exe` is a GUI-subsystem binary; `capture_output` on it can deadlock. `run.py check` uses the HTTP API by default and only falls back to the CLI with `--ollama-cli`.

---

## 6. The FastAPI 0.148 route-table change

FastAPI 0.148 changed how `include_router` stores routes. Instead of **flattening** the included router's routes into `app.router.routes`, it keeps a single `_IncludedRouter` entry. Any code or test that scans `app.routes` for a path therefore finds **nothing** for any included router — including `api/routes/org.py`, `api/routes/enterprise.py`, `api/routes/api_keys.py` and `api/routes/proxies.py`.

`tests/route_helpers.py` provides the shared workaround:

| Helper | Returns |
|---|---|
| `iter_routes(app)` | Every route object on the app, descending into included routers (max depth 6). |
| `route_methods(app, prefix="")` | `{(METHOD, path)}` for every route whose path starts with `prefix`. |
| `route_paths(app, prefix="")` | The set of route paths starting with `prefix`. **Caveat:** with an empty prefix it reads `app.routes` directly rather than the flattened set — pass a prefix to get the flattened behaviour. |

```python
from tests.route_helpers import iter_routes, route_methods

methods = route_methods(app, "/org")          # {("GET", "/org/tree"), ...}
routes = list(iter_routes(app))               # flat, includes nested routers
```

`_nested_routes()` handles both shapes: it looks for `.routes`, then `.original_router` (an `APIRouter` — take its `.routes`), then `.router`. **Use these helpers in new route-surface assertions** rather than iterating `app.routes` directly, or the test will pass vacuously.

---

## 7. Prioritised plan to close the gap

Security-critical paths first. "Target" is the coverage the area should reach.

| # | Area | Why it matters | Target | Effort |
|---|---|---|---|---|
| 1 | **Signed-grant verification** — `api/billing/grant_signature.py` (77%), `api/billing/license_guard.py` (72%), `api/billing/entitlement_token.py` (69%) | This is the whole "you cannot grant yourself a paid plan by editing `warehouse.duckdb`" story. Every branch here is a branch an attacker walks: wrong signature, wrong plan, wrong email, expired token, revoked key, expired grace. | 90% | **M** — the code is already there; it needs table-driven tests over `verify_grant`, `canonical_grant_value`, `verify_token`, `verified_keygen_plan`, `verified_cloud_sync_plan`. |
| 2 | **Entitlement gate** — `api/billing/entitlements.py` (77%), `api/billing/subs.py` (65%), `api/billing/schema.py` (74%) | `_verify_effective` is the switch that decides free vs paid. `subs.py` owns period advance, cancel, past-due and revoke — the lifecycle that decides whether a paid plan is still live. `schema.py` at 74% means the one-time grant migration (`_migrate_unsigned_grants`) is entirely untested. | 85% | **M** — `subs.py` needs lifecycle-transition tests (renew, cancel-at-period-end, refund → `CANCELLED`, lazy expiry); the migration needs a "legacy unsigned row gets signed exactly once" test. |
| 3 | **RBAC** — `api/rbac.py` (99%), `api/middleware/rbac_middleware.py` (88%), `api/routes/api_keys.py` (100%) | The modules themselves are well covered, but the *enforcement* surface is not: the middleware's public-path allow-list, the `data-role` / `data-permission` checks and the API-key scope checks need explicit negative tests (role escalation attempts, revoked keys, expired keys). | 90% | **S** |
| 4 | **Audit** — `api/audit.py` (93%), `api/middleware/audit_middleware.py` (78%) | The audit log is the evidence trail for "who did what". The middleware's dispatch paths (which events fire for which route) are barely exercised; if it silently stops recording, nobody finds out until an incident. | 90% | **S** |
| 5 | **SQL AST / static safety gate** — `agent/sqlgen.py::check_read_only` (module 90%), `mcp_servers/sql_guard.py` (83%), `api/security_paths.py` (100%) | `check_read_only` is the level-2 gate that only allows `SELECT` / `WITH`, exactly one statement, and no DDL/DML keyword. It has a string/comment stripper that is easy to get wrong — a bypass here is a data-exfiltration or destructive-write path. Needs adversarial cases (keyword inside a string literal, inside a comment, unicode/whitespace tricks, stacked statements). | 95% for the gate itself | **M** |
| 6 | **Upload content sniffing** — `api/security_uploads.py` (38%) | The module exists precisely because an extension check is not a content check: a ZIP renamed to `.csv` reaches pandas/openpyxl otherwise. At 38% the magic-byte table, the empty-file case and the "ZIP without `xl/` entries" case are barely tested. | 90% | **S** — the module is small; a table-driven test over `_MAGIC` covers it. |
| 7 | **SSO / OAuth state validation** — `api/sso.py` (87% overall; the state store at lines 46–72 is partly uncovered), `api/google_login.py` (51%) | The `state` parameter binds an authorization response to the request that started it. The store's expiry pruning and single-use consumption are the CSRF defence — lines 49, 52–53, 59 and 69 are uncovered. `google_login.py` at 51% is the app's own sign-in path. | 90% | **S** |
| 8 | **Billing server checkout surface** — `billing_server/main.py` (85%), `billing_server/keymint.py` (72%), `billing_server/razorpay_client.py` (78%) | The money path: order/subscription creation, HMAC verification, webhook signature verification, idempotency markers, and the entitlement/feature-ticket minting. `razorpay_client.py` at 78% leaves the whole live-Razorpay HTTP path (lines 35–56, 61–70) unexercised — that is where a wrong amount or a missing `customer_notify` would hide. | 90% | **M** |
| 9 | **Rate limiting + trusted proxies** — `api/middleware/rate_limit.py` (78%) | The `TRUSTED_PROXY_NETWORKS` logic decides whose `X-Forwarded-For` is believed. A regression there either shares one bucket across all users (lockout) or lets a client spoof its IP and bypass the limit. | 90% | **S** |
| 10 | **The monolith** — `api/main.py` (39%, 10,127 lines) | The biggest single gap. The routes that are untested are the ones nobody has exercised: uploads, `/ask`, `/sql`, the export family, the pipeline family, the catalog, notifications, SSO callbacks. Splitting the file into routers is the structural fix; targeted route tests are the immediate one. | 60% near-term, 80% after the split | **L** — this is a multi-week effort, and the right first step is to extract routers and test them in isolation. |
| 11 | **LLM dependency management** — `api/llm_deps.py` (30%) | 652 statements of installer/downloader/job-management code. Lower risk than the above (it shells out to installers), but a 30% file is a 30% file. | 70% | **M** |
| 12 | **The desktop shell** — `shell/app.py` (18%), `shell/runtime_check.py` (13%), `shell/tray.py` (11%) | The shell is Windows-only and hard to test, but `runtime_check.py` is the preflight that decides whether the app starts at all, and `port.py` (100%) / `single_instance.py` (66%) are the parts that misbehave quietly. | 60% | **M** — extract the pure logic (path resolution, the `is_our_app` probe) and test it. |
| 13 | **The security-critical floor** — raise `fail_under` from 60 toward the 80% target | The gate is currently 60 with the measured value at 76.13%. Every point of the gate above the measurement is a point the suite could regress without failing CI. | 65 → 70 → 75 → 80 | **S** per step, but only after items 1–9 land. **Never lower the gate.** |

### Order of work

1. Items **1–7** first: they are the security boundary, they are small, and they are the parts where a regression is silent.
2. Item **8** next — it is the money path.
3. Items **9–12** after.
4. Item **13** last, in 5-point steps, and only once the measurement is comfortably above the new floor.

### Non-goals

- Chasing a percentage on `api/main.py` by writing shallow "route returns 200" tests. Extract the router first, then test behaviour.
- Testing code that only exists to satisfy a coverage number. If a branch is unreachable, delete it or mark it `# pragma: no cover` and say why in the docstring.
