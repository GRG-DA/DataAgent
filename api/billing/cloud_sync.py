"""Cloud entitlement sync — pull the plan from the billing server.

On Google login / startup the desktop app asks the billing server (HMAC-signed
request) what plan the logged-in user's email has. This is how renewals,
refunds and admin/billing-site grants follow the Google account across
machines. Design rules:

- Fail-secure: network errors / unconfigured server → keep the local plan
  (a signed product key already on disk stays valid offline; never downgrade
  because of a flaky connection).
- Server-signed success (plan pulled) is applied through the same writers the
  keygen storage uses (premium_settings + user_subscriptions + subscriptions).
- Only public material in the app: server URL + sync secret per deploy.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 8.0
DEFAULT_SOURCE = "cloud_sync"


def billing_sync_base_url() -> str:
    """Billing server base URL from env (BILLING_SYNC_URL) or settings."""
    import os

    url = (os.environ.get("BILLING_SYNC_URL") or "").strip()
    if url:
        return url.rstrip("/")
    try:
        from config import settings as _settings

        url = str(getattr(_settings.billing, "sync_url", "") or "").strip()
        if url:
            return url.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    try:
        import yaml
        from pathlib import Path

        p = Path("config/settings.yaml")
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            url = str(((data.get("billing") or {}).get("sync_url")) or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return url.rstrip("/")


def billing_sync_secret() -> str:
    import os

    secret = (os.environ.get("BILLING_SYNC_SECRET") or "").strip()
    if secret:
        return secret
    try:
        from config import settings as _settings

        return str(getattr(_settings.billing, "sync_secret", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def is_configured() -> bool:
    return bool(billing_sync_base_url() and billing_sync_secret())


def _sign(email: str, exp: int, secret: str) -> str:
    return hmac.new(secret.encode(), f"{email}|{exp}".encode(), hashlib.sha256).hexdigest()


def fetch_remote_plan(email: str, timeout: float = REQUEST_TIMEOUT) -> Optional[Dict[str, Any]]:
    """Ask the billing server for the plan of ``email``.

    Returns {"plan": "pro"|"lifetime"|"free", "status": ..., "expires_at": ...}
    or None when unconfigured / unreachable / unauthorized.
    """
    email = (email or "").strip().lower()
    base = billing_sync_base_url()
    secret = billing_sync_secret()
    if not email or not base or not secret:
        return None
    exp = int(time.time())
    try:
        resp = httpx.get(
            f"{base}/v1/entitlements",
            params={"email": email, "exp": str(exp), "sig": _sign(email, exp, secret)},
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.debug("cloud sync returned %s for %s", resp.status_code, email)
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cloud sync unreachable: %s", exc)
        return None
    plan = str(data.get("plan", "")).lower()
    if plan not in ("free", "pro", "lifetime"):
        return None
    token = data.get("token")
    if plan in ("pro", "lifetime") and token:
        # P2: paid responses carry a server-signed token. Verify before
        # trusting — a forged response without the private key fails closed.
        # A paid response with *no* token is still accepted (the billing
        # server may not have a signing key configured); that grant is then
        # written as a local grant, which api.billing.entitlements verifies
        # against a signed local grant record. See apply_remote_plan.
        try:
            from api.billing.entitlement_token import verify_token

            verify_token(str(token), email)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cloud sync token invalid for %s: %s", email, exc)
            return None
    return {
        "plan": plan,
        "status": str(data.get("status", "active")),
        "expires_at": data.get("expires_at"),
        "auto_renew": data.get("auto_renew"),
        "cancel_at_period_end": data.get("cancel_at_period_end"),
        "source": DEFAULT_SOURCE,
        "token": token,
    }


def apply_remote_plan(user_id: str, email: str, remote: Dict[str, Any]) -> Optional[str]:
    """Write a remotely-confirmed plan into the local billing tables.

    Uses api.subscriptions.set_plan so premium_settings / user_subscriptions /
    canonical subscriptions all update in one place. Returns the applied plan
    or None when nothing changed.
    """
    if not remote:
        return None
    plan = remote.get("plan")
    if plan not in ("free", "pro", "lifetime"):
        return None

    # Always record the server-reported renewal state (Phase 4, ADR-004) —
    # even when the plan itself won't change — so the subscription page can
    # render renewal/auto-renew offline and staleness is tracked per email.
    from api.billing.renewal_snapshot import store_snapshot

    store_snapshot(email, remote)

    from api.billing.entitlements import get_plan_for_user

    try:
        current = get_plan_for_user(user_id)
    except Exception:  # noqa: BLE001
        current = "free"

    # Never downgrade a locally-activated key because of a transient/unknown
    # remote state: server "expired/none" only downgrades if local plan came
    # from the same source (cloud sync) — a local product key always wins.
    if plan in ("free",) and current in ("pro", "lifetime"):
        return None

    # Plan unchanged → nothing to do (idempotent re-apply is safe but noisy).
    if plan == current:
        # Refresh the stored token even when the plan is unchanged so weekly
        # phone-home keeps working without a plan flip.
        token = remote.get("token")
        if token and plan in ("pro", "lifetime"):
            try:
                from api.billing.entitlement_token import store_token, verify_token

                verify_token(str(token), email)
                store_token(email, str(token))
            except Exception:  # noqa: BLE001
                pass
        return None

    from api.subscriptions import set_plan

    expires_at = remote.get("expires_at")
    duration_days = None
    if expires_at:
        try:
            from datetime import datetime, timezone
            delta = datetime.fromisoformat(str(expires_at)) - datetime.now(timezone.utc).replace(tzinfo=None)
            duration_days = max(1, int(delta.total_seconds() // 86400))
        except (ValueError, TypeError):
            duration_days = None
    token = remote.get("token")
    provider = "razorpay"
    if token and plan in ("pro", "lifetime"):
        # P2 token path: verify, persist, and mark the row cloud_sync so the
        # guard enforces the signature (DB-only edits grant nothing).
        try:
            from api.billing.entitlement_token import store_token, verify_token

            verify_token(str(token), email)
            store_token(email, str(token))
            provider = "cloud_sync"
        except Exception as exc:  # noqa: BLE001
            logger.debug("cloud sync token rejected for %s: %s", email, exc)
            return None
    # A paid plan written without a token lands as a "razorpay" row. That is
    # no longer trusted local state: api.billing.entitlements requires a valid
    # local grant signature on any non-keygen / non-cloud_sync row, so a
    # hand-edited warehouse row grants nothing.
    set_plan(user_id, plan, duration_days, provider=provider)
    logger.info("cloud sync applied plan=%s for user=%s (email=%s)", plan, user_id, email)
    return plan


def sync_user(user_id: str, email: str, sso_subject: str = "") -> Optional[str]:
    """Convenience: fetch + apply. Returns the applied plan (or None)."""
    if not is_configured():
        return None
    remote = fetch_remote_plan(email)
    if remote is None:
        return None
    return apply_remote_plan(user_id, email, remote)
