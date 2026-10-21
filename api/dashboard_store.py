"""
Dashboard Store — persistence for PBI Studio dashboards.

Single DuckDB table `analytics.pbi_studios` with JSON blob storage.
Dashboards are scoped by owner_id ('' = legacy/anonymous, visible to all).
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from api.state import loader


TABLE = "pbi_studios"
SCHEMA = "analytics"


def _get_con():
    """Get a warehouse connection (falls back to a standalone loader if the
    shared app state hasn't been initialized yet)."""
    if loader is None:
        from config import settings
        from etl.load import WarehouseLoader
        import api.state as _state
        _state.loader = WarehouseLoader(
            db_path=settings.duckdb.database_path,
            schema=settings.duckdb.schema,
            config=settings.duckdb.config,
        )
        return _state.loader.get_connection()
    return loader.get_connection()


def _ensure_table() -> None:
    """Create the pbi_studios table if it doesn't exist, and migrate it to
    include the owner_id column (legacy rows get owner_id = '')."""
    with _get_con() as con:
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.{TABLE} (
                id VARCHAR,
                name VARCHAR NOT NULL,
                dash JSON NOT NULL,
                owner_id VARCHAR NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migration: add owner_id to tables created before this column existed.
        cols = [r[0] for r in con.execute(
            f"SELECT column_name FROM information_schema.columns WHERE table_schema = '{SCHEMA}' AND table_name = '{TABLE}'"
        ).fetchall()]
        if "owner_id" not in cols:
            con.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ADD COLUMN owner_id VARCHAR NOT NULL DEFAULT ''")


def _row_to_dash(row: tuple) -> dict:
    """Convert a DB row to dashboard dict."""
    return {
        "id": row[0],
        "name": row[1],
        "dash": json.loads(row[2]) if isinstance(row[2], str) else row[2],
        "owner_id": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }


def create_dashboard(name: str, dash: dict, owner_id: str = "") -> dict:
    """Create a new dashboard."""
    _ensure_table()
    dash_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    with _get_con() as con:
        con.execute(
            f"INSERT INTO {SCHEMA}.{TABLE} (id, name, dash, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [dash_id, name, json.dumps(dash), owner_id, now, now],
        )

    return {"id": dash_id, "name": name, "dash": dash, "owner_id": owner_id,
            "created_at": now, "updated_at": now}


def get_dashboard(dash_id: str) -> Optional[dict]:
    """Get a dashboard by ID."""
    _ensure_table()
    with _get_con() as con:
        row = con.execute(
            f"SELECT id, name, dash, owner_id, created_at, updated_at FROM {SCHEMA}.{TABLE} WHERE id = ?",
            [dash_id],
        ).fetchone()

    if row:
        return _row_to_dash(row)
    return None


def list_dashboards(owner_id: Optional[str] = None, include_legacy: bool = False) -> list:
    """List dashboards (metadata only).

    When owner_id is given, returns the user's own dashboards.  The legacy
    anonymous ones (``owner_id = ''``) are admin-only — they used to be
    handed to every authenticated caller, which in a multi-tenant deployment
    leaked dashboards across customers (ADR-009 D1).  Pass
    ``include_legacy=True`` for an admin listing.  When owner_id is omitted,
    every dashboard is returned.
    """
    _ensure_table()
    with _get_con() as con:
        if owner_id:
            clause = "owner_id = ?"
            params = [owner_id]
            if include_legacy:
                clause = "(owner_id = ? OR owner_id = '')"
            rows = con.execute(
                f"""SELECT id, name, owner_id, created_at, updated_at
                    FROM {SCHEMA}.{TABLE}
                    WHERE {clause}
                    ORDER BY updated_at DESC""",
                params,
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT id, name, owner_id, created_at, updated_at FROM {SCHEMA}.{TABLE} ORDER BY updated_at DESC"
            ).fetchall()

    return [
        {"id": r[0], "name": r[1], "owner_id": r[2], "created_at": r[3], "updated_at": r[4]}
        for r in rows
    ]


def _can_access(row: tuple, owner_id: Optional[str], include_legacy: bool = False) -> bool:
    """A row is accessible when it is owned by the user.

    Legacy rows (``owner_id = ''``) are admin-only unless *include_legacy*
    is set — see :func:`list_dashboards`.
    """
    if owner_id is None:
        return True
    row_owner = row[3] or ""
    if row_owner == "":
        return bool(include_legacy)
    return row_owner == owner_id


def update_dashboard(dash_id: str, dash: dict, owner_id: Optional[str] = None,
                     include_legacy: bool = False) -> Optional[dict]:
    """Update an existing dashboard (ownership-checked)."""
    _ensure_table()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    with _get_con() as con:
        row = con.execute(
            f"SELECT id, name, dash, owner_id, created_at, updated_at FROM {SCHEMA}.{TABLE} WHERE id = ?",
            [dash_id],
        ).fetchone()
        if not row:
            return None
        if not _can_access(row, owner_id, include_legacy):
            return None
        con.execute(
            f"UPDATE {SCHEMA}.{TABLE} SET dash = ?, updated_at = ? WHERE id = ?",
            [json.dumps(dash), now, dash_id],
        )
        row = con.execute(
            f"SELECT id, name, dash, owner_id, created_at, updated_at FROM {SCHEMA}.{TABLE} WHERE id = ?",
            [dash_id],
        ).fetchone()

    if row:
        return _row_to_dash(row)
    return None


def delete_dashboard(dash_id: str, owner_id: Optional[str] = None,
                     include_legacy: bool = False) -> bool:
    """Delete a dashboard (ownership-checked)."""
    _ensure_table()
    with _get_con() as con:
        row = con.execute(
            f"SELECT id, name, dash, owner_id, created_at, updated_at FROM {SCHEMA}.{TABLE} WHERE id = ?",
            [dash_id],
        ).fetchone()
        if not row:
            return False
        if not _can_access(row, owner_id, include_legacy):
            return False
        con.execute(f"DELETE FROM {SCHEMA}.{TABLE} WHERE id = ?", [dash_id])
    return True


def dashboard_exists(dash_id: str) -> bool:
    """Check if dashboard exists."""
    _ensure_table()
    with _get_con() as con:
        row = con.execute(f"SELECT 1 FROM {SCHEMA}.{TABLE} WHERE id = ?", [dash_id]).fetchone()
    return row is not None