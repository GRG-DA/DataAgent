# TDD Evidence: self-serve role defaults + AI-build gating + shell port fallback

**Task:** fix shipped-app defects — (1) "AI dashboard build failed … viewer does not
allow dashboard:create" toasts, (2) "Port 8002 is already in use" startup abort;
then rebuild the installer.
**Commits:** fix commit `53060be` pushed to `origin/master` (GRG-DA/DataAgent).
**Plan:** no `*.plan.md` — journeys derived during this TDD run; decision record:
`docs/decisions/ADR-024-self-serve-default-role-and-port-fallback.md`.

## User journeys

1. As a Google-provisioned user, I want a self-serve account on first sign-in so
   that I can build dashboards immediately.
2. As a **viewer** (intentionally read-only), I want a clear message when the AI
   builder is unavailable, without being sent through a questionnaire that can
   never succeed.
3. As a desktop user, I want the app to start on a nearby free port when a
   foreign process holds 8002, and keep working when it launches from the tray.

## Task report

| # | Task | Validation command | Result |
|---|------|--------------------|--------|
| 1 | RED: port tests authored before `shell/port.py` change (fallback to busy+1, range exhaustion raises, reuse path unchanged) | `python -m pytest tests\test_cov_export2.py::test_shell_port tests\test_cov_wave3.py::test_shell_port_owned_and_foreign …` | RED — `PortOccupiedError: Port 65136 is already in use …` (old code) |
| 2 | GREEN: `shell/port.py` fallback implemented | same command | passed |
| 3 | RED→GREEN: `serve_index` marker (injected after `<head`, before first `<script src=`, exactly once) | `python -m pytest tests\test_spa_serving.py` | RED (marker absent) → GREEN |
| 4 | RED→GREEN: role fall-throughs (`_public_info`, `GoogleLoginConfig`, shipped yaml default) | `python -m pytest tests\test_auth_role_defaults.py` | RED (`viewer`) → GREEN |
| 5 | Full sweep incl. security regressions + auth hardening | `python -m pytest tests\test_security_regressions.py tests\test_auth_hardening.py tests\test_cov_wave3.py tests\test_cov_export2.py tests\test_spa_serving.py tests\test_auth_role_defaults.py -q --no-cov` | **123 passed** |

## Test specification

| # | What is guaranteed | Test | Type | Result | Evidence |
|---|--------------------|------|------|--------|----------|
| 1 | Foreign process on the pinned port → backend drifts to pinned+1 (owned) | `tests/test_cov_export2.py::test_shell_port` | unit | PASS | pytest run above |
| 2 | Foreign lease on the whole fallback range → `PortOccupiedError` | same | unit | PASS | private-port stub makes it deterministic |
| 3 | A DataAgent backend on the pinned port is still reused (no fallback), `owned=False` | same + `tests/test_cov_wave3.py::test_shell_port_owned_and_foreign` | unit | PASS | pytest run above |
| 4 | `/` carries the same-origin marker before the first external script, exactly once | `tests/test_spa_serving.py` | integration (TestClient) | PASS | pytest run above |
| 5 | A record with **no** role surfaces as `user` (self-serve standard), never `viewer` | `tests/test_auth_role_defaults.py::test_public_info_missing_role_defaults_to_user` | unit | PASS | pytest run above |
| 6 | Explicit stored roles pass through verbatim (viewer stays viewer) | `::test_public_info_keeps_existing_roles` | unit | PASS | pytest run above |
| 7 | Google auto-provision default (class + shipped yaml) is `user` | `::test_google_login_config_defaults_to_user`, `::test_shipped_yaml_google_login_role_is_user` | unit/config | PASS | pytest run above |
| 8 | JS modules remain syntactically valid after edits | `node --check web/js/api.js web/js/pages/dashboardStudio.js` | static | PASS | exit 0 |

## Coverage and known gaps

- Coverage gate (`fail_under = 74` in `pyproject.toml`) not run on this incremental
  sweep (CI/next full run covers it); targeted suites fully green.
- Gaps (deliberate): no route-level test pinning the JWT mint sites for an
  explicit-viewer login (middleware reads the store, so the token claim is not
  authoritative — flagged as follow-up by the security review); client-side
  `_canCreateDashboards()` has no JS test runner in this repo (SPA-side tests
  documented as losing coverage in fresh clones, ADR-023).
- Pre-commit guard: `gitleaks protect --staged` → "no leaks found".

## Security review

Inline `security-reviewer` verdict: **PASS with notes, no blockers** — explicit
roles untouched, RBAC fail-closed unchanged (`AuthMiddleware` reads the store,
not the token claim), no secrets in the diff, port drift fail-closed on loopback.

## Addendum (2026-11-03, second fix round)

Investigating the shipped install's store (1 `admin` + 12 viewers with no
`sso_provider`, 2 `billing_accounts` viewers) found two more viewer sinks,
both fixed with their own RED/GREEN:

| # | Guarantee | Test | RED evidence |
|---|-----------|------|--------------|
| 10 | Legacy `config/users.json` import with missing/blank role → `user` | `tests/test_auth_role_defaults.py::test_legacy_import_missing_or_blank_role_becomes_self_serve_user` | `git stash` revert → 3 failed (3,5 passed); `stash pop` → 8 passed |
| 11 | Explicit legacy roles (incl. `viewer`) preserved | `::test_legacy_import_keeps_explicit_roles` | covered by above run |
| 12 | `UserRecord` dataclass default is `user` | `::test_user_record_default_role_is_self_serve` | same |
| 13 | Central (billing/checkout) non-first account provisions as `user` | `::test_central_provision_nonfirst_account_is_self_serve` | same |

Scope note: SQL column defaults (`DEFAULT 'viewer'`) intentionally kept
(fail-closed SQL layer; no code path relies on them). Existing user stores
are NOT auto-migrated — each install promotes accounts deliberately via
Admin → Users (`PATCH /auth/users/{username}`, audited).

Sweep with all rounds applied: **195 passed**
(`test_security_regressions`, `test_auth_hardening`, `test_account_lifecycle`,
`test_central_accounts`, `test_billing_server_accounts`,
`test_auth_role_defaults`, `test_cov_wave3`, `test_cov_export2`).
