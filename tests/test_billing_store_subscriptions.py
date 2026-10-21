"""Subscription rows and per-order grant windows in the billing store.

Written before the `subscriptions` table, `orders.grant_days`, and the
subscription helpers existed (TDD). Pro is now an auto-renewing monthly
subscription (Razorpay Subscriptions): each row mirrors Razorpay's status and
paid-through date, while lifetime stays a one-time order grant. Folding these
rows into a current entitlement is `billing_server.grants`' job — the store
only keeps them durable.
"""
from __future__ import annotations


# ── orders: per-order grant window ───────────────────────────────────


def test_mark_paid_stores_grant_days(store_env):
    store = store_env["store"]
    store.record_order("o1", "Buyer@Example.com", "pro", 49900, "INR")
    order = store.mark_paid("o1", "pay_1", grant_days=31)
    assert order["status"] == "paid"
    assert order["grant_days"] == 31


def test_mark_paid_without_grant_days_leaves_null(store_env):
    store = store_env["store"]
    store.record_order("o2", "buyer@example.com", "lifetime", 1000000, "INR")
    order = store.mark_paid("o2", "pay_2")
    assert order["grant_days"] is None


def test_mark_paid_preserves_existing_grant_days(store_env):
    store = store_env["store"]
    store.record_order("o3", "buyer@example.com", "pro", 49900, "INR")
    store.mark_paid("o3", "pay_3", grant_days=31)
    again = store.mark_paid("o3", "pay_3")  # retried webhook without grant_days
    assert again["grant_days"] == 31


# ── subscriptions: upsert + read ─────────────────────────────────────


def test_upsert_subscription_inserts_then_updates(store_env):
    store = store_env["store"]
    row = store.upsert_subscription("sub_1", "Buyer@Example.com", "pro", status="created")
    assert row["subscription_id"] == "sub_1"
    assert row["email"] == "buyer@example.com"
    assert row["plan"] == "pro"
    assert row["status"] == "created"
    assert row["current_end"] is None
    assert row["cancel_at_period_end"] in (0, False)

    row = store.upsert_subscription(
        "sub_1", "buyer@example.com", "pro",
        status="active",
        current_end="2026-11-15T00:00:00+00:00",
        payment_id="pay_9",
    )
    assert row["status"] == "active"
    assert row["current_end"] == "2026-11-15T00:00:00+00:00"
    assert row["payment_id"] == "pay_9"


def test_upsert_subscription_keeps_created_at_and_cancel_flag(store_env):
    store = store_env["store"]
    first = store.upsert_subscription("sub_2", "buyer@example.com", "pro", status="active")
    store.set_cancel_at_period_end("sub_2", True)
    again = store.upsert_subscription(
        "sub_2", "buyer@example.com", "pro",
        status="active",
        current_end="2026-12-01T00:00:00+00:00",
        payment_id="pay_10",
    )
    assert again["created_at"] == first["created_at"]
    assert again["cancel_at_period_end"] in (1, True)

    # A stale webhook without current_end/payment_id must not wipe the
    # recorded paid-through date or the original payment reference.
    stale = store.upsert_subscription("sub_2", "buyer@example.com", "pro", status="pending")
    assert stale["status"] == "pending"
    assert stale["current_end"] == "2026-12-01T00:00:00+00:00"
    assert stale["payment_id"] == "pay_10"


def test_get_subscription_unknown_returns_none(store_env):
    assert store_env["store"].get_subscription("sub_missing") is None


def test_subscriptions_for_email_case_insensitive_newest_first(store_env):
    store = store_env["store"]
    store.upsert_subscription("sub_a", "Buyer@Example.com", "pro")
    store.upsert_subscription("sub_b", "buyer@example.com", "pro")
    store.set_cancel_at_period_end("sub_a", True)  # bumps sub_a's updated_at
    rows = store.subscriptions_for_email("BUYER@example.com")
    assert [r["subscription_id"] for r in rows] == ["sub_a", "sub_b"]


def test_set_cancel_at_period_end_flags_and_reports(store_env):
    store = store_env["store"]
    store.upsert_subscription("sub_3", "buyer@example.com", "pro", status="active")
    row = store.set_cancel_at_period_end("sub_3", True)
    assert row["cancel_at_period_end"] in (1, True)
    row = store.set_cancel_at_period_end("sub_3", False)
    assert row["cancel_at_period_end"] in (0, False)
    assert store.set_cancel_at_period_end("sub_missing", True) is None


# ── the same flow over the remote backend ────────────────────────────


def test_subscription_flow_over_libsql_backend(store_env, fake_turso, monkeypatch):
    store = store_env["store"]
    monkeypatch.setattr(store_env["cfg"], "TURSO_DATABASE_URL", "libsql://fake.turso.io")
    monkeypatch.setattr(store_env["cfg"], "TURSO_AUTH_TOKEN", "tok_123")

    store.upsert_subscription(
        "sub_r", "buyer@example.com", "pro",
        status="active", current_end="2026-11-15T00:00:00+00:00",
    )
    store.record_order("o_r", "buyer@example.com", "pro", 49900, "INR")
    store.mark_paid("o_r", "pay_r", grant_days=31)

    sub = store.get_subscription("sub_r")
    assert sub["status"] == "active" and sub["current_end"]
    assert store.subscriptions_for_email("buyer@example.com")[0]["subscription_id"] == "sub_r"
    assert store.get_order("o_r")["grant_days"] == 31
    assert fake_turso["connects"], "remote backend was never used"
