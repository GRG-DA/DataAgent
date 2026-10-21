"""DataAgent offline key engine.

Ed25519-signed one-time product keys.

Key format:
    DATAAGENT-<base32(payload_json_bytes + 64-byte_signature), grouped 5 chars>

Payload (compact JSON, canonical key order):
    {"product":"dataagent","plan":"pro|lifetime|trial","expiresAt":"ISO"|null,
     "nonce":"<16 hex>","ver":1,"email":"buyer@example.com"}

The ``email`` field binds the key to the account that bought it, so a key
cannot be shared between accounts. It is optional in the format (legacy
keys validate without it) but the activation endpoint refuses unbound keys.

The signature covers the exact payload bytes, so validation must re-serialize
the payload with the same canonical form (json.dumps with sort_keys=True and
the same separators).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)

PRODUCT = "dataagent"
KEY_PREFIX = "DATAAGENT"
VERSION = 1
SIG_LEN = 64
PLANS = ("pro", "lifetime", "trial")

# ── the development-key guard ───────────────────────────────────────────
#
# Fingerprint of the development keypair (the key the admin build must
# NEVER sign with).  It is resolved from three places, in this order:
#
#   1. ``DEV_PUBLIC_FINGERPRINT`` below (the source-tree default),
#   2. the ``DATAAGENT_DEV_PUBLIC_FINGERPRINT`` environment variable,
#   3. the bundled development keypair ``keygen_app/keys/dev_private.pem``,
#      if the operator unpacked one there.
#
# EMPTY means "not configured" — it does NOT disable the guard.  See
# ``assert_not_dev_key``.
#
# ── RELEASE STEP R1 — MANDATORY BEFORE THE FIRST COMMERCIAL SALE ───────
#
# The public key baked into the shipped app (``keygen_app/baked_public_key.py``)
# is the public half of a REAL Ed25519 key pair.  Before the first paid
# sale that pair MUST be regenerated, because the shipped app trusts it:
#
#   1. ``python -m keygen_app.keygen gen-keys --out <new-private>.pem``
#      (keep the private key OUT of the repository and out of logs),
#   2. re-bake ``keygen_app/baked_public_key.py`` with the new public PEM
#      (``PUBLIC_KEY_PEM``),
#   3. update the hardcoded fingerprint in ``keygen_app/config.py`` (the
#      ``_HARDCODED_FINGERPRINT`` constant and ``PRODUCT_PUBLIC_FINGERPRINT``)
#      to the new public key's fingerprint,
#   4. set ``DATAAGENT_DEV_PUBLIC_FINGERPRINT`` (or ``DEV_PUBLIC_FINGERPRINT``)
#      to the fingerprint of the OLD / development keypair, so the guard
#      below refuses to sign with it.
#
# Until step 1-3 are done, anybody who holds the private half of the
# current pair can mint valid licences.  This procedure is also the
# release checklist item in docs/LAUNCH-FIX-PLAN.md (C8).
DEV_PUBLIC_FINGERPRINT = ""

#: Environment variable carrying the development key's fingerprint (for
#: deployments that keep it out of the source tree).
DEV_FINGERPRINT_ENV = "DATAAGENT_DEV_PUBLIC_FINGERPRINT"

#: The bundled development keypair, when there is one.
DEV_PRIVATE_KEY_PATH = Path(__file__).parent / "keys" / "dev_private.pem"

#: Explicit, loud opt-in that allows signing with the development key.
#: Set it to 1 only on a machine that must mint throw-away keys.
ALLOW_DEV_KEY_SIGNING_ENV = "DATAAGENT_ALLOW_DEV_KEY_SIGNING"

#: Set it to 1 to make an unknown development fingerprint a hard refusal
#: even outside a release build (the default for a frozen / production
#: build).
STRICT_SIGNING_ENV = "DATAAGENT_STRICT_KEY_SIGNING"


def _truthy_env(name: str) -> bool:
    """True when the environment variable is switched on."""
    return str(os.environ.get(name, "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _release_context() -> bool:
    """True when this build must not sign with an unverified key.

    A packaged (PyInstaller) build and a production deployment both have to
    fail closed: there the operator is shipping licences.
    """
    if getattr(sys, "frozen", False):
        return True
    if _truthy_env(STRICT_SIGNING_ENV):
        return True
    return str(os.environ.get("APP_ENV", "") or "").strip().lower() == "production"


def dev_public_fingerprints() -> set:
    """Every fingerprint that identifies a development key.

    Empty when the development key is unknown — which is NOT the same as
    "there is no development key": the guard then fails closed (see
    ``assert_not_dev_key``).
    """
    found = set()
    if DEV_PUBLIC_FINGERPRINT:
        found.add(DEV_PUBLIC_FINGERPRINT.strip().lower())
    env_fp = str(os.environ.get(DEV_FINGERPRINT_ENV, "") or "").strip().lower()
    if env_fp:
        found.add(env_fp)
    try:
        if DEV_PRIVATE_KEY_PATH.exists():
            dev_key = load_private_key(DEV_PRIVATE_KEY_PATH, env_var="")
            found.add(public_fingerprint(dev_key.public_key()))
    except Exception:  # noqa: BLE001 — an unreadable dev key is not fatal here
        pass
    return found


def _dev_banner(reason: str) -> None:
    """Print the loud banner that says a development key is in use."""
    banner = (
        "\n"
        + "=" * 72 + "\n"
        + "  DEV MODE — you are signing with a DEVELOPMENT key.\n"
        + f"  Reason: {reason}\n"
        + "  Any key signed with it is NOT a valid purchase. Never ship it,\n"
        + "  never hand it to a customer, and never bake its public key into\n"
        + "  the product (see the release procedure in keygen_app/keygen.py).\n"
        + "=" * 72 + "\n"
    )
    print(banner, file=sys.stderr, flush=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def public_fingerprint(public_key: ed25519.Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(raw).hexdigest()[:16]


def generate_keypair(private_path: Path) -> tuple[ed25519.Ed25519PrivateKey, str]:
    """Create a fresh Ed25519 keypair and write the private key PEM to disk."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(pem)
    os.chmod(private_path, 0o600)
    return private_key, public_fingerprint(private_key.public_key())


def load_private_key(private_path: Path | None = None, env_var: str = "LICENSE_PRIVATE_KEY") -> ed25519.Ed25519PrivateKey:
    """Load the signing key from PEM file or base64(PEM) env var."""
    pem: bytes | None = None
    if env_var and os.environ.get(env_var):
        pem = base64.b64decode(os.environ[env_var])
    elif private_path and private_path.exists():
        pem = private_path.read_bytes()
    if not pem:
        raise FileNotFoundError(
            f"No signing key found. Provide {env_var} or a PEM at {private_path}. "
            "Run `python -m keygen_app.keygen --gen-keys` to create one."
        )
    return serialization.load_pem_private_key(pem, password=None)


def load_public_key(public_pem: str | bytes) -> ed25519.Ed25519PublicKey:
    if isinstance(public_pem, str):
        public_pem = public_pem.encode()
    return serialization.load_pem_public_key(public_pem)


def assert_not_dev_key(private_key: ed25519.Ed25519PrivateKey) -> None:
    """Refuse to sign with the bundled development keypair.

    The check is never silently disabled:

    * the signing key matches a known development fingerprint -> refuse;
    * no development fingerprint is known at all -> the guard cannot prove
      the key is safe, so a release build (frozen, or ``APP_ENV=production``,
      or ``DATAAGENT_STRICT_KEY_SIGNING=1``) refuses as well;
    * only a development checkout signs on, and it says so loudly.

    ``DATAAGENT_ALLOW_DEV_KEY_SIGNING=1`` is the explicit opt-in: it signs
    with the development key and prints a loud banner.
    """
    fingerprint = public_fingerprint(private_key.public_key())
    dev_fingerprints = dev_public_fingerprints()
    allowed = _truthy_env(ALLOW_DEV_KEY_SIGNING_ENV)

    if fingerprint in dev_fingerprints:
        if allowed:
            _dev_banner("the signing key IS the development key")
            return
        raise RuntimeError(
            "Refusing to sign with the development key. Generate a real "
            "keypair with `python -m keygen_app.keygen gen-keys`, and set "
            "the development key's fingerprint (DEV_PUBLIC_FINGERPRINT or "
            f"{DEV_FINGERPRINT_ENV}) so this guard knows what to refuse."
        )

    if not dev_fingerprints:
        if allowed:
            _dev_banner(
                "the development key's fingerprint is not configured, so the "
                "signing key could not be verified"
            )
            return
        if _release_context():
            raise RuntimeError(
                "Refusing to sign: the development key's fingerprint is not "
                "configured, so the signing key cannot be verified. Set "
                f"{DEV_FINGERPRINT_ENV} (or DEV_PUBLIC_FINGERPRINT) to the "
                "fingerprint of the development keypair, or set "
                f"{ALLOW_DEV_KEY_SIGNING_ENV}=1 to sign with this key anyway."
            )
        logger.warning(
            "The development key's fingerprint is not configured — the "
            "development-key guard cannot verify the signing key. Set %s "
            "before you ship.",
            DEV_FINGERPRINT_ENV,
        )


def _canonical_payload(
    plan: str,
    expires_at: datetime | None,
    nonce: str,
    email: str | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "product": PRODUCT,
        "plan": plan,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "nonce": nonce,
        "ver": VERSION,
    }
    if email:
        payload["email"] = email.strip().lower()
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def generate_key(
    private_key: ed25519.Ed25519PrivateKey,
    plan: str,
    days: int | None = None,
    nonce: str | None = None,
    email: str | None = None,
) -> str:
    """Sign and encode a product key.

    plan: 'pro' | 'lifetime' | 'trial'
    days: validity length. None or 0 => lifetime (expiresAt null).
    email: buyer email the key is bound to (lowercased in the payload).
           Omit only for legacy/tooling keys — the app refuses to activate
           keys without an owner binding.
    """
    if plan not in PLANS:
        raise ValueError(f"plan must be one of {PLANS}, got {plan!r}")
    assert_not_dev_key(private_key)
    nonce = nonce or secrets.token_hex(8)
    expires_at: datetime | None = None
    if days:
        expires_at = _utcnow() + timedelta(days=days)
    payload = _canonical_payload(plan, expires_at, nonce, email)
    signature = private_key.sign(payload)
    body = base64.b32encode(payload + signature).decode().rstrip("=")
    grouped = "-".join(body[i : i + 5] for i in range(0, len(body), 5))
    return f"{KEY_PREFIX}-{grouped}"


def _normalize_key(key: str) -> str:
    return key.strip().upper().replace("-", "").replace(" ", "")


def parse_key(key: str) -> tuple[bytes, bytes]:
    """Return (payload_bytes, signature_bytes) from a raw key string."""
    norm = _normalize_key(key)
    if not norm.startswith(KEY_PREFIX):
        raise ValueError("not a DataAgent key")
    body = norm[len(KEY_PREFIX) :]
    if not body:
        raise ValueError("empty key body")
    try:
        raw = base64.b32decode(body + "=" * ((8 - len(body) % 8) % 8))
    except Exception as exc:  # binascii.Error / ValueError
        raise ValueError("malformed key encoding") from exc
    if len(raw) <= SIG_LEN:
        raise ValueError("key too short")
    return raw[:-SIG_LEN], raw[-SIG_LEN:]


def validate_key(key: str, public_key: ed25519.Ed25519PublicKey) -> dict[str, Any]:
    """Verify signature and return the decoded payload dict.

    Raises ValueError on any failure (bad signature, unknown product,
    expired, unsupported version).
    """
    payload_bytes, signature = parse_key(key)
    try:
        public_key.verify(signature, payload_bytes)
    except InvalidSignature as exc:
        raise ValueError("invalid signature") from exc
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed payload") from exc
    if payload.get("product") != PRODUCT:
        raise ValueError("key is not for this product")
    if payload.get("ver") != VERSION:
        raise ValueError("unsupported key version")
    plan = payload.get("plan")
    if plan not in PLANS:
        raise ValueError("unknown plan")
    expires_at = payload.get("expiresAt")
    if expires_at is not None:
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise ValueError("malformed expiry") from exc
        if expires_at <= _utcnow():
            raise ValueError("key has expired")
    return payload


def bound_email(payload: dict[str, Any]) -> str:
    """Email a validated key is bound to, or "" for legacy unbound keys."""
    return str(payload.get("email") or "").strip().lower()


def redeem_key(
    key: str,
    public_key: ed25519.Ed25519PublicKey,
    machine_id: str | None = None,
) -> dict[str, Any]:
    """Validate a key and return the redemption result dict."""
    payload = validate_key(key, public_key)
    return {
        "status": "redeemed",
        "plan": payload["plan"],
        "expires_at": payload.get("expiresAt"),
        "key_hash": hashlib.sha256(key.encode()).hexdigest()[:16],
        "machine_id": machine_id,
    }


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="keygen", description="DataAgent key tool")
    sub = parser.add_subparsers(dest="cmd")

    gen = sub.add_parser("gen-keys", help="create a signing keypair")
    gen.add_argument("--out", default=str(Path(__file__).parent / "keys" / "ed25519_private.pem"))

    mk = sub.add_parser("make", help="generate a product key")
    mk.add_argument("--key", default=str(Path(__file__).parent / "keys" / "ed25519_private.pem"))
    mk.add_argument("--plan", choices=PLANS, default="pro")
    mk.add_argument("--days", type=int, default=None, help="validity in days (omit for lifetime)")
    mk.add_argument("--email", required=True,
                    help="buyer email the key is bound to (activation requires it)")

    chk = sub.add_parser("check", help="validate a key")
    chk.add_argument("key")
    chk.add_argument("--pub", default=str(Path(__file__).parent / "keys" / "ed25519_public.pem"))

    args = parser.parse_args()

    if args.cmd == "gen-keys":
        private_key, fp = generate_keypair(Path(args.out))
        pub = private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        pub_path = Path(args.out).with_name("ed25519_public.pem")
        pub_path.write_bytes(pub)
        print(f"private key: {args.out}")
        print(f"public  key: {pub_path}")
        print(f"public fingerprint: {fp}")
        print(
            "Keep the private key OUT of the repository and out of logs. "
            "Set DEV_PUBLIC_FINGERPRINT (or the "
            "DATAAGENT_DEV_PUBLIC_FINGERPRINT environment variable) to the "
            "fingerprint of the DEVELOPMENT keypair, so the admin build "
            "refuses to sign with it."
        )
    elif args.cmd == "make":
        private_key = load_private_key(Path(args.key))
        print(generate_key(private_key, args.plan, args.days, email=args.email))
    elif args.cmd == "check":
        public_key = load_public_key(Path(args.pub).read_bytes())
        try:
            payload = validate_key(args.key, public_key)
            print("VALID", json.dumps(payload, sort_keys=True))
        except ValueError as exc:
            print("INVALID:", exc)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()