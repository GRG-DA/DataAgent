# ADR-008 — SQL surface security hardening (F1–F6)

Date: 2026-10-17
Status: Implemented (audit-driven; TDD; verified)

## Context

A structured security audit of every path where user input reaches SQL
surfaces (visual query builder, `/sql`, `/execute`, `/tasks/run`, `/ask-sql`,
`/bi/query`, `/merge`, DuckDB MCP server) produced six verified findings,
each reproduced with a working PoC against throwaway databases (never the
live warehouse):

1. **F1 — Visual query builder injection.** `POST /query/visual/execute` with
   `{"from_table": "sales", "limit": "0; DROP TABLE analytics.victim; SELECT 1"}`
   executed the stacked DROP because `limit`/`offset` were interpolated raw
   and `order_by` values reached `ORDER BY` unvalidated. `JoinDetector`
   interpolated table names raw into `SELECT * FROM {table}` (`/auto-join`,
   `/sample`, `/metrics`).
2. **F2 — Stacked statements / write verbs.** `/sql`, `/execute`, `/tasks/run`
   and `SQLTool.execute` accepted multi-statement input; regex-only guards
   were bypassable (`FROM/**/`, comments-as-whitespace).
3. **F3 — File-read table functions.** DuckDB's `read_text`, `read_blob`,
   `glob`, `read_csv_auto`, `read_json_auto` … let any caller read local
   files (`.jwt_secret`, `win.ini`, home-dir listings) and platform tables
   (`api_keys`) — including via comment-inserted identifiers that defeated
   `\bFROM\s+`-style regexes.
4. **F4 — Writes on user-SQL paths.** Nothing prevented INSERT/UPDATE/DELETE/
   DDL from executing through user-facing SQL endpoints; `read_only=True`
   DuckDB connections alone do **not** stop `read_text`/stacked commands
   (audit-proven), so app-level guards plus transactions were required.
5. **F5 — RLS not wired.** The tenant-row-level-security filter
   (`apply_rls`) existed but no endpoint applied it, so authenticated users
   could read other tenants' rows.
6. **F6 — DuckDB MCP server accepted non-SELECT.** The read-only MCP server
   blocked writes only via its own connection mode; PRAGMA and chained
   statements were accepted in some paths.

## Decision

1. **F1 — Coerce and quote at the boundary.** `api/querybuilder.py`:
   `_coerce_row_count` accepts only non-negative integers/plain-digit strings
   (rejects `"5; DROP …"`, floats, bools); `QuerySpec.__post_init__` applies
   it to `limit`/`offset`; `quote_table_ref` quotes every identifier;
   `SortSpec.__post_init__` rejects non-string sort columns with a clean
   error, with a belt-and-braces isinstance check in `_build_query`;
   `SQLBuilder.execute` re-validates before running. `/merge` DESCRIBE and
   left/right table names are quoted.
2. **F2/F3 — AST-first validation.** `agent/tools_sql.py` `validate_query`
   parses with sqlglot and validates on the AST (no regex matching for
   security decisions): single statement only, SELECT/WITH/VALUES roots,
   blocked keywords (INSERT/UPDATE/DELETE/DROP/…), forbidden functions via
   exact names plus `READ_*`/`HTTP_*`/`S3_*`/`AZURE_*`/`GCS_*`/`PARQUET_*`
   prefixes scanned through `find_all(exp.Func)` (sqlglot 30 returns
   dedicated nodes like `exp.ReadParquet`, not `Anonymous`), and platform
   tables (`api_keys`, …) denied in any schema. Messages stay
   user-friendly (uppercased function names, root type reported).
3. **F4 — Read-only transactions.** `etl/readonly.py` exposes
   `readonly_transaction(conn)` (BEGIN → statement → ROLLBACK); `SQLTool.execute`
   and `SQLBuilder.execute` run the statement inside it, and `/bi/query`
   wraps execution in it. `etl/load.py` keeps the legacy contract
   `execute_sql(sql, params, read_only=False)` (ETL writes persist) while
   user-SQL routes `/sql`, `/execute`, `/tasks/run` pass `read_only=True`;
   `loader.query()` always rolls back.
4. **F5 — Wire RLS everywhere SQL is served.** `apply_rls(query, user)`
   rewrites each SELECT via sqlglot to AND-merge a tenant filter into
   existing WHERE clauses (or add one), preserving statement structure;
   wired into `/sql`, `/execute`, `/tasks/run`, `/ask-sql`. Two sqlglot-30
   quirks fixed: bare `WHERE …` fragments cannot be parsed (lift the node
   from `parse_one("SELECT 1 WHERE …")` instead) and
   `iter_expressions()` must be materialized (`list(...)`) before mutation.
5. **F6 — Stdlib MCP guard.** New `mcp_servers/sql_guard.py`
   (`assert_read_only`) — no third-party imports, because the MCP venv has
   no sqlglot: it strips comments, blanks literals, requires a safe leading
   keyword (SELECT/WITH/VALUES/DESCRIBE/SHOW/EXPLAIN/FROM), rejects a second
   statement, blocked keywords/functions, string-literal table refs, and
   PRAGMA. `duckdb_mcp_server.py` imports it via try/except (package vs
   script execution) and opens the warehouse read-only as before.

## Consequences

- All six audited PoC payloads are now blocked end-to-end, with the victim
  table intact after every attempt; legit queries (plain selects, dynamic
  WHERE, aggregated visual queries) still execute.
- User-SQL endpoints are structurally read-only even if a new write slipped
  past the validator; the ETL pipeline keeps its write contract via the
  explicit `read_only` flag.
- Multi-tenant reads through SQL endpoints now always carry the tenant
  predicate; the RLS manager default tenant ("default") applies to
  unauthenticated/server contexts.
- The MCP server rejects non-SELECT input without adding dependencies to
  either venv (main venv must never grow sqlglot for MCP; `.venv-mcp` must
  never grow heavy deps).
- Error messages remain friendly ("Multiple statements not allowed",
  "Forbidden function detected: READ_TEXT", "Access denied to platform
  table: api_keys") and are returned as JSON `{"success": false, "error":
  …}` or 400s, never 500s.

## Verification

- Six TDD suites, 150 tests, all green:
  `tests/test_querybuilder_injection.py` (36),
  `tests/test_sql_validator_hardening.py` (59),
  `tests/test_loader_readonly.py` (8),
  `tests/test_rls_wiring.py` (9),
  `tests/test_mcp_duckdb_guard.py` (36),
  `tests/test_merge_endpoint_injection.py` (2).
- End-to-end PoC (`final_poc.py`, TestClient against the real routes):
  limit injection → clean `success: false`; stacked `; DROP` → 400;
  `read_text('C:/Windows/win.ini')` → 400 `Forbidden function detected`;
  `FROM/**/api_keys` → 400 `Access denied to platform table`; victim table
  alive afterwards.
- Validator PoCs (`validator_check.py`, `validate_bypass.py`): every audited
  payload denied on the app path; `SET`, stacked and `read_*` blocked, plain
  SELECT and dynamic WHERE allowed.
- Full suite: **2051 passed, 21 failed + 1 error** — the identical
  pre-existing environmental set as ADR-007 (agent2 mocking/proxifly,
  export2 `ds_*`/`ve_*`/`cr_*`/`shell_*`, cors validator, billing db files
  on disk, subscription page); zero failures attributable to these changes.
- Test-isolation fix discovered during verification: `tests/conftest.py`
  could be re-imported under a second module name (`from tests.conftest
  import …`), whose guard clobbered `billing_db`'s monkeypatched warehouse
  path mid-test; the guard now redirects only the real `data/curated`
  warehouse path.
- `python -m py_compile` clean on every touched module.
