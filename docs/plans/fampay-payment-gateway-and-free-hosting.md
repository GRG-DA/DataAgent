# Plan — FamGateway payments (free UPI rail) + free hosting for data-agent.me

> **Status: approved (implementation in progress), 2026-10-23.** Decision record:
> `docs/decisions/ADR-016-famgateway-payment-rail.md`. Read with
> `docs/deployment/billing-server.md` and `docs/FRONTENDS.md` ("The pricing page
> and checkout wiring"), and `ADR-004-keyless-subscriptions.md`.

## 0. The verdict up front

| Question | Answer |
|---|---|
| Can FamPay be integrated as a payment gateway? | **No** — FamPay/FamApp (Trio) is a consumer UPI app with no merchant API. Closest real product: **FamGateway (famgateway.in)**, a third-party UPI *automation* service (not a licensed PSP, not affiliated with FamPay/Trio — their own disclaimer says so). |
| Do we use FamGateway? | **Yes — as the single live rail** (user decision A, 2026-10-23). It is 100% free for merchants (0% fees, no KYC), gives a hosted checkout + QR + UPI intent, HMAC-signed webhooks, and a server-to-server order-verify API. |
| What happens to Razorpay? | **Dormant, not deleted.** All Razorpay code stays behind an env provider switch; subscription endpoints 503 when unconfigured. Revivable by flipping one env var. |
| Where does the gateway live? | **Billing server + marketing-site storefront only**, never the desktop app (ADR-004/ADR-010 already enforce this). |
| How does recurring Pro work without subscriptions? | **FamGateway supports one-time payments only.** Pro is sold as a pre-paid 31-day window (new order row per payment, `grant_days=31`, entitlement = longest live window). Renewal = pay again. Lifetime stays one-time ₹10,000. |
| Is the hosting free? | Marketing site: Cloudflare Pages (commercial-friendly). Billing server: Oracle Always Free **x86_64** VPS per the runbook (never ARM — libsql has no aarch64 wheels). **Do not run paid checkout on Vercel Hobby** (non-commercial ToS). |

## 1. Provider facts (verified 2026-10-23 vs famgateway.in, /docs, openapi.json, llms.txt)

- `POST /api/create-order` (JSON, `X-Api-Key` header): `amount` in **INR rupees
  (integer)**, `customer_email`, optional `webhook_url`, `redirect_url` → returns
  `order_id`, hosted `checkout_url`, `qr_url`, `upi_intent` (5-min atomic session).
- `GET /api/verify-order.php?order_id=<id>` — server-to-server authoritative
  verification (returns UTR, `transaction_id`, status `success` / `pending`;
  408 when the order expired). Endpoint base: `https://famgateway.in`.
- Webhook: `POST <webhook_url>` with headers `X-FamGateway-Signature`
  (**HMAC-SHA256 of the raw body, keyed with the merchant API key**) and
  `X-FamGateway-Event`; events `payment.success` / `payment.verified`; statuses
  `success` / `COMPLETED` (case-insensitive match in practice). Merchant must
  ack HTTP 200 within ~10 s; no retry guarantees documented → treat first-copy
  idempotently.
- No subscriptions, no hosted refunds API; refunds handled manually in the
  dashboard. IMAP receipt-parser option exists — do not use; rely on webhooks
  + verify endpoint + the hosted dashboard for reconciliation.
- **Trust posture:** unregulated, effectively one maintainer → kill-switch env
  (`PAYMENTS_PROVIDER=off`), dormant Razorpay, UTR audit trail, and ADR-016
  disclaimers on the storefront.

## 2. Implementation (TDD; feature branch = current work)

### Phase 2 — billing server (RED → GREEN)

New module `billing_server/famgateway_client.py` (mirrors `razorpay_client.py`):
- `create_order(amount_rupees, customer_email, webhook_url=None, redirect_url=None, order_id_hint=None) -> dict`
- `verify_order(order_id) -> Optional[dict]` (network-safe: returns dict or raises
  `FamGatewayError`; caller decides fallback policy)
- `verify_webhook_signature(raw_body: bytes, signature: str, api_key: str) -> bool`
- httpx timeout 20 s, typed `FamGatewayError`.

Config (`billing_server/config.py`):
- `PAYMENTS_PROVIDER = env("PAYMENTS_PROVIDER", "razorpay")`
- `FAMGATEWAY_API_KEY`, `FAMGATEWAY_API_BASE` (default `https://famgateway.in`),
  `FAMGATEWAY_WEBHOOK_URL`, `FAMGATEWAY_REDIRECT_URL` (fallback:
  `BILLING_PUBLIC_URL + /success?order_id=`).
- `require_ready(order=True)` gains famgateway branches (key present only when
  the provider is famgateway).

Routes (`billing_server/main.py`):
- `POST /create-order`: when provider = `famgateway`, create a FamGateway order
  for **both** `pro` and `lifetime` (pro raises "use /create-subscription"
  **only** in razorpay mode), record with `provider='famgateway'` +
  `grant_days=31` for pro, respond `{order_id, checkout_url, amount, currency
  "INR", name, description}`.
- `POST /famgateway/webhook` (fail-closed): HMAC verify → event/status allowlist
  → `seen_event("fam.payment.success", f"{order_id}:{utr}")` idempotency →
  underpay guard (webhook amount rupees × 100 vs order paise) → defense-in-depth
  `verify_order()` (network exception ⇒ trust the signed body) → `mark_paid`
  with `payment_id = utr or transaction_id or 'fam'` → optional receipt email.
- Store: `orders.provider` column (legacy rows default `'razorpay'`) + unique
  index on UTR payment ids` (partial index on non-empty values) in the LLVM
  `_ensure_schema` widening block.
- Dormant Razorpay: `/create-subscription` unchanged; in famgateway mode stays
  correctly 503 (env won't carry plan id).
- `/healthz` reports `payments_provider` + famgateway readiness bits.

Endpoint/policy notes:
- Idempotency ref format: `fam.payment.success` + `order_id:utr`
  (UTR fallback: `transaction_id`, then `order_id`).
- Refunds: manual in dashboard + admin flip of `orders.status`; out of scope
  for the webhook surface.

### Phase 3 — storefront (`website/`)

- `js/billing.js`: route BOTH plans through `POST /create-order`; on response
  `checkout_url` → `window.location.href = checkout_url` (no Razorpay popup for
  new purchases). Keep Razorpay popup code path intact for razorpay mode
  (response lacking `checkout_url` falls back to the popup flow).
- Copy: Pro button "Pay ₹499 for 31 days (renew by paying again)"; add
  payment-note "UPI via FamGateway — a third-party service, not affiliated
  with FamPay/FamApp/Trio" + keep the unconfigured-deploy `BILLING_READY` guard.
- Guard append: if provider knowledge needed at page scope, rely on backend
  responses only (no new secrets in the static site).

### Phase 4 — app-side copy

- `website` billing-page claims + any app "how to renew" strings: drop
  auto-renew language when famgateway mode is deployed; key parity tests stay
  green (`test_launch_billing_legal.py`, `test_launch_frontend_claims.py`).

### Phase 5 — deploy kit

- `deploy/billing.env.example`: `PAYMENTS_PROVIDER`, `FAMGATEWAY_*` vars with
  comments; keep Razorpay vars documented-dormant.
- Runbook addendum in `docs/deployment/billing-server.md`: webhook URL
  `https://<billing-host>/famgateway/webhook` registered in the FamGateway
  dashboard, `redirect_url` = `https://data-agent.me/billing.html` success
  route, and "set provider" flip on the VPS + Cloudflare Pages deploy.

### Gates (unchanged invariant)

- Webhooks fail-closed on bad/missing/foreign signature (`400/401`).
- No double grant on replay (same UTR ⇒ `duplicate: true`).
- Underpaid webhook mints nothing (`ignored: "underpaid"`).
- Dormant Razorpay routes stay inert when famgateway is active.
- Trust boundary: no key material ever reaches website JS, the app, or logs.
- Price parity + legal claims tests keep passing.

## 3. Out of scope

- Real auto-renewal / dunning (FamGateway cannot do it; email nudges optional
  later), FamGateway-side refund automation, switching the marketing host away
  from its current free-tier host, and any IMAP-based receipt scraping.
