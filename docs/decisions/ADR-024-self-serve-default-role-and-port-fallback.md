# ADR-024: Missing-role records and new Google sign-ups are self-serve `user`, not `viewer`

**Status:** Accepted (2026-11-03)

## Context

Two shipped-app defect reports:

1. AI dashboard builds in Dashboard Studio failed on every attempt with
   "AI dashboard build failed. Is the server running? Permission denied:
   your role 'viewer' does not allow dashboard:create."
2. Port-conflict aborts ("Port 8002 is already in use by another
   application") even though free ports existed next to 8002.

Investigation of the role half:

- `config/settings.yaml` had two conflicting defaults: `rbac.default_role`
  is `user` (the self-serve standard that self-registration grants,
  per OPERATIONS.md and `api/main.py` registration), but
  `google_login.default_role: "viewer"` — so every Google auto-provisioned
  account started read-only.
- On the reference machine's installed app store the damage was visible:
  1 admin + **14 viewer** accounts, all Google-provisioned, none able to
  create dashboards.
- A second family of fall-throughs defaulted a *role-less* record to
  `viewer` as well: `UserManager._public_info` (`api/auth.py`) and the two
  `role=info.get("role", "viewer")` token mints in `api/main.py` (password
  login and Google login JWT). Any legacy record missing its role surfaced
  in the session as `viewer`, which also confused the client —
  `web/js/pages/dashboardStudio.js` relayed the resulting 403 behind an
  "Is the server running?" prefix, and the AI build entry points let
  read-only accounts walk through the whole questionnaire only to fail at
  the end.

Viewer remains a legitimate, deliberately assignable role (read-only
accounts). No bulk store migration was made: existing `viewer` records
keep their role, and an admin can re-grant through the audited
`PATCH /auth/users/{username}` endpoint (Admin → Users). Weakening or
auto-promoting stored roles was explicitly rejected — RBAC stays
fail-closed; only *missing* data falls through, and it falls to the same
self-serve standard every other signup path grants.

## Decision

1. **Google auto-provisioning defaults to `user`** (`api/google_login.py`
   `GoogleLoginConfig.default_role`, `config/settings.yaml`
   `google_login.default_role`), matching `rbac.default_role`.
2. **A role-less record resolves to `self_serve_default_role()`**
   (new helper in `api/auth.py` = `settings.rbac.default_role`, always
   `user` as the fall-through). Used by `_public_info` and both JWT mints.
   Explicit stored roles are never overridden.
3. **UI stops asking a read-only account for the impossible**:
   `dashboardStudio.js` gates the AI-build entry points with a clear
   "Your account is read-only" message and reports 401/403 verbatim
   (session expiry / RBAC detail) instead of prefixing
   *"Is that server running?"* — the server was clearly running.

## Consequences

- New Google sign-ups (and installs over the fix) are fully usable
  self-serve accounts with fewer surprises; operators who deliberately
  want read-only Google sign-ups set `google_login.default_role: viewer`.
- The 14 existing viewer accounts on the reference install are fixed by
  the operator through Admin → Users (audited), not by a side-channel
  store edit.
- `shell/port.py` now drifts to the first free port (8003..8012) when a
  foreign process owns 8002 instead of refusing to start; the SPA learns
  the backend origin two ways: the window's `?api_port=` navigation hook
  (`shell/window.py`) and the `window.__DATAAGENT_API_SAME_ORIGIN__`
  marker `serve_index` injects, which `web/js/api.js` honours — closing
  the tray "Open in browser" path that had no querystring. Reuse of an
  existing DataAgent backend on 8002 is unchanged.
- Tests: `tests/test_cov_export2.py::test_shell_port` (fallback +
  exhaustion), `tests/test_cov_wave3.py::test_shell_port_owned_and_foreign`,
  `tests/test_spa_serving.py` (marker injection), and
  `tests/test_auth_role_defaults.py` (role fall-throughs).
