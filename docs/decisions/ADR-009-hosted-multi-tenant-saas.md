# ADR-009: Hosted multi-tenant SaaS — supersedes ADR-002's data-plane decision

- **Status:** Accepted (supersedes ADR-002 §"the app keeps its data local")
- **Date:** 2026-10-20
- **Supersedes:** ADR-002 (data-locality decision only; ADR-002's central-accounts and
  keyless-subscription decisions stand). ADR-003 (hosted billing store) stands.
- **Related:** ADR-008 (SQL surface hardening), ADR-004 (keyless subscriptions)

## Context

DataAgent shipped as a **local-first, single-user** application: one DuckDB file, one
`analytics` schema, one process, one machine. `docs/OPERATIONS.md` §8 and ADR-002 both
state the data plane never leaves the user's machine.

A 2026-10-20 readiness assessment found that the shipped configuration cannot be hosted
for multiple tenants:

| Finding | Evidence |
|---|---|
| **No tenancy model at all.** Zero of the warehouse's 62 tables carry a `tenant_id`. | `api/rls.py:32`; warehouse inspection |
| RLS is shipped **disabled**, so `apply_rls()` is a no-op on every call site. | `config/settings.yaml:200`; `api/rls.py:81-82`; ADR-008's verification section is stale on this point |
| `/upload` and `/load` take **no identity** and write to a global table namespace. Two tenants uploading `sales.csv` collide; `/load?if_exists=replace` **destroys** the other tenant's table. | `api/routes/data.py:113,136-152,347,382-388`; `etl/load.py:152-165` |
| Any authenticated user can `SELECT` every other tenant's table via `POST /sql`. | `api/main.py:1214`; `agent/tools_sql.py:142` |
| First **central** registrant becomes a global admin **without** `DATAAGENT_BOOTSTRAP_TOKEN` — the gate that `/auth/register` enforces. | `api/central_accounts.py:85` vs `api/main.py:6844-6862` |
| There is **no HTTP route to change a user's role**; roles are edited by hand in `config/users.json`. | `docs/OPERATIONS.md:70` |
| `config/users.json` is a **per-process, unlocked, full-file-rewrite** store. Two replicas each hold their own copy; a registration on A is invisible on B and the unlocked rewrite loses users. | `api/auth.py:20,28,52-59` |
| Sessions live in `InMemorySessionStore`; `RedisSessionStore` is dead code. | `api/session.py:138,234` |
| Rate limiting is **per-process and per-IP only**; `/ask` (the expensive LLM path), `/upload` and `/load` are **not limited at all**. | `api/middleware/rate_limit.py:22-29,66,147` |
| The audit trail is a per-container file. `logs/audit.log` is lost when the container is replaced. | `config/settings.yaml:210` |
| `scripts/build_secure_zip.py` has **no `data` exclusion**, so every release zip contains customers' uploaded CSVs. | `scripts/build_secure_zip.py:56-62` |
| One-writer DuckDB makes >1 API replica structurally impossible. | `etl/load.py:19-49`; `api/db.py:1-13`; `docs/OPERATIONS.md:212-236` |

The control plane (auth, RBAC, signed entitlements, content-sniffed uploads, audit) is
genuinely good and is **kept**. The data plane has no tenancy model and that is what
blocks a hosted launch.

## Decision

**DataAgent becomes a hosted multi-tenant SaaS.** The data plane moves to the server.

### D1 — Tenant isolation is *schema* separation, not query rewriting

Each tenant gets a **dedicated DuckDB schema**, `analytics_t_<tenant_slug>`, in the same
warehouse file. Tenant user tables live only in their own schema. Platform tables
(billing, users, sessions, API keys, catalog metadata, SLA, budgets, workflows) stay in
`analytics` and are reachable by every tenant, so they carry an explicit `owner_id` /
`tenant_id` check.

The shared connection's `search_path` is set per request to
`analytics_t_<tenant>, analytics`, so a query written as `FROM ppc_ads` resolves to the
caller's own table and nothing else.

**Why not the `tenant_id` column + RLS rewriter?** RLS is a *query rewriter*, not a data
model. It was never switched on because **no table has a `tenant_id` column** — turning it
on would inject `tenant_id = 'x'` into every SELECT and fail with `BinderException`.
Making it work would mean a schema migration of 62 tables plus a rewrite of every
read path, and it would still leave the *table namespace* global (two tenants cannot both
own a table called `sales`). Schema separation gives:

- **Physical** isolation, not a filter that can be forgotten in one code path.
- **No cross-tenant name collisions** — `sales` in tenant A and `sales` in tenant B are
  different tables.
- **No `tenant_id` column** to add, backfill or forget to populate.
- A **fail-closed** property: a connection whose search path was never set has *no*
  schema in scope, so unqualified names fail to resolve.

**The enforcement boundary, stated precisely.** DuckDB has **no per-session schema
ACLs**. `search_path` decides how *unqualified* names resolve; an *explicitly qualified*
reference (`other_schema.sales`) still resolves. Isolation therefore has two layers:

1. **Search path** — every unqualified reference, which is what all of the app's own
   queries use, resolves inside the caller's schema.
2. **SQL policy** — `agent/tools_sql.py` refuses any reference to another tenant's
   `analytics_t_*` schema and to the system catalogs (`information_schema`,
   `pg_catalog`), for both the AST and the text-fallback validators. The tenant schema
   name embeds a hash of the tenant id, so it is not enumerable either.

Both layers are covered by `tests/test_saas_tenancy.py`. Layer 2 is the enforcement
point for a hostile query; layer 1 is what makes the app's own code path correct by
construction. A deployment that must defend against a *hostile DuckDB client* (rather
than a hostile SQL string) needs per-tenant database files — that is part of the
horizontal-scale work below.

RLS stays available as a **defence-in-depth** filter for platform tables, and the
`tenant_id` rewriter is retained for the tables that do carry the column.

### D2 — Identity and sessions move out of the process

`config/users.json` (per-process, unlocked, full-file rewrite) and
`InMemorySessionStore` (per-process) are replaced by a **SQLite-backed store** at
`data/users.db` — a real, transactional, multi-process-safe store that needs no extra
infrastructure. The store is behind a narrow repository interface
(`api/user_store.py`) so the PostgreSQL swap for a large deployment is a
one-implementation change, not a rewrite.

### D3 — The first central registrant must present the bootstrap token

`api/central_accounts.py` gets the same `DATAAGENT_BOOTSTRAP_TOKEN` gate that
`/auth/register` already enforces. On a hosted instance, "whoever registers first is
admin" is a takeover, not a bootstrap.

### D4 — Roles become a managed resource

`PATCH /auth/users/{username}` changes a role, `DELETE /auth/users/{username}` removes
the account **and cascades** to its warehouse data, dashboards, conversations, API keys,
subscriptions and notification preferences. Role changes are audited.

### D5 — Quotas and audit become authoritative, not per-process

The rate limiter gains a per-user dimension and covers `/ask`, `/ask-sql`, `/upload` and
`/load`. The limiter and the audit sink both gain a pluggable backend (Redis /
SQLite / file) so a single-process default still works locally while a hosted
deployment can share state across replicas. The AI daily cap moves to the same shared
backend.

### D6 — The entitlement signing key leaves the data host

`data/.grant_key` signs local grants. In a hosted deployment it must not sit beside
every tenant's data. The key path becomes configurable (`DATAAGENT_GRANT_KEY`) so the
billing server can be the sole authority for paid plans, and the app can verify a
server-signed entitlement instead of minting one.

### D7 — Billing is wired, not stubbed

`billing.external_url` must be set for the upgrade button to have a target and
`billing.sync_url`/`sync_secret` for entitlement sync. The app refuses to advertise
"upgrade" against an empty URL and logs an actionable configuration error at startup.

## Consequences

### Accepted costs

- **The data plane is no longer local.** ADR-002's core promise is withdrawn. The
  product is now "hosted SaaS" for the data plane; the *desktop* build remains
  available and still runs single-tenant (one tenant, the local `default` schema).
- **One DuckDB file still means one writer.** Tenant isolation is solved; *horizontal
  scale* is not. The first horizontal step is Postgres for identity/metadata/billing
  plus object storage for raw uploads, with DuckDB retained as the per-tenant analysis
  engine. That is a separate, later ADR.
- **Every query path must set the tenant schema.** A path that forgets is a
  cross-tenant read. Mitigation: the tenant context is a `contextvar` set by
  middleware, the default is an empty schema (fail-closed), and a dedicated test
  asserts that a tenant cannot see another tenant's table.
- **SQLite is not Postgres.** It is the right first step (zero infrastructure, real
  transactions, multi-process safe) but a high-write hosted deployment will need the
  Postgres swap.

### Explicitly out of scope for this ADR

- Replacing DuckDB as the system of record (horizontal scale).
- Object storage for raw uploads (mitigated by D5/D7 shipping rules; a
  `data/raw` exclusion in the release zip is the immediate fix).
- A customer-facing admin UI for role management (the HTTP API is the contract; the
  UI is follow-up).
- Multi-region, SSO/SAML federation beyond what `api/sso.py` already provides.

## Verification

- `tests/test_saas_tenancy.py` — cross-tenant isolation: tenant A cannot read, write,
  list or drop tenant B's table; the same logical table name coexists per tenant.
- `tests/test_saas_identity.py` — role management, delete cascade, bootstrap gate.
- `tests/test_saas_ops.py` — per-user rate limiting, `/ask` limited, shared-backend
  audit, release-zip exclusion.
- The existing suite (2104 tests) stays green.
