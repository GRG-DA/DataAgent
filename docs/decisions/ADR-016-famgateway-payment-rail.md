# ADR-016 — FamGateway as the live payment rail; Razorpay dormant

Date: 2026-10-23
Status: Accepted (implementation phases tracked in
`docs/plans/fampay-payment-gateway-and-free-hosting.md`).

## Context

The checkout had exactly one configured rail: Razorpay. Two problems force a
second look:

1. **Cost geometry.** Razorpay's standard gateway is fine at UPI's 0% MDR but
   the account setup, dashboard and per-flow approvals are overhead for a solo
   operator. A genuine 0-fee alternative exists that buyers in India already
   trust: **FamGateway** (famgateway.in) — a third-party, non-custodial UPI
   automation service (verified 2026-10-23: 100% free for merchants, 0% fees,
   no KYC, hosted checkout page, UPI intent + QR, HMAC-signed webhooks, a
   server-to-server order-verify endpoint). It is *not* a licensed PSP and is
   not affiliated with FamPay/FamApp/Trio (their own site says so).
2. **The provider is effectively one maintainer and unregulated.** A paid
   entitlement must never silently depend on an unregulated third party whose
   API key balance, webhook availability and long-term existence are outside
   our control without a kill-switch.

Constraints that carry over unchanged:

- **ADR-004 (keyless entitlements)** — purchases grant the plan to the buyer's
  email; no product keys are minted. All grant logic must keep working for
  both rails.
- **ADR-010 (local-first)** — the gateway lives on the billing server and the
  marketing-site storefront only; the desktop app never sees payment code.
- Price parity tests (`test_launch_billing_legal.py`) pin one price list.
- Hosting is free-tier (Cloudflare Pages + Oracle Always Free x86_64 VPS).

## Decision

1. **`PAYMENTS_PROVIDER` selects the live rail**: `famgateway` | `razorpay` |
   `off`, read from the environment, default `razorpay` (existing deployments
   and tests keep the current behavior until the flag is flipped).

2. **FamGateway mode (env `PAYMENTS_PROVIDER=famgateway`) serves BOTH plans
   through one-time UPI payments** (`POST /create-order` → FamGateway
   `create-order` → hosted `checkout_url`). Because FamGateway has no
   recurring billing, **Pro becomes a pre-paid 31-day window**: every payment
   creates a NEW order row with `grant_days = 31` (the same window
   `LEGACY_PRO_GRANT_DAYS` already encodes) and `billing_server.grants`
   folds multiple paid orders into the longest, un-expired window — renewal
   is simply "pay again". Lifetime (₹10,000) is a plain one-time order.

3. **`POST /famgateway/webhook` mirrors the Razorpay webhook's fail-closed
   shape**: HMAC-SHA256 over the raw body with `FAMGATEWAY_API_KEY` as the
   HMAC secret (`X-FamGateway-Signature`), constant-time compare, event and
   status allowlist (`payment.success` / `payment.verified`, `success` /
   `COMPLETED`), idempotency via the existing `events` table keyed on
   `order_id:utr`, underpaid rejection, and a UTR recorded as `payment_id`.
   The server **additionally verifies the order** with FamGateway's
   `verify-order.php` (network failure falls back to trusting the
   HMAC-signed body — the signature is the enforcement boundary).

4. **Segregation of identities.** `store.record_order` grows a `provider`
   column (backfilled `'razorpay'` for legacy rows) so reconciliations and
   refunds target the right rail, and a UTR payment-id never collides with a
   Razorpay `pay_…` id. UTRs are unique: a second webhook claiming the same
   UTR against a different order cannot double-grant.

5. **Razorpay stays dormant, not deleted.** `/create-subscription`,
   `/verify-subscription`, `/razorpay/webhook` remain registered; in
   famgateway mode they are inert (subscription creation requires Razorpay
   keys that won't be configured). Dormancy is enforced by
   `config.require_ready` plus the provider switch — no dual-active state, no
   code churn if Razorpay returns.

6. **Kill-switch `PAYMENTS_PROVIDER=off`** renders bothRails disabled and the
   storefront shows the existing "checkout is not enabled" guard
   (`BILLING_READY` already renders that).

7. **Trust posture.** Secrets only on the billing server env. The unconfigured
   default is fail-closed. Expiry/refund handling for FamGateway mode relies
   on the 31-day order window (no auto-renew to revoke; refunds are handled
   manually in the FamGateway dashboard + `orders.status='refunded'` via the
   admin path).

## Consequences

- + Zero payment fees; buyers pay by UPI with hosted checkout and QR.
- + No recurring-Razorpay dependency to go live on data-agent.me.
- − Pro no longer auto-renews in famgateway mode: entitlement expiry is the
  reminder system (success page + app copy will say renew = pay again).
  Mockup of replacement reminders (email nudge) remains optional future work.
- − An unregulated provider is single point of trust; mitigation is the
  provider kill-switch, dormant Razorpay, and the UTR audit trail.
- − FamGateway's trademark/disclaimers must appear on the storefront
  ("not affiliated with FamPay/Trio; not a licensed PSP") — enforced by copy
  tests.

## Refs

- Plan: `docs/plans/fampay-payment-gateway-and-free-hosting.md`
- ADR-002 (central accounts), ADR-003 (Turso), ADR-004 (keyless), ADR-010 (local-first)
- `docs/deployment/billing-server.md` (Oracle x86_64 + Caddy runbook)

## Hardening follow-ups (from the 65aa63a code review; live-rail gate)

None of these are exploitable while the trust boundary is the provider's
HMAC, but they should be closed before the rail takes real traffic:

1. **Missing `amount` skips the underpay check** (`main.py` fam webhook:
   `if recorded and paid_paise and …`). A success event that omits `amount`
   (or the verify-verdict amount, which is never compared at all) grants
   without an amount comparison. Decide: either treat a missing amount on a
   success event as "cannot compare → ignore, let verify_order() re-derive
   it", or cross-check `confirmed.get("amount")` (rupees→paise) against the
   recorded order.
2. **`order_id`-absent verify verdicts pass leniently** (documented inline;
   seen → not). If FamGateway's live responses always carry `order_id`,
   tighten to require it.
3. **Dev-server mock emits a relative `/dev/checkout` URL**, which the
   https-only `checkoutRedirect()` gate refuses (`billing.js`). Dev still
   works (falls back to the mock popup), but the redirect leg of the dev
   walkthrough is dead. Options: absolute https dev URL behind an env flag,
   or a localhost-allowlisted `http://` branch that never ships to prod.
