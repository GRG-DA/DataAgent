# DataAgent — Agent Instructions

Local-first LLM data analytics agent: Python + FastAPI backend, DuckDB warehouse, ETL pipelines, web/pywebview UI, JWT auth, plan entitlements (checkout lives on a separate external billing site), PyInstaller packaging, Docker.

## Commands

- `python run.py` — launch web UI (auto-reload with `WAREHOUSE_RELOAD=1` env).
- `setup.ps1` — create venv, install requirements, init database (repo root).
- `docker compose up --build` — containerized deployment (see `docker-compose.yml`).
- Test/serve/ETL scripts live under `scripts/`, `etl/`, `api/`.
- **React marketing site** (`website-react/`): `npm run dev` (port 8090), `npm run build`, `npm run preview`. Verify with `python scripts\verify_react_site.py` (needs preview on 8090 + Chrome CDP on 9333). Legacy static site remains in `website/` until sign-off.

## Critical constraints

- **Never** `pip install mcp` into the main venv — it pulls starlette >= 0.49 and breaks the pinned FastAPI/starlette pair (installed: FastAPI 0.148.0 / starlette 1.9.0). Keep the main environment clean (see `requirements.txt` note). MCP servers run from the separate `.venv-mcp` venv (`duckdb` + `mcp<2` pinned).
- **One writer at a time on the DuckDB warehouse.** On Windows, opening `data/curated/warehouse.duckdb` read-write from multiple processes causes "file in use" errors. Use read-only connections for analysis; run ETL writes through one dedicated process. The `duckdb` MCP server (`mcp_servers/duckdb_mcp_server.py`) opens the warehouse read-only and blocks non-SELECT SQL.
- `config/users.json`, `db_connections.json`, `google_login_pending.json`, `llm_runtime.json` are runtime/credential state — gitignored. Never read them into git, logs, or chat without redaction.
- `data/.jwt_secret` and similar key material must never be logged, committed, or surfaced to the model context.

## Workflow (ECC)

1. **Plan** first for anything non-trivial — use the `planner` agent or `/plan`.
2. **TDD** — write the test first (red), implement (green), refactor. Target 80%+ coverage (`/tdd`, `tdd-guide`).
3. **Review** — run the `code-reviewer` agent after writing/modifying code (`/code-review`).
4. **Security** — before any commit touching auth, payments, user input, or SQL: `/security` + `security-reviewer`. Scan with `agentshield scan --path .`.
5. **Verify** — confirm the change works (`/verify`, `verification-loop`).
6. **Remember** — log decisions in `docs/decisions/` (ADR), session learning in the ECC memory vault (`/learn`, `instinct-status`).

Use the specialized subagents where they fit: `python-reviewer`, `security-reviewer`, `architect`, `database-reviewer`, `docs-lookup`.

## Coding style

- Many small files over few large ones; functions under 50 lines.
- Immutability: return new copies, never mutate inputs.
- Validate all user input at API boundaries (schema-based); parameterize all SQL.
- Error messages must not leak internals or secrets. FastAPI → user-friendly messages, detailed server-side logging.
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`, `perf:`, `ci:`.

### Security invariants — do not regress these

These are enforced by tests in `tests/test_security_regressions.py` and friends. If a
change breaks one, fix the change, not the test.

- **Identifiers from a path parameter never reach SQL unvalidated.** Use
  `api.security_paths.validate_table_name` / `validate_column_name` (strict
  identifier regex) and wrap reads in `etl.readonly.readonly_transaction`.
- **The notebook sandbox blocks file reads by capability, not by name.** The
  `pd`/`np` handles are `_SafeModule` proxies: they refuse `read_*`, the
  `pandas.io`/`numpy.lib` submodules, and anything that returns a module
  object. Do not hand a raw module to `exec`.
- **Uploads are content-sniffed**, not extension-checked. `api.security_uploads.validate_content`
  rejects a file whose leading bytes contradict its extension.
- **Paid entitlements are signed.** A non-`keygen` / non-`cloud_sync` subscription
  row must carry a valid grant signature (`api.billing.grant_signature`) over its
  own contents. Editing `warehouse.duckdb` must never grant a paid plan. When you
  write a subscription row, sign it.
- **RLS fails closed.** `api.rls.rewrite_query` raises rather than returning an
  unfiltered query, and the request user must be threaded into the agent
  (`answer_question(user=...)`) or the filter is silently skipped.
- **RBAC is enforced by `api.middleware.rbac_middleware`.** Register a new path in
  `PATH_PERMISSIONS` / `PREFIX_PERMISSIONS` when you add a mutating route. The
  `user` role is the self-serve default; `viewer` is read-only.
- **Ownership comes from the session, never the request body.** Derive `owner_id`
  from `request.state.user`; a client-supplied `owner_id` is never trusted.
- **SSO `state` is single-use and server-verified** (`api.sso._remember_state` /
  `_consume_state`).
- **First-admin registration requires `DATAAGENT_BOOTSTRAP_TOKEN`.**
- **Never commit or ship runtime secrets**: `.env`, `config/users.json`,
  `data/.jwt_secret`, `data/.grant_key`, `data/.grant_migrated`,
  `config/db_connections.json`, `config/llm_runtime.json`,
  `config/google_login_pending.json`. `scripts/build_secure_zip.py` excludes them
  and self-checks the archive; the PyInstaller specs exclude credential state.

## MCP tools available in this project

Freebuff loads its MCP servers from `.agents/mcp.json` (duckdb, memory, sequential-thinking, filesystem, chrome-devtools, context7, playwright, fetch). opencode/VS Code/Copilot use `.opencode/opencode.json`, `.vscode/mcp.json`, and `.github/mcp.json` respectively — keep them in sync when adding a server.

- `duckdb` — query the warehouse (see `.opencode/opencode.json`; read-only discipline).
- `memory` — long-term memory store for cross-session context.
- `sequential-thinking` — deeper reasoning on complex problems.
- `filesystem` — scoped to `data/`, `docs/`, `config/`, `outputs/`.
- `chrome-devtools` — official Chrome DevTools MCP (in `.agents/mcp.json`): drives a live Chrome for direct UI testing — navigate, click, fill, screenshot, console + network inspection. Use it to verify web UI flows end-to-end (e.g. PBIX import in Dashboard Studio).
- `context7` — up-to-date library/framework docs with code examples (backs the `docs-lookup` agent).
- `playwright` — Microsoft's Playwright MCP: real-browser automation for the web UI and React site (navigate/click/fill/screenshot, network + console). Complements `chrome-devtools`; prefer it for scripted E2E flows.
- `fetch` — fetch a URL and return it as markdown (`uvx mcp-server-fetch`); handy for API docs, release notes, and changelogs.
- `chrome-insight` / `project-insight` — from global config (web/UI debugging, codebase analysis).
- `grok` — Grok second-opinion tools (`grok_review`, `grok_challenge`, `grok_consult`, `grok_chat`). Needs the **Grok CLI** installed ([x.ai](https://x.ai/cli)) or `XAI_API_KEY` in the environment. See "Grok (GitHub PR review)" below.
- `laya` — local Jev-style typed decision model (Convai Laya, Apache 2.0) in the dedicated `.venv-laya` venv (`laya[mcp]`): tools `laya_status`, `laya_route`, `laya_predict`, `laya_preset` for choice/score/noul decisions only — not chat or code. First run downloads HF checkpoints; `LAYA_PRELOAD=1` warms english+multilingual at server start. Keep out of the main venv (heavy torch/transformers deps).

## Skills

Curated ECC skills are vendored in `skills/` (auto-loaded): tdd-workflow, security-review, coding-standards, frontend-patterns, frontend-slides, backend-patterns, e2e-testing, verification-loop, api-design, strategic-compact, eval-harness, clean-code, refactoring-patterns, working-with-legacy-code, software-design-philosophy, pragmatic-programmer, clean-architecture, technical-documentation, high-perf-browser, design-taste-frontend, redesign-existing-projects, ux-heuristics, improve-code-quality — plus the Strix security suite (api-security-testing, penetration-testing-with-strix, owasp-top-10-testing, ...). Mirrored into `.claude/skills/` and `.agents/skills/` for Claude Code and Freebuff.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Grok (GitHub PR review)

This repo wires in Grok as a Copilot-style reviewer (free open-source `grok-cli-mcp` by howardpen9):

- **GitHub PR bot** — `.github/workflows/grok-review.yml` runs the `howardpen9/grok-mcp` composite action on every PR to main-ish: Grok reviews the diff, posts a sticky comment, and fails the check on a `block` verdict. Requires a repo **Actions secret `XAI_API_KEY`** (`github.com/<owner>/<repo>/settings/secrets/actions`). On GitHub's Ubuntu runners the action auto-installs the Grok CLI, so no local setup is needed there.
- **GitHub Copilot / VS Code Copilot** — `.github/mcp.json` and `.vscode/mcp.json` expose a `grok` local MCP server (`npx -y grok-cli-mcp`), giving Copilot four tools: `grok_review`, `grok_challenge`, `grok_consult`, `grok_chat`.
- **opencode** — the `grok` entry in `.opencode/opencode.json` exists but is **`enabled: false`**: published `grok-cli-mcp@0.2.0` emits draft-04 JSON Schema (`"exclusiveMinimum": true`) which the model API rejects (400 `Tool ... invalid 'parameters' schema`), and CLI-mode cannot detect the Grok CLI on Windows. Keep it disabled until a fixed/API-mode release is published — then flip `enabled` back to `true`. Do not re-enable while `grok-cli-mcp@0.2.0` is installed.
- **Access/billing** — xAI API is pay-per-call (from ~$2/M tokens); new accounts include ~$25 credit. Free/local route: install the Grok CLI (`curl -fsSL https://x.ai/cli/install.sh | bash`, or the npm `@xai-official/grok` shim) and run `grok login` to use your grok.com tier via browser OAuth. The published `grok-cli-mcp@0.2.0` is CLI-mode only, and its CLI detection does **not** resolve npm `.cmd`/`.ps1` shims on Windows — set `XAI_API_KEY` (Windows user env var) so tool calls work here, or use WSL for the CLI-mode path.
- Never commit an `XAI_API_KEY`; it lives only in the GitHub secret / your environment.
