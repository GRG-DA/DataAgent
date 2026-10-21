# Operating DataAgent as a hosted SaaS

Companion to `docs/decisions/ADR-009-hosted-multi-tenant-saas.md` (the design
decision) and `docs/OPERATIONS.md` (the general runbook).  This page covers
**running it for other people**.

---

## 1. What changed, in one paragraph

Every account now belongs to a **tenant**, and every tenant owns a **dedicated
DuckDB schema** (`analytics_t_<slug>`).  A request's tenant is bound by
`api/middleware/tenancy_middleware.py` and applied as the session's
`search_path`, so a query written as `FROM sales` resolves to the caller's own
table.  The `default` tenant owns the pre-existing `analytics` schema, so a
single-user install and the desktop build are unchanged.

Identity moved from `config/users.json` to `data/users.db` (SQLite), which is
transactional and safe across processes.  Roles are now manageable over HTTP.

---

## 2. Minimum configuration for a hosted deployment

| Variable | Required | Why |
|---|---|---|
| `DATAAGENT_BOOTSTRAP_TOKEN` | **yes** | The first account (the operator's admin) must present it. Without it, the first registrant over HTTP cannot become admin, and the central-accounts path is refused too. |
| `BILLING_EXTERNAL_URL` | **yes** | Where checkout lives. Empty ⇒ the app is a free-plan deployment with no purchase path. |
| `BILLING_SYNC_URL` + `BILLING_SYNC_SECRET` | recommended | Entitlement sync from the billing server. Without it, paid plans are granted by an admin over HTTP. |
| `REDIS_URL` | recommended | Shares the rate limiter and the audit sink across replicas. Without it both are per-process. |
| `TRUSTED_PROXY_NETWORKS` | **yes, behind a LB** | Without it every client behind the proxy shares one rate-limit bucket (a global lockout, and an easy way for one tenant to exhaust everyone's quota). |
| `TRUSTED_HOSTS` | **yes, in production** | Host-header allow-list (DNS-rebinding guard). Defaults to `*`. With `APP_ENV=production` the app **refuses to start** without an explicit list. |
| `DATAAGENT_GRANT_KEY` | recommended | The local grant-signing key. Keep it **outside** the data host in a hosted deployment — the billing server should be the authority for paid plans. |
| `APP_ENV` | **yes, in production** | `production` turns on the fail-fast checks: an explicit `TRUSTED_HOSTS` and a non-loopback CORS allow-list are required. |
| `API_CORS_ORIGINS` | **yes, in production** | Comma-separated allow-list of browser origins. Required (non-loopback) with `APP_ENV=production`. |
| `METRICS_TOKEN` | recommended | Bearer token a Prometheus scraper presents to `GET /metrics`. Without it the exposition is restricted to authenticated administrators — it names every API path, per-path request counts and the tenant count, so it is never anonymous. |
| `NOTIFICATION_SMTP_HOST` + `NOTIFICATION_SMTP_USER` / `_PASSWORD` | **yes, for the account lifecycle** | Without it the password-reset / verification / invite endpoints answer but **send nothing**. The app logs a warning at startup. |
| `SECURITY_SESSION_FORCE_SECURE_COOKIE` | **yes, in production** | Forces `Secure` on the session cookie even when the app sees plain HTTP behind a TLS-terminating proxy. |
| `DATABASE_URL` | recommended | PostgreSQL for the identity store and sessions. Without it the app uses SQLite (`data/users.db`) and a per-process session store. |
| `DATAAGENT_USER_STORE` | optional | `postgres` \| `json` \| `sqlite`. Defaults to Postgres when `DATABASE_URL` is set, with a logged fallback to SQLite. |
| `SESSION_BACKEND` | optional | `memory` \| `redis` \| `postgres`. Defaults to Redis with `REDIS_URL`, Postgres with `DATABASE_URL`. |
| `BILLING_TENANT_AI_DAILY_CAP` | recommended | Per-tenant daily AI-request budget. `0` = unlimited. A per-user cap alone lets one tenant's many users exhaust the shared LLM budget. |
| `DATAAGENT_PUBLIC_URL` | recommended | The address the account-lifecycle emails point at (the links carry the token in the URL **fragment**). |

The app logs a warning at startup for each of these that is missing.

---

## 3. First-run sequence

```bash
# 1. One instance only — DuckDB is single-writer (see §6).
export DATAAGENT_BOOTSTRAP_TOKEN="$(openssl rand -hex 24)"
export BILLING_EXTERNAL_URL="https://billing.example.com"
export TRUSTED_HOSTS="analytics.example.com"
export TRUSTED_PROXY_NETWORKS="10.0.0.0/8"

# 2. Start the app.
python run.py web

# 3. Create the operator's admin. This is the ONLY account that is not
#    given its own tenant, so its data lands in the `analytics` schema
#    where the existing tooling expects it.
curl -X POST http://127.0.0.1:8002/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"ops-admin","password":"<strong-password>","role":"admin",
       "bootstrap_token":"'"$DATAAGENT_BOOTSTRAP_TOKEN"'"}'
```

Every later signup is assigned its own tenant and its own schema automatically.

---

## 4. Managing accounts

Roles are now an API, not a JSON edit:

```bash
# List accounts
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1:8002/auth/users

# Change a role
curl -X PATCH -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"role":"data_analyst"}' \
  http://127.0.0.1:8002/auth/users/some-user

# Move an account to a specific tenant (an organisation)
curl -X PATCH -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"acme"}' \
  http://127.0.0.1:8002/auth/users/some-user

# Delete an account — cascades to its data
curl -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8002/auth/users/some-user
```

**The delete cascade** removes the account's rows in the platform tables
(dashboards, conversations, query history, API keys, subscription,
notification preferences) and drops its tenant schema when it was the last
member of that tenant. The last admin account cannot be deleted (HTTP 409).

---

## 5. Multi-tenant behaviour you can rely on

- **Same table name, different tenants.** Tenant A and tenant B can each own a
  table called `sales`; they are different physical tables.
- **Uploads never collide.** Each tenant uploads into
  `data/raw/<tenant-slug>/`, so two tenants uploading `sales.csv` cannot
  overwrite each other.
- **A tenant cannot read another tenant's data.** Unqualified names resolve
  inside the caller's schema; the SQL policy refuses
  `other_schema.*`, `information_schema` and `pg_catalog` references.
- **Platform tables stay shared** (billing, SLA, budgets) and are scoped by
  `owner_id` / `user_id` in SQL. The **data catalog** is tenant-scoped: an
  asset is only listed for the tenant that registered it, and assets with no
  tenant (the shared calendar dimension) are admin-only.
- **Legacy owner-less dashboards are admin-only.** They used to be visible to
  every authenticated caller.

---

## 6. The one-writer rule (still true)

DuckDB allows **one writer per database file**. Concretely:

- Run **one** API process against a given `warehouse.duckdb`.
- Run ETL writes through that same process (or a single dedicated writer).
- A second process opening the file for writing fails with
  `IO Error: File is already open` (Windows) or
  `WarehouseLockedError` (this app's friendly wrapper).

**Horizontal scale is not solved by this ADR.** Tenant *isolation* is solved;
running *replicas* is not. The next step (a separate ADR) is: Postgres for
identity/metadata/billing, object storage for raw uploads, and DuckDB retained
as the per-tenant analysis engine behind a query service.

### 6.1 Per-tenant database files (opt-in)

Schema separation is not a *file*-level boundary. DuckDB has **no per-session
schema ACLs**: `search_path` decides how unqualified names resolve, but an
explicitly qualified reference (`other_schema.sales`) still resolves. The SQL
policy (`agent/tools_sql.py`) refuses those references, which is enough against
a hostile **query** — but not against a hostile **DuckDB client** that opens
`data/curated/warehouse.duckdb` directly.

For that threat model there is an opt-in mode that gives each tenant its own
file:

```yaml
# config/settings.yaml
security:
  tenancy:
    per_tenant_database: false   # default OFF
```

```bash
# or per process
export DATAAGENT_PER_TENANT_DATABASE=1
```

With it on:

| What | Where |
|---|---|
| A tenant's data tables | `data/tenants/<tenant-slug>/warehouse.duckdb`, schema `analytics_t_<slug>` |
| The platform `analytics` schema (billing, users, dashboards, catalog metadata) | the shared `data/curated/warehouse.duckdb` |
| The `default` (operator) tenant | the shared file, as before |

`api/tenancy.py::tenant_database_path()` resolves the file; `etl/load.py`,
`api/querybuilder.py`, `agent/tools_sql.py` and `api/compliance.py` open it
instead of the shared warehouse when the mode is on.

**It is off by default, and with it off nothing changes** — every tenant keeps
its schema inside the shared file, exactly as before.

Operational notes:

- **One writer per file** still applies (see above), but the files are now
  independent: a tenant's file can be written while the platform file is
  being read. Only one process may write a given file.
- Back up `data/tenants/` alongside `data/curated/`. `scripts/build_secure_zip.py`
  already excludes the whole `data/` directory from release archives.
- The platform schema is **not** on a tenant file's search path, so a query
  that mixes a tenant table with a platform table must run against the shared
  connection (`api.db.get_connection()`), not the tenant file.
- Turning the mode on does **not** migrate existing data: a tenant's tables
  that already live in the shared file stay there. Treat it as a fresh-data
  mode, or move the schemas yourself while the app is stopped.

---

## 7. Verifying isolation

`tests/test_saas_tenancy.py` is the guard. To check a deployment:

```bash
# 1. Create two accounts in separate tenants and give each a table.
# 2. Confirm isolation from the API:
#    - GET  /tables          -> only your own tables
#    - POST /sql             -> only your own tables
#    - GET  /tables/<other>  -> 404
#    - GET  /export/csv/<other> -> 404
#    - DELETE /tables/<other>   -> 403/404
```

A live end-to-end proof (two accounts, same table name, no leakage either
way) is in the verification report for the 2026-10-20 run.

---

## 8. Rate limits and quotas

`config/settings.yaml` → `security.rate_limit` sets the window and attempt
count per bucket:

| Bucket | Default | Covers |
|---|---|---|
| `login` / `register` / `accounts` | 10 / 5 / 10 per min | auth (keyed on IP — the identity is not known yet) |
| `sql` | 120 per min | `/sql` |
| `execute` | 30 per min | `/execute` (the Python sandbox) |
| `ask` | 20 per min | `/ask` — the LLM path |
| `ask_sql` | 30 per min | `/ask-sql` |
| `upload` / `load` | 20 per min | the write paths |

The limiter keys on the **authenticated user** when there is one and falls
back to the client IP, so one customer behind a shared NAT cannot be
throttled by another. With `REDIS_URL` set the window is shared across
replicas; without it, it is per-process.

The daily AI cap (`BILLING_FREE_AI_DAILY_CAP`) is separate and is metered into
the warehouse, so it is already shared across replicas.

---

## 9. Audit trail

`logs/audit.log` (the default) is a per-container file and is lost when the
container is replaced.  For a hosted deployment set a shared sink:

```bash
export AUDIT_SINK=redis    # with REDIS_URL set
export AUDIT_SINK=sqlite   # durable, single host
```

`api/audit_sinks.py` provides both.  `AUDIT_SINK=redis` is also selected
automatically when `REDIS_URL` is set.

---

## 10. Release hygiene

`scripts/build_secure_zip.py` excludes the whole `data/` directory, so a
release archive can never carry a customer's uploaded CSVs, the SQLite
identity store, the grant-signing key or the JWT secret.  The script's
self-check asserts this.  `scripts/backup.ps1` includes `data/users.db`.

---

## 11. Known limitations

- **One replica.** See §6.
- **SQLite, not Postgres.** Right for a first deployment; swap in Postgres
  when write volume or replica count demands it. The store sits behind
  `api/user_store.py` so the swap is one implementation.
- **The entitlement signing key is local** unless you set
  `DATAAGENT_GRANT_KEY` to a key the billing server also holds.
- **Revoked-JWT tracking is per-process** (`api/auth.py::_REVOKED_JTIS`) plus a
  JSON file. A multi-replica deployment needs it in the shared store.
- **The catalog is tenant-scoped** (assets are stamped with their tenant at
  registration). Business terms and metric definitions stay organisation-level.
- **`/ask` latency is dominated by the LLM** (~20–90 s). Put a timeout in
  front of it and show the user the step stream (already implemented).

---

## 12. Web-facing security posture

This section describes the browser-facing hardening added for the hosted
deployment: the CSRF guard, the cookie flags, the CORS allow-list and the
host allow-list.  It is additive to the sections above.

### 12.1 Environment variables

| Variable | Required | Why |
|---|---|---|
| `APP_ENV` | **yes, in production** | `production` turns the startup checks into hard failures: the app refuses to start with a development CORS allow-list or without `TRUSTED_HOSTS`. Also logs a WARNING while `api.cors_origins` still contains `localhost` / `127.0.0.1` / `*`. |
| `API_CORS_ORIGINS` | **yes, in production** | Comma-separated allow-list of the origins allowed to call the API. In production it must name at least one real (non-loopback) origin; `*` is refused. |
| `API_CORS_CREDENTIALS` | only for a cross-origin SPA | `1` sends cookies / `Authorization` on cross-origin requests. **OFF by default** — a wildcard allow-list plus credentials is refused by browsers anyway. Turn it on only together with an explicit allow-list. |
| `SECURITY_SESSION_FORCE_SECURE_COOKIE` | **yes, in production** | `1` forces the `Secure` attribute on the session and CSRF cookies. Off by default so a local install over plain HTTP keeps working; a hosted deployment must set it, because a spoofed `X-Forwarded-Proto: http` header must not be able to downgrade the cookie. |
| `TRUSTED_HOSTS` | **yes, in production** | Host-header allow-list (DNS-rebinding guard). The startup check warns when it is unset or `*`, and refuses to start in production. |

### 12.2 CSRF — the double-submit cookie

The app authenticates a browser two ways:

* a **Bearer JWT** — a header only JavaScript can set, so a forged
  cross-site request cannot carry it.  Those requests are not CSRF-able.
* the **`session_id` HttpOnly cookie** — the browser attaches it to every
  request to the origin, including one triggered by somebody else's page.
  Those requests *are* CSRF-able.

`api/middleware/csrf.py` therefore guards **only** state-changing requests
(`POST`, `PUT`, `PATCH`, `DELETE`) that are authenticated by the session
cookie.  Such a request must send the `csrf_token` cookie value in the
`X-CSRF-Token` header:

| | |
|---|---|
| Cookie | `csrf_token` — readable by JavaScript on purpose (not `HttpOnly`), `SameSite=Lax` |
| Header | `X-CSRF-Token: <the cookie value>` |
| Check | header == cookie (constant-time) **and** the value is the one this session is entitled to (`HMAC-SHA256(session_id)`) |

The last check is what a plain double-submit lacks: a hostile sibling
subdomain that can *inject* a `csrf_token` cookie must not be able to make
its own value acceptable.  Because the token is derived from the session id
it needs no second secret, is identical on every replica, and survives a
restart.

**The SPA contract** — one line in the front-end fetch wrapper:

```js
const csrf = document.cookie.split("; ")
  .find(c => c.startsWith("csrf_token="));
if (csrf) config.headers["X-CSRF-Token"] = csrf.split("=")[1];
```

The server hands the cookie out with the login response and refreshes it on
any authenticated GET, so the SPA always has one.  A request that arrives
without a usable token is answered **403** with
`{"detail": "CSRF token missing or invalid. ..."}` — never a 500, and the
expected token is never echoed.  A failed check also re-issues the cookie,
so a client that merely lost it recovers with one retry.

Not checked (each for a reason, all listed in `api/middleware/csrf.py`):
`/auth/login`, `/auth/register`, `/auth/accounts/login`,
`/auth/accounts/register` (no session exists yet, so no token can exist),
`/auth/forgot-password`, `/auth/reset-password`, `/auth/verify-email`,
`/auth/resend-verification`, `/auth/accept-invite` (the caller proves
possession of a single-use email token) and `/auth/logout` (a forged logout
is a nuisance, and refusing it would strand a user whose cookie is
missing).

### 12.3 Cookies

`session_id` is `HttpOnly`, `SameSite=Lax`, `Path=/` and 8 hours long.  It
is marked `Secure` when the request reached the app over HTTPS — directly
or through a TLS-terminating proxy (`X-Forwarded-Proto: https`) — and always
when `SECURITY_SESSION_FORCE_SECURE_COOKIE=1`.  `csrf_token` carries the
same `Secure` / `SameSite` attributes but is deliberately **not**
`HttpOnly`, because the SPA has to read it.

### 12.4 CORS

`api.cors_origins` is the allow-list; `api.cors_credentials` (default
`false`) controls whether cookies travel with cross-origin requests.  The
app logs a WARNING while the allow-list still contains `localhost`,
`127.0.0.1` or `*`, and with `APP_ENV=production` it **refuses to start**
unless the operator named at least one real origin.  A wildcard is refused
in production.

### 12.5 Host allow-list

`api/middleware/host_guard.py` owns the policy around `TRUSTED_HOSTS`:
unset or `*` logs a WARNING (the `Host` header is not validated, so DNS
rebinding is possible) and, with `APP_ENV=production`, the app refuses to
start.

---

## 13. Legal pages

The pages under `web/legal/` (`privacy`, `terms`, `dpa`, `security`) are
served by a route that fills in the operator's details from
**`config/legal.yaml`**, so the four pages cannot drift apart.

A value that is still a `[[MARKER]]` is replaced with a visible notice —
never published as `[[COMPANY_NAME]]` — and the app logs a WARNING at
startup naming the markers that remain. `scripts/ci/preflight.ps1` reports
them too.

Before launch, fill in `config/legal.yaml`:

```
company.name / address / jurisdiction / contact_email / security_email / dpo_email
legal.effective_date / data_region / retention_days
subprocessors.hosting / storage / email / llm / monitoring
```

---

## 14. Monitoring

`GET /metrics` exposes a dependency-free Prometheus registry: HTTP request
counters and a duration histogram (paths normalised so ids never become
label cardinality), SQL and AI counters, rate-limit counters, a tenant count
and per-tenant request counters (tenants are **hashed** — a raw tenant id
never becomes a label), plus `dataagent_health_component_ok` per dependency.

The exposition is **not anonymous**. Present `METRICS_TOKEN` as a bearer
token, or read it with an admin session. `METRICS_PUBLIC=1` makes it
public for a trusted sidecar.

`deploy/prometheus/alert_rules.yml` ships the alert rules (down, not-ready,
a failed dependency, a 5xx rate above 5 %, rate limiting, a p95 above 5 s
and an AI-usage spike). Point a Prometheus at the app with:

```yaml
scrape_configs:
  - job_name: dataagent
    metrics_path: /metrics
    bearer_token_file: /etc/prometheus/secrets/dataagent_metrics_token
    static_configs:
      - targets: ['dataagent:8002']
```

---

## 15. Schema migrations

`api/migrations.py` is an ordered, versioned migration runner with a ledger
in the identity database (`data/users.db`). Migrations are idempotent and
are **appended only** — never renumber a released migration, because a
deployed instance would skip or repeat it. The runner is called at startup;
a failure is logged and the next start retries it.
