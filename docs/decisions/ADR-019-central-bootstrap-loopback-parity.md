# ADR-019 — Central bootstrap gate follows the loopback exemption

Date: 2026-10-25
Status: Accepted (amends ADR-009 D3)

## Context

ADR-009 D3 requires the first central registrant to present
`DATAAGENT_BOOTSTRAP_TOKEN`. The local `/auth/register` route already uses
`api.main._bootstrap_gate_required` to exempt a loopback-only,
non-production desktop install — otherwise the owner of a fresh local install
is locked out of their own app, which ships no token.

Central accounts did not share that decision: `api/central_accounts.py`
gated the first central user unconditionally. With cloud sync configured the
web UI uses central email/password, and it posts no bootstrap token. A fresh
desktop install therefore received `403` on the first register/login. The
same path also passes the (empty) request token during login, so even an
existing central account could not provision locally.

## Decision

1. Extract the decision into `api/bootstrap_gate.py`:
   `bootstrap_gate_required(request)` returns `False` only when the request is
   local (no `X-Forwarded-For`/`Forwarded`/`X-Real-IP` and a loopback client),
   the bind host is loopback (`127.0.0.1`, `localhost`, `::1`, `[::1]`), and
   `current_app_env()` is a local/development value. It returns `True` for any
   forwarded/off-host request, any other host or environment, and when settings
   cannot be read (fail closed). This closes the reverse-proxy bypass: a
   loopback bind behind Caddy still sees `X-Forwarded-For`.

2. `api/main.py` imports it as `_bootstrap_gate_required` (keeping the
   existing test/import name) and guards the startup warning with it.

3. `api/central_accounts.py::_enforce_bootstrap_gate()` returns early when
   the gate is not required. Otherwise the existing token requirement is
   unchanged, including the identical 403 messages.

## Consequences

- A fresh desktop install can register or sign in through central accounts
  without a token, exactly like local registration.
- A network-reachable or production instance still requires the bootstrap
  token for its first central user; no token is trusted from the request body
  when the gate applies.
- The gate is configuration- and request-driven, not UI-driven: exposing the
  app on a non-loopback host re-enables it, and a forwarded request cannot
  claim the loopback exemption.

## Verification

- `tests/test_saas_identity.py` — existing remote-gate tests now force a
  non-loopback host; new tests cover loopback central register and login
  without a token, both provisioning the first user as admin.
- `tests/test_launch_core_api.py` — the shared helper follows the bind host,
  gates unknown environments, and sees through a loopback reverse proxy via
  forwarded headers or a non-loopback client address.
