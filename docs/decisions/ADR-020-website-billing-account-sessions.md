# ADR-020: Website billing account sessions

**Date:** 2026-10-25
**Status:** Accepted

## Context

The marketing site is static and the desktop app keeps its own local accounts.
Buyers still need a way to see the plan and purchase history attached to the
email they paid with. The portal is intentionally billing-only: no hosted
workspace, no dataset upload, and no change to anonymous checkout.

The existing billing API already issues short-lived Ed25519 session tokens for
the desktop app. A browser cannot keep that token safely across visits, so the
website needs a server-side session that is not readable by JavaScript.

## Decision

- The billing API issues an opaque 32-byte random token on register and login,
  stores only its SHA-256 hash in `account_sessions`, and sets it as
  `da_session`.
- The cookie is `HttpOnly`, `Secure`, `SameSite=Lax`, path `/`, and domain
  `.data-agent.me`; the default lifetime is 30 days. Logout deletes the stored
  hash and clears the cookie.
- `GET /v1/accounts/me` accepts either the app's Bearer token or the cookie.
  `GET /v1/accounts/summary` and `POST /v1/accounts/logout` are cookie-aware.
- CORS stays allow-listed to the production site origins, with
  `allow_credentials=True` and only `Content-Type` / `Authorization` headers.
- Checkout remains anonymous and email-keyed. When a session exists the site
  prefills and locks the checkout email so the purchase cannot drift to a
  different account.
- The summary exposes only buyer-visible fields (plan, dates, status, amount,
  currency, provider, order id). License keys, payment ids, notes, and raw rows
  never leave the server.
- Accounts are email-verified before the summary reveals a plan or purchase
  history. Verification links are single-use, expire after 24 hours, and are
  stored only as SHA-256 hashes. The link uses a URL fragment so the token is
  not sent in HTTP logs or `Referer` headers.
- Registering an existing **unverified** email claims it for the new password
  and revokes old sessions. Registering a **verified** email returns `409`.
  This resolves the race between a buyer and someone who merely typed the
  buyer's address first.
- Resend and verification attempts are rate-limited. Until verification, the
  summary returns `plan: null`, `status: "unverified"`, and no purchases.

## Consequences

- The website never stores a session token in `localStorage` or reads the
  cookie from JavaScript.
- The desktop app's Bearer flow is unchanged; Bearer logout is not revocation.
- The cookie is scoped to the registrable domain, so it is sent to the
  marketing host as well as the billing host. It is HttpOnly and only the
  billing API reads it.
- Purchase history remains keyed by email, but plan and purchase metadata are
  hidden until the email link is confirmed. Destructive actions (cancel,
  delete, refund) still require a separate authorization decision; verified
  email alone is sufficient for read-only summary access.
- Verification depends on `RESEND_API_KEY`. Without it, registration still
  succeeds but reports `verification_sent: false`; the account stays locked
  until an operator configures email delivery.
