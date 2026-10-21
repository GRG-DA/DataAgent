# ADR-002 — Central accounts + remove Firebase

Date: 2026-10-16
Status: implemented (security review passed with documented follow-ups)

## Context

The app is local-first: the warehouse, chats, dashboards and exports live on
the user's machine. Firebase was doing three jobs, all optional and all
replaceable:

1. Google sign-in (`api/firebase_auth.py` verifies Firebase ID tokens from
   `web/js/auth.js` + the vendored Firebase JS SDK).
2. A redundant purchase mirror (`api/billing/firestore_store.py`,
   best-effort `record_purchase` from `api/billing/cloud_sync.py`).
3. A secondary entitlement read path (fallback in
   `api/billing/entitlements.py`).

None of these are the source of truth anymore:

- Purchases/keys/orders already live in the billing server's SQLite store
  (`billing_server/store.py` — orders, redemptions, feature_uses, abuse
  blocks) and entitlements are already pulled via `GET /v1/entitlements`
  with an HMAC-signed request + Ed25519-signed response token.
- Native Google OAuth (`api/google_login.py`, `/auth/google/login*`) already
  exists and does not need Firebase.
- Local username/password accounts (`api/auth.py`, `/auth/register`,
  `/auth/login`) already exist for offline/first-run installs.

Two problems remain: (a) credentials are only local, so an account does not
follow the user across machines; (b) Firebase adds a vendor, a vendored SDK
(~250KB of JS), secret material in the tree and 6 test files of coupling.

## Decision

1. **Remove Firebase entirely** — auth module, Firestore mirror, SDK,
   config, env vars, dependency and all tests.
2. **Add central accounts to the billing server** — it stores *only*
   username/password (hashed) and purchase records. New endpoints
   `POST /v1/accounts/register`, `POST /v1/accounts/login`,
   `GET /v1/accounts/me` (session token signed with the licensing Ed25519
   key, same trust anchor as entitlement tokens).
3. **The app keeps its data local.** The central DB never sees warehouse
   data, chats, dashboards, files or exports. The app-side module
   (`api/central_accounts.py`) proxies register/login to the billing server
   and provisions the same local session local accounts get; when the
   billing server is unconfigured or unreachable, the app falls back to
   local accounts (fail-open to local, like cloud_sync fails secure to the
   local plan).
4. **Google sign-in stays** via the native OAuth flow
   (`/auth/google/login`), not Firebase.

## Consequences

- `web/js/auth.js` drops the Firebase SDK and drives the native Google
  popup flow instead; the vendored Firebase SDK files are deleted.
- `FIREBASE_*` env vars and `settings.firebase` are gone; CSP `frame-src`
  keeps `accounts.google.com` (native OAuth) but drops `*.firebaseapp.com`.
- Credential handling must meet DPDP/GDPR expectations: bcrypt hashing,
  per-account login rate limits, no password/email in logs, TLS-only in
  production, deletion path is a support action for now.
- `config/firebase-service-account.json` (if present locally) must be
  revoked in the Firebase console and deleted; it is never needed again.
- Password reset by email is a documented follow-up (Resend is already
  wired into the billing server); v1 ships register/login/me.
- Existing Firebase-provisioned users (if any) re-register or use
  "Continue with Google"; there is no silent migration.

## Rollout order

1. Remove the Firestore mirror (entitlements fallback, cloud_sync mirror,
   `firestore_store.py`) — behavior-neutral.
2. Remove `api/firebase_auth.py` + router + `/config` exposure + CSP entry.
3. Frontend: native Google popup + email/password form when centralized.
4. Config/env/requirements cleanup.
5. Billing server accounts (tests first) + app-side proxy module (tests
   first).
6. Full test suite + security pass.

## Implementation notes (2026-10-16)

All rollout steps are complete. `config/firebase-service-account.json` was
deleted locally; **the corresponding key must still be revoked in the
Firebase/GCP console** (manual action). Test evidence: 27 billing/proxy
account tests (16 billing server + 11 app proxy), full suite at 1,637 passed
with only the pre-existing py3.13/Windows drift failures (unrelated files).

Security review hardening landed in the same pass:

- **Account-takeover guard** — `provision_user` only re-enters local users
  whose `sso_provider == "billing_accounts"`. A billing account claiming an
  email that already exists locally (Google or local password user) gets
  409 "sign in with that method" instead of inheriting the local identity.
  The billing server does not verify email ownership yet; this guard is the
  stopgap.
- Anti-timing: unknown-email logins still run bcrypt against a dummy hash.
- bcrypt 72-byte limit enforced in `validate_password` (400, not 500);
  over-long login passwords fail as normal 401s.
- Account logs use `mask_email()` (no full email in logs, per this ADR).

## Known follow-ups (security review)

1. **Email verification** — the proper fix for the takeover guard's
   underlying gap: verify ownership on register (Resend is wired into the
   billing server), then allow verified-email linking to Google/local users.
2. **Proxy-aware rate limiting / cookies** — billing server and app limiter
   key on the peer IP, so behind nginx/LB all clients share one bucket
   (global-lockout DoS). Run uvicorn with `--proxy-headers` +
   `forwarded-allow-ips` from known proxy CIDRs, or move limits to the edge.
   The app proxy already honors `X-Forwarded-Proto` for the `Secure` cookie.
3. **Blocking httpx calls** in async endpoints (up to 8s event-loop stall) —
   switch to `httpx.AsyncClient`.
4. Register 409 doubles as an email-enumeration oracle (accepted for v1;
   a neutral response + verification email would fix it).
