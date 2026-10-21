# ADR-001 — Fix bundled opencode AI dead after install

Date: 2026-10-07
Status: accepted (implemented)

## Context

After installing `DataAgent-Setup.exe`, the AI (opencode free tier via the
bundled `opencode.exe` loopback server on `127.0.0.1:3000`) never answered.
`logs/opencode-server.log` showed a `listening … / ServeError` crash loop and
`logs/app.log` showed `OpenCode server not reachable at
http://127.0.0.1:3000` on every AI request, so all answers fell back to the
rule-based engine. The binary, model (`big-pickle`), session API and network
were all verified healthy — the failure was purely in server lifecycle
management.

## Root causes

1. **Spawn storm.** Every first-AI request built a fresh `OpencodeProvider`
   (`_server_started=False`) and called `ensure_opencode_server`, which
   spawned a duplicate `opencode serve` whenever the probe failed. N
   concurrent requests → N processes fighting for port 3000; losers crash
   with `ServeError`.
2. **Wedged-port deadlock.** When a process held :3000 without answering
   `/config` (still cold-starting, or a wedged orphan), every later ensure
   spawned another doomed duplicate and returned `False` forever.
3. **20s ready wait < cold start.** First start of the 180MB binary
   (Defender scan + instance bootstrap) takes ~12–30s; the 40×0.5s wait
   timed out and reported "not reachable" for a server that was still
   starting. The server was also only started lazily on first AI use.
4. **Stale pidfile** pointed at dead PIDs across restarts.
5. **Wrong health signals.** `_llm_available` probed the zen host
   (`opencode.ai:443`) instead of the loopback server, so a healthy
   server-mode setup was still labeled "rule-based fallback"
   (`llm_used=False`). Settings → provider status never checked the
   loopback server either, showing green while the AI was dead.
6. **Server-mode settings not persistable.** `opencode_server_url` and
   friends were missing from `_RUNTIME_ALLOWED_KEYS` and from the provider
   update endpoint, so the UI could not repair server mode.

## Decision

- `agent/opencode_bootstrap.py`: process-wide `_spawn_lock` held only for
  the spawn *decision* (fast probe + bind check + `Popen`), with a
  `_spawn_in_progress` flag so latecomers wait instead of double-binding;
  held-but-silent ports are waited on (`_HOLDER_WAIT_S=30s`) not
  double-bound; ready wait raised to 60s; stale/garbage pidfiles are
  cleared; auto-spawn is loopback-only (remote `server_url` is assumed
  externally managed); blocking probes moved off the event loop
  (`asyncio.to_thread`); control chars stripped from hosts in log lines.
- `api/main.py`: background (fire-and-forget) server pre-start at lifespan
  startup when opencode is the default provider in server mode;
  `_llm_available` and `_provider_status("opencode")` probe the loopback
  server; new `GET /llm/providers/opencode/server` status endpoint
  (`{installed, running, port, pid, binary, version, port_busy}`);
  provider update accepts/persists `server_url`.
- `agent/llm_client.py`: `update_provider_config(..., server_url=...)`
  hot-reloads the rebuilt provider.
- `config/__init__.py`: `opencode_server_{url,auto_start,hostname,port}`
  added to `_RUNTIME_ALLOWED_KEYS`.
- `tests/test_opencode_serve_robustness.py`: 7 regression tests (spawn
  lock, no duplicate spawn on busy port, stale pidfile cleanup, slow cold
  start, single spawn under concurrency, no remote auto-spawn, runtime
  keys). Also fixed `test_cov_agent2.py::TestOpencodeBootstrap` mid-test
  mock restore (was restoring the mock instead of the real function).

## Follow-up (same day): installed shell session still failed once

The first installed-shell session (10:38) still reported "server not
reachable" while a later session works. Live forensics showed:

- A **foreign process** was involved: `...\npm\node_modules\opencode-ai\bin\opencode.exe`
  (an unrelated npm-installed CLI, running since 09:52) alongside our
  `C:\Program Files\DataAgent\opencode.exe` holder — a classic
  port-holder mystery with no name attached.
- The failure window recovered on its own; the same installed build now
  answers `/ask` with `LLM_USED: True` through the shell backend.

Decision: `server_status()` now reports `holder: {pid, image}` (Windows
`netstat`/`tasklist`, stdlib only, never raises), the holder-timeout error
names the holding process, and `GET /llm/providers/opencode/server`
surfaces it — the next "not answering" report identifies the exact holder
instead of guessing. 2 regression tests cover the parser.

Test hygiene lesson: a test double that patches `subprocess.Popen` globally
also intercepts `subprocess.check_output` (built on Popen), producing a
phantom "spawn" count. The `serve`-spawn fake now only counts argv
containing `serve` and delegates everything else to the real Popen.

## Root cause (final): frozen bundle dropped certifi's cacert.pem

In-process diagnostics (`GET /debug/opencode-probe`, since removed) showed
from inside the frozen backend: raw-socket TCP ok, hand-written HTTP 200,
urllib 200 — but **every httpx call died with
`FileNotFoundError: [Errno 2]` at `ssl.create_default_context()`**.
`_internal/certifi/` contained only `py.typed`: the shell spec's anti-crack
filter dropped **every** `.pem`, including the public CA bundle. Since
httpx builds its SSL context eagerly per client, 100% of frozen HTTP calls
failed while raw sockets worked — matching all symptoms (probes fail,
`port_busy` true, externals fine).

Fix: `scripts/DataAgentShell.spec` now excludes only real private-key
material (`ed25519_private*`, `*private*.pem`) and always keeps
`cacert.pem` (locked in by `test_shell_spec_keeps_public_ca_bundle`).
Verified `cacert.pem` (234KB) present in the bundle; frozen log then shows
`GET /config 200`, `POST /session 200`, `POST .../message 200` and
`Step 1 SQL upgraded via LLM` — genuine end-to-end AI in the installed app.

Also fixed en route: `_llm_available` now uses HTTP-level truth
(`GET /config`) in server mode instead of TCP (a wedged holder previously
faked `llm_used=True` with zero LLM calls); `server_status` carries
`last_error`; a mid-debug `/debug/opencode-probe` endpoint + its
auth-middleware entry were removed after use.

Note: `DataAgent.spec` on disk had been clobbered by an interrupted build
(the build script's backup/restore only runs on success); restored the
hand-maintained legacy spec verbatim.

## Consequences

- Installed app AI works out of the box; first AI request no longer pays
  the cold start (pre-start at launch); concurrent first requests spawn
  once; Settings can diagnose (`/server` endpoint) and repair
  (`server_url`) server mode.
- The installed `DataAgent-Setup.exe` on this machine predates the fix —
  **rebuild + reinstall** (`scripts/build_windows.ps1` + Inno) to ship it.
- Pre-existing, unrelated failures remain: 7 in `test_cov_agent2.py`
  (pandas/viz/warp/llama/proxifly env drift) and 5 in `test_cov_main2.py`
  (managers/querybuilder) — untouched by this change.
