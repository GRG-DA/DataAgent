# ADR-005 — Analytics DDL constraint parity with boot-time strip

Date: 2026-10-16
Status: Implemented (DDL sweep applied to 14 api/ modules; app-level
dedupe restored; live-verified)

## Context

On startup, `api/db.py:strip_constraint_indexes` (called from
`api/main.py:213`) rebuilds every `analytics.*` table without PRIMARY KEY /
UNIQUE / secondary-index constraints, so that ETL loads and analytics DML
never hit DuckDB 1.1.3's duplicated-row constraint failures. Production
warehouses therefore run PK/UNIQUE-free; but the DDL in the `api/` modules
still declared PRIMARY KEY / UNIQUE constraints and only lost them on the
**next boot**.

That gap caused two classes of live failures:

1. **Lazily-created tables** (created by a manager at first use, not by boot
   migration) kept their PK/UNIQUE for the life of the process.
2. **Fresh databases** (new installs, tests) ran with constraints that
   production had already stripped — behavior that passed locally could
   `ConstraintException` differ in production and vice versa
   (`UPDATE ... RETURNING`, indexed-column UPDATEs).

Verified DuckDB 1.1.3 behavior matrix (probes):

- `rowcount` is `-1` for **all** UPDATE/DELETE → `rowcount > 0` success
  checks are always False.
- On a table with PRIMARY KEY + secondary index: `UPDATE` touching an
  indexed column raises `ConstraintException: Duplicate key ... violates
  primary key constraint`; `UPDATE ... RETURNING` always raises.
- Without a PK (production shape): all of the above work, including
  `RETURNING`.

## Decision

1. **Remove PRIMARY KEY / UNIQUE declarations from analytics DDLs** in the
   api/ modules (42 declarations across 14 files: collaboration, catalog,
   premium, org, notifications, masking, cost_usage, subscriptions,
   marketplace, measures, sla, compliance, user_data, workflows), so fresh
   and lazy-created tables match the production post-strip shape on
   day one. Uniqueness that is still business-required is enforced at the
   application level (see 2).
2. **Replace `rowcount > 0` success checks with existence checks**
   (`SELECT 1` before/after) or canonical
   `DELETE + INSERT`-in-transaction upserts for updates that touch indexed
   columns. Applied to masking, collaboration, marketplace, api_keys,
   compliance (revoke/anonymize/retention counts).
3. **Group membership dedupe moves into `OrgManager.add_user_to_group`** —
   the composite PK `(group_id, user_id)` used to reject duplicates by
   constraint violation; the method now checks membership explicitly and
   returns `False` for a duplicate add (preserves the API contract on both
   constrained and stripped shapes).
4. **`JoinDetector.get_sample_data` degrades gracefully** — returns an empty
   DataFrame for missing tables instead of raising `CatalogException`, so
   `detect_joins` (auto-join) returns `[]` for unknown tables as its
   contract implies.

## Consequences

- Fresh installs behave like production from the first request; no
  first-boot behavior change.
- Uniqueness now lives in application code where it is testable; new DML
  must not assume constraint enforcement (repo rule: existence checks, never
  rowcount).
- `tests/test_pipeline_runs_sla_fix.py` locks in the DELETE+INSERT pattern;
  `TestManagersDirect` + org/querybuilder tests lock in the app-level
  dedupe and graceful degradation.
- Known trade-off: raw SQL inserts (outside managers) can now create
  duplicate membership rows; nothing in the codebase does this, but the
  constraint no longer backstops it.

## Verification

- Full suite: 1769 passed, 21 pre-existing failures + 1 error (pandas-3
  drift in `test_cov_agent2`, test-local helpers in `test_cov_export2`,
  pydantic CORS validator probe, packaging artifact) — the two sweep-caused
  failures fixed and re-run green.
- Live (server restarted, real warehouse): pipeline run + heartbeat recorded
  in `/pipeline/runs`; masking rule CRUD; retention policy update; org group
  duplicate add rejected; API key create + delete; privacy request execute;
  marketplace product + access request; warehouse table delete via UI
  (`x` dropped, re-delete 404); chat SSE stream + persisted reply.
