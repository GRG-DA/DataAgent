"""Billing server configuration.

All payment/license secrets live ONLY here (env vars on the billing host).
The DataAgent app holds no payment material — just the Ed25519 PUBLIC key
(baked via keygen_app/baked_public_key.py).
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Payment rail (ADR-016): famgateway | razorpay | off ──────────────
# Exactly one rail is live; razorpay code stays dormant, not deleted.
# Default 'razorpay' keeps existing deployments and tests green until the
# billing host flips the env — fail-closed either way.
PAYMENTS_PROVIDER = os.environ.get("PAYMENTS_PROVIDER", "razorpay").strip().lower()

# ── FamGateway (famgateway.in; UPI automation, one-time payments) ─────
FAMGATEWAY_API_KEY = os.environ.get("FAMGATEWAY_API_KEY", "")
FAMGATEWAY_API_BASE = os.environ.get("FAMGATEWAY_API_BASE",
                                     "https://famgateway.in").strip()
# Registered in the FamGateway dashboard as the webhook URL for this server.
FAMGATEWAY_WEBHOOK_URL = os.environ.get("FAMGATEWAY_WEBHOOK_URL", "").strip()
# Optional post-payment redirect. May contain the {order_id} placeholder.
FAMGATEWAY_REDIRECT_URL = os.environ.get("FAMGATEWAY_REDIRECT_URL", "").strip()

# Public origin of the billing site ('https://data-agent.me') — used to build
# de-facto redirect targets when FAMGATEWAY_REDIRECT_URL is not set.
BILLING_PUBLIC_URL = os.environ.get("BILLING_PUBLIC_URL", "").strip()

# ── Razorpay (test: rzp_test_…, live: rzp_live_…) ────────────────────
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# ── Razorpay Subscriptions (auto-renewing monthly Pro) ───────────────
# The monthly plan is created once in the Razorpay dashboard/API and its id
# is pasted here (scripts/razorpay_create_plan.py prints one to copy).
RAZORPAY_PLAN_ID_PRO = os.environ.get("RAZORPAY_PLAN_ID_PRO", "").strip()
# Cycles per subscription; Razorpay caps total_count at 100, and 100 monthly
# charges (≈8 years) is "until cancelled" for every practical purpose.
SUBSCRIPTION_TOTAL_COUNT = 100

# ── License signing (Ed25519 PEM). Shared format with keygen_app. ────
# LICENSE_PRIVATE_KEY takes base64(PEM); LICENSE_PRIVATE_KEY_PATH a file.
LICENSE_PRIVATE_KEY = os.environ.get("LICENSE_PRIVATE_KEY", "")
LICENSE_PRIVATE_KEY_PATH = os.environ.get(
    "LICENSE_PRIVATE_KEY_PATH",
    str(Path(__file__).parent / "keys" / "ed25519_private.pem"),
)

# ── Entitlement read API: clients sign requests with this secret ─────
ENTITLEMENTS_SYNC_SECRET = os.environ.get("ENTITLEMENTS_SYNC_SECRET", "")

# ── Key delivery email (Resend; free tier). Empty disables sending —
#    the key is still shown on the success page. ─────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("BILLING_EMAIL_FROM", "DataAgent <billing@yourdomain.com>")

# ── Support/admin view token (GET /admin/registry). Empty disables the
#    view entirely — fail-closed; the endpoint never serves without it. ─
ADMIN_TOKEN = os.environ.get("BILLING_ADMIN_TOKEN", "")

# ── Store ────────────────────────────────────────────────────────────
DB_PATH = os.environ.get("BILLING_DB_PATH", str(Path(__file__).parent / "billing.sqlite3"))

# Hosted store (Turso/libSQL). Empty TURSO_DATABASE_URL = local SQLite above.
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

# ── Pricing (paise; ONE price list) ────────────────────────────────────
# The checkout is the authority for what a customer is charged; the app
# mirrors it (`api/subscriptions.py` -> PLANS and `api/billing/seed.py` ->
# PRICES) and `tests/test_launch_billing_legal.py` fails if they drift.
#
# The app used to advertise Pro at $6/mo while this list billed ₹499/mo —
# two price lists for one plan, i.e. a refund generator (C2).  Both sides
# now say the same thing: Pro ₹499/mo, Lifetime ₹10,000 one-time.
#
# Pro bills as an auto-renewing monthly Razorpay subscription (no one-time
# orders); lifetime stays a one-time order and is the HEADLINE tier
# (LAUNCH-READINESS §8: it is the only tier whose price absorbs a metered
# AI allowance, and the keygen / offline-verification path already
# supports it).  Pro is kept as a capped secondary tier: every plan has a
# finite daily AI-request cap (`ai_requests_daily`), because unlimited AI
# on a paid plan is a guaranteed negative margin (C3).
#
# `ai_requests_daily` is documentation for the checkout pages and the
# support team; the enforced caps live in the app
# (`api/billing/seed.py` -> LIMITS, `BILLING_*_AI_DAILY_CAP`).
PLANS = {
    "pro": {"amount": 49900, "currency": "INR", "cycle": "MONTHLY", "days": 31,
            "subscription": True, "recommended": False,
            "ai_requests_daily": 50,
            "label": "DataAgent Pro — monthly, auto-renews"},
    "lifetime": {"amount": 1000000, "currency": "INR", "cycle": "ONETIME",
                 "days": None, "subscription": False,
                 "recommended": True,
                 "ai_requests_daily": 100,
                 "label": "DataAgent Lifetime — one-time, everything in Pro"},
}


class ConfigError(RuntimeError):
    pass


def require_ready(*, order: bool = False, webhook: bool = False, sign: bool = False, sync: bool = False) -> None:
    """Fail-closed guards: raise a clear error if required secrets are missing.

    Provider-gated (ADR-016): the razorpay checks apply in razorpay mode,
    FAMGATEWAY_API_KEY in famgateway mode; 'off' disables orders entirely.
    """
    missing = []
    if PAYMENTS_PROVIDER not in ("famgateway", "razorpay", "off"):
        raise ConfigError(
            "PAYMENTS_PROVIDER must be famgateway, razorpay, or off, "
            "got %r" % PAYMENTS_PROVIDER)
    if order:
        if PAYMENTS_PROVIDER == "off":
            raise ConfigError("Payments are disabled (PAYMENTS_PROVIDER=off)")
        if PAYMENTS_PROVIDER == "famgateway":
            if not FAMGATEWAY_API_KEY:
                missing.append("FAMGATEWAY_API_KEY")
        else:  # 'off' is rejected above; razorpay is the remaining rail.
            if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
                missing.append("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET")
    if webhook:
        if PAYMENTS_PROVIDER == "famgateway":
            if not FAMGATEWAY_API_KEY:
                missing.append("FAMGATEWAY_API_KEY")
        elif not RAZORPAY_WEBHOOK_SECRET:
            missing.append("RAZORPAY_WEBHOOK_SECRET")
    if sign:
        from billing_server.keymint import load_private_key
        try:
            load_private_key()
        except FileNotFoundError as exc:
            raise ConfigError(str(exc)) from exc
    if sync and not ENTITLEMENTS_SYNC_SECRET:
        missing.append("ENTITLEMENTS_SYNC_SECRET")
    if missing:
        raise ConfigError("Missing billing configuration: " + ", ".join(missing))
