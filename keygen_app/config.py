"""Key Manager configuration.

The customer build bakes the PUBLIC key (and its fingerprint) into the
binary. The admin build additionally loads the PRIVATE key from a PEM file
or the LICENSE_PRIVATE_KEY env var, and refuses to run if the key is the
bundled development keypair.

.. warning::

   RELEASE STEP — the baked key must be regenerated before the first sale.

   The public key compiled into ``keygen_app/baked_public_key.py`` is matched
   against ``_HARDCODED_FINGERPRINT`` below (currently ``deb8b1e5d88e42e4``).
   A frozen customer build verifies licences against that baked key and raises
   *"Baked public key fingerprint mismatch — build tampered."* if the two ever
   disagree — so the two MUST be updated together.

   The private half of the currently baked key is **not** in this tree
   (``keygen_app/keys/`` is empty and ``*.pem`` is gitignored). That means the
   currently baked key **cannot sign anything**: no licence can be minted with
   it, and no key already issued with it could be re-derived.

   To make the product sellable, do ALL of the following, in order, BEFORE the
   first release build:

   1. Generate a fresh Ed25519 keypair::

          python -m keygen_app.keygen gen-keys

      This writes ``keygen_app/keys/ed25519_private.pem`` (gitignored — never
      commit it) and ``ed25519_public.pem``.

   2. Compute the fingerprint of the new public key::

          python -c "from keygen_app.keygen import public_fingerprint; \\
                     from keygen_app.config import load_public_key; \\
                     print(public_fingerprint(load_public_key()))"

   3. Write the new public key into ``keygen_app/baked_public_key.py``
      (``PUBLIC_KEY_PEM``) and update ``_HARDCODED_FINGERPRINT`` in THIS file
      to the new fingerprint. Both must change in the same commit.

   4. Keep the private key OUT of the repository and OUT of the installer. It
      belongs in a secret manager or on the operator's machine only.

   5. Verify before shipping::

          python -m pytest tests/test_license_guard.py tests/test_packaging.py -q

   The fingerprint check at ``load_public_key()`` makes step 3 mandatory: a
   mismatched pair bricks every licence check in the shipped app.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from keygen_app.baked_public_key import PUBLIC_KEY_PEM
from keygen_app.keygen import (
    DEV_PUBLIC_FINGERPRINT,
    dev_public_fingerprints,
    public_fingerprint,
)

BASE_DIR = Path(__file__).parent
KEYS_DIR = BASE_DIR / "keys"
PRIVATE_KEY_PATH = KEYS_DIR / "ed25519_private.pem"
PUBLIC_KEY_PATH = KEYS_DIR / "ed25519_public.pem"

# Fingerprint of the key this build is allowed to sign with. The build
# scripts fail if the on-disk key does not match this fingerprint.
PRODUCT_PUBLIC_FINGERPRINT = os.environ.get(
    "LICENSE_PUBLIC_FINGERPRINT", "deb8b1e5d88e42e4"
)

# Hardcoded production fingerprint — the frozen (.exe) build always checks
# against this, ignoring the LICENSE_PUBLIC_FINGERPRINT env override so an
# attacker cannot point the exe at their own keypair via environment.
_HARDCODED_FINGERPRINT = "deb8b1e5d88e42e4"

# SHA-256 of the placeholder passcode. When the admin hash is still the
# default, check_admin_passcode() fails closed (returns False for everything)
# so the default can never unlock the Key Manager.
_DEFAULT_PASSCODE_HASH = hashlib.sha256(b"change-me").hexdigest()

# Admin passcode (SHA-256 hex). Set via env or replace the constant.
ADMIN_PASSCODE_HASH = os.environ.get(
    "KEYGEN_ADMIN_PASSCODE_HASH",
    hashlib.sha256(b"change-me").hexdigest(),
)

# Hosted redemption server used for online activation + sync.
REDEEM_SERVER_URL = os.environ.get("REDEEM_SERVER_URL", "http://localhost:8010")


def is_frozen() -> bool:
    """True inside a PyInstaller .exe (sys.frozen is set)."""
    return bool(getattr(sys, "frozen", False))


def _expected_fingerprint() -> str:
    """Fingerprint the running build trusts.

    Frozen builds ignore the env override (anti-crack: env cannot redirect
    the exe to an attacker keypair). Dev/test runs keep the override for
    fixture keypairs.
    """
    if is_frozen():
        return _HARDCODED_FINGERPRINT
    return PRODUCT_PUBLIC_FINGERPRINT


def load_public_key() -> ed25519.Ed25519PublicKey:
    """Load the baked-in public key.

    Frozen (.exe): baked constant only + fingerprint must match the
    hardcoded production fingerprint — env/file overrides are ignored so
    setting LICENSE_PUBLIC_KEY cannot redirect verification to an
    attacker key. Raises RuntimeError on tamper.
    Dev/test: env override > PEM file > compiled constant (existing).
    """
    if is_frozen():
        if not PUBLIC_KEY_PEM:
            raise FileNotFoundError("Baked public key missing in frozen build.")
        key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode())
        fp = public_fingerprint(key)
        if fp != _HARDCODED_FINGERPRINT:
            raise RuntimeError(
                "Baked public key fingerprint mismatch — build tampered."
            )
        if fp in dev_public_fingerprints():
            raise RuntimeError("Frozen build uses a development key. Refusing.")
        return key
    pem = os.environ.get("LICENSE_PUBLIC_KEY")
    if pem:
        return serialization.load_pem_public_key(pem.encode())
    if PUBLIC_KEY_PATH.exists():
        return serialization.load_pem_public_key(PUBLIC_KEY_PATH.read_bytes())
    if PUBLIC_KEY_PEM:
        return serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode())
    raise FileNotFoundError(
        f"Public key not found at {PUBLIC_KEY_PATH}. Generate a keypair first "
        "(`python -m keygen_app.keygen gen-keys`)."
    )


def public_fingerprint_of() -> str:
    return public_fingerprint(load_public_key())


def check_public_key_fingerprint() -> str:
    """Return the loaded public key fingerprint, raising on dev/tampered key.

    Frozen builds always enforce the hardcoded production fingerprint.
    Dev/test runs enforce it for file/baked keys, but an explicit
    LICENSE_PUBLIC_KEY env override (fixture keypairs) only checks the dev
    fingerprint so tests can inject their own keypair.
    """
    fp = public_fingerprint_of()
    if fp in dev_public_fingerprints():
        raise RuntimeError(
            "This build is using the development public key. Refusing to run."
        )
    if not is_frozen() and os.environ.get("LICENSE_PUBLIC_KEY"):
        return fp
    expected = _expected_fingerprint()
    if expected and fp != expected:
        raise RuntimeError(
            "Public key fingerprint mismatch — untrusted signing key."
        )
    return fp


def check_admin_passcode(passcode: str) -> bool:
    # Fail closed while the placeholder hash is still configured: the
    # default passcode must never unlock the Key Manager.
    import hmac as _hmac

    if ADMIN_PASSCODE_HASH == _DEFAULT_PASSCODE_HASH:
        return False
    return _hmac.compare_digest(
        hashlib.sha256(passcode.encode()).hexdigest(), ADMIN_PASSCODE_HASH
    )