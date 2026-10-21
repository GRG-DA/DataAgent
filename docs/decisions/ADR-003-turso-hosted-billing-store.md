# ADR-003 — Turso (hosted libSQL) as the production billing store

Date: 2026-10-16
Status: implemented (live smoke passed; credential rotation pending — see below)

## Context

The billing server persists orders, keys, redemptions, feature-use audit rows
and central accounts in a local SQLite file (`billing_server/store.py`,
WAL mode, single process). That is ideal for local/dev, but the hosted
billing site plus central accounts (ADR-002) need durability and shared state
that survives a single VPS disk: deploys, reboots, or a redeployed container
must not orphan purchases or accounts, and a backup story must exist.

Requirements: SQLite-compatible (the store is written against sqlite3:
`row_factory`, per-call connections, `executescript`), no server ops, cheap or
free at low volume, low latency for the primary market (India), and a
zero-config local-first default preserved.

## Decision

Use **Turso** (hosted libSQL) as the production backend, reachable through the
optional `libsql` PyPI client, selected by env var — no other code changes at
call sites:

1. `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` (read in
   `billing_server/config.py`) switch `store._connect()` from local SQLite to
   the remote database. Unset = exact previous local behavior (tests and dev
   machines are unaffected).
2. `billing_server/libsql_adapter.py` bridges the client's gaps: the libsql
   client has no `row_factory` and no `executescript`, so the adapter wraps
   connections with sqlite3.Row-like access (`row["col"]`, `row[0]`,
   `dict(row)`) and commits/rolls back/closes via the context manager.
3. Schema init runs once per target per process (`_initialized_targets`)
   instead of on every call — the DDL now executes statement-by-statement
   (the remote client has no `executescript`).
4. Database provisioned via the Turso Platform API: org `grgg`, group
   `default` at `aws-ap-south-1` (Mumbai), database `dataagent-billing`
   (`libsql://dataagent-billing-grgg.aws-ap-south-1.turso.io`). Free tier —
   one group only; note new-style location keys (`aws-ap-south-1`) because
   legacy keys like `bom` are rejected.

Rejected alternatives: SQLite file on a VPS volume (no backup/replica story,
manual ops); Postgres/Supabase (heavier ops, per-project limits, overkill at
this volume); central Postgres via Docker Compose (couples billing deploys
to infra the product otherwise doesn't need).

## Consequences

- Production billing state is durable, backed up (Turso point-in-time), and
  reachable from any billing instance; local installs are unchanged.
- `libsql==0.1.11` added to `requirements.txt` (lazy-imported; local mode
  never needs it). `billing_server/.env.example` documents the two vars.
- Latency: ~106 ms cold `SELECT 1` from the dev machine to Mumbai; store
  calls are low-frequency (checkout/webhook/login), so acceptable.
- Actions already taken: region chosen for India; separate DB auth token
  minted (stored only in the temp credentials file, never in git).
- Follow-ups: rotate the Turso platform API token (it appeared in a chat
  transcript) and confirm a database token rotation procedure before launch;
  verify Turso disk/row usage against the free tier once live traffic
  exists.
- Tests: `tests/test_billing_store_turso.py` covers backend selection and the
  full store flow over a fake libsql module (no network); live smoke against
  the real database passed (create/read/redeem/counts, temp rows deleted).
