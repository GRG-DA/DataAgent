"""Key minting — wraps keygen_app.keygen (Ed25519 product keys).

Pro keys are time-boxed (31 days) so renewals rotate the key; lifetime keys
never expire. The private key never leaves the billing host.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from keygen_app.keygen import generate_key

# Import config lazily-friendly: tests can monkeypatch these attributes.
import billing_server.config as cfg


def load_private_key() -> ed25519.Ed25519PrivateKey:
    """Load the signing key from env (base64 PEM) or PEM file."""
    pem: bytes | None = None
    if cfg.LICENSE_PRIVATE_KEY:
        pem = base64.b64decode(cfg.LICENSE_PRIVATE_KEY)
    elif cfg.LICENSE_PRIVATE_KEY_PATH and Path(cfg.LICENSE_PRIVATE_KEY_PATH).exists():
        pem = Path(cfg.LICENSE_PRIVATE_KEY_PATH).read_bytes()
    if not pem:
        raise FileNotFoundError(
            "No license signing key found. Set LICENSE_PRIVATE_KEY (base64 PEM) "
            "or LICENSE_PRIVATE_KEY_PATH, or run "
            "`python -m keygen_app.keygen gen-keys --out billing_server/keys/ed25519_private.pem`."
        )
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("LICENSE_PRIVATE_KEY is not an Ed25519 private key")
    return key


def mint_key(plan: str, email: str = "") -> str:
    """Sign a product key for the plan, bound to the buyer's email.

    The binding is inside the signed payload; the app refuses to activate a
    key on any account other than this email. lifetime => no expiry.
    """
    plan = (plan or "").lower()
    if plan not in cfg.PLANS:
        raise ValueError(f"unknown plan: {plan}")
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("refusing to mint an unbound key: buyer email is required")
    private = load_private_key()
    days = cfg.PLANS[plan].get("days")
    return generate_key(private, plan, days=days, email=email)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Short-lived entitlement tokens (P2) ──────────────────────────


ENTITLEMENT_TOKEN_PRODUCT = "dataagent-ent"
ENTITLEMENT_TOKEN_VERSION = 1
ENTITLEMENT_TOKEN_DAYS = 14


def token_days() -> int:
    """Token lifetime in days (env ENTITLEMENT_TOKEN_DAYS, default 7, 1-30)."""
    try:
        days = int(os.environ.get("ENTITLEMENT_TOKEN_DAYS", str(ENTITLEMENT_TOKEN_DAYS)))
    except ValueError:
        return ENTITLEMENT_TOKEN_DAYS
    return max(1, min(30, days))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def mint_entitlement_token(email: str, plan: str,
                           grant_expires_at: str | None = None,
                           days: int | None = None) -> str:
    """Mint a short-lived signed plan assertion for an email.

    Unlike product keys (one-time, long-lived), entitlement tokens prove the
    billing server *currently* considers this email paid. The app verifies
    the Ed25519 signature with the baked public key, so DB-only edits grant
    nothing on the cloud_sync path. Token lifetime defaults to 7 days and
    forces a weekly phone-home; the underlying grant expiry rides along as
    ``grant_expires_at`` for display.
    """
    import json
    from datetime import datetime, timedelta, timezone

    plan = (plan or "").lower()
    if plan not in ("pro", "lifetime"):
        raise ValueError(f"entitlement token plan must be pro|lifetime, got {plan!r}")
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("entitlement token requires a buyer email")
    now = datetime.now(timezone.utc)
    payload = {
        "product": ENTITLEMENT_TOKEN_PRODUCT,
        "ver": ENTITLEMENT_TOKEN_VERSION,
        "email": email,
        "plan": plan,
        "iat": now.isoformat(),
        "exp": (now + timedelta(days=days or token_days())).isoformat(),
        "grant_expires_at": grant_expires_at,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = load_private_key().sign(raw)
    return f"{_b64url(raw)}.{_b64url(sig)}"


def verify_entitlement_token(token: str, expected_email: str = "") -> dict:
    """Verify an entitlement token server-side (via the private key's public half).

    Mirrors the app-side check in api/billing/entitlement_token.py so the
    ticket endpoint can authenticate token-bearing callers. Raises ValueError.
    """
    import json
    from datetime import datetime, timezone

    if not token or "." not in token:
        raise ValueError("malformed entitlement token")
    raw_b64, sig_b64 = token.rsplit(".", 1)
    pad = lambda s: s + "=" * ((4 - len(s) % 4) % 4)  # noqa: E731
    try:
        raw = base64.urlsafe_b64decode(pad(raw_b64))
        sig = base64.urlsafe_b64decode(pad(sig_b64))
    except Exception as exc:
        raise ValueError("malformed entitlement token encoding") from exc
    try:
        load_private_key().public_key().verify(sig, raw)
    except Exception as exc:
        raise ValueError("invalid entitlement token signature") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed entitlement token payload") from exc
    if payload.get("product") != ENTITLEMENT_TOKEN_PRODUCT:
        raise ValueError("not an entitlement token")
    if payload.get("ver") != ENTITLEMENT_TOKEN_VERSION:
        raise ValueError("unsupported entitlement token version")
    if str(payload.get("plan", "")).lower() not in ("pro", "lifetime"):
        raise ValueError("unknown plan in entitlement token")
    email = str(payload.get("email") or "").strip().lower()
    if not email or (expected_email and email != expected_email.strip().lower()):
        raise ValueError("entitlement token email mismatch")
    try:
        exp_dt = datetime.fromisoformat(str(payload.get("exp") or ""))
    except ValueError as exc:
        raise ValueError("malformed entitlement token expiry") from exc
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    from datetime import datetime as _dt
    if exp_dt <= _dt.now(timezone.utc):
        raise ValueError("entitlement token expired")
    return payload


# ── Feature tickets (P3): per-action server authorization ────────

FEATURE_TICKET_PRODUCT = "dataagent-ticket"
FEATURE_TICKET_VERSION = 1
FEATURE_TICKET_HOURS = 24

#: Which plans may ticket each server-gated feature.
FEATURE_PLANS: dict[str, tuple[str, ...]] = {
    "ip_rotation": ("pro", "lifetime"),
}


def mint_feature_ticket(email: str, feature: str, plan: str) -> str:
    """Mint a 24h signed ticket binding {email, feature, plan}.

    Tickets are checked by value endpoints (e.g. the proxy relay): the app
    cannot mint them, and DB edits cannot forge them — only this server key.
    """
    import json
    from datetime import datetime, timedelta, timezone

    plan = (plan or "").lower()
    email = (email or "").strip().lower()
    allowed = FEATURE_PLANS.get(feature)
    if not allowed:
        raise ValueError(f"unknown feature: {feature!r}")
    if plan not in allowed:
        raise ValueError(f"plan {plan!r} does not cover feature {feature!r}")
    if not email:
        raise ValueError("feature ticket requires an email")
    now = datetime.now(timezone.utc)
    payload = {
        "product": FEATURE_TICKET_PRODUCT,
        "ver": FEATURE_TICKET_VERSION,
        "email": email,
        "feature": feature,
        "plan": plan,
        "iat": now.isoformat(),
        "exp": (now + timedelta(hours=FEATURE_TICKET_HOURS)).isoformat(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = load_private_key().sign(raw)
    return f"{_b64url(raw)}.{_b64url(sig)}"


def verify_feature_ticket(token: str, feature: str = "") -> dict:
    """Verify a feature ticket (server side, via the private key's public half).

    Returns the payload dict; raises ValueError on any failure.
    """
    import json
    from datetime import datetime, timezone

    if not token or "." not in token:
        raise ValueError("malformed feature ticket")
    raw_b64, sig_b64 = token.rsplit(".", 1)
    pad = lambda s: s + "=" * ((4 - len(s) % 4) % 4)  # noqa: E731
    try:
        raw = base64.urlsafe_b64decode(pad(raw_b64))
        sig = base64.urlsafe_b64decode(pad(sig_b64))
    except Exception as exc:
        raise ValueError("malformed feature ticket encoding") from exc
    public_key = load_private_key().public_key()
    try:
        public_key.verify(sig, raw)
    except Exception as exc:
        raise ValueError("invalid feature ticket signature") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed feature ticket payload") from exc
    if payload.get("product") != FEATURE_TICKET_PRODUCT:
        raise ValueError("not a feature ticket")
    if payload.get("ver") != FEATURE_TICKET_VERSION:
        raise ValueError("unsupported feature ticket version")
    if feature and payload.get("feature") != feature:
        raise ValueError("feature ticket is for another feature")
    try:
        exp_dt = datetime.fromisoformat(str(payload.get("exp") or ""))
    except ValueError as exc:
        raise ValueError("malformed feature ticket expiry") from exc
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    from datetime import datetime as _dt
    if exp_dt <= _dt.now(timezone.utc):
        raise ValueError("feature ticket expired")
    return payload


def public_fingerprint() -> str:
    """SHA-256[:16] of the raw public key — surfaced so ops can verify the baked key matches."""
    raw = load_private_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(raw).hexdigest()[:16]
