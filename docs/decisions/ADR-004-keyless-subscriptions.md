# ADR-004 — Keyless monthly subscriptions (email/account entitlements)

Date: 2026-10-16
Status: Phases 1–5 implemented and green; deployment kit added
(`docs/deployment/billing-server.md` + `billing_server/Dockerfile` +
`deploy/`). Remaining: go-live checklist (Razorpay account actions — see
Phase 5 item 4).

## Context

Pro was a one-time purchase that minted a signed `DATAAGENT-…` product key,
emailed it to the buyer, and required key activation (first-account-wins
redemption). That model worked but carried real costs:

- key sharing (one key activateable by the first account that redeems it),
  key-delivery email as the only provisioning path, and support burden for
  lost keys;
- no recurring revenue — Pro had to be re-sold manually;
- the purchase → email → activate loop is slower than account-based access.

The commercial goal is a **₹499/month auto-renewing Pro subscription** handled
by Razorpay Subscriptions, with entitlements that simply follow the buyer's
email/account — while keeping Lifetime (₹10,000 one-time) unchanged and
existing keys working.

The billing server is already hosted with a durable store (ADR-002 central
accounts, ADR-003 Turso) and signature-verified Razorpay webhooks.

## Decision

1. **Pro = Razorpay Subscription** (monthly, ₹499, plan id from
   `RAZORPAY_PLAN_ID_PRO`). Checkout creates a subscription
   (`POST /create-subscription`), a client callback verifies the checkout HMAC
   (`POST /verify-subscription`), and lifecycle is owned by
   `subscription.*` webhooks. Lifetime stays a one-time order
   (`/create-order` + `/verify`); `/create-order` now rejects Pro with
   `400 "Pro is a subscription — use /create-subscription"`.

2. **Entitlements are keyless.** Purchases grant the plan to the buyer's
   email; the app resolves plan + signed entitlement token from the billing
   server (email/account based). No key is minted, returned, emailed, or
   shown on the success page for new purchases.

3. **Legacy keys remain redeemable** (`/v1/keys/redeem`, admin registry,
   support-path minting only). Legacy Pro orders keep their original 31-day
   grant window (`orders.grant_days`; `LEGACY_PRO_GRANT_DAYS = 31` fallback);
   `scripts/migrate_keys_to_email_access.py` backfills them explicitly.

4. **Cancellation defaults to at-period-end** (`POST /v1/subscriptions/cancel`,
   Bearer session): Pro stays active until `current_end`; refunds revoke.

The grants engine (`billing_server/grants.py`) is the single fold:
lifetime first, else the max unexpired grant; any subscription row with
`current_end > now` grants Pro status-independently; `renews` is
`status ∈ {active, authenticated, pending} ∧ ¬cancel_at_period_end`.

### Implemented evidence (Phases 1–3)

- Store: `subscriptions` table + helpers, `orders.grant_days`, Turso adapter
  (`tests/test_billing_store_subscriptions.py`).
- Grants: `billing_server/grants.py`, public `order_grant`/`subscription_grant`
  wrappers (`tests/test_billing_grants.py`).
- Razorpay client: create/fetch/cancel subscription, `create_plan`, checkout
  signature (`tests/test_billing_razorpay_subscriptions.py`).
- Server: `/create-subscription`, `/verify-subscription`, subscription webhook
  routing with per-cycle idempotency (`{sub_id}:{payment_id|created_at}`),
  keyless `/verify` + `/success`, entitlements fold with
  `auto_renew`/`cancel_at_period_end`, cancel endpoint
  (`tests/test_billing_server_subscriptions.py`).
- Regression: all legacy billing suites realigned to keyless semantics via
  support-path key minting (307 passed across 14 suites); `keymint`
  entitlement token window 7 → 14 days; `emailer.send_subscription_email`.
- Scripts: `scripts/razorpay_create_plan.py`,
  `scripts/migrate_keys_to_email_access.py` (dry-run verified).

## Consequences

- No key sharing, no key-delivery path for new purchases; renewals, cancel,
  refund, and dunning follow the email automatically.
- Razorpay **Subscriptions capability must be enabled** (KYC) for live mode —
  test mode works today. `total_count` is capped at 100 cycles (≈8 years).
- One subscription row per subscription id; `subscription.charged` fires every
  cycle and must stay idempotent per payment (implemented).
- The app no longer needs to activate keys for new buyers, but must keep the
  redeem path for legacy keys and never downgrade local lifetime/key grants on
  transient sync failures (existing cloud-sync rule).
- Credential rotation (Turso tokens, webhook secret) stays on the launch
  checklist — see ADR-003.

## Phase 4 — App-side keyless (implemented, 2026-10-16)

Goal: subscription state (renewal, cancel) is visible in the app; plan
resolution follows email without regressions for legacy keys.

Evidence:
- `api/billing/renewal_snapshot.py` (new): per-email snapshot in
  `premium_settings` (`renewal_snapshot:<email>`) with
  `is_subscription` / `auto_renew` / `cancel_at_period_end` / `renews_at` /
  `synced_at`; `is_stale()` (24h TTL + renewal-within-24h);
  `refresh_if_stale()` phone-home (best-effort, fail-secure);
  `subscription_renewal_for_user()` (user_id → email → snapshot).
- `api/billing/cloud_sync.py`: `fetch_remote_plan()` passes through
  `auto_renew` / `cancel_at_period_end`; `apply_remote_plan()` records the
  snapshot on every valid response (including no-op/free, for last-checked
  throttling). Never-downgrade rule untouched.
- `api/billing/entitlements.py`: `get_entitlements()` adds `renewal` (None for
  key / lifetime / admin / free accounts).
- `api/subscriptions.py` `GET /subscriptions/me`: phones home when stale and
  returns `renewal` (None for non-subscribers).
- `web/js/pages/subscription.js`: "Auto-renews on <date>" / "Cancels on
  <date>" line, "Last checked" stamp, "Manage on the billing site" button;
  trial + legacy key redeem blocks unchanged.
- Tests: `tests/test_renewal_snapshot.py`, `tests/test_subscriptions_me_renewal.py`,
  `tests/test_subscription_page_renewal.py`, extended
  `tests/test_cloud_entitlement_sync.py` — 38 passed (Phase 4 set); billing
  regression 352 passed across 19 suites.


## Phase 5 — Website storefront (implemented, 2026-10-16)

Goal: billing site sells the monthly subscription; lifetime flow unchanged.

1. **`website/js/billing.js`** (done): Pro CTA → `POST /create-subscription`
   → Razorpay Checkout in subscription mode (`subscription_id`, `handler` →
   `POST /verify-subscription` → redirect `/success?subscription_id=`).
   Lifetime keeps `/create-order` + `/verify` (keyless success). Pro hint copy
   "$6/mo · ≈ ₹499/mo · auto-renews · cancel anytime"; notices/AI labels no
   longer promise key delivery; the Payment Link fallback is Lifetime-only
   (links cannot create subscriptions). Split into `startSubscription()` /
   `startOneTime()`.
2. **`website/billing.html`** (done): subtitle now "auto-renews monthly, cancel
   anytime … unlocks with the email you pay with — no codes to paste"; the
   Payment Links comment documents Lifetime only.
3. **`billing_server/dev_server.py`** (done): fakes
   `razorpay_client.create_subscription` / `fetch_subscription`, sets a
   `RAZORPAY_PLAN_ID_PRO` dev default, replaces the `/create-subscription`
   route with a wrapper that adds `checkout_url=/dev/sub-checkout`, and adds
   `/dev/sub-pay` + the subscription mock popup (real HMAC → real
   `/verify-subscription` → `/success?subscription_id=`).
4. **Deployment (launch checklist — user-side Razorpay account actions):**
   runbook in `docs/deployment/billing-server.md` (single-origin Caddy
   routing — the server has no CORS middleware — plus the systemd unit,
   Dockerfile and prod env template shipped in `deploy/` and
   `billing_server/`): create the plan with
   `scripts/razorpay_create_plan.py` (test first, then
   live), set `RAZORPAY_PLAN_ID_PRO`; enable the Subscriptions capability
   (KYC); subscribe the webhook to `subscription.*` (authenticated, activated,
   charged, pending, halted, cancelled, completed, paused, resumed, expired)
   plus `payment.captured` / `payment_link.paid` / `refund.processed`; run
   `scripts/migrate_keys_to_email_access.py --apply` against production.

Evidence: `tests/test_website_subscription_flow.py` (static storefront
contract) + `tests/test_dev_server_subscription.py` (mock endpoints) — 16
passed; combined billing+dev+website regression 368 passed in one process; live
HTTP walkthrough (`$TEMP\opencode\walkthrough_phase5.py`) — buy monthly →
verify → `/success?subscription_id=` shows "Auto-renews on" → signed
`/v1/entitlements` returns `plan=pro, auto_renew=true,
cancel_at_period_end=false`; legacy Lifetime order still grants and folds.

## Rejected alternatives

- **Keep minting keys for subscriptions** — key delivery/redemption adds no
  security (the email already owns the plan server-side) and keeps the
  sharing/support burden.
- **Payment Links for Pro** — no recurring lifecycle events; Subscriptions
  give `subscription.charged` / `halted` / `cancelled` for free.
- **Razorpay Orders + manual monthly re-billing** — no auto-renew, no dunning.

## Verification plan

TDD per task (RED → GREEN), then the billing regression set (368 tests across
21 suites, including `tests/test_renewal_snapshot.py`,
`tests/test_subscriptions_me_renewal.py`,
`tests/test_subscription_page_renewal.py`,
`tests/test_cloud_entitlement_sync.py`,
`tests/test_website_subscription_flow.py`,
`tests/test_dev_server_subscription.py`), plus `tests/test_billing_prices.py`
and the dev-server E2E walkthrough (buy monthly → verify → renewal visible →
entitlements; Lifetime order still grants keylessly).
