# CI and operations checks

Two workflows guard every change, plus a small set of operator scripts that
run the same checks locally.

---

## 1. `.github/workflows/pytest.yml`

| Job | What it does | Gate |
|---|---|---|
| `lint` | `ruff check --select E9,F63,F7,F82,F811` over `api agent etl analysis warehouse config billing_server keygen_app lib mcp_servers scripts` (`graphify-out/`, `build/`, `dist/`, `web/vendor/`, `node_modules/`, the `.venv-*` trees are excluded). ruff is installed in the step - it is not in `requirements-test.txt`. | Non-zero exit on any finding. |
| `dependency-audit` | `pip-audit --strict -r requirements.txt -r requirements-test.txt`. | Non-zero exit on any advisory. pip-audit has no severity tiers, so this is stricter than "high/critical only". |
| `tests` | `pytest` on Python **3.11 / 3.12 / 3.13** (`fail-fast: false`). Coverage is measured on the real package layout only: `--cov=api --cov=agent --cov=etl --cov=analysis --cov=warehouse --cov=config` (never `--cov=.`). | `--cov-fail-under=74`, the value configured in `pyproject.toml` → `[tool.coverage.report] fail_under`. A change can never lower the gate. |

Also in the `tests` job:

- **Isolation** - `DATAAGENT_USER_DB` and `DATAAGENT_USER_LEGACY_JSON` point at
  `$RUNNER_TEMP`, so `tests/conftest.py` (which uses `setdefault`) can never
  fall back to the repository's `data/users.db`. A step asserts that
  `data/users.db` was not created.
- **Matrix leg** - one extra leg (`python 3.13`, `identity-store: dedicated`)
  runs the suite with its own identity-store path and asserts the isolation.
- **Caching** - pip (via `actions/setup-python`) and `.pytest_cache`.
- **Timeouts** - every job has `timeout-minutes` (10 / 20 / 45) so a hung
  step cannot burn the runner.

`fail_under` is asserted by `tests/test_ci_workflows.py`: if you raise it in
`pyproject.toml`, the test fails until the workflow is updated to match.

The measured total sits just above the gate (74.6-74.7% against a 74% gate),
so the margin is thin: the CLI `--cov` list measures the app packages only
(it drops `billing_server`, which `pyproject.toml` still lists under
`[tool.coverage.run] source`). Raising the gate means raising the measured
coverage first - never lowering the gate to land a change.

## 2. `.github/workflows/security.yml`

| Job | What it does | Gate |
|---|---|---|
| `codeql` | CodeQL for `python` and `javascript` (`build-mode: none`). The JavaScript extractor covers the vanilla front end in `web/`. | Results are uploaded to GitHub code scanning (SARIF) by the `analyze` step. |
| `secret-scan` | `gitleaks detect` over the **whole git history** (`fetch-depth: 0`). | Non-zero exit on a finding. |
| `dependency-review` | `actions/dependency-review-action` on the pull-request dependency delta. | `fail-on-severity: high`. Needs the dependency graph enabled. |
| `agentshield` | Installs `ecc-agentshield@1.6.0` (npm) and runs `agentshield scan --path .`, then the drift gate `scripts/ci/agentshield_gate.py`. | A **new** critical/high finding fails the job. The tree has accepted findings today, recorded in `scripts/ci/agentshield-baseline.json` - that baseline is the only accepted exception. The SARIF report is uploaded to code scanning. |

## 3. `scripts/ci/` - run the same checks locally

| Script | What it does |
|---|---|
| `run_tests.ps1` / `run_tests.sh` | Installs `requirements.txt` + `requirements-test.txt`, runs the suite with the CI coverage scope and gate, redirects the identity store to a temp directory and prints a summary. `--skip-install` skips the install step. |
| `preflight.ps1` / `preflight.sh` | Pre-deploy check for a **hosted** deployment (mirrors `docs/SAAS-OPERATIONS.md` §2): bootstrap token, billing URL, trusted hosts, trusted proxy networks, identity store, warehouse, audit sink, LLM provider. Prints a PASS/WARN/FAIL table and exits non-zero on any FAIL. It never prints a value, only whether a variable is set. |
| `agentshield_scan.sh` | Runs `agentshield scan --path .` and applies the drift gate. `--save-baseline` rewrites the baseline (review the diff before committing it). |

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\run_tests.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\preflight.ps1
```

```bash
# POSIX
sh scripts/ci/run_tests.sh
sh scripts/ci/preflight.sh
```

The preflight scripts read the **process environment only** - load your
deployment environment first (`set -a; . ./.env; set +a`).

## 4. Adding an exception

- **Coverage** - raise `fail_under` in `pyproject.toml`; never lower it to
  land a change. `tests/test_ci_workflows.py` keeps the workflow and the
  config in sync.
- **Lint** - a finding is a finding. Fix the code, or widen the rule set in
  the workflow *and* explain why in the comment above the step.
- **Dependency audit** - add the narrow `--ignore-vuln <GHSA-id>` flag to the
  audit step **and** list the advisory id plus the reason in the comment
  above it. Never add a blanket `|| true`.
- **Secret scan** - a real leak must be rotated and purged from the history,
  not allow-listed. Only add a `.gitleaks.toml` allow-list entry for a
  documented false positive.
- **agentshield** - a **new** critical/high finding fails the job. The
  accepted findings live in `scripts/ci/agentshield-baseline.json`; extend it
  deliberately (and review the diff) or fix the finding.
  `scripts/ci/agentshield_scan.sh` runs the same scan + gate locally; install
  the CLI with `npm install -g ecc-agentshield@1.6.0`.

`tests/test_ci_workflows.py` parses the workflow YAML and asserts these
properties, so CI cannot silently regress.
