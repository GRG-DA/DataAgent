"""Store backend selection + the Turso/libSQL compatibility adapter.

Written before ``billing_server.libsql_adapter`` existed (TDD). The `libsql`
client is faked into ``sys.modules`` and backed by a real in-memory SQLite
engine (shared cache), so schema init, parameterized SQL, row mapping, and
commit/rollback are exercised end-to-end without any network connection.

The fake deliberately mirrors the real client's gaps: tuple rows, no
``row_factory``, no ``executescript`` — the adapter must bridge all three.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# `store_env` and `fake_turso` fixtures live in tests/conftest.py — shared
# with test_billing_store_subscriptions.py.


def _use_turso(store_env, monkeypatch, url="libsql://fake.turso.io", token="tok_123"):
    monkeypatch.setattr(store_env["cfg"], "TURSO_DATABASE_URL", url)
    monkeypatch.setattr(store_env["cfg"], "TURSO_AUTH_TOKEN", token)


# ── backend selection ────────────────────────────────────────────────


def test_default_backend_is_local_sqlite(store_env):
    con = store_env["store"]._connect()
    try:
        assert isinstance(con, sqlite3.Connection)
    finally:
        con.close()
    assert Path(store_env["cfg"].DB_PATH).exists()


def test_turso_env_switches_to_libsql(store_env, fake_turso, monkeypatch):
    _use_turso(store_env, monkeypatch)
    con = store_env["store"]._connect()
    assert fake_turso["connects"] == [
        {"database": "libsql://fake.turso.io", "auth_token": "tok_123"}
    ]
    assert not Path(store_env["cfg"].DB_PATH).exists()
    with con:  # commit path + close through the wrapper
        pass


def test_turso_mode_requires_token(store_env, fake_turso, monkeypatch):
    _use_turso(store_env, monkeypatch, token="")
    with pytest.raises(RuntimeError, match="TURSO_AUTH_TOKEN"):
        store_env["store"]._connect()
    assert fake_turso["connects"] == []


def test_missing_libsql_package_gives_clear_error(store_env, monkeypatch):
    _use_turso(store_env, monkeypatch)
    monkeypatch.setitem(sys.modules, "libsql", None)
    with pytest.raises(RuntimeError, match="libsql"):
        store_env["store"]._connect()


# ── behavior over the remote backend ─────────────────────────────────


def test_full_store_flow_over_libsql_backend(store_env, fake_turso, monkeypatch):
    store = store_env["store"]
    _use_turso(store_env, monkeypatch)

    account = store.create_account("Buyer@Example.com", "bcrypt-hash")
    assert account and account["email"] == "buyer@example.com"
    assert store.create_account("buyer@example.com", "again") is None
    assert store.get_account_by_email("buyer@example.com")["user_id"] == account["user_id"]

    store.record_order("o1", "Buyer@Example.com", "pro", 49900, "INR")
    order = store.mark_paid("o1", "pay_1")
    assert order["status"] == "paid" and order["email"] == "buyer@example.com"

    store.attach_key("o1", "DA-KEY", "hash_1")
    latest = store.latest_order_for_email("buyer@example.com")
    assert latest["license_key"] == "DA-KEY"

    first = store.redeem_key("hash_1", "buyer@example.com", account["user_id"])
    assert first["status"] == "redeemed"
    again = store.redeem_key("hash_1", "buyer@example.com", account["user_id"])
    assert again["status"] == "already_owner"

    assert store.seen_event("payment", "pay_1") is False
    assert store.seen_event("payment", "pay_1") is True

    assert store.registry_counts() == {
        "orders": 1, "paid": 1, "refunded": 0, "redeemed": 1,
    }
    store.touch_account(account["user_id"])

    # one fresh remote connection per store call, as designed
    assert len(fake_turso["connects"]) >= 10
    assert all(c["auth_token"] == "tok_123" for c in fake_turso["connects"])


def test_schema_runs_once_per_target(store_env, fake_turso, monkeypatch):
    store = store_env["store"]
    _use_turso(store_env, monkeypatch)

    store.get_order("missing-1")
    store.get_order("missing-2")

    orders_ddl = [s for s in fake_turso["ddls"] if "CREATE TABLE IF NOT EXISTS orders" in s]
    assert len(orders_ddl) == 1
