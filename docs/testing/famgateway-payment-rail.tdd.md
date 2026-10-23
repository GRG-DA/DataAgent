# TDD Evidence — FamGateway payment rail (ADR-016)

Date: 2026-10-23. Journeys derived during this run (no `*.plan.md` provided;
intent came from `docs/plans/fampay-payment-gateway-and-free-hosting.md`
read as untrusted input and sanitized against execution).

## User journeys
- As a buyer on the storefront, I pay ₹499 (or ₹10,000) by UPI on the hosted
  FamGateway checkout, so that my DataAgent Pro (31-day window) or Lifetime
  grant attaches to the email I paid with.
- As the billing server, I only grant when the provider's HMAC authenticates
  the event AND every idempotency/ownership check passes, so that one UTR can
  pay exactly one order, ever.
- As a buyer on a razorpay-mode deployment, my existing subscription/one-time
  flows keep working (rails are provider-gated, not deleted).
- As an operator of the app, I see my Pro window's expiry date on the
  subscription page and renew by paying again (no auto-renew on UPI rail).

## Task report
| Task | Command | Key evidence | Guarantee |
|---|---|---|---|
| Phase 1-2: provider config, client, webhook, store | `python -m pytest tests\test_billing_famgateway.py` | RED `9307c37` (5 failing), GREEN `d38d8bc` | Signature → allowlist → idempotency → underpay → UTR-owner → verify → grant order enforced; 16 tests |
| Phase 3: storefront provider-aware copy + /v1/payments-info | pytest site suites | `238016c` | Storefront copy pins (prices, disclaimers, rails) pass; payments-info reports live provider |
| Phase 4: app-side windowed renewal | `tests\test_renewal_snapshot.py`, `tests\test_subscriptions_me_renewal.py`, `tests\test_subscription_page_renewal.py` | `c94be59` | Non-sub grants expose expires_at; subscription page says renew-by-paying-again |
| Phase 5: deploy kit | site suites + docs | `db77902` | Env vars + runbook §2b (webhook URL, HMAC keyed with API key, payments-info sanity check) |
| Review hardening (F1-F6, F9-F14) | 8 RED tests → 29 GREEN | `65aa63a` | /verify-rails dormant off-razorpay (403); empty-key HMAC forgeable → fail closed; unknown provider → 503; fam order never settleable via /verify; create-order returns plan; no-ref events ignored pre-marker; ignored paths roll marker back (corrected redelivery with same UTR grants — proven end-to-end); verdict order-id cross-check; signed-body-only decisions; store warns when UTR-uniqueness index fails |
| Review LOW follow-ups | 282-test sweep | `4a60e80` | buy-lock releases on dismiss/verify-fail; OverflowError amounts can't 500 the webhook; rollback test asserts the `failures` row (not a stub) |

## Verification
- `python -m pytest` (18-suite sweep, 2026-10-23): **282 passed**, 14 warnings
  (pre-existing CSS-only). Notable suites: test_billing_famgateway.py (29),
  test_billing_server.py, test_security_regressions.py, test_launch_site_*.
- agentshield scan `--path .`: 98 findings, all pre-existing MCP-config
  hygiene; none on billing_server/, website/, or famgateway files.
- code-reviewer verdict on `db77902..65aa63a`: **APPROVE** (0 critical/high);
  6 LOW → 3 fixed in `4a60e80`, 3 documented as ADR-016 follow-ups.

## Coverage and known gaps
- Not covered by automated tests: real-money flows end-to-end (no live
  FamGateway sandbox in CI); dev_server mock redirect leg (https-only gate
  refuses its relative URL — documented in ADR-016 follow-ups).
- Intentional leniencies (documented inline + ADR): verify verdicts without
  `order_id` pass (legacy schema), missing `amount` skips the underpay
  comparison (verify_orders re-derivation still gates) — both flagged for
  tightening before the rail takes real traffic.
- `graphify update .` timed out at 180s this session; graph is stale until
  re-run with a larger timeout.

## Merge evidence
All commits landed directly on `master` in order
`6bf3eaf → 9307c37 → d38d8bc → 238016c → c94be59 → db77902 → ce50913 → 65aa63a → 4a60e80`;
checkpoint mapping: plan docs → RED → GREEN → phase 3 → phase 4 → phase 5 →
cleanup → review fixes → review follow-ups. Nothing squashed.
