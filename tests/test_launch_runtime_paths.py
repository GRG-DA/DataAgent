"""Launch-critical runtime path resolution (LAUNCH-FIX-PLAN D2 + D3).

The frozen Windows build installs to ``C:\\Program Files\\DataAgent`` — a
directory a normal (non-elevated) user cannot write to. The JWT signing
secret and the paid-plan grant key used to be resolved from ``__file__``, so
in a frozen build they landed *inside* the bundle
(``<install>\\ _internal\\data\\...``), the write raised ``OSError``, the
error was swallowed, and a fresh random value was minted on every launch:

* **D2** — a new JWT secret invalidates every session cookie and CSRF token
  issued by the previous run, so the user is silently logged out every time
  they reopen the app.
* **D3** — a new grant key invalidates every HMAC-signed paid-plan grant
  row, so a *paying customer loses their entitlement on every restart*.

Both must resolve to the writable per-user runtime directory and must
survive a restart. These tests pin that guarantee.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from api import runtime_paths


# ── helpers ────────────────────────────────────────────────────────────


def _same_path(left: Path, right: Path) -> bool:
    """True when both paths point at the same location (case-insensitive on Windows)."""
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_inside(child: Path, parent: Path) -> bool:
    """True when *child* is *parent* or lives under it."""
    child_norm = os.path.normcase(os.path.abspath(str(child)))
    parent_norm = os.path.normcase(os.path.abspath(str(parent)))
    if child_norm == parent_norm:
        return True
    return child_norm.startswith(parent_norm.rstrip("\\/") + os.sep)


def _clear_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every env var that short-circuits the file-based lookups."""
    for name in (
        "DATAAGENT_DATA_DIR",
        "JWT_SECRET",
        "DATAAGENT_GRANT_KEY",
        "DATAAGENT_GRANT_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def _load_jwt_secret() -> str:
    """Import ``api.auth`` lazily and return its secret loader."""
    module = importlib.import_module("api.auth")
    return module._load_jwt_secret()


# ── api/runtime_paths.py ───────────────────────────────────────────────


def test_runtime_data_dir_is_the_cwd_in_a_source_checkout(monkeypatch, tmp_path):
    """A source checkout (``python run.py``) keeps the runtime dir at the cwd.

    Nothing may change for developers: ``data/.jwt_secret`` has to stay at
    ``<repo>/data/.jwt_secret`` exactly as it is today.
    """
    _clear_secret_env(monkeypatch)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)

    assert _same_path(runtime_paths.runtime_data_dir(), checkout)


def test_dataagent_data_dir_env_var_wins(monkeypatch, tmp_path):
    """``DATAAGENT_DATA_DIR`` overrides the cwd."""
    _clear_secret_env(monkeypatch)
    override = tmp_path / "override"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(override))

    assert _same_path(runtime_paths.runtime_data_dir(), override)


def test_dataagent_data_dir_env_var_wins_over_the_frozen_fallback(monkeypatch, tmp_path):
    """The env var also wins when the build is frozen."""
    _clear_secret_env(monkeypatch)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    override = tmp_path / "override"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(override))

    assert _same_path(runtime_paths.runtime_data_dir(), override)


def test_frozen_runtime_dir_is_never_inside_the_bundle(monkeypatch, tmp_path):
    """A frozen build must never resolve state inside the PyInstaller bundle."""
    _clear_secret_env(monkeypatch)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    resolved = runtime_paths.runtime_data_dir()

    assert not _is_inside(resolved, bundle), (
        "the frozen runtime dir must never live inside the bundle "
        f"(resolved to {resolved})"
    )


def test_frozen_runtime_dir_ignores_an_override_pointing_into_the_bundle(
    monkeypatch, tmp_path,
):
    """Even an explicit override may not put writable state in the bundle."""
    _clear_secret_env(monkeypatch)
    bundle = tmp_path / "bundle"
    (bundle / "data").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(bundle / "data"))

    resolved = runtime_paths.runtime_data_dir()

    assert not _is_inside(resolved, bundle), (
        "the frozen runtime dir must never live inside the bundle "
        f"(resolved to {resolved})"
    )


def test_runtime_data_dir_creates_nothing(monkeypatch, tmp_path):
    """The path resolver is pure — it must not create directories."""
    _clear_secret_env(monkeypatch)
    target = tmp_path / "never-created"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(target))

    runtime_paths.runtime_data_dir()

    assert not target.exists()


def test_runtime_file_does_not_create_parents(monkeypatch, tmp_path):
    """``runtime_file`` only computes a path."""
    _clear_secret_env(monkeypatch)
    target = tmp_path / "never-created"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(target))

    resolved = runtime_paths.runtime_file("data", ".jwt_secret")

    assert _same_path(resolved, target / "data" / ".jwt_secret")
    assert not target.exists()


def test_runtime_file_is_inside_the_runtime_dir(monkeypatch, tmp_path):
    """The resolved secret path is under the runtime dir and is writable there."""
    _clear_secret_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime_dir))

    resolved = runtime_paths.runtime_file("data", ".jwt_secret")

    assert _is_inside(resolved, runtime_dir)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text("probe", encoding="utf-8")
    assert resolved.read_text(encoding="utf-8") == "probe"


# ── D2: the JWT secret must persist across launches ────────────────────


def test_jwt_secret_is_written_to_the_runtime_dir(monkeypatch, tmp_path):
    """The secret lands in ``<runtime dir>/data/.jwt_secret``."""
    _clear_secret_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime_dir))

    secret = _load_jwt_secret()

    expected = runtime_dir / "data" / ".jwt_secret"
    assert expected.exists(), f"the JWT secret was not persisted at {expected}"
    assert expected.read_text(encoding="utf-8").strip() == secret


def test_jwt_secret_survives_a_restart(monkeypatch, tmp_path):
    """D2 regression: the second launch must reuse the persisted secret.

    Before the fix the secret was minted fresh on every launch, so every
    session cookie and CSRF token from the previous run became invalid and
    the user was silently logged out.
    """
    _clear_secret_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime_dir))

    first = _load_jwt_secret()
    second = _load_jwt_secret()

    assert first == second
    assert (runtime_dir / "data" / ".jwt_secret").exists()


def test_jwt_secret_in_a_frozen_build_stays_out_of_the_bundle(monkeypatch, tmp_path):
    """A frozen build persists the secret in the per-user dir, not the bundle."""
    _clear_secret_env(monkeypatch)
    bundle = tmp_path / "bundle"
    (bundle / "data").mkdir(parents=True)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime_dir))

    first = _load_jwt_secret()
    second = _load_jwt_secret()

    assert first == second
    assert (runtime_dir / "data" / ".jwt_secret").exists()
    assert not (bundle / "data" / ".jwt_secret").exists()


# ── D3: the paid-plan grant key must persist across launches ──────────


def test_grant_key_is_stable_and_lives_in_the_runtime_dir(monkeypatch, tmp_path):
    """D3 regression: the grant key is written once and reused."""
    _clear_secret_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime_dir))
    grant_signature = importlib.import_module("api.billing.grant_signature")

    first = grant_signature.load_grant_key()
    second = grant_signature.load_grant_key()

    assert first == second
    assert len(first) >= 32
    assert (runtime_dir / "data" / ".grant_key").exists()


def test_grant_key_in_a_frozen_build_stays_out_of_the_bundle(monkeypatch, tmp_path):
    """A frozen build persists the grant key in the per-user dir, not the bundle."""
    _clear_secret_env(monkeypatch)
    bundle = tmp_path / "bundle"
    (bundle / "data").mkdir(parents=True)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime_dir))
    grant_signature = importlib.import_module("api.billing.grant_signature")

    first = grant_signature.load_grant_key()
    second = grant_signature.load_grant_key()

    assert first == second
    assert (runtime_dir / "data" / ".grant_key").exists()
    assert not (bundle / "data" / ".grant_key").exists()


def test_a_signed_paid_grant_survives_a_restart(monkeypatch, tmp_path):
    """The exact paid-entitlement guarantee.

    A grant signed with the key of launch #1 must still verify after launch
    #2 — otherwise a paying customer is dropped to the free plan every time
    the app restarts.
    """
    _clear_secret_env(monkeypatch)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime_dir))
    grant_signature = importlib.import_module("api.billing.grant_signature")

    key_before = grant_signature.load_grant_key()
    assert (runtime_dir / "data" / ".grant_key").exists(), (
        "the grant key was not persisted in the runtime dir"
    )

    row = {
        "user_id": "user-1",
        "plan_id": "plan_pro",
        "payment_provider": "razorpay",
        "grant_nonce": "nonce-abc123",
        "current_period_end": "2026-12-31",
        "expires_at": "2026-12-31",
        "trial_ends_at": None,
    }
    row["grant_signature"] = grant_signature.sign_grant(
        row["user_id"], row["plan_id"], row["payment_provider"],
        row["grant_nonce"], row["current_period_end"], row["expires_at"],
        row["trial_ends_at"],
    )
    assert grant_signature.verify_grant(row) is True

    # Simulate a restart: reload the module (drops any in-process state)
    # and load the key again from disk.
    grant_signature = importlib.reload(grant_signature)
    assert grant_signature.load_grant_key() == key_before
    assert grant_signature.verify_grant(row) is True
