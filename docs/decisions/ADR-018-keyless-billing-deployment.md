# ADR-018 — Keyless billing deployment on a split origin

Date: 2026-10-25
Status: Accepted

## Context

The billing server was designed to run on the same origin as the storefront
(`docs/deployment/billing-server.md`). Production now uses:

- `https://data-agent.me` — marketing site + storefront, deployed to Vercel;
- `https://pay.data-agent.me` — billing API on the Azure VM
  (`dataagent-billing`), behind Caddy;
- FamGateway as the live UPI rail (ADR-016).

Two things break under that split:

1. The storefront's `fetch()` calls are cross-origin. The server had no
   `CORSMiddleware`, so browsers blocked every checkout/account request.
2. The server was expected to hold an Ed25519 license-signing key. The live
   deployment intentionally does not: entitlement plans are tokenless
   (ADR-004) and the desktop app applies them locally. Without a key,
   `/healthz` reported `ok: false` and central login returned `503`, even
   though purchasing worked.

## Decision

1. **Add an exact CORS allow-list** in `billing_server/main.py`. Default
   origins are `https://data-agent.me` and `https://www.data-agent.me`;
   `BILLING_CORS_ORIGINS` overrides them (comma-separated, exact scheme +
   host). Methods are `GET`, `POST`, `OPTIONS`; headers `Content-Type`;
   credentials disabled. The middleware is registered immediately after the
   `FastAPI` app so the existing security-header middleware stays outermost.

2. **Add keyless mode** via `BILLING_KEYLESS=1` (`KEYLESS` in
   `billing_server/config.py`):
   - `/healthz` reports `keyless: true` and is `ok` when the payment rail is
     ready even though `license_key` is `false`;
   - `/v1/accounts/login` returns `200 {"ok": true, "email", "user_id"}`
     without minting a session token when the signing key is absent;
   - outside keyless mode, a missing key still fails closed with `503`.

3. **Serve the API only on `pay.data-agent.me`.** Caddy proxies the API
   routes plus `/famgateway/*`; the Vercel-hosted storefront sets
   `window.BILLING_API_URL = 'https://pay.data-agent.me'`.

4. **Bake sync configuration into the installer, with a one-time backfill.**
   Fresh installs copy the bundled `settings.yaml`; existing installs keep
   their file, so `run.py::_backfill_billing_sync()` fills only empty
   `billing.external_url`, `billing.sync_url`, and `billing.sync_secret`
   values from the bundle. It preserves comments and user values, is guarded
   by `.billing_sync_defaults_applied`, and never replaces an invalid YAML
   file.

## Consequences

- The storefront and API can live on different hosts without a wildcard.
- Purchases work without server-side license signing; the local app's grant
  signature remains the authorization boundary for paid plans.
- The server is not a general-purpose session-token issuer in keyless mode;
  central accounts still authenticate, but the app does not need the token.
- Existing installs pick up the baked sync configuration once; operators can
  still clear/edit values afterwards without the next launch overwriting
  them.

## Verification

- `tests/test_billing_server.py` — keyless `/healthz`, CORS allow/deny.
- `tests/test_billing_server_accounts.py` — keyless login omits token;
  non-keyless missing key still `503`.
- `tests/test_launch_frozen_bootstrap.py` — backfill fills empties, keeps
  custom values and comments, runs once, leaves invalid YAML untouched.
