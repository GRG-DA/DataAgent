# Launch Technical Review — DataAgent as a Hosted SaaS

**Date:** 2026-10-21 · **Scope:** implementation-level pre-launch review of the hosted
multi-tenant build (ADR-009). **Companion to** [`LAUNCH-READINESS.md`](LAUNCH-READINESS.md),
which covers concept/viability/pricing. This document covers *whether the code is safe to
expose to strangers*, and what to do about it in order.

**Method:** source read of `api/`, `agent/`, `etl/`, `export/`, `billing_server/`, `keygen_app/`,
`docker-compose.yml`, `nginx.conf`, `Dockerfile`, `deploy/`, `scripts/`, `docs/`; the full test
suite (2 456 passed / 3 skipped / 75.49 % coverage); live probing of the running app; and a
sandbox-escape proof-of-concept executed against a faithful replica of the route's own checker.

**Everything marked "verified" below was confirmed by reading the cited code in this repository
during this review — not inferred.

---

## Verdict

| Question | Answer |
|---|---|
| Is the **control plane** production-grade? | **Yes.** Auth, RBAC, signed entitlements, content-sniffed uploads, the audit trail, the metrics registry, container hygiene — all solid. |
| Is the **data plane** safe to expose to strangers? | **No.** Six defects, each reachable by a default self-serve account. Four are one-request compromises. |
| Can a customer arrive, be served, and pay unattended? | **No.** Signup lands in the operator's tenant; no mail is ever sent; the plan never syncs; the AI cost ceiling is unlimited. |
| Can it be run and recovered? | **Partly.** TLS is a commented-out block, the backup requires stopping the app, and the Docker image cannot change its own LLM provider. |
| **Do not launch** until | the six P0 items are fixed and the end-to-end tenant-isolation test exists. |

---

## 0. Corrections to earlier claims in this project

Two of my own earlier statements were wrong, and the gap between them and the truth is exactly
where the worst defect lives. Recording them so the record is honest:

1. **ADR-009's "Verification" section overclaims.** It says
   `tests/test_saas_tenancy.py` proves cross-tenant isolation. It does not: that file exercises
   *helper functions* against two hand-made DuckDB connections. It never drives HTTP `/ask` or
   `/sql`. (Evidence: no `TestClient` / `client.` / `ask` reference in the file.)
2. **The earlier "cross-tenant isolation proven live, 22/22" claim was incomplete.** It covered
   `/tables`, `/sql`, `/profile`, `/export` and `DELETE /tables/*` — all of which do pass. It did
   **not** cover `/ask`, the headline feature. `/ask` is broken (P0-3). The verification gap hid
   the defect.

The lesson generalises: **the isolation proof must drive the real endpoints, not the helpers.**

---

## 1. P0 — launch-blocking (all verified)

### P0-1 · `/execute` sandbox escape → remote code execution
`api/main.py:9850`, `:9852-9863`, `:9889`

`_SafeModule.__init__` does `object.__setattr__(self, "_module", module)` — the real module is
stored in the instance `__dict__`. Python resolves attributes from `__dict__` **before** consulting
`__getattr__`, so `pd._module` returns the genuine pandas module and the entire capability gate is
bypassed:

- `__getattr__`'s `name.startswith("_")` guard — never reached
- the `_BLOCKED_NAMES` / `_BLOCKED_PREFIXES` denylist — never reached
- the "never hand back a submodule" check (`isinstance(value, ModuleType)`) — never reached

The AST checker bans the literal name `ctypes` but not `ctypeslib`, and `_module` is not a banned
attribute, so `np._module.ctypeslib.load_library(...)` returns a live `ctypes.CDLL` → arbitrary
native code execution as the API process user.

**Impact:** read `data/.jwt_secret`, `data/.grant_key`, and `data/curated/warehouse.duckdb` — the
**shared** file holding *every tenant's* data, so one request bypasses the entire schema-per-tenant
boundary. Then the cloud metadata service, and tampering with `logs/audit.log`.

**Reachable by default:** `/execute` needs `query:execute`
(`api/middleware/rbac_middleware.py:55`), and the **default `user` role holds it**
(`config/settings.yaml:218-219`). Every self-serve signup can attempt this.

**Fix**
1. Keep the backing module in a **module-level map keyed by id** — never on the instance — or
   drop `pd`/`np` from the namespace entirely (`api/main.py:9889`). That alone removes the vector.
2. **Run the code in a killable child process** (`multiprocessing` "spawn" + `terminate()` +
   `RLIMIT_AS`), not a thread. This also fixes P0-4's sibling problem (M5/M6 below).
3. Until (1) and (2) are done, **disable `/execute` for hosted deployments** or restrict it to admins.

> Even with a perfect checker, in-process arbitrary Python is not acceptable for multi-tenant
> hosting. A name-denylist is structurally the wrong shape: an AST denylist cannot enumerate the
> reachable capabilities of a C extension.

### P0-2 · API-key authentication never binds a tenant
`api/middleware/auth_middleware.py:329-336` (and the bearer→API-key fallback at `:367-375`)

`_authenticate_api_key()` builds `User(user_id=…, username=…, role=…, auth_method="api_key", …)`
— **no `tenant_id`**. The session path (`:304-311`) and the JWT path (`:353-361`) both set it.

`resolve_tenant_id()` (`api/tenancy.py:246-249`) therefore returns `default` for an API-key user,
and `tenant_schema("default")` (`api/tenancy.py:86-87`) is the **platform** `analytics` schema —
the operator's data plus every platform table.

**Impact:** every tenant's API key silently reads and writes the operator's data. API keys are
exactly the credential a SaaS customer is told to use for programmatic access. Zero exploitation
effort.

**Fix:** pass `tenant_id=user_info.get("tenant_id")` in both branches — the store already returns it
(`api/auth.py:252`).

### P0-3 · `/ask` reads the operator's schema
`agent/planner.py` (81 occurrences of `analytics.`), `agent/orchestrator.py:739,743,980`,
`agent/sqlgen.py:609,912`

The NL→SQL pipeline hardcodes the platform schema. `agent/sqlgen.py:912` *instructs the model*:
`Reference tables with the analytics schema, e.g. analytics."table_name"`. The deterministic
planner has ~40 hardcoded `analytics.<table>` builders on top.

`agent/tools_sql.py:38-43` — `_BLOCKED_SCHEMAS` is `{information_schema, pg_catalog, main, temp}`.
**`analytics` is not blocked**, so an explicit `analytics.<table>` reference passes the policy.

**Impact:** a hosted tenant asks *"show me sales"* → the planner/LLM emits `analytics.sales` → the
**operator's** table. The tenant's own data in `analytics_t_<slug>` is never queried. This is
simultaneously a cross-tenant data leak **and** a broken headline feature.

**Fix**
1. Derive the schema from `tenant_schema()` in `agent/planner.py`, `agent/orchestrator.py` and
   `agent/sqlgen.py` (the schema context header and the prompt).
2. Add `analytics` to `_BLOCKED_SCHEMAS` for non-default tenants — the safety net.
3. **Then write the end-to-end HTTP isolation test** ADR-009 claims: two accounts, two tenants,
   same table name, asserting through `POST /ask`, `POST /sql`, `GET /tables`, an export and a
   Dashboard Studio visual that neither can see the other.

### P0-4 · Orchestrator singleton race → cross-tenant chat history
`api/main.py:129,1344-1348` · `agent/orchestrator.py:219-227,281-306,317,428-430`

`orchestrator` is a process-wide global. Every `/ask` calls
`orchestrator.set_memory(orchestrator._memory, session_id)`, which sets `self.session_id`
(`orchestrator.py:224-227`); that value is read back **later**, after the full LLM pipeline
(up to 60–90 s) at `orchestrator.py:428-430`.

Request A sets `session_id=X`; request B sets `session_id=Y`; A's reply is appended to **B's**
conversation, and B's history is sent to the LLM as context for A's answer. **A cross-tenant leak
through the chat path.**

The same singleton also holds `_available_tables` (`:317`) and an unbounded
`execution_history` (`:219`, appended at `:281,296,306,421`).

**Fix:** make `session_id` / `memory` / `_available_tables` request-scoped (parameters, not
instance state), or give each request its own `Orchestrator`. Bound `execution_history`.

### P0-5 · SSRF → cloud-metadata takeover
`etl/extract.py:237,265-269` → `api/main.py:3798-3816`

`/connect/api` calls `requests.get(current_url, headers=req_headers, params=req_params, timeout=30)`
with a **fully user-supplied** URL, headers, method and body. No scheme check, no host resolution,
no private-range denial, redirects followed by default — and `pagination={"type": "next_url"}`
takes the **next URL from the response body**, so a first-hop allowlist would be bypassed anyway.

**Impact:** `http://169.254.169.254/latest/meta-data/iam/security-credentials/<role>` → the IAM
credentials land in the tenant's own warehouse table → read back → **cloud account takeover of the
hosting account**. Plus internal-network pivot and credential stuffing via the arbitrary headers.

Gate: `require_feature(FEATURE_CONNECT_PAGE)` — **paid tenants only**, which is every customer.

**Fix:** one `api/security_egress.py` helper: parse → resolve DNS → reject loopback / link-local
(169.254, fe80) / RFC1918 / CGNAT / ULA / `0.0.0.0` / `::1` / metadata IPs / non-HTTP(S) schemes;
apply to the initial URL, to the `next_url` pagination value, and **re-check after every redirect**
(`allow_redirects=False`, follow manually). Apply to `/connect/api` and `/connect/database`.

### P0-6 · Exports read the platform schema
`api/main.py` export handlers (`schema=settings.duckdb.schema`) → `export/bi_export.py:29-36,59,111,221,321`

Every export handler passes the **platform** schema, and `bi_export.py` runs
`SELECT * FROM {schema}.{table}` — explicitly qualified to `analytics`.
`/export/zip` exports **every table in the platform schema** in one ZIP.

**Fix:** resolve the schema per request (`tenant_schema()` / `tenant_search_path()`), open via
`tenant_connection()`, and drop the explicit `{schema}.` qualification in `bi_export.py`.

### Also P0 (one-liners)
- **`POST /share` takes the owner from the request body** — `api/main.py:2392`
  `shared_by=request.get("shared_by", "anonymous")`. This is precisely the anti-pattern
  `AGENTS.md` forbids ("Ownership comes from the session, never the request body"). Any user can
  mint a share link attributed to another user and point it at another tenant's `target_id`.
  `share_links` has no `tenant_id`, so `GET /share/{token}` resolves globally.
  **Fix:** derive `shared_by` from `request.state.user`; add `tenant_id` to `share_links` and
  filter on it.
- **RBAC is not deny-by-default.** `api/middleware/rbac_middleware.py:172,198-199` — an unmapped
  path returns `None` and the request proceeds; `MUTATING_METHODS` (`:128`) is defined and never
  used, so the docstring's "deny-by-default for mutating verbs" is not what the code does.
  ~100 mutating routes are reachable by `viewer`/`user` (catalog terms/metrics, queries,
  alerts, schedules, experiments, lineage, quality snapshots, watcher, cache clear, compliance,
  marketplace, tasks, `/bi/query`, and the `html|pptx|pdf|markdown` exports — which are *not* in
  the map, so `viewer` can export those four formats while CSV/XLSX/BI are blocked).
  **Fix:** 403 any mutating verb on an unmapped path, then add the missing paths explicitly; add a
  test that enumerates `app.routes` and fails when a mutating route has no rule.

---

## 2. P1 — the customer cannot be served or pay

7. **Central-accounts signup gets no tenant.** `api/central_accounts.py:113-120` calls
   `add_user(...)` with no `tenant_id` → the account lands in the **operator's** tenant until
   the next restart (`ensure_user_tenants()`). **Fix:** call `assign_tenant()` +
   `update_user(tenant_id=…)` exactly as `api/main.py:6976-6986` does; test it.
8. **No email is ever sent.** `_send_verification_link` exists
   (`api/routes/account.py:241-256`) but is reachable only from `/auth/resend-verification`
   (`:386`); `/auth/register` never calls it, and login never checks `email_verified`.
   `api/mailer.py:57-62` logs *"Mail not configured"* and returns `False` — and
   `/auth/forgot-password` still answers `{"success": true}`. **Password reset is a silent
   no-op.** **Fix:** send on signup; make the mailer fail loudly when SMTP is unset; make `email`
   required.
9. **The plan never syncs.** `cloud_sync.sync_user` is called only from the central-accounts and
   Google login paths. A local-username signup that buys on the billing site never polls for
   its plan, and there is no "Sync now" button (`web/js/pages/subscription.js`).
   **Fix:** call `sync_user` on `/auth/login`; add the button.
10. **`/clean` is broken for tenants** — `api/routes/data.py:217-218` reads flat
    `data/raw` while upload wrote to the tenant directory → *"File not found"*. Also not
    rate-limited.
11. **No AI cost ceiling.** `api/billing/seed.py:154-163` sets `ai_requests = -1`
    (unlimited) on every plan, and `BILLING_TENANT_AI_DAILY_CAP` defaults to `0` = unlimited.
    At ~5–15 K tokens per request, **$6/mo Pro with unlimited AI is negative margin.**
    Also: the app prices Pro at **$6/mo** (`api/subscriptions.py:21-43`) while the checkout
    prices it at **₹499/mo** (`billing_server/config.py:55-61`). Reconcile them.
12. **The billing server is real code but is not deployed.** `billing_server/main.py` is
    1 087 lines of working code — Razorpay orders and subscriptions, HMAC-verified checkout,
    signature-verified webhooks over the raw body, idempotency markers, refund→revoke,
    an HMAC-signed entitlement read API, Ed25519 entitlement tokens, abuse detection, and a
    readiness probe that names missing secrets. **Do not rewrite it.** What is missing is
    deployment, Razorpay KYC, the 11 webhook event registrations, `ENTITLEMENTS_SYNC_SECRET`
    on both hosts, a real Ed25519 keypair, and **one live low-value purchase → webhook → refund
    → grant revoked** (`docs/BILLING.md:297`, still unchecked).
    *Note:* `keygen_app/keygen.py:45` — `DEV_PUBLIC_FINGERPRINT = ""` makes
    `assert_not_dev_key()` a **no-op**, so the promised "the admin build refuses to sign with
    this key" is not enforced. And because the public key is baked, the operator must regenerate
    the pair, re-bake `baked_public_key.py` **and** update `keygen_app/config.py:35` — a release
    step that is not documented.

---

## 3. P2 — you cannot run it

13. **HTTPS is a commented-out block.** `nginx.conf:49-74` — the whole 443 server block is
    commented; port 443 is not published. `scripts/enable_tls.ps1` is **Windows**-only and does
    not even uncomment the compose lines — it tells you to (`:24-26`). No renewal timer, no cert
    copy into the container. **There is no Linux production path for the app at all.**
14. **Backup requires stopping the app.** `scripts/hosted_backup.py:164-185` opens the
    warehouse read-only and, on failure, raises *"the warehouse is locked by another process …
    Stop the app (or take a volume snapshot)"*. `docs/BACKUP-RESTORE.md:263-266` confirms the
    systemd unit does `ExecStartPre=systemctl stop dataagent.service`. Coverage is good
    (warehouse, tenant files, `users.db`, `audit.db`, `data/raw`, config) and secrets are
    excluded by default — but there is **no restore drill, no schedule, no off-site copy, no
    archive encryption, and no documented RPO/RTO**.
    **Fix:** make a volume snapshot of `data/` (warehouse + WAL + `users.db` together) the primary
    mechanism; add a drill script that restores into a scratch root, boots the app and asserts
    `warehouse` and `identity_store` are `ok: true`.
15. **The Docker `config` mount is read-only** (`docker-compose.yml:19`), but
    `POST /llm/providers/update` writes `config/llm_runtime.json` (`api/main.py:8761,8802-8806`)
    and `/pipeline/save` writes under `config/` too. **The LLM provider cannot be changed in
    Docker.** Mount it read-write, or move that state to a writable volume.
16. **No CI deploy.** `.github/workflows/docker-prod.yml` builds the image, runs Trivy with
    **`exit-code: "0"`** — so the scan **never gates**, despite the step name saying
    "fail on HIGH/CRITICAL" — then lints the compose file. No push, no registry, no deploy job.
    A broken image passes green.
17. **Single replica, structurally.** One DuckDB file = one writer. `--scale api=2` fails at
    startup. There are **no rolling deploys**; every release is a hard stop.

---

## 4. P3 — operability and remaining isolation

18. **Global, non-tenant-scoped state:** `queries/history` (hands every tenant the last 50 saved
    SQL of everyone), `alerts`, `schedules`, `experiments`, `lineage`, `quality_trends`,
    `org_departments/teams/groups`, `measures` (a measure defined by tenant A is evaluated
    against tenant B's same-named table — wrong numbers *and* a leak of A's business logic),
    `marketplace` / `data_products`.
19. **CSV / Excel formula injection** — `export/bi_export.py:60,145,267,322` writes cell values
    with no sanitisation; openpyxl turns a leading `=` into a **live formula**. The data is
    attacker-controlled (an uploaded CSV). **HTML injection** in the same file (`:549-577`) and in
    `/report/{table}` (`api/main.py:5566`) — column names and cell values are interpolated raw
    into HTML.
20. **Observability gaps:** plain-text logs with no `request_id` field
    (`config/settings.yaml:130`) — the audit trail is correlatable but the logs are not; no error
    tracking; **no `prometheus.yml` / `alertmanager.yml`**, so the shipped
    `DataAgentNotReady` rule depends on a blackbox job that does not exist here; no disk-space
    metric; the p95 > 5 s alert is meaningless when `/ask` legitimately takes 20–90 s.
21. **The warehouse is not under the migration ledger.** `api/migrations.py` covers only the
    SQLite store. Eight warehouse `ALTER TABLE`s remain ad-hoc inside module `__init__`s, each
    wrapped in `except Exception: pass` — including a `RENAME COLUMN stripe_* → provider_*`
    (`api/billing/schema.py:289`) — so a half-applied migration is **silent**.
22. **Performance:** the shared connection's `search_path` is mutated per request
    (`api/dashstudio.py:758,798,2071,2557`, `api/masking.py:137,317`) and **persists after the
    request**. `api/tenancy.py:399-428` opens a fresh DuckDB connection per request across 60+
    endpoints. `/ask` does N+1 schema introspection per table. The `/execute` timeout is a
    `ThreadPoolExecutor` timeout — **CPython cannot kill a thread**, so one `while True: pass`
    burns a core for the process lifetime.
23. **`.dockerignore` omits** `config/revoked_jtis.json` and `config/google_oauth_token.json`.

---

## 5. What is genuinely good — do not touch it

- **`billing_server/main.py`** — real, working code; signed webhooks, idempotency, refunds, abuse
  detection. Do not rewrite.
- **The grant-signing / licence-guard chain** (`api/billing/grant_signature.py`,
  `api/billing/license_guard.py`) — a hand-edited warehouse row grants **nothing**. Fail-closed
  throughout. Better than most commercial products.
- **The metrics registry and ops endpoints** — bounded cardinality, hashed tenants, correct
  degraded-vs-unhealthy semantics, metric names consistent with the shipped alert rules.
- **Container hygiene** — non-root, healthcheck, restart policy, resource limits, log rotation,
  loopback-only publish, read-only config mount.
- **The browser-facing security posture** — signed double-submit CSRF, host allow-list, CORS
  allow-list, production fail-fast startup checks, `Secure` cookie enforcement.
- **The identity/session store abstractions** — SQLite/Postgres/JSON and memory/Redis/Postgres
  behind narrow interfaces, with parity tests.
- **Upload content-sniffing** (`api/security_uploads.py`) — the extension is never trusted.
- **The test suite** — 2 456 real tests with real DuckDB fixtures and real HTTP round-trips.
- **The `/sql` and `/bi/query` policy** (`agent/tools_sql.py`) — platform tables, system
  catalogs, other tenants' schemas, and every DuckDB file/network table function are refused,
  and the statement runs inside a rolled-back transaction.
- **RLS fails closed** (`api/rls.py:101-116`) — *but* `config/settings.yaml:230` has
  `rls.enabled: false`, so RLS is currently **inert**; schema separation is doing the work.
  Do not advertise RLS as a live control.

---

## 6. The plan, in order

### Release 1 — "not remotely exploitable" (P0)
Sandbox escape · API-key tenant · `/ask` schema · orchestrator scoping · egress allow-list ·
export schema · share owner · RBAC deny-by-default. Add the missing **end-to-end HTTP tenant
isolation test** and correct ADR-009's Verification section.
*Every item is a local, testable change — no infrastructure needed.*

### Release 2 — "a customer can arrive and pay" (P1)
Tenant on central signup · verification mail + loud SMTP failure · plan sync on login + a
"Sync now" button · `/clean` tenant fix · a real AI cost ceiling · reconcile the price lists ·
deploy the billing server + Razorpay KYC · **one live low-value purchase → webhook → refund →
grant revoked**.

### Release 3 — "it can run and be recovered" (P2)
TLS on Linux with renewal · volume-snapshot backup as the primary mechanism + a restore drill +
RPO/RTO · fix the read-only config mount · CI deploy with a **failing** Trivy gate.

### Release 4 — operability (P3)
JSON logging with `request_id` · error tracking · `prometheus.yml` + Alertmanager · tenant-scope
the remaining global state · formula/HTML escaping · warehouse migrations under the ledger.

---

## 7. Two decisions that gate everything

1. **The product fork.** `docs/LAUNCH-READINESS.md` concludes the moat is **local-first**
   ("100 % Local, $0, no subscription", "$120 lifetime"), while ADR-009 moves the data plane to
   the server. Both cannot be true, and a hosted enterprise buyer asks *"where does my data
   live?"* on the first call. **Which is the product?**
   *If local-first:* P0-1 (RCE), P0-5 (SSRF) and P0-4 (the race) drop to medium — but the
   single-writer constraint, the backup story and the customer journey still need Release 2–3.
   *If hosted SaaS:* everything in Release 1 is mandatory, and the tenancy model has to become
   the product's spine rather than a retrofit.
2. **Do you keep `/execute` at all?** In-process arbitrary Python is not defensible for
   multi-tenant hosting. Options: **(a)** run it in a locked-down child process, **(b)** drop
   `pd`/`np` and keep it for teaching, **(c)** remove the route and ship the Notebook as a managed
   query sandbox.

---

## Appendix — reproducing the findings

```bash
# The isolation proof that must exist (and does not yet):
#   two accounts, two tenants, the same table name, then assert through
#   POST /ask, POST /sql, GET /tables, an export and a Studio visual
#   that neither can see the other.

# The sandbox escape (P0-1) — against the route's own checker:
pd._module        # -> the real pandas module, bypassing every guard

# The API-key tenant (P0-2):
curl -H "X-API-Key: <key>" .../tables   # -> the operator's tables

# The export schema (P0-6) and the share owner (P0-6 / share):
#   /export/zip, /export/csv/<table>, POST /share {"shared_by": "someone-else"}
```

**Evidence index** — every finding in this document cites `file:line` in the repository as it
stood on 2026-10-21, and the critical ones were re-confirmed by reading that code during the
review.
