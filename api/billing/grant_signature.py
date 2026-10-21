"""Signed local grants — close the "edit the DuckDB file" entitlement bypass.

Background
----------
``api.billing.entitlements._verify_effective`` re-verifies a paid plan when
its row says ``payment_provider='keygen'`` (Ed25519 product key) or
``'cloud_sync'`` (server-minted Ed25519 entitlement token). Both of those
fail closed to ``free`` when the signature does not check out.

Every *other* provider value was treated as "trusted local state" and handed
back verbatim. That is the hole this module closes: a hand-edited row in
``warehouse.duckdb`` with ``payment_provider='razorpay'`` and
``plan_id='plan_lifetime'`` granted a full paid entitlement with no key, no
signature and no server round-trip.

Threat model
------------
The attacker can edit the DuckDB file but does not hold the app's signing
key. Every locally-issued grant therefore carries an HMAC-SHA256 signature
over its canonical value, keyed by a secret stored **outside** the database
(``data/.grant_key``). Editing a row changes the canonical value, so the
signature no longer matches and the grant is refused.

This is defence in depth, not DRM: an attacker with full filesystem access
can also read the key file. What it does close is the exact hole named in
``api.billing.license_guard`` — "an attacker can edit ``warehouse.duckdb``
to ``plan=pro`` without ever owning a key" — and it makes the Ed25519
product-key path the *only* way to obtain a paid plan in a hardened install.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from datetime import datetime
from hashlib import sha256
from typing import Any, Optional

logger = logging.getLogger(__name__)

_KEY_FILENAME = ".grant_key"
_MIN_KEY_LENGTH = 32

#: Where the key came from.  Only the *source* is ever logged — never the
#: key itself.
SOURCE_ENV = "environment"
SOURCE_FILE = "file"
SOURCE_LEGACY = "legacy-file"
SOURCE_GENERATED = "generated"

#: The last source that was logged (so a hot path does not flood the log).
_key_source_state = {"marker": None}


def _key_path() -> str:
    """The historical key location: ``data/.grant_key``."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", _KEY_FILENAME)


def _key_file_path() -> Optional[str]:
    """The configured key file **outside** the data directory (or None).

    ``DATAAGENT_GRANT_KEY_FILE`` wins; ``BILLING_GRANT_KEY_FILE`` (the
    configuration) is the fallback.
    """
    value = (os.environ.get("DATAAGENT_GRANT_KEY_FILE") or "").strip()
    if value:
        return value
    try:
        from config import settings

        configured = (settings.billing.grant_key_file or "").strip()
        if configured:
            return configured
    except Exception:  # noqa: BLE001 — the configuration is optional
        pass
    return None


def grant_key_path() -> str:
    """The file the grant key is read from (and written to)."""
    return _key_file_path() or _key_path()


def _log_key_source(source: str, path: str = "") -> None:
    """Log which source the grant key came from — never the key."""
    marker = f"{source}:{path}"
    if _key_source_state["marker"] == marker:
        return
    _key_source_state["marker"] = marker
    logger.info(
        "The grant key comes from the %s%s",
        source,
        f" ({path})" if path else "",
    )


def _read_key_file(path: str) -> Optional[bytes]:
    """The key stored at *path*, when there is a usable one."""
    try:
        with open(path, "rb") as fh:
            existing = fh.read().strip()
    except (FileNotFoundError, OSError):
        return None
    if existing and len(existing) >= _MIN_KEY_LENGTH:
        return existing
    return None


def _write_key_file(path: str, key: bytes) -> bool:
    """Write *key* to *path* with owner-only permissions."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Create with 0600 from the start so the key is never briefly world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return True
    except OSError as exc:
        logger.warning("grant key could not be persisted (%s); using in-memory key", exc)
        return False


def load_grant_key() -> bytes:
    """Return the local grant-signing key, creating it on first use.

    The source, in order of precedence:

    1. ``DATAAGENT_GRANT_KEY`` — the key itself, injected by a secret
       manager or an orchestrator secret.
    2. ``DATAAGENT_GRANT_KEY_FILE`` — a key file **outside** the data
       directory, so a hosted deployment keeps the signing key off the
       data host (ADR-009 D6).
    3. ``data/.grant_key`` — the historical location (back-compat).
    4. a freshly generated key.

    Wherever the key is written it gets owner-only permissions.  If it
    cannot be persisted the key is still returned (in-memory only) so the
    app keeps working; it simply will not survive a restart, which forces
    a re-grant rather than silently dropping everyone to free.
    """
    env_key = (os.environ.get("DATAAGENT_GRANT_KEY") or "").strip()
    if env_key:
        _log_key_source(SOURCE_ENV)
        return env_key.encode("utf-8")

    configured_file = _key_file_path()
    if configured_file:
        existing = _read_key_file(configured_file)
        if existing:
            _log_key_source(SOURCE_FILE, configured_file)
            return existing

    legacy_path = _key_path()
    existing = _read_key_file(legacy_path)
    if existing:
        _log_key_source(SOURCE_LEGACY, legacy_path)
        return existing

    new_key = secrets.token_hex(32).encode("ascii")
    target = configured_file or legacy_path
    if _write_key_file(target, new_key):
        _log_key_source(SOURCE_GENERATED, target)
    return new_key


def _normalize_value(value: Any) -> str:
    """Render a canonical-value part as a stable string.

    Timestamps reach the signer in two shapes: an ISO string when the grant
    is written, and a ``datetime`` when the row is read back out of DuckDB.
    Both must produce the same bytes, so datetimes are converted to ISO
    format here.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def canonical_grant_value(
    user_id: str,
    plan_id: str,
    payment_provider: str,
    grant_nonce: str,
    current_period_end: Optional[str],
    expires_at: Optional[str],
    trial_ends_at: Optional[str] = None,
) -> str:
    """Canonical string that a grant signature covers.

    Every field that determines *what the user is entitled to* is included:

    - ``user_id`` / ``plan_id`` — prevents transplanting a valid signature
      from one user or plan onto another.
    - ``payment_provider`` — a signature minted for a trial row cannot be
      reused on a row relabelled as a paid provider.
    - ``grant_nonce`` — random per grant, so two identical grants never
      share a signature.
    - ``current_period_end`` / ``expires_at`` / ``trial_ends_at`` — the
      validity window, so an expired grant cannot be resurrected by editing
      the status back to ACTIVE.

    ``status`` is deliberately *not* covered: the lifecycle writes that
    change it (ACTIVE -> PAST_DUE -> EXPIRED, cancel) only ever remove or
    defer entitlement, and the dates above are what actually bound access.
    """
    return "|".join(
        _normalize_value(part)
        for part in (
            user_id,
            plan_id,
            payment_provider,
            grant_nonce,
            current_period_end,
            expires_at,
            trial_ends_at,
        )
    )


def sign_grant(
    user_id: str,
    plan_id: str,
    payment_provider: str,
    grant_nonce: str,
    current_period_end: Optional[str],
    expires_at: Optional[str],
    trial_ends_at: Optional[str] = None,
) -> str:
    """Return the hex HMAC-SHA256 signature for a grant."""
    value = canonical_grant_value(
        user_id, plan_id, payment_provider, grant_nonce,
        current_period_end, expires_at, trial_ends_at,
    )
    return hmac.new(load_grant_key(), value.encode("utf-8"), sha256).hexdigest()


def verify_grant(row: dict[str, Any]) -> bool:
    """True when *row* carries a valid signature for its own contents.

    Rows written before signing existed have no ``grant_signature`` and are
    refused. The one-time migration in :func:`migrate_legacy_grants` signs
    them on first run so real customers are not dropped.
    """
    signature = str(row.get("grant_signature") or "")
    if not signature:
        return False
    expected = sign_grant(
        str(row.get("user_id") or ""),
        str(row.get("plan_id") or ""),
        str(row.get("payment_provider") or ""),
        str(row.get("grant_nonce") or ""),
        row.get("current_period_end"),
        row.get("expires_at"),
        row.get("trial_ends_at"),
    )
    return hmac.compare_digest(signature, expected)


def new_grant_nonce() -> str:
    """Fresh random nonce for a new grant."""
    return secrets.token_hex(16)
