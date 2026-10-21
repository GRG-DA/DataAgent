"""Purchase emails via Resend (free tier).

Fire-and-forget best-effort: if RESEND_API_KEY is unset or the send fails the
purchase still succeeded — entitlement is always resolvable by email.

``send_subscription_email`` is the live path (auto-renewing monthly Pro, no
product key). ``send_key_email`` stays for legacy/support key minting only.
"""
from __future__ import annotations

import logging

import httpx

import billing_server.config as cfg

logger = logging.getLogger("billing.emailer")

API = "https://api.resend.com/emails"
TIMEOUT = 15.0


def _html(key: str, plan: str, email: str = "") -> str:
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    bound_note = ""
    if email:
        bound_note = (
            f'<p style="color:#b45309;font-size:13px">The key is tied to '
            f'<b>{email}</b> — sign in to DataAgent with that account. '
            "It cannot be activated on a different account.</p>"
        )
    return f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto">
  <h2 style="color:#4f46e5">Your DataAgent {plan_label} key</h2>
  <p>Thanks for your purchase! Activate Pro features in the app:</p>
  <p><b>DataAgent → Settings → Subscription → “I have a product key”</b></p>
  {bound_note}
  <p style="margin:24px 0">
    <code style="display:block;background:#f4f4f5;padding:14px 18px;border-radius:10px;
                 font-size:15px;letter-spacing:.5px;word-break:break-all">{key}</code>
  </p>
  <p style="color:#64748b;font-size:13px">
    Keep this email — the key is your proof of purchase. {plan_label} never needs
    card details inside the app. Questions? Reply to this email.
  </p>
</div>"""


def send_key_email(email: str, key: str, plan: str) -> bool:
    """Send the product key. Returns True on success; never raises."""
    if not cfg.RESEND_API_KEY:
        logger.info("RESEND_API_KEY unset — skipping email for %s (key on success page)", email)
        return False
    try:
        resp = httpx.post(
            API,
            headers={
                "Authorization": f"Bearer {cfg.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": cfg.EMAIL_FROM,
                "to": [email],
                "subject": f"Your DataAgent {plan} key",
                "html": _html(key, plan, email),
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("key email to %s failed: %s", email, exc)
        return False


def _subscription_html(plan: str, email: str, renews_at: str = "") -> str:
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    renew_note = ""
    if renews_at:
        renew_note = (
            f'<p>Pro renews automatically on <b>{renews_at[:10]}</b>. Cancel anytime in '
            "DataAgent → Settings → Subscription — Pro stays active until then.</p>"
        )
    return f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto">
  <h2 style="color:#4f46e5">Your DataAgent {plan_label} is active</h2>
  <p>Thanks for subscribing! Sign in to DataAgent with <b>{email}</b> —
  every Pro feature is already unlocked, on all your devices.</p>
  {renew_note}
  <p style="color:#64748b;font-size:13px">
    No codes to paste — your subscription is tied to this email. Manage or
    cancel it any time from Settings → Subscription. Questions? Reply to this email.
  </p>
</div>"""


def send_subscription_email(email: str, plan: str, renews_at: str = "") -> bool:
    """Send the subscription confirmation. Returns True on success; never raises."""
    if not cfg.RESEND_API_KEY:
        logger.info("RESEND_API_KEY unset — skipping subscription email for %s", email)
        return False
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    try:
        resp = httpx.post(
            API,
            headers={
                "Authorization": f"Bearer {cfg.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": cfg.EMAIL_FROM,
                "to": [email],
                "subject": f"Your DataAgent {plan_label} subscription is active",
                "html": _subscription_html(plan, email, renews_at),
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscription email to %s failed: %s", email, exc)
        return False
