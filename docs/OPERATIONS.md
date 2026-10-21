# Operations guide

How to run DataAgent for real: local dev on Windows, Docker, TLS, the first-admin bootstrap, every environment variable that matters, what must never be committed or shipped, backups, the one-writer DuckDB rule, health probes, and troubleshooting. Written for whoever deploys or supports an instance.

---

## 1. Local dev on Windows

```powershell
.\setup.ps1                    # venv + requirements + data dirs + init-db + check
python run.py check            # environment check (never writes the warehouse)
python run.py init-db          # create data/curated/warehouse.duckdb
python run.py                  # API on http://127.0.0.1:8002
python run.py web              # same thing, explicit
python run.py shell            # desktop shell (pywebview window)
```

`setup.ps1` creates `venv/`, installs `requirements.txt`, creates `data/raw`, `data/staging`, `data/curated`, `logs`, `outputs/charts`, `outputs/reports`, `config/pipelines`, `config/schedules`, then runs `run.py init-db` and `run.py check`.

`run.py check` is read-only by design: it verifies the presence of `config/model_config.yaml`, `config/db_config.yaml`, `config/settings.yaml`, `requirements.txt`, creates the missing directories, probes Ollama over HTTP (`http://localhost:11434/api/tags`, 5s cap — the CLI fallback is opt-in via `--ollama-cli` because `ollama.exe` is a GUI-subsystem binary that can deadlock `capture_output` on Windows), and probes the warehouse with a **read-only** `duckdb.connect(..., read_only=True)` + `SELECT 1`.

> **`run.py check` never opens the warehouse read-write.** If the database is missing it says `Run: python run.py init-db`.

### Auto-reload

`run.py` passes `reload=os.getenv("WAREHOUSE_RELOAD", "") == "1"` to uvicorn. Auto-reload spawns a **second** process that also opens `warehouse.duckdb` read-write, which on Windows produces "file in use" errors. It is opt-in via `WAREHOUSE_RELOAD=1`. (The `--debug` flag applies only to the desktop shell, not to the API.) `config/settings.yaml` → `app.debug` is also `false` and carries the same warning.

### The desktop shell

`python run.py shell` → `shell/app.py::run_shell()`:

1. **Preflight** (`shell/runtime_check.py`): the per-user data dir must be creatable and writable, and on Windows the **Edge WebView2 runtime** must be present (registry probe for `EdgeUpdate\ClientState\{F3017226-…}`, then an `EdgeWebView/Application` folder probe). A failure shows a MessageBox and exits 1.
2. **Single-instance guard** (`shell/single_instance.py`): a Windows named mutex `Local\DataAgentShell` (POSIX: an `fcntl.flock` on a temp lockfile). A second instance exits with "DataAgent is already running."
3. **Port resolution** (`shell/port.py`): the shell pins `settings.app.port` (8002) rather than drifting to a free port, because the SPA hardcodes the API origin to 8002 (`web/js/api.js` reads `window.API_PORT`). If the port is busy but `/health` answers with a DataAgent-shaped payload, the shell **reuses** that backend (`owned=False`); otherwise it refuses with "Port 8002 is already in use by another application."
4. **Backend thread** (`shell/server.py`) starts uvicorn in a thread unless the port was already serving.
5. **Window** (`shell/window.py`): pywebview, 1400×900, min 1024×700, dark background `#0f172a`, showing `shell/splash.html` until the backend is ready, then navigating to the app URL (`/?api_port=<port>` when the port differs from 8002).
6. **Tray icon** (`shell/tray.py`, Windows only — on Linux pystray and pywebview both drive GTK and the tray thread grabs the main context): "Open in browser", "Open logs folder", "About", "Quit".
7. **JS↔Python bridge** (`shell/bridge.py`): pywebview injects `window.pywebview.api` with `get_info()`, `open_browser()`, `quit()`, `tail_log(n)`.
8. **Update check** (`shell/update.py`): compares `app.version` from `config/settings.yaml` against the latest GitHub release tag from `https://api.github.com/repos/DataAgent/DataAgent/releases/latest` (5s timeout). It never blocks startup and never raises — offline returns "no update".

---

## 2. First-admin bootstrap — **read this before exposing a port**

On a fresh install with **zero users**, the first account to register becomes `admin`. That was a real hole: when the Docker publish bypassed nginx, whoever reached port 8002 first took over the instance.

The fix is a bootstrap token:

| | |
|---|---|
| Env var | `DATAAGENT_BOOTSTRAP_TOKEN` |
| Compared with | `hmac.compare_digest(supplied, expected)` |
| Without it | **Nobody can create the first admin over HTTP.** Registration returns "No users exist and no bootstrap token is configured. Set DATAAGENT_BOOTSTRAP_TOKEN (or create an admin out-of-band) before registering." |
| With it | The first registrant must present the matching token in the request body (`bootstrap_token`), or registration fails with "A valid bootstrap token is required to create the first account." |

Generate one:

```powershell
python -c "import secrets;print(secrets.token_hex(32))"
```

Then put it in `.env` (or the Compose environment) and restart. The first account is created with:

```powershell
curl.exe -X POST http://127.0.0.1:8002/auth/register `
  -H "Content-Type: application/json" `
  -d '{"username":"admin","password":"<strong-password>","role":"admin","bootstrap_token":"<your-token>"}'
```

After the first account exists, **self-registration can only grant the standard role** (`settings.rbac.default_role`, i.e. `user`). An explicit request for a privileged role is rejected outright ("Self-registration is limited to standard accounts. Ask an administrator to grant elevated roles."). Roles are managed over HTTP (ADR-009 D4): `GET /auth/users` (admin-only) lists them, `PATCH /auth/users/{username}` changes a role (admin-only, audited, role validated against `settings.rbac.roles`) and `DELETE /auth/users/{username}` removes the account and cascades to its data. The user directory itself lives in the SQLite store `data/users.db` (`api/user_store.py`); `config/users.json` is only read once on the first start and is never written again.

Rate limiting applies: 5 registrations/minute per IP, 10 logins/minute per IP (`security.rate_limit` in `config/settings.yaml`, overridable with `SECURITY_RATE_LIMIT_REGISTER_ATTEMPTS` / `SECURITY_RATE_LIMIT_LOGIN_ATTEMPTS`).

---

## 3. Docker

```bash
docker compose up --build              # api + nginx
docker compose up --build --profile full   # + ollama (port 11434)
```

| Service | Container | Published | Notes |
|---|---|---|---|
| `api` | `dataagent-api` | **`127.0.0.1:8002:8002`** | loopback only — see below |
| `web` | `dataagent-web` (nginx:alpine) | `80:80` | serves `web/` and proxies `/api/` |
| `ollama` | `dataagent-llm` | `11434:11434` | `--profile full` only |

> **The API port is bound to `127.0.0.1` on purpose.** Publishing 8002 on all interfaces exposed the API directly, bypassing nginx's TLS termination and its `limit_req` zone — and the "first registrant becomes admin" rule then handed admin to whoever reached the port first. Keep it on loopback and let nginx be the only way in.

Other wiring:

- `JWT_SECRET` is **required** in Compose: `JWT_SECRET=${JWT_SECRET:?Set JWT_SECRET in .env (see .env.example)}` — the container refuses to boot without it.
- `TRUSTED_HOSTS` defaults to `localhost,127.0.0.1,web` (DNS-rebinding guard).
- `DATAAGENT_BOOTSTRAP_TOKEN` is passed through (empty by default).
- `./config` is mounted **read-only** (`./config:/app/config:ro`) so the app can read `settings.yaml` + `llm_runtime.json` but can never write secrets back into the host checkout.
- `./data`, `./logs`, `./outputs` are read-write mounts.
- The `api` image runs as the unprivileged `appuser` (uid 10001) — the runtime stage copies pip packages to `/opt/pydeps` (not `/root`, which the unprivileged user cannot traverse).
- `api` healthcheck: `curl -f http://localhost:8002/ready` every 30s. `web` healthcheck: `wget --spider http://127.0.0.1/health` (127.0.0.1, not `localhost` — nginx `listen 80` binds IPv4 only and `localhost` can resolve to `::1` inside the container). `web` waits for `api` to be healthy (`depends_on: condition: service_healthy`).
- The `Dockerfile` also declares its own `HEALTHCHECK` against `/health`.

---

## 4. TLS

`nginx.conf` ships with the `:443` server block **commented out**. To enable public HTTPS:

```powershell
powershell -File scripts/enable_tls.ps1 -Domain analytics.example.com -Email you@example.com
```

`scripts/enable_tls.ps1`:

1. Runs `certbot/certbot certonly --webroot` against the running nginx (port 80 serves the ACME challenge) with a `certs` Docker volume.
2. Uncomments the `server { … }` template block in `nginx.conf`, strips the leading `# ` from every line, and replaces `analytics.example.com` with your domain.
3. Prints the two manual steps it cannot do for you:
   - add `- certs:/etc/letsencrypt:ro` under `web.volumes` in `docker-compose.yml`;
   - add `certs:` under the top-level `volumes`;
   - then `docker compose up -d --force-recreate web`.

The uncommented block sets `ssl_protocols TLSv1.2 TLSv1.3`, HSTS (`max-age=31536000; includeSubDomains`), and proxies `/api/` over `https`. The Compose file also carries a commented `"443:443"` publish line to uncomment.

---

## 5. Environment variables

| Variable | Required? | What it does | Example |
|---|---|---|---|
| `JWT_SECRET` | **Yes for any shared deployment** | Signs session JWTs (HS256). Rejects published placeholders (`change-me-in-production`, `your-secret-key`, `secret`, …) and anything shorter than 16 chars — **the app refuses to start**. Falls back to `data/.jwt_secret` (auto-generated as `secrets.token_hex(32)`) when unset. | `python -c "import secrets;print(secrets.token_hex(32))"` → 64 hex chars |
| `DATAAGENT_BOOTSTRAP_TOKEN` | Required to create the first admin | Bootstrap gate for the first (admin) registration; compared with `hmac.compare_digest`. Without it, nobody can create the first admin over HTTP. | 32 random hex bytes |
| `TRUSTED_HOSTS` | Yes in production | Comma-separated `Host` allow-list for the `TrustedHostMiddleware` (DNS-rebinding guard). Defaults to `*` (permissive) when unset; Compose defaults to `localhost,127.0.0.1,web`. | `analytics.example.com,www.example.com` |
| `TRUSTED_PROXY_NETWORKS` | Yes for Docker | CIDRs (or plain IPs) whose `X-Forwarded-For` header is trusted. **In Docker the api container sees nginx's bridge address, not loopback** — without this, the header is ignored entirely and every user shares one rate-limit bucket (10 failed logins anywhere locks everyone out). | `172.16.0.0/12` |
| `BILLING_EXTERNAL_URL` | No | URL of the external checkout site (`BillingSettings`, `env_prefix="BILLING_"`). Empty ⇒ self-hosted free plan, no in-app purchase path. | `https://billing.example.com` |
| `BILLING_SYNC_URL` | No | Base URL of the billing server serving `/v1/entitlements`. Empty ⇒ no cloud sync. | `https://billing.example.com` |
| `BILLING_SYNC_SECRET` | No | HMAC secret for those requests. Must match the server's `ENTITLEMENTS_SYNC_SECRET`. Both URL **and** secret are required for sync. | 32 random hex bytes |
| `LLM_PROVIDER` | No | Active LLM provider (`ollama`, `lmstudio`, `openai`, `openrouter`, `anthropic`, `groq`, `gemini`, `xai`, `deepseek`, `opencode`). Empty ⇒ rule-based fallback still works. | `ollama` |
| `LLM_MODEL` | No | Model name for the active provider. | `qwen2.5:7b` |
| `LLM_BASE_URL` | No | Base URL of the local provider. | `http://localhost:11434` |
| `APP_HOST` | No | Bind address. `config/settings.yaml` defaults to `127.0.0.1`; Compose sets `0.0.0.0` so the nginx sidecar and the published port can reach the API. | `0.0.0.0` |
| `APP_PORT` | No | Listen port. Must match the SPA's `window.API_PORT` (default 8002). | `8002` |
| `API_CORS_ORIGINS` | No | Extra browser origins allowed to call the API (comma-separated or JSON list). Env wins last, over the `settings.yaml` localhost defaults. | `https://analytics.example.com` |
| `DATAAGENT_GRANT_KEY` | No | Overrides the local grant-signing key (`data/.grant_key`). Set it explicitly when the key file cannot be persisted, or every local grant is invalidated on restart. | 32+ random bytes |
| `LICENSE_GRACE_DAYS` | No | Days a locally-valid key keeps working offline without a server check. Default 14, clamped 1–90. No billing server configured ⇒ no grace enforcement. | `14` |
| `BILLING_FREE_AI_DAILY_CAP` | No | Daily AI-request cap for the Free plan. Default 25; `-1` = unlimited. | `25` |
| `BILLING_TRIAL_ENABLED` | No | `0` / `false` / `no` / `off` disables the 14-day Pro trial. | `1` |
| `BILLING_TRIAL_DAYS` | No | Trial length in days. Default 14, clamped 1–90. | `14` |
| `BILLING_PRO_AI_DAILY_CAP` | No | Daily AI-request cap for Pro. Default `-1` (unlimited). | `-1` |
| `BILLING_CURRENCY` | No | Display currency for the seeded prices. Default `USD`. | `USD` |
| `WAREHOUSE_RELOAD` | No | `1` enables uvicorn auto-reload. Opens the warehouse twice on Windows — opt-in only. | `1` |
| `SECURITY_RATE_LIMIT_LOGIN_ATTEMPTS` / `SECURITY_RATE_LIMIT_REGISTER_ATTEMPTS` | No | Per-IP sliding-window limits. Defaults 10/min and 5/min. | `10` |

Provider keys (`LLM_OPENAI_KEY`, `LLM_ANTHROPIC_KEY`, `LLM_GROQ_KEY`, `LLM_GEMINI_KEY`, `LLM_XAI_KEY`, `LLM_DEEPSEEK_KEY`, `LLM_OPENROUTER_KEY`), Google OAuth (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI`), Google **login** (`GOOGLE_LOGIN_CLIENT_ID` / `GOOGLE_LOGIN_CLIENT_SECRET` / `GOOGLE_LOGIN_REDIRECT_URI`) and SSO (`SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`, `SSO_SAML_CERT`, `SSO_SAML_KEY`) are all optional — an empty provider key means that provider is skipped, and empty Google/SSO config means those login paths are disabled.

`run.py` loads `.env` into the environment at startup (`load_dotenv(..., override=False)`), so bare-metal runs pick it up too. `config/__init__.py` additionally reads `.env` through pydantic-settings.

---

## 6. Secrets that must never be committed or shipped

| Path | What it holds |
|---|---|
| `.env` | `JWT_SECRET`, provider API keys, `DATAAGENT_BOOTSTRAP_TOKEN`, billing sync secret |
| `config/users.json` | Password hashes and (legacy) API keys — legacy directory, imported once into `data/users.db` and no longer written |
| `data/users.db` | The user directory: password hashes, salts, MFA secrets, API keys (SQLite, ADR-009 D2) |
| `data/.jwt_secret` | The JWT signing key (auto-generated as `secrets.token_hex(32)` when `JWT_SECRET` is unset) |
| `data/.grant_key` | The local grant-signing key (HMAC secret for signed local grants) |
| `data/.grant_migrated` | The one-time grant-migration marker — **deleting it re-signs every unsigned row**, so it must stay outside the warehouse and out of any shared archive |
| `config/db_connections.json` | Saved external database credentials |
| `config/llm_runtime.json` | Settings-UI runtime provider/model state (may hold keys) |
| `config/google_login_pending.json` (+ `config/google_login_pending/`) | OAuth flow state |
| `config/revoked_jtis.json` | Revoked JWT ids (not secret, but runtime state) |
| `data/curated/warehouse.duckdb*` | User data |

`.gitignore` already excludes `.env`, `billing_server/.env`, `*.pem`, `*.key`, `config/users.json`, `config/db_connections.json`, `config/google_login_pending.json`, `config/google_login_pending/`, `config/llm_runtime.json`, `config/revoked_jtis.json`, `data/`, `logs/`, `outputs/`.

### The shareable zip

`scripts/build_secure_zip.py` rebuilds the shareable archive with **all** of the above excluded (`.env` and any `*.env` variant, `data/.jwt_secret`, `data/.grant_key`, `data/.grant_migrated`, `config/users.json`, `config/db_connections.json`, `config/google_login_pending.json`, `config/llm_runtime.json`, `revoked_jtis.json`, `users.json`, `warehouse.duckdb`, `app.duckdb`, plus any `*.pem` / `*.key` / `id_rsa*` / `*.pfx` / `*.p12` / `*.keystore`). The exclusion list and the self-check are derived from the **same predicate**, so they cannot drift; the self-check re-opens the finished zip and aborts the build with `SENSITIVE FILES PRESENT IN ZIP` if anything sensitive slipped in.

```powershell
python scripts/build_secure_zip.py --root . --out ..\DataAgent-secure.zip
```

The PyInstaller specs exclude the credential state as well: `DataAgent.spec` and `scripts/DataAgentShell.spec` filter every collected data file through `_is_credential_state()`, which drops `users.json`, `db_connections.json`, `google_login_pending.json`, `llm_runtime.json`, `revoked_jtis.json`, `.jwt_secret`, `.grant_key`, `.grant_migrated` and any `.env` variant. `scripts/build_windows.ps1` additionally fails the build if any `*ed25519*`/`*private*` `.pem` is found in `dist/` ("Private key material bundled").

---

## 7. Backups

**Back up:**

| Path | Why |
|---|---|
| `data/curated/warehouse.duckdb` (+ `.wal`) | All user data, dashboards, subscriptions, entitlements |
| `config/` | `settings.yaml`, `model_config.yaml`, `db_config.yaml`, and the runtime `*.json` state |
| `.env` | Secrets and deployment configuration |
| `data/.jwt_secret` | The JWT signing key — without it every session token is invalidated on restore |
| `data/.grant_key` | The grant-signing key — without it every local grant fails its signature check and users drop to free |

**Regenerable (do not need backing up):**

- `data/raw`, `data/staging` — ETL scratch space.
- `logs/`, `outputs/` — regenerated.
- `data/.grant_migrated` — regenerates on the next start (and re-signs unsigned rows; keep it if you have legitimately unsigned legacy rows).
- `analytics.plan_prices` / `plan_features` / `plan_limits` / `plans` — reseeded by `seed_billing_data()` on every start.
- `config/llm_runtime.json` — re-entered in the Settings page.
- The `analytics.*` billing tables — reseeded, but **subscription rows are not**: they are the grant history and they must be in the warehouse backup.

`scripts/backup.ps1` copies the warehouse, the WAL, `config/users.json`, `config/llm_runtime.json`, `config/db_connections.json` and `.env` into `backups/<timestamp>/`. For a consistent DuckDB snapshot, **stop writes first** (`docker compose stop api`, or stop the bare-metal server) — a copy of a live DuckDB file can be torn.

---

## 8. The one-writer DuckDB rule

**Only one process may write `warehouse.duckdb` at a time.**

DuckDB allows multiple connections to the same file *inside* one process, but a second **process** opening it read-write collides. On Windows this surfaces as:

```
IO Error: Cannot open file "...warehouse.duckdb" because it is being used by another process
```

The rules that keep it working:

| Path | Mode |
|---|---|
| The API process | One process, two connection strategies, both inside the same process. `api/db.py::get_connection` keeps **one** process-wide read-write connection for the dashboard / BI / export paths, guarded by `_bi_query_lock` for the execute+fetch window — a fresh `duckdb.connect` per request is what caused the historic "file in use" 500s. `etl/load.py::WarehouseLoader.get_connection` opens a **fresh** connection per call, serialized by the module-level `_warehouse_write_lock` (the billing/metering layer relies on that lock: an unlocked multi-connection read-modify-write would race). |
| ETL writes | **One dedicated process.** `etl/load.py` and the ETL scripts own the write path. |
| The MCP server (`mcp_servers/duckdb_mcp_server.py`) | `duckdb.connect(path, read_only=True)` and non-SELECT SQL is blocked by `mcp_servers/sql_guard.py`. |
| Analysis / query tools | Read-only execution inside a transaction that is always rolled back (`etl/readonly.py::readonly_transaction`) — even a statement that slipped past validation cannot persist a write. |
| `run.py check` | Read-only probe only (`duckdb.connect(..., read_only=True)` + `SELECT 1`). |

**How the failure looks.** `/ready` deliberately does **not** open a fresh read-only connection (`_db_readonly_ok()` runs `SELECT 1` on the *shared* connection). A fresh `duckdb.connect(..., read_only=True)` against a file the process already holds read-write fails with `ConnectionException: ... a different configuration than existing connections`, which would make `/ready` report 503 forever and keep Compose/K8s health gates closed.

Other symptoms of two writers: `FATAL: Failed to delete all rows from index` on boot (secondary ART indexes over a shared file), `BinderException` on upserts (the boot-time `strip_constraint_indexes` migration removes primary keys, so `ON CONFLICT` has no index to bind to), and intermittent 500s on the dashboard CRUD routes.

Practical rules: one API process; never `uvicorn --reload` unless `WAREHOUSE_RELOAD=1` is set (and only on a scratch warehouse); the MCP server is read-only; ETL runs as its own process, not inside the API.

---

## 9. Health and readiness

| Endpoint | Checks | Returns |
|---|---|---|
| `GET /health` | Component status (database / extractor / cleaner / loader / orchestrator) and the active LLM description | `{"status": "healthy", "version", "components", "llm_model"}` |
| `GET /live` | **Process only — never touches the DB** | `{"status": "alive", "version"}` |
| `GET /ready` | Process up **and** the warehouse readable (`SELECT 1` on the shared connection) | `{"status": "ready", "version"}` or **503** `{"status": "not_ready"}` |

Wiring:

- Compose `api` healthcheck → `curl -f http://localhost:8002/ready` (30s interval, 10s timeout, 3 retries, 20s start period). `web` waits for it.
- Compose `web` healthcheck → `wget --spider http://127.0.0.1/health` (proxied to the API).
- `Dockerfile` HEALTHCHECK → `curl -f http://localhost:8002/health`.

The billing server has its own probe: `GET /healthz` reports each component (`razorpay`, `webhook`, `license_key`, `sync`, `email`, `subscription_plan`) and `ok: true` once the required three (razorpay, webhook, license_key) are configured.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| **401 on every request** | The JWT secret changed. `JWT_SECRET` in the environment wins over `data/.jwt_secret`; if the env value changed, every issued token is invalid. Also check the client sends `Authorization: Bearer <token>` and that the token was not signed with a different secret than the one the server loaded. Rotating the secret invalidates all tokens; revoked JTIs persist in `config/revoked_jtis.json`. |
| **"Refusing to start: the JWT signing key … is a published placeholder"** | `JWT_SECRET` is `change-me-in-production`, `your-secret-key`, `secret`, … or is shorter than 16 characters. Generate a real one. The app will not boot with a forgeable key. |
| **"Event loop is closed" in tests** | `asyncio.get_event_loop()` returned a **closed** loop because an earlier test closed it. The two provider tests that hit this now use `asyncio.run(...)` instead — `tests/test_cov_agent.py::TestOpenAIProvider::test_client_rebuilds_when_closed` and `tests/test_cov_agent2.py::TestLlmDeep::test_ollama_client_rebuild_and_list_fail`. If you add a test that closes a loop, use `asyncio.run` and never `get_event_loop().run_until_complete`. |
| **"IO Error: Cannot open file … used by another process" / "file in use"** | Two processes are writing the warehouse. Stop the second one (ETL script, second API instance, `uvicorn --reload` child). Remember: one writer, read-only everywhere else. |
| **"FATAL: Failed to delete all rows from index" on boot** | A secondary ART index over a shared file. The startup migration (`api/db.py::heal_indexed_tables`) rebuilds it; if it persists, stop every other process touching the file and restart once. |
| **`/ready` returns 503 forever** | The warehouse is not readable by the app process — usually a second reader with a different connection configuration, or a locked file. Check the app log for the DuckDB error. |
| **429 with `X-Upgrade-Required: true` on AI requests** | The Free-plan daily AI cap (`BILLING_FREE_AI_DAILY_CAP`, default 25/day) was reached. The response is a metered block, not a bug: `X-Required-Feature: ai_unlimited`. Wait for the daily period to roll over or upgrade. |
| **403 with `X-Upgrade-Required: true` on a premium endpoint** | The plan does not include the feature. `X-Required-Feature` names the key. The frontend shows the upgrade overlay. |
| **Ollama not reachable** | `LLM_BASE_URL` must point at a running Ollama (`http://localhost:11434`). Check `GET /health` — it appends "(unreachable — running in rule-based fallback mode)" when the provider is configured but not answering. Everything except the AI chat still works in fallback mode. `python run.py check` probes `/api/tags`. In Docker, run the `full` profile (`docker compose --profile full up`) and point `LLM_BASE_URL` at `http://ollama:11434`. |
| **A paid plan shows as free after editing the DB** | Expected. A hand-edited row no longer matches its grant signature, so `_verify_effective` refuses it and fails closed to `free`. The log line is `local grant rejected for <user>: missing or invalid signature (provider=…)`. For `keygen` rows look for `keygen plan rejected for …: invalid_signature` / `email_mismatch` / `plan_mismatch`; for `cloud_sync`, `cloud_sync plan rejected for …: no_token` / `bad_token`. |
| **Nobody can create the first admin** | `DATAAGENT_BOOTSTRAP_TOKEN` is unset. Set it and restart, then register with `bootstrap_token` in the body — or provision an admin out-of-band. |
| **429 on login / register** | Per-IP sliding window (10 logins/min, 5 registrations/min). Also check `TRUSTED_PROXY_NETWORKS` in Docker: without it every user shares one bucket and one user's failures lock everyone out. |

---

## 11. Release checklist

In order:

1. **Freeze and branch.** Conventional commit message (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
2. **Tests green.** `python -m pytest -q` — expect 2090 passed. Then `python -m pytest -q --cov=. --cov-report=term-missing` — the gate is `fail_under = 60` in `pyproject.toml`; the current measurement is **76.13%**. See `docs/COVERAGE.md`.
3. **Security pass** on anything touching auth, payments, user input or SQL: `agentshield scan --path .` plus the `security-reviewer` agent.
4. **Bump the version** in `config/settings.yaml` → `app.version` (the shell's update check compares it against the GitHub release tag).
5. **Build the secure zip**: `python scripts/build_secure_zip.py` — the self-check must print `self-check OK: no sensitive files in zip`.
6. **Build the Windows installer** (if shipping the desktop app): `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-Cythonize] [-Sign]`. The build aborts if private key material lands in `dist/`. Checksums land in `dist/SHA256SUMS.txt`.
7. **Tag and push** — the tag is what `shell/update.py` polls (`api.github.com`).
8. **Deploy the app** (Docker or bare metal). If the JWT secret changed, `docker compose up -d --force-recreate api web` — old tokens invalidate on restart.
9. **Deploy the billing server** (only if the billing code changed): `scp -r billing_server/` → `systemctl restart dataagent-billing` → check `curl -s http://127.0.0.1:8090/healthz` says `ok: true`.
10. **Post-release smoke test**: `/health`, `/live`, `/ready`, the marketing site's pricing page, and one real (small) purchase if billing changed.
