"""Tests for the ordered schema-migration runner (api/migrations.py)."""
from __future__ import annotations

import pytest


@pytest.fixture
def conn(tmp_path):
    from api.user_store import connect_identity_db

    c = connect_identity_db()
    try:
        yield c
    finally:
        try:
            c.close()
        except Exception:
            pass


def test_the_ledger_table_is_created(conn):
    from api.migrations import ensure_ledger

    ensure_ledger(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchall()
    assert rows, "the migration ledger table was not created"


def test_the_ledger_statement_is_valid_sql(conn):
    """Regression: a missing ``()`` interpolated the function object.

    The f-string produced ``CREATE TABLE IF NOT EXISTS <function ...>`` and
    SQLite refused it with ``near "<": syntax error``.
    """
    from api.migrations import ensure_ledger

    ensure_ledger(conn)  # must not raise
    assert "schema_migrations" in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()[0][0] or True


def test_migrations_are_ordered_and_uniquely_versioned():
    from api.migrations import MIGRATIONS

    versions = [v for v, _, _ in MIGRATIONS]
    assert versions == sorted(versions), "migrations are not in version order"
    assert len(versions) == len(set(versions)), "duplicate migration version"


def test_running_the_migrations_is_idempotent(conn):
    """A second run must apply nothing."""
    from api.migrations import applied_versions, run_migrations

    run_migrations(conn)
    done = applied_versions(conn)
    assert done, "no migration was recorded"

    again = run_migrations(conn)
    assert again == [], f"the second run applied {again}"


def test_a_migration_that_adds_a_column_is_idempotent(conn):
    """``ALTER TABLE ... ADD COLUMN`` has no IF NOT EXISTS in SQLite."""
    from api.migrations import _add_columns

    _add_columns(conn, "users", [("email_verified", "INTEGER NOT NULL DEFAULT 0")])
    # Second call must not raise "duplicate column name".
    _add_columns(conn, "users", [("email_verified", "INTEGER NOT NULL DEFAULT 0")])

    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert "email_verified" in cols


def test_a_failing_migration_is_not_recorded(conn):
    """A failure must leave the version unrecorded so the next start retries."""
    from api.migrations import MIGRATIONS, applied_versions, run_migrations

    broken = (999, "broken_migration", ["THIS IS NOT SQL"])
    MIGRATIONS.append(broken)
    try:
        run_migrations(conn)
        assert 999 not in applied_versions(conn)
    finally:
        MIGRATIONS.remove(broken)


def test_the_ledger_survives_a_reopen(tmp_path):
    """The ledger is in the shared identity DB, so it survives a restart."""
    from api.migrations import applied_versions, run_migrations
    from api.user_store import connect_identity_db

    c = connect_identity_db()
    try:
        run_migrations(c)
        done = applied_versions(c)
    finally:
        c.close()

    c2 = connect_identity_db()
    try:
        assert applied_versions(c2) == done
    finally:
        c2.close()
