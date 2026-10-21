"""The api_keys index-migration rebuild must survive the tenant_id column.

``DuckDBAPIKeyStore._migrate_drop_indexes`` rebuilds the table when it
carries a PRIMARY KEY or a secondary index (DuckDB cannot UPDATE a
table with an index, so every key mutation failed).

The rebuild copied the rows with a bare ``SELECT *``.  When the
``tenant_id`` column was added, the new table had one column more than
the legacy one and ``SELECT *`` failed with a column-count mismatch -
so the migration rolled back and the store could not initialise on an
upgraded install.

These tests drive the rebuild against both a legacy table WITH the
column and one WITHOUT it.
"""

from __future__ import annotations

import duckdb
import pytest


@pytest.fixture()
def conn(tmp_path):
    con = duckdb.connect(str(tmp_path / "keys.duckdb"))
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    yield con
    con.close()


def _legacy_table(conn, with_tenant: bool):
    """A legacy api_keys table (with a PRIMARY KEY, so the rebuild runs)."""
    conn.execute("DROP TABLE IF EXISTS analytics.api_keys")
    conn.execute("DROP TABLE IF EXISTS analytics.api_keys_legacy")
    tenant = ", tenant_id VARCHAR" if with_tenant else ""
    conn.execute(f"""
        CREATE TABLE analytics.api_keys (
            key_id VARCHAR NOT NULL PRIMARY KEY,
            user_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            key_hash VARCHAR NOT NULL,
            key_prefix VARCHAR(8) NOT NULL,
            scopes VARCHAR[] NOT NULL,
            created_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP,
            last_used_at TIMESTAMP,
            revoked BOOLEAN DEFAULT FALSE,
            revoked_at TIMESTAMP{tenant}
        )
    """)
    cols = (
        "key_id, user_id, name, key_hash, key_prefix, scopes, created_at, "
        "expires_at, last_used_at, revoked, revoked_at"
        + (", tenant_id" if with_tenant else "")
    )
    vals = (
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
        + (", ?" if with_tenant else "")
    )
    params = ["k1", "u1", "legacy", "hash1", "da_k1", ["read"],
              "2026-01-01 00:00:00", None, None, False, None]
    if with_tenant:
        params.append("t-1")
    conn.execute(
        f"INSERT INTO analytics.api_keys ({cols}) VALUES ({vals})", params
    )


def test_the_rebuild_keeps_the_rows_with_a_tenant_column(conn):
    from api.api_keys import DuckDBAPIKeyStore

    _legacy_table(conn, with_tenant=True)
    DuckDBAPIKeyStore._migrate_drop_indexes(conn)

    rows = conn.execute(
        "SELECT key_id, tenant_id FROM analytics.api_keys"
    ).fetchall()
    assert rows == [("k1", "t-1")]


def test_the_rebuild_keeps_the_rows_without_a_tenant_column(conn):
    from api.api_keys import DuckDBAPIKeyStore

    _legacy_table(conn, with_tenant=False)
    DuckDBAPIKeyStore._migrate_drop_indexes(conn)

    rows = conn.execute("SELECT key_id FROM analytics.api_keys").fetchall()
    assert rows == [("k1",)]

    # The rebuilt table has the tenant_id column (it is added by
    # _migrate_add_tenant_id), and the row is simply NULL there.
    cols = [
        r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'api_keys'"
        ).fetchall()
    ]
    assert "tenant_id" in cols


def test_the_rebuild_leaves_no_legacy_table(conn):
    from api.api_keys import DuckDBAPIKeyStore

    _legacy_table(conn, with_tenant=True)
    DuckDBAPIKeyStore._migrate_drop_indexes(conn)

    tables = [
        r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'api_keys%'"
        ).fetchall()
    ]
    assert tables == ["api_keys"]
