"""Central accounts (ADR-002): email + bcrypt password + Ed25519 session tokens.

Scope discipline: this store holds ONLY the credential (email, hash) and links
to purchase records via ``email`` in ``orders``. No warehouse, chats, or file
data — those never leave the user's machine.

Session tokens are signed with the same licensing Ed25519 key that signs
entitlement tokens, so the app has a single trust anchor (the baked public
key). Login is rate-limited in-process: the billing server is a single-instance
SQLite deployment, so a mutex-guarded sliding window needs no extra infra.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional, Tuple

import bcrypt

from fastapi import HTTPException, Request

from billing_server import keymint

SESSION_TOKEN_PRODUCT = "dataagent-session"
SESSION_TOKEN_VERSION = 1
SESSION_TOKEN_DAYS = 30
EMAIL_VERIFICATION_HOURS = 24

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
# bcrypt only considers the first 72 bytes of a password; anything longer is
# rejected up front (raising from bcrypt itself would surface as a 500).
MAX_PASSWORD_BYTES = 72

# endpoint bucket -> (max requests, window seconds), keyed by client IP
LOGIN_LIMIT: Tuple[int, float] = (10, 60.0)
REGISTER_LIMIT: Tuple[int, float] = (5, 60.0)
#: Checkout opens a provider-side order (a 5-min UPI session + hosted page)
#: per call — generous for real buyers sharing one NAT IP, still a real cap
#: for a spammer hammering FamGateway's create-order API on our key.
CREATE_ORDER_LIMIT: Tuple[int, float] = (60, 60.0)
RESEND_VERIFICATION_LIMIT: Tuple[int, float] = (3, 300.0)
VERIFY_EMAIL_LIMIT: Tuple[int, float] = (10, 60.0)

_LIMITS: Dict[str, Tuple[int, float]] = {
    "login": LOGIN_LIMIT,
    "register": REGISTER_LIMIT,
    # cancel and unknown buckets fall back to REGISTER_LIMIT below.
    "create-order": CREATE_ORDER_LIMIT,
    "resend-verification": RESEND_VERIFICATION_LIMIT,
    "verify-email": VERIFY_EMAIL_LIMIT,
}

_rate_lock = threading.Lock()
_hits: Dict[str, Deque[float]] = defaultdict(deque)


# ── passwords ────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), (password_hash or "").encode())
    except Exception:  # noqa: BLE001 — malformed hash never authenticates
        return False


def validate_password(password: str) -> None:
    """Raise ValueError when the password fails the server-side floor.

    The web client enforces the stricter 12+ complexity policy; the server
    keeps a minimal floor (length) so API callers cannot register "short".
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password must be at most {MAX_PASSWORD_BYTES} bytes "
            "(multi-byte characters count as more than one)"
        )


_dummy_hash: Optional[bytes] = None


def dummy_password_hash() -> bytes:
    """A lazily-built valid bcrypt hash used to keep unknown-email logins as
    slow as real ones (anti user-enumeration timing signal)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(b"invalid-dummy-password", bcrypt.gensalt())
    return _dummy_hash


def mask_email(email: str) -> str:
    """Log-safe identifier: keeps one character of local part and domain."""
    local, _, domain = (email or "").partition("@")
    dom, _, tld = domain.rpartition(".")
    if local and tld:
        return f"{local[0]}***@{dom[:1]}***.{tld}"
    return "***"


# ── session tokens (Ed25519, mirrors keymint token style) ────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def new_session_token() -> str:
    """Opaque website session token (32 random bytes, URL-safe)."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Only the SHA-256 digest is persisted; a DB leak cannot replay sessions."""
    return hashlib.sha256((token or "").encode()).hexdigest()


def session_expires_at(days: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=days if days is not None else SESSION_TOKEN_DAYS)).isoformat()


def new_email_verification_token() -> str:
    """Single-use, opaque email-ownership token (32 random bytes)."""
    return secrets.token_urlsafe(32)


def hash_email_verification_token(token: str) -> str:
    """Persist only the digest; a database leak cannot replay a link."""
    return hashlib.sha256((token or "").encode()).hexdigest()


def email_verification_expires_at(hours: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    return (now + timedelta(hours=hours if hours is not None else EMAIL_VERIFICATION_HOURS)).isoformat()


def email_verification_link(token: str) -> str:
    """Fragment links keep the token out of HTTP logs and Referer headers."""
    from billing_server import config as cfg

    base = (getattr(cfg, "BILLING_SITE_URL", "") or "https://data-agent.me").rstrip("/")
    return f"{base}/account.html#verify={token}"


def mint_session_token(email: str, user_id: str, days: Optional[int] = None) -> str:
    """Sign a session assertion for an account. Raises FileNotFoundError when
    the signing key is unavailable (fail-closed: no key, no session)."""
    email = (email or "").strip().lower()
    if not email or not user_id:
        raise ValueError("session token requires email and user_id")
    now = datetime.now(timezone.utc)
    payload = {
        "product": SESSION_TOKEN_PRODUCT,
        "ver": SESSION_TOKEN_VERSION,
        "email": email,
        "user_id": user_id,
        "iat": now.isoformat(),
        "exp": (now + timedelta(days=days if days is not None else SESSION_TOKEN_DAYS)).isoformat(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = keymint.load_private_key().sign(raw)
    return f"{_b64url(raw)}.{_b64url(sig)}"


def verify_session_token(token: str) -> dict:
    """Verify a session token. Returns the payload; raises ValueError on failure."""
    if not token or "." not in token:
        raise ValueError("malformed session token")
    raw_b64, sig_b64 = token.rsplit(".", 1)
    pad = lambda s: s + "=" * ((4 - len(s) % 4) % 4)  # noqa: E731
    try:
        raw = base64.urlsafe_b64decode(pad(raw_b64))
        sig = base64.urlsafe_b64decode(pad(sig_b64))
    except Exception as exc:
        raise ValueError("malformed session token encoding") from exc
    try:
        keymint.load_private_key().public_key().verify(sig, raw)
    except Exception as exc:
        raise ValueError("invalid session token signature") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed session token payload") from exc
    if payload.get("product") != SESSION_TOKEN_PRODUCT:
        raise ValueError("not a session token")
    if payload.get("ver") != SESSION_TOKEN_VERSION:
        raise ValueError("unsupported session token version")
    if not payload.get("email") or not payload.get("user_id"):
        raise ValueError("session token missing identity")
    try:
        exp_dt = datetime.fromisoformat(str(payload.get("exp") or ""))
    except ValueError as exc:
        raise ValueError("malformed session token expiry") from exc
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    if exp_dt <= datetime.now(timezone.utc):
        raise ValueError("session token expired")
    return payload


# ── login/register rate limiting (in-process, single instance) ───────


def reset_rate_limits() -> None:
    with _rate_lock:
        _hits.clear()


def check_rate_limit(request: Request, bucket: str) -> None:
    limit, window = _LIMITS.get(bucket, REGISTER_LIMIT)
    ip = request.client.host if request.client else "unknown"
    key = f"{bucket}:{ip}"
    now = time.monotonic()
    with _rate_lock:
        if len(_hits) > 4096:  # opportunistic hygiene: drop stale keys
            for stale in [k for k, dq in _hits.items() if not dq or now - dq[-1] > window]:
                _hits.pop(stale, None)
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(
                status_code=429,
                detail="too many requests — try again shortly",
                headers={"Retry-After": str(int(window))},
            )
        q.append(now)
