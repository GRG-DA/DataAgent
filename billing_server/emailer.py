"""Purchase emails via Resend (free tier).

Fire-and-forget best-effort: if RESEND_API_KEY is unset or the send fails the
purchase still succeeded — entitlement is always resolvable by email.

``send_subscription_email`` is the live path (auto-renewing monthly Pro, no
product key). ``send_key_email`` stays for legacy/support key minting only.
"""
from __future__ import annotations

import html
import logging

import httpx

import billing_server.config as cfg

logger = logging.getLogger("billing.emailer")

API = "https://api.resend.com/emails"
TIMEOUT = 15.0


def _payload(email: str, subject: str, html_body: str, text: str) -> dict:
    payload = {
        "from": cfg.EMAIL_FROM,
        "to": [email],
        "subject": subject,
        "html": html_body,
        "text": text,
    }
    reply_to = getattr(cfg, "EMAIL_REPLY_TO", "").strip()
    if reply_to:
        payload["reply_to"] = reply_to
    return payload


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


def _key_text(key: str, plan: str, email: str = "") -> str:
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    bound_note = ""
    if email:
        bound_note = (
            f"\nThe key is tied to {email} - sign in to DataAgent with that "
            "account. It cannot be activated on a different account.\n"
        )
    return (
        f"Your DataAgent {plan_label} key\n\n"
        "Thanks for your purchase! Activate Pro features in the app:\n"
        'DataAgent -> Settings -> Subscription -> "I have a product key"\n'
        f"{bound_note}\n{key}\n\n"
        "Keep this email - the key is your proof of purchase. "
        "Questions? Reply to this email.\n"
    )


def send_key_email(email: str, key: str, plan: str) -> bool:
    """Send the product key. Returns True on success; never raises."""
    if not cfg.RESEND_API_KEY:
        logger.info("RESEND_API_KEY unset — skipping email for %s (key on success page)", email)
        return False
    return _send_email_or_log(
        email,
        f"Your DataAgent {plan} key",
        _html(key, plan, email),
        _key_text(key, plan, email),
    )


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


def _subscription_text(plan: str, email: str, renews_at: str = "") -> str:
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    renew_note = ""
    if renews_at:
        renew_note = (
            f"\nPro renews automatically on {renews_at[:10]}. Cancel anytime in "
            "DataAgent -> Settings -> Subscription - Pro stays active until then.\n"
        )
    return (
        f"Your DataAgent {plan_label} is active\n\n"
        f"Thanks for subscribing! Sign in to DataAgent with {email} - "
        "every Pro feature is already unlocked, on all your devices.\n"
        f"{renew_note}\n"
        "No codes to paste - your subscription is tied to this email. Manage or "
        "cancel it any time from Settings -> Subscription. Questions? Reply to "
        "this email.\n"
    )


def send_subscription_email(email: str, plan: str, renews_at: str = "") -> bool:
    """Send the subscription confirmation. Returns True on success; never raises."""
    if not cfg.RESEND_API_KEY:
        logger.info("RESEND_API_KEY unset — skipping subscription email for %s", email)
        return False
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    return _send_email_or_log(
        email,
        f"Your DataAgent {plan_label} subscription is active",
        _subscription_html(plan, email, renews_at),
        _subscription_text(plan, email, renews_at),
    )


def _grant_html(plan: str, email: str, expires_at: str = "") -> str:
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    if expires_at:
        window_note = (
            f'<p>Your 31-day Pro window is active until <b>{expires_at}</b>. '
            "To keep Pro, pay again any time — renewal is just another payment "
            "on the pricing page (UPI, no card details anywhere).</p>"
        )
    else:
        window_note = (
            "<p>This is a one-time purchase — nothing to renew, nothing expires.</p>"
        )
    return f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto">
  <h2 style="color:#4f46e5">Your DataAgent {plan_label} is active</h2>
  <p>Thanks for your purchase! Sign in to DataAgent with <b>{email}</b> —
  your plan is unlocked on all your devices.</p>
  {window_note}
  <p style="color:#64748b;font-size:13px">
    No codes to paste — your purchase is tied to this email. Keep this
    message as your receipt. Questions? Reply to this email.
  </p>
</div>"""


def _grant_text(plan: str, email: str, expires_at: str = "") -> str:
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    if expires_at:
        window_note = (
            f"Your 31-day Pro window is active until {expires_at}. To keep Pro, "
            "pay again any time - renewal is just another payment on the "
            "pricing page (UPI, no card details anywhere).\n"
        )
    else:
        window_note = (
            "This is a one-time purchase - nothing to renew, nothing expires.\n"
        )
    return (
        f"Your DataAgent {plan_label} is active\n\n"
        f"Thanks for your purchase! Sign in to DataAgent with {email} - "
        "your plan is unlocked on all your devices.\n\n"
        f"{window_note}\n"
        "No codes to paste - your purchase is tied to this email. Keep this "
        "message as your receipt. Questions? Reply to this email.\n"
    )


def send_grant_email(email: str, plan: str, expires_at: str = "") -> bool:
    """Keyless purchase confirmation (FamGateway rail).

    ``expires_at`` (YYYY-MM-DD) is the Pro window's end date; empty for a
    perpetual lifetime grant. Returns True on success; never raises.
    """
    if not cfg.RESEND_API_KEY:
        logger.info("RESEND_API_KEY unset — skipping grant email for %s", email)
        return False
    plan_label = cfg.PLANS.get(plan, {}).get("label", plan)
    subject = (f"Your DataAgent {plan_label} window is active until {expires_at}"
               if expires_at else f"Your DataAgent {plan_label} is active")
    return _send_email_or_log(
        email,
        subject,
        _grant_html(plan, email, expires_at),
        _grant_text(plan, email, expires_at),
    )


def send_expiry_nudge_email(email: str, expires_on: str) -> bool:
    """One renew reminder per window (sent by /admin/expiry-nudge).

    FamGateway has no recurring billing, so renewal is pay-again — this is
    the only reminder that is not the expiry itself. Never raises.
    """
    if not cfg.RESEND_API_KEY:
        logger.info("RESEND_API_KEY unset — skipping expiry nudge for %s", email)
        return False
    billing_url = f"{cfg.BILLING_SITE_URL}/billing.html"
    text = (
        f"Your DataAgent Pro access ends on {expires_on}\n\n"
        f"Your 31-day DataAgent Pro window (signed in as {email}) is about to end.\n\n"
        "To keep Pro uninterrupted, pay INR 499 for the next 31 days here:\n"
        f"{billing_url}\n\n"
        "Renewal is just another UPI payment, and your grant folds into the "
        "longest live window automatically.\n\n"
        "Questions or refunds? Reply to this email.\n"
    )
    return _send_email_or_log(
        email,
        subject=f"Your DataAgent Pro access ends on {expires_on} — renew in a minute",
        html=f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto">
  <h2 style="color:#b45309">Pro ends on <b>{expires_on}</b></h2>
  <p>Your 31-day DataAgent Pro window (signed in as <b>{email}</b>) is about
  to end.</p>
  <p>To keep Pro uninterrupted, pay ₹499 for the next 31 days on the
  <a href="{billing_url}">pricing page</a> — renewal is just another UPI
  payment, and your grant folds into the longest live window automatically.</p>
  <p style="color:#64748b;font-size:13px">Questions or refunds? Reply to this
  email.</p>
</div>""",
        text=text,
    )


def send_verification_email(email: str, link: str) -> bool:
    """Confirmation link for a website account. Returns True on success."""
    if not cfg.RESEND_API_KEY:
        logger.info("RESEND_API_KEY unset - skipping verification email")
        return False
    safe_link = html.escape(link, quote=True)
    body = f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto">
  <h2 style="color:#4f46e5">Confirm your email for DataAgent</h2>
  <p>Use the button below to prove this email belongs to you. After that,
  you can see the plan and purchase history attached to this address.</p>
  <p style="margin:24px 0">
    <a href="{safe_link}"
       style="display:inline-block;background:#4f46e5;color:#fff;padding:12px 20px;
              border-radius:8px;text-decoration:none;font-weight:600">
      Confirm email
    </a>
  </p>
  <p style="color:#64748b;font-size:13px">
    This link expires in 24 hours and works once. If you did not create this
    account, ignore this email; no plan or purchase history is visible.
  </p>
</div>"""
    text = (
        "Confirm your email for DataAgent\n\n"
        "Use this link to prove this email belongs to you:\n"
        f"{link}\n\n"
        "After that, you can see the plan and purchase history attached to "
        "this address.\n\n"
        "This link expires in 24 hours and works once. If you did not create "
        "this account, ignore this email; no plan or purchase history is "
        "visible.\n"
    )
    return _send_email_or_log(
        email,
        "Confirm your email for DataAgent",
        body,
        text,
    )


def _send_email_or_log(email: str, subject: str, html_body: str, text: str) -> bool:
    """One Resend POST shared by all lifecycle emails."""
    try:
        resp = httpx.post(
            API,
            headers={
                "Authorization": f"Bearer {cfg.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=_payload(email, subject, html_body, text),
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        from billing_server.accounts import mask_email

        logger.warning("email to %s failed: %s", mask_email(email), exc)
        return False
