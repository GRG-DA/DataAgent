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
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

PRODUCT = "dataagent"
KEY_PREFIX = "DATAAGENT"
VERSION = 1
SIG_LEN = 64
PLANS = ("pro", "lifetime", "trial")

# Fingerprint of the bundled development keypair (keys/dev_private.pem).
# The admin build refuses to sign with this key so a dev key can never ship.
DEV_PUBLIC_FINGERPRINT = ""


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
    """Refuse to sign with the bundled development keypair."""
    if DEV_PUBLIC_FINGERPRINT and public_fingerprint(private_key.public_key()) == DEV_PUBLIC_FINGERPRINT:
        raise RuntimeError(
            "Refusing to sign with the development key. Generate a real keypair "
            "with `python -m keygen_app.keygen --gen-keys` and set the fingerprint."
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
        print("Set DEV_PUBLIC_FINGERPRINT / PRODUCT fingerprint in config.py.")
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