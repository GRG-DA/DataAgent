# DataAgent — local-first LLM data analytics agent

Python + FastAPI backend, DuckDB warehouse, ETL pipelines, web/pywebview UI,
JWT auth + RBAC, plan entitlements (checkout lives on a separate external
billing site), PyInstaller packaging, Docker.

> Note: older `README`/`RESEARCH` revisions described a trading-bot demo
> (`collector.py`, `trader.py`, backtests). That content is archived in
> `RESEARCH.md` and does not apply to this tree. See
> `docs/decisions/0001-frontend-and-docs-baseline.md`.

## Quickstart (Windows)

```powershell
.\setup.ps1                   # venv + requirements + init-db
python run.py check          # env check
python run.py init-db        # init data/curated/warehouse.duckdb
python run.py                # API on http://127.0.0.1:8002
python run.py shell          # desktop shell (pywebview)
docker compose up --build    # api :8002 + nginx :80 (+ ollama with --profile full)
```

Auto-reload opens the warehouse twice (Windows “file in use”): opt-in only
with `WAREHOUSE_RELOAD=1`.

## What it does

- **Upload → clean → load:** `POST /upload`, `POST /clean`, `POST /clean/preview`, `POST /load` (CSV/XLSX/Parquet/JSON/TSV, 100 MB default cap).
- **Ask data:** `POST /ask`, `POST /ask-sql`, `POST /sql` — planner + SQL gen (DuckDB-only `SELECT`/`WITH`) + pandas + viz, with rule-based fallback when no LLM is reachable.
- **Analysis:** profiling, quality/PII, forecasting, anomalies, segmentation, hypothesis/regression/AB tests, insights, storytelling (`/analysis/*`, `/profile/*`, `/quality/*`).
- **Dashboards:** widget/layout backend (`api/dashboards.py`) + Power BI-style studio (`api/dashstudio.py`: import-paste/file/warehouse, model, report, `.pbix` export, recreate-from-image, `.pbix` import).
- **Enterprise:** catalog, SLA, cost/usage, compliance, workflows, masking, collaboration, org, API keys, SSO/SAML, audit, RLS (see `config/settings.yaml: security`).
- **Billing:** plans/entitlements/usage metering in-app; checkout externally. See `docs/BILLING.md`.

Frontend: `web/` (app SPA). The marketing site (`website/`) ships separately via its own hosting and is not part of this repository — see `docs/FRONTENDS.md` and ADR-023.

## Layout

| Path | Role |
|---|---|
| `api/main.py` | FastAPI app + core routes (large — see ADR 0001 for split plan) |
| `api/routes/` | extracted routers (`org`, `api_keys`, `enterprise`, `proxies`) |
| `api/dashstudio.py`, `api/dashboards.py`, `api/measures.py` | BI/studio backends |
| `api/billing/` | plans, entitlements, metering, license guard |
| `agent/` | orchestrator, planner, SQL gen, LLM clients, tools (sql/pandas/viz) |
| `etl/` | extract, clean, load, connectors, watcher |
| `warehouse/` | DuckDB schema + system tables |
| `analysis/`, `export/` | stats/ML pipelines + BI/PDF/PPTX export |
| `web/`, `shell/` | SPA + pywebview shell |
| `billing_server/` | standalone billing service (Razorpay) |
| `config/` | `settings.yaml`, `model_config.yaml`, `db_config.yaml` (seeds; runtime `*.json` gitignored) |
| `scripts/`, `packaging/`, `installers/` | build, verify, installer assets |
| `tests/` | 2456 tests (`pytest -q`) |

## Config

- Copy `.env.example` → `.env`, set `JWT_SECRET` (`python -c "import secrets;print(secrets.token_hex(32))"`).
- **First admin:** set `DATAAGENT_BOOTSTRAP_TOKEN` and present it when registering the first account — otherwise nobody can create the first admin. See `docs/OPERATIONS.md`.
- LLM: default Ollama `qwen2.5:7b` (`config/model_config.yaml` + `LLM_*` env; Settings page persists to `config/llm_runtime.json`). Cloud providers optional; unreachable LLM → rule-based fallback with a labeled note. The AI chat needs a local model — SQL, Excel, dashboards and exports work without one.
- Warehouse: `data/curated/warehouse.duckdb`, schema `analytics`.
- Billing: `billing.external_url` / `sync_url` empty = self-hosted free plan.

Full ops: `docs/OPERATIONS.md`. Billing & entitlements: `docs/BILLING.md`.

Frontends: `docs/FRONTENDS.md`. Coverage plan: `docs/COVERAGE.md`.

## Tests / quality

```powershell
python -m pytest -q                                   # 2456 tests
python -m pytest tests/test_packaging.py tests/test_billing_consistency.py -q
python -m pytest tests/test_security_regressions.py -q  # SQLi / sandbox / entitlement regressions
python -m pytest -q --cov=. --cov-report=term-missing  # coverage (enforced gate 74%, measured ~76.6%)
```

Workflow: plan → TDD → code-review → security-review (auth/SQL/billing) → verify. See `AGENTS.md`.

## Constraints

- Never `pip install mcp` into the main venv (breaks FastAPI/starlette). MCP servers run from `.venv-mcp`.
- One DuckDB writer at a time on Windows; MCP server is read-only, non-SELECT blocked.
- Runtime secrets — never commit or log: `config/users.json`, `config/db_connections.json`, `config/google_login_pending.json`, `config/llm_runtime.json`, `data/.jwt_secret`, `data/.grant_key`, `data/.grant_migrated`, `.env`.

## License

The published source is licensed under the PolyForm Noncommercial 1.0.0
(`LICENSE`): use, modification and redistribution are allowed for personal,
educational and other noncommercial purposes. Commercial use requires the
author's permission. Note this is not an OSI open-source licence, and the
marketing site (`website/`) is not part of this repository.
