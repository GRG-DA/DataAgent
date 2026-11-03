"""Billing server configuration.

All payment/license secrets live ONLY here (env vars on the billing host).
The DataAgent app holds no payment material — just the Ed25519 PUBLIC key
(baked via keygen_app/baked_public_key.py).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

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
# Strict verify-verdict handling (ADR-016 follow-up): when truthy, a
# verify-order verdict that carries NO order_id field no longer tallies
# leniently — the webhook refuses and the /admin/reconcile sweep still
# recovers real payments. Flip to 1 once live verify-order responses are
# confirmed to always carry order_id.
FAMGATEWAY_STRICT_VERDICT = os.environ.get(
    "FAMGATEWAY_STRICT_VERDICT", "").strip().lower() in ("1", "true", "yes", "on")

# Public origin of the billing site ('https://data-agent.me') — used to build
# de-facto redirect targets when FAMGATEWAY_REDIRECT_URL is not set.
BILLING_PUBLIC_URL = os.environ.get("BILLING_PUBLIC_URL", "").strip()

# Public storefront that hosts account.html (email-verification links).
BILLING_SITE_URL = os.environ.get(
    "BILLING_SITE_URL", "https://data-agent.me").strip().rstrip("/")

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

# Keyless deployments serve tokenless entitlements and let central login
# succeed without a session token. The desktop app verifies the plan locally.
KEYLESS = os.environ.get("BILLING_KEYLESS", "").strip().lower() in (
    "1", "true", "yes", "on")

# ── Website account session cookie ──────────────────────────────────────
# The storefront is served from data-agent.me and calls pay.data-agent.me,
# so the cookie is scoped to the registrable domain. It is opaque and
# server-revocable, unlike the Ed25519 token the desktop app uses.
SESSION_COOKIE_NAME = os.environ.get(
    "BILLING_SESSION_COOKIE_NAME", "da_session").strip() or "da_session"
SESSION_COOKIE_DOMAIN = os.environ.get(
    "BILLING_SESSION_COOKIE_DOMAIN", ".data-agent.me").strip()
SESSION_COOKIE_SECURE = os.environ.get(
    "BILLING_SESSION_COOKIE_SECURE", "true").strip().lower() in (
    "1", "true", "yes", "on")

def _parse_cors_origins(raw: str) -> list[str]:
    """Return only sane HTTPS/localhost origins; never a wildcard."""
    origins: list[str] = []
    for item in (raw or "").split(","):
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            logger.warning("BILLING_CORS_ORIGINS ignores wildcard '*'")
            continue
        parts = urlsplit(origin)
        local_http = (
            parts.scheme == "http"
            and (parts.hostname or "").lower() in ("localhost", "127.0.0.1", "::1")
        )
        if not (parts.scheme == "https" and parts.hostname) and not local_http:
            logger.warning("BILLING_CORS_ORIGINS ignores non-HTTPS origin %r", origin)
            continue
        if parts.path or parts.query or parts.fragment or parts.username or parts.password:
            logger.warning("BILLING_CORS_ORIGINS ignores malformed origin %r", origin)
            continue
        origins.append(origin)
    return origins


# Origins allowed to call the API from the storefront (comma-separated).
# Same-origin deployments can leave this at the production defaults.
CORS_ORIGINS = _parse_cors_origins(
    os.environ.get(
        "BILLING_CORS_ORIGINS",
        "https://data-agent.me,https://www.data-agent.me",
    )
)

# ── Entitlement read API: clients sign requests with this secret ─────
ENTITLEMENTS_SYNC_SECRET = os.environ.get("ENTITLEMENTS_SYNC_SECRET", "")

# ── Key delivery email (Resend; free tier). Empty disables sending —
#    the key is still shown on the success page. ─────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("BILLING_EMAIL_FROM", "DataAgent <billing@data-agent.me>")
EMAIL_REPLY_TO = os.environ.get(
    "BILLING_EMAIL_REPLY_TO", "DataAgent Support <billing@data-agent.me>"
).strip()

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
