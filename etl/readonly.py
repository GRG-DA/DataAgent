"""
Read-only execution helpers for DuckDB connections.

Shared by the warehouse loader, the agent SQL tool and the visual query
builder so every "read-only" execution path physically cannot persist
writes: statements run inside a transaction that is always rolled back.
"""

from contextlib import contextmanager

import duckdb


@contextmanager
def readonly_transaction(conn: duckdb.DuckDBPyConnection):
    """Yield *conn* inside a transaction that always rolls back on exit.

    SELECTs behave normally; any DDL/DML that slipped past validation is
    discarded when the context exits (defense in depth for the audit fix).
    """
    conn.execute("BEGIN TRANSACTION")
    try:
        yield conn
    finally:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001 - tx may already be finished
            pass
