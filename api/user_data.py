"""
User Data — per-account chat conversations, query history, and preferences.

Each account sees only its own rows. Rows created before login
(user_id = '') are legacy orphans and are never surfaced to any user.

This module also owns the **data-subject rights**: the self-service GDPR
export (:func:`build_user_export`) and the per-user erasure
(:func:`delete_user_records`).
"""
import json
import logging
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth_middleware import get_current_user, User
from api.tenancy import DEFAULT_TENANT_ID, tenant_schema

router = APIRouter()

SCHEMA = "analytics"

logger = logging.getLogger(__name__)


def _get_con():
    """The warehouse connection for the platform schema.

    The loader is resolved from the **current** settings on every call.
    The module used to bind ``api.state.loader`` at import time, which
    froze it to whatever warehouse existed when the module was first
    imported: the create/migrate step and the read step could then talk to
    two different databases, and a table left in an older shape raised a
    ``BinderException`` depending on which caller ran first.
    """
    import api.state as _state
    from config import settings
    from etl.load import WarehouseLoader

    current = _state.loader
    if current is None or str(getattr(current, "db_path", "")) != str(
        settings.duckdb.database_path
    ):
        current = WarehouseLoader(
            db_path=settings.duckdb.database_path,
            schema=settings.duckdb.schema,
            config=settings.duckdb.config,
        )
        _state.loader = current
    return current.get_connection()


#: Columns every platform table must carry.  ``CREATE TABLE IF NOT
#: EXISTS`` never adds a column, so a table that was created by an older
#: release (or by another tool) keeps its old shape and every INSERT
#: fails with a Binder Error.  The columns are added one by one,
#: idempotently.  They are added without constraints (DuckDB refuses to
#: add a NOT NULL column), which is safe: every INSERT below provides the
#: value explicitly.
_REQUIRED_COLUMNS: Dict[str, tuple] = {
    "user_preferences": (("updated_at", "TIMESTAMP"),),
    "chat_conversations": (("created_at", "TIMESTAMP"), ("updated_at", "TIMESTAMP")),
    "chat_messages": (("created_at", "TIMESTAMP"),),
    "query_history": (
        ("sql", "VARCHAR"),
        ("result_summary", "VARCHAR"),
        ("created_at", "TIMESTAMP"),
    ),
}


def _migrate_columns(con) -> None:
    """Add the columns the app needs that a table does not have yet."""
    for table, columns in _REQUIRED_COLUMNS.items():
        for column, ddl in columns:
            try:
                con.execute(
                    f"ALTER TABLE {SCHEMA}.{table} "
                    f"ADD COLUMN IF NOT EXISTS {column} {ddl}"
                )
            except Exception as exc:  # noqa: BLE001 — the table may not exist
                logger.debug("could not add %s.%s: %s", table, column, exc)


def _ensure_platform_shape(con) -> None:
    """Align the platform tables with the columns the app reads.

    ``CREATE TABLE IF NOT EXISTS`` never changes an existing table, so a
    table created by an older release keeps its old shape and every query
    against the new columns raises a ``BinderException``.  The migration
    adds the missing columns; it is idempotent, and a failure is logged
    rather than fatal — the caller still gets a document.
    """
    try:
        _migrate_columns(con)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not align the platform table shape: %s", exc)


def _ensure_tables() -> None:
    with _get_con() as con:
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.user_preferences (
                user_id VARCHAR,
                prefs JSON NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.chat_conversations (
                id VARCHAR,
                user_id VARCHAR NOT NULL DEFAULT '',
                title VARCHAR NOT NULL DEFAULT 'New chat',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.chat_messages (
                id VARCHAR,
                conversation_id VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL DEFAULT '',
                role VARCHAR NOT NULL,
                content VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.query_history (
                id VARCHAR,
                user_id VARCHAR NOT NULL DEFAULT '',
                query VARCHAR NOT NULL,
                sql VARCHAR NOT NULL DEFAULT '',
                result_summary VARCHAR NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _migrate_columns(con)


# ── Preferences ───────────────────────────────────────────────────────

@router.get("/user/preferences")
async def get_preferences(user: User = Depends(get_current_user)):
    _ensure_tables()
    with _get_con() as con:
        row = con.execute(
            f"SELECT prefs FROM {SCHEMA}.user_preferences WHERE user_id = ?",
            [user.user_id],
        ).fetchone()
    if not row:
        return {"prefs": {}}
    return {"prefs": json.loads(row[0]) if isinstance(row[0], str) else row[0]}


@router.put("/user/preferences")
async def set_preferences(body: Dict[str, Any], user: User = Depends(get_current_user)):
    _ensure_tables()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with _get_con() as con:
        row = con.execute(
            f"SELECT 1 FROM {SCHEMA}.user_preferences WHERE user_id = ?",
            [user.user_id],
        ).fetchone()
        if row:
            con.execute(
                f"UPDATE {SCHEMA}.user_preferences SET prefs = ?, updated_at = ? WHERE user_id = ?",
                [json.dumps(body), now, user.user_id],
            )
        else:
            con.execute(
                f"INSERT INTO {SCHEMA}.user_preferences (user_id, prefs, updated_at) VALUES (?, ?, ?)",
                [user.user_id, json.dumps(body), now],
            )
    return {"prefs": body}


# ── Chat conversations ────────────────────────────────────────────────

def _conv_row(row: tuple) -> dict:
    return {
        "id": row[0],
        "user_id": row[1],
        "title": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


@router.get("/user/conversations")
async def list_conversations(user: User = Depends(get_current_user)):
    _ensure_tables()
    with _get_con() as con:
        rows = con.execute(
            f"""SELECT id, user_id, title, created_at, updated_at
                FROM {SCHEMA}.chat_conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC LIMIT 100""",
            [user.user_id],
        ).fetchall()
    return {"conversations": [_conv_row(r) for r in rows]}


class ConversationCreate(BaseModel):
    title: str = "New chat"


@router.post("/user/conversations")
async def create_conversation(body: ConversationCreate, user: User = Depends(get_current_user)):
    _ensure_tables()
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with _get_con() as con:
        con.execute(
            f"INSERT INTO {SCHEMA}.chat_conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            [conv_id, user.user_id, body.title or "New chat", now, now],
        )
        row = con.execute(
            f"SELECT id, user_id, title, created_at, updated_at FROM {SCHEMA}.chat_conversations WHERE id = ?",
            [conv_id],
        ).fetchone()
    return _conv_row(row)


@router.get("/user/conversations/{conv_id}")
async def get_conversation(conv_id: str, user: User = Depends(get_current_user)):
    _ensure_tables()
    # Plan-gated history window: free users see only the last N days.
    from api.billing.entitlements import get_entitlements
    ent = get_entitlements(user.user_id, getattr(user, "plan", None))
    history_days = int(ent.get("max_chat_history_days", 7))
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=history_days)).isoformat()
    with _get_con() as con:
        conv = con.execute(
            f"SELECT id, user_id, title, created_at, updated_at FROM {SCHEMA}.chat_conversations WHERE id = ?",
            [conv_id],
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv[1] != user.user_id:
            raise HTTPException(status_code=403, detail="Not your conversation")
        msgs = con.execute(
            f"""SELECT role, content, created_at FROM {SCHEMA}.chat_messages
                WHERE conversation_id = ? AND created_at >= CAST(? AS TIMESTAMP)
                ORDER BY created_at ASC""",
            [conv_id, cutoff],
        ).fetchall()
    return {
        **_conv_row(conv),
        "history_days": history_days,
        "messages": [
            {"role": m[0], "content": m[1], "created_at": m[2]} for m in msgs
        ],
    }


class MessageCreate(BaseModel):
    role: str
    content: str


@router.post("/user/conversations/{conv_id}/messages")
async def add_message(conv_id: str, body: MessageCreate, user: User = Depends(get_current_user)):
    _ensure_tables()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with _get_con() as con:
        conv = con.execute(
            f"SELECT user_id FROM {SCHEMA}.chat_conversations WHERE id = ?",
            [conv_id],
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv[0] != user.user_id:
            raise HTTPException(status_code=403, detail="Not your conversation")
        msg_id = str(uuid.uuid4())
        con.execute(
            f"INSERT INTO {SCHEMA}.chat_messages (id, conversation_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [msg_id, conv_id, user.user_id, body.role, body.content, now],
        )
        con.execute(
            f"UPDATE {SCHEMA}.chat_conversations SET updated_at = ? WHERE id = ?",
            [now, conv_id],
        )
    return {"id": msg_id, "role": body.role, "content": body.content, "created_at": now}


@router.put("/user/conversations/{conv_id}")
async def rename_conversation(conv_id: str, body: Dict[str, Any], user: User = Depends(get_current_user)):
    _ensure_tables()
    title = str(body.get("title", "")).strip() or "New chat"
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with _get_con() as con:
        conv = con.execute(
            f"SELECT user_id FROM {SCHEMA}.chat_conversations WHERE id = ?",
            [conv_id],
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv[0] != user.user_id:
            raise HTTPException(status_code=403, detail="Not your conversation")
        con.execute(
            f"UPDATE {SCHEMA}.chat_conversations SET title = ?, updated_at = ? WHERE id = ?",
            [title, now, conv_id],
        )
    return {"id": conv_id, "title": title, "updated_at": now}


@router.delete("/user/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: User = Depends(get_current_user)):
    _ensure_tables()
    with _get_con() as con:
        conv = con.execute(
            f"SELECT user_id FROM {SCHEMA}.chat_conversations WHERE id = ?",
            [conv_id],
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv[0] != user.user_id:
            raise HTTPException(status_code=403, detail="Not your conversation")
        con.execute(f"DELETE FROM {SCHEMA}.chat_messages WHERE conversation_id = ?", [conv_id])
        con.execute(f"DELETE FROM {SCHEMA}.chat_conversations WHERE id = ?", [conv_id])
    return {"deleted": True}


# ── Query history ─────────────────────────────────────────────────────

class QueryHistoryCreate(BaseModel):
    query: str
    sql: str = ""
    result_summary: str = ""


@router.get("/user/query-history")
async def list_query_history(user: User = Depends(get_current_user), limit: int = 50):
    _ensure_tables()
    with _get_con() as con:
        rows = con.execute(
            f"""SELECT id, user_id, query, sql, result_summary, created_at
                FROM {SCHEMA}.query_history
                WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ?""",
            [user.user_id, min(max(limit, 1), 200)],
        ).fetchall()
    return {"history": [
        {"id": r[0], "user_id": r[1], "query": r[2], "sql": r[3],
         "result_summary": r[4], "created_at": r[5]} for r in rows
    ]}


@router.post("/user/query-history")
async def add_query_history(body: QueryHistoryCreate, user: User = Depends(get_current_user)):
    _ensure_tables()
    hist_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with _get_con() as con:
        con.execute(
            f"INSERT INTO {SCHEMA}.query_history (id, user_id, query, sql, result_summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [hist_id, user.user_id, body.query, body.sql, body.result_summary, now],
        )
    return {"id": hist_id, "query": body.query, "created_at": now}


@router.delete("/user/query-history")
async def clear_query_history(user: User = Depends(get_current_user)):
    _ensure_tables()
    with _get_con() as con:
        con.execute(f"DELETE FROM {SCHEMA}.query_history WHERE user_id = ?", [user.user_id])
    return {"deleted": True}


# ── Data-subject rights: export & erasure ─────────────────────────────
#
# **Policy (per-user erasure).**  A tenant's *data tables* are shared by
# every member of the tenant: one person's rows cannot be separated
# from the tenant's rows.  A per-user erasure therefore removes only the
# records that belong to the user alone — chats, messages, query
# history, preferences, notifications, webhooks, API keys, the
# subscription, the comments, the annotations and the dashboards — and
# leaves the tenant's tables untouched.  Removing the tenant's tables is
# a tenant-level operation (the account-delete cascade), never a
# per-user one: doing it would destroy the other members' data.

#: Hard cap on the rows exported from any single table.
MAX_EXPORT_ROWS_PER_TABLE = 50_000

#: Hard cap on the size of the exported JSON document.
MAX_EXPORT_BYTES = 10 * 1024 * 1024

#: The version of the export document format.
EXPORT_VERSION = "1.0"

#: Keys that must never leave the platform, whatever table they came from.
_SECRET_KEYS = frozenset({
    "password", "password_hash", "salt", "api_key", "api_key_hash",
    "key_hash", "mfa_secret", "secret", "secret_hash", "token",
    "session_id", "smtp_password", "reset_token", "api_secret",
})

#: A column that says "these rows belong to one person".
_PERSONAL_OWNER_COLUMNS = frozenset({
    "user_id", "owner_id", "author_id", "subject_id", "shared_by", "actor_id",
})

#: The platform's own tables.  They are never exported as "tenant data":
#: they hold other people's rows and the platform's metadata (ADR-009 D1).
_PLATFORM_TABLES = frozenset({
    "account_tokens", "annotations", "api_keys", "approval_actions",
    "budget_alerts", "business_terms", "calendar", "catalog_assets",
    "catalog_tags", "chat_conversations", "chat_messages", "comments",
    "consent_records", "cost_budgets", "dashboards", "data_products",
    "masked_audit", "masking_rules", "measures", "metric_definitions",
    "notification_preferences", "notifications", "org_departments",
    "org_group_members", "org_groups", "org_teams", "pbi_studios",
    "pii_inventory", "pipeline_runs", "privacy_requests",
    "product_access_requests", "product_ratings", "query_costs",
    "query_history", "retention_policies", "revoked_jtis", "sessions",
    "share_links", "sla_breach_events", "sla_definitions", "sla_results",
    "store_meta", "user_preferences", "user_subscriptions", "users",
    "webhook_endpoints", "workflow_definitions", "workflow_instances",
    # billing
    "checkout_orders", "coupon_redemptions", "coupons", "invoices",
    "payment_events", "payments", "plan_features", "plan_limits",
    "plan_prices", "plans", "premium_settings", "subscription_events",
    "subscriptions", "usage_events", "usage_records",
})

#: The platform tables a user's export covers: section -> (table, owner column).
_EXPORT_TABLES: Dict[str, Tuple[str, str]] = {
    "conversations": ("chat_conversations", "user_id"),
    "messages": ("chat_messages", "user_id"),
    "query_history": ("query_history", "user_id"),
    "preferences": ("user_preferences", "user_id"),
    "notification_preferences": ("notification_preferences", "user_id"),
    "api_keys": ("api_keys", "user_id"),
    "comments": ("comments", "author_id"),
    "annotations": ("annotations", "author_id"),
    "dashboards": ("dashboards", "owner_id"),
}

#: The API-key columns that may be exported.  The hash is not one of them.
_API_KEY_COLUMNS: Tuple[str, ...] = (
    "key_id", "name", "key_prefix", "scopes", "created_at",
    "expires_at", "last_used_at", "revoked", "revoked_at",
)

#: The platform tables a per-user erasure touches: table -> owner column.
_ERASURE_TABLES: Tuple[Tuple[str, str], ...] = (
    ("chat_conversations", "user_id"),
    ("chat_messages", "user_id"),
    ("query_history", "user_id"),
    ("user_preferences", "user_id"),
    ("notification_preferences", "user_id"),
    ("notifications", "user_id"),
    ("webhook_endpoints", "user_id"),
    ("api_keys", "user_id"),
    ("user_subscriptions", "user_id"),
    ("comments", "author_id"),
    ("annotations", "author_id"),
    ("dashboards", "owner_id"),
)

#: A SQL identifier that is safe to interpolate.
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _now_iso() -> str:
    """The current UTC time, as an ISO string."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _json_safe(value: Any) -> Any:
    """Convert a DuckDB value into something ``json.dumps`` accepts."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _scrub_secrets(value: Any) -> Any:
    """Drop secret-bearing keys from a decoded row."""
    if isinstance(value, dict):
        return {
            key: _scrub_secrets(item)
            for key, item in value.items()
            if str(key).lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_scrub_secrets(item) for item in value]
    return value


def _safe_ident(name: str) -> bool:
    """True when *name* is safe to interpolate into SQL."""
    return bool(_SAFE_IDENT.match(str(name or "")))


def _resolve_loader():
    """The warehouse loader: the app's own, or one built from the settings."""
    import api.state as _state
    from config import settings

    current = _state.loader
    if current is not None:
        return current
    from etl.load import WarehouseLoader

    current = WarehouseLoader(
        db_path=settings.duckdb.database_path,
        schema=settings.duckdb.schema,
        config=settings.duckdb.config,
    )
    _state.loader = current
    return current


@contextmanager
def _export_connections(tenant_id: str) -> Iterator[Tuple[Any, Any]]:
    """The (tenant, platform) connections the export reads from.

    The tenant's own data tables live in the tenant's schema; the
    platform tables live in the shared schema.  With the opt-in
    per-tenant database mode the two are different *files*, so the
    export needs a connection to each (ADR-009 D1).
    """
    warehouse = _resolve_loader()
    with warehouse.get_connection(tenant_id) as tenant:
        with warehouse.get_connection(DEFAULT_TENANT_ID) as platform:
            yield tenant, platform


def _table_exists(con, schema: str, table: str) -> bool:
    """True when *table* exists in *schema*."""
    row = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchone()
    return row is not None


def _decoded(con, rows) -> List[Dict[str, Any]]:
    """Turn DuckDB rows into dicts."""
    cols = [desc[0] for desc in con.description]
    return [dict(zip(cols, row)) for row in rows]


def _existing_columns(con, table: str) -> Tuple[str, ...]:
    """The columns the platform table actually has."""
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
        [SCHEMA, table],
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _platform_rows(con, table: str, column: str, value: str, limit: int,
                   columns: Optional[Tuple[str, ...]] = None) -> Tuple[List[Dict[str, Any]], int]:
    """The rows of a platform table that belong to one user.

    Returns the rows (capped at *limit*) and the total number of
    matching rows, so a truncated export can say what it left out.
    """
    select = ", ".join(columns) if columns else "*"
    total = con.execute(
        f"SELECT COUNT(*) FROM {SCHEMA}.{table} WHERE {column} = ?", [value]
    ).fetchone()[0]
    rows = con.execute(
        f"SELECT {select} FROM {SCHEMA}.{table} WHERE {column} = ? LIMIT ?",
        [value, limit + 1],
    ).fetchall()
    return _decoded(con, rows)[:limit], int(total)


def _tenant_rows(con, schema: str, table: str, limit: int) -> Tuple[List[Dict[str, Any]], int]:
    """The rows of one tenant table."""
    quoted = f'"{schema}"."{table}"'
    total = con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
    rows = con.execute(f"SELECT * FROM {quoted} LIMIT ?", [limit + 1]).fetchall()
    return _decoded(con, rows)[:limit], int(total)


def _has_personal_owner(con, schema: str, table: str) -> bool:
    """True when the table's rows belong to one person."""
    cols = con.execute(f'DESCRIBE "{schema}"."{table}"').fetchall()
    return any(str(col[0]).lower() in _PERSONAL_OWNER_COLUMNS for col in cols)


def _export_profile(user_id: str) -> Dict[str, Any]:
    """The caller's account profile — never any secret material."""
    from api.auth import user_manager

    info = user_manager.get_user_info_by_id(user_id) or {}
    return {
        "user_id": info.get("user_id", user_id),
        "username": info.get("username", ""),
        "email": info.get("email", ""),
        "email_verified": bool(info.get("email_verified", False)),
        "full_name": info.get("full_name", ""),
        "role": info.get("role", ""),
        "tenant_id": info.get("tenant_id", ""),
        "is_active": bool(info.get("is_active", True)),
        "created_at": info.get("created_at", ""),
        "last_login": info.get("last_login", ""),
        "sso_provider": info.get("sso_provider", ""),
        "has_api_key": bool(info.get("has_api_key", False)),
    }


def _export_subscription(con, user_id: str) -> Dict[str, Any]:
    """The caller's plan and subscription row."""
    out: Dict[str, Any] = {
        "plan": "", "status": "", "started_at": "", "expires_at": "",
        "effective_plan": "",
    }
    try:
        row = con.execute(
            f"SELECT plan, status, started_at, expires_at "
            f"FROM {SCHEMA}.user_subscriptions WHERE user_id = ?",
            [user_id],
        ).fetchone()
    except Exception:  # noqa: BLE001 — the table may not exist yet
        row = None
    if row:
        out.update({
            "plan": row[0] or "",
            "status": row[1] or "",
            "started_at": _json_safe(row[2]),
            "expires_at": _json_safe(row[3]),
        })
    try:
        from api.billing.entitlements import get_plan_for_user

        out["effective_plan"] = get_plan_for_user(user_id)
    except Exception:  # noqa: BLE001 — the plan is a nicety, not the export
        out["effective_plan"] = out["plan"]
    return out


def _tenant_data(con, tenant_id: str, limit: int) -> Dict[str, Any]:
    """The tenant's own data tables and their rows.

    The tenant's tables are **shared by every member of the tenant**, so
    they are exported with a note saying so.  The platform's own tables
    are never included: they hold other people's rows.
    """
    schema = tenant_schema(tenant_id)
    out: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "schema": schema,
        "shared": True,
        "note": (
            "The tables below are **shared**: they belong to the tenant, not "
            "to one person, so every member of the tenant reads and writes "
            "them. They are included because they are the workspace you use — "
            "a per-user deletion cannot remove them without destroying the "
            "other members' data."
        ),
        "tables": {},
    }
    try:
        names = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = ? ORDER BY table_name",
            [schema],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — reported, never hidden
        out["error"] = str(exc)
        return out
    for (name,) in names:
        if not _safe_ident(name) or name in _PLATFORM_TABLES:
            continue
        try:
            if _has_personal_owner(con, schema, name):
                continue
            rows, total = _tenant_rows(con, schema, name, limit)
        except Exception:  # noqa: BLE001 — one bad table must not stop the export
            continue
        out["tables"][name] = {
            "row_count": total,
            "rows": rows,
            "truncated": total > len(rows),
        }
    return out


def _row_lists(doc: Dict[str, Any]) -> List[Tuple[Dict[str, Any], str]]:
    """The (container, key) pairs of every row list in the document."""
    found: List[Tuple[Dict[str, Any], str]] = [
        (doc, key) for key, value in doc.items() if isinstance(value, list)
    ]
    tables = (doc.get("tenant_data") or {}).get("tables") or {}
    for entry in tables.values():
        if isinstance(entry, dict) and isinstance(entry.get("rows"), list):
            found.append((entry, "rows"))
    return found


def _size(doc: Dict[str, Any]) -> int:
    """The serialised size of the document, in bytes."""
    return len(json.dumps(doc, default=str).encode("utf-8"))


def _fit_byte_budget(doc: Dict[str, Any], budget: int) -> bool:
    """Cut the document down to *budget* bytes. True when it had to cut."""
    if _size(doc) <= budget:
        return False
    lists = _row_lists(doc)
    for _ in range(24):
        if _size(doc) <= budget:
            break
        if not any(len(container[key]) for container, key in lists):
            break
        for container, key in lists:
            rows = container[key]
            container[key] = rows[: len(rows) // 2]
    return True


def _truncation_report(doc: Dict[str, Any], available: Dict[str, int],
                       limit: int) -> Dict[str, Any]:
    """What the export left out, and why."""
    by_table: Dict[str, Any] = {}
    for name, total in available.items():
        rows = doc.get(name)
        if isinstance(rows, list) and total > len(rows):
            by_table[name] = {
                "rows_exported": len(rows),
                "rows_available": total,
                "reason": "row_limit" if len(rows) >= limit else "byte_limit",
            }
    tables = (doc.get("tenant_data") or {}).get("tables") or {}
    for name, entry in tables.items():
        rows = entry.get("rows") or []
        total = int(entry.get("row_count", 0))
        if total > len(rows):
            by_table[f"tenant_data.{name}"] = {
                "rows_exported": len(rows),
                "rows_available": total,
                "reason": "row_limit" if len(rows) >= limit else "byte_limit",
            }
    return {
        "row_limit_per_table": limit,
        "byte_limit": MAX_EXPORT_BYTES,
        "by_table": by_table,
    }


def _collect_platform_sections(platform, user_id: str, limit: int):
    """The per-user platform rows, how many were available, what was missing."""
    sections: Dict[str, Any] = {}
    available: Dict[str, int] = {}
    unavailable: Dict[str, str] = {}
    for name, (table, column) in _EXPORT_TABLES.items():
        wanted = _API_KEY_COLUMNS if name == "api_keys" else None
        try:
            if wanted is not None:
                present = _existing_columns(platform, table)
                wanted = tuple(col for col in wanted if col in present)
            rows, total = _platform_rows(
                platform, table, column, user_id, limit, wanted,
            )
        except Exception as exc:  # noqa: BLE001 — reported, never hidden
            logger.warning("the export could not read %s: %s", table, exc)
            # The message stays generic: a DuckDB binder error names the
            # table's columns, which would put a secret column's *name*
            # into the document.
            unavailable[name] = f"the {table} table is not available"
            rows, total = [], 0
        sections[name] = rows
        available[name] = total
    return sections, available, unavailable


def build_user_export(user_id: str, tenant_id: str, *, con=None,
                      platform_con=None) -> Dict[str, Any]:
    """Everything the platform holds about *user_id*.

    The document covers the profile, the plan and subscription, the
    chats and their messages, the query history, the preferences, the
    notification preferences, the API-key **metadata** (never the key
    and never its hash), the comments, the annotations, the dashboards
    and the rows of the tenant's own data tables.

    Two hard caps apply: at most ``MAX_EXPORT_ROWS_PER_TABLE`` rows per
    table and at most ``MAX_EXPORT_BYTES`` bytes in total.  A cap that
    is hit is reported in ``truncated`` / ``truncation`` — data is never
    dropped silently.

    *con* reads the tenant's tables and *platform_con* the platform
    tables; both default to connections opened here.  Another tenant's
    tables are never reachable from either.
    """
    if con is None:
        with _export_connections(tenant_id) as (tenant_con, plat_con):
            return build_user_export(
                user_id, tenant_id, con=tenant_con, platform_con=plat_con
            )

    platform = platform_con if platform_con is not None else con
    # The export reads the platform tables with ``SELECT *``, so a table
    # left in an older shape would silently produce a document with a
    # different — and incomplete — column set.
    _ensure_platform_shape(platform)
    limit = MAX_EXPORT_ROWS_PER_TABLE
    sections, available, unavailable = _collect_platform_sections(
        platform, user_id, limit
    )
    doc: Dict[str, Any] = {
        "export_version": EXPORT_VERSION,
        "generated_at": _now_iso(),
        "subject": {"user_id": user_id, "tenant_id": tenant_id},
        "profile": _export_profile(user_id),
        "subscription": _export_subscription(platform, user_id),
    }
    doc.update(sections)
    doc["tenant_data"] = _tenant_data(con, tenant_id, limit)
    doc["unavailable"] = unavailable
    _fit_byte_budget(doc, MAX_EXPORT_BYTES)
    doc["truncation"] = _truncation_report(doc, available, limit)
    doc["truncated"] = bool(doc["truncation"]["by_table"])
    return _json_safe(_scrub_secrets(doc))


def delete_user_records(user_id: str, *, con=None) -> Dict[str, Any]:
    """Remove one user's platform-scoped records.

    **Policy.**  A tenant's *data tables* are shared by every member of
    the tenant, so one person's rows cannot be separated from the
    tenant's rows.  A per-user erasure therefore removes only the
    records that belong to the user alone — chats, messages, query
    history, preferences, notifications, webhooks, API keys, the
    subscription, the comments, the annotations and the dashboards — and
    leaves the tenant's tables untouched.  Removing the tenant's tables
    is a tenant-level operation (the account-delete cascade), never a
    per-user one: doing it would destroy the other members' data.

    Returns a summary of what was removed.
    """
    if con is None:
        from api.db import get_connection

        return delete_user_records(user_id, con=get_connection())

    summary: Dict[str, Any] = {
        "tables": [], "rows": 0, "errors": [], "tenant_tables_removed": [],
    }
    if not user_id:
        return summary
    for table, column in _ERASURE_TABLES:
        if not (_safe_ident(table) and _safe_ident(column)):
            continue
        try:
            if not _table_exists(con, SCHEMA, table):
                continue
            result = con.execute(
                f"DELETE FROM {SCHEMA}.{table} WHERE {column} = ?", [user_id]
            )
            row = result.fetchone()
            count = int(row[0]) if row else 0
        except Exception as exc:  # noqa: BLE001 — one bad table must not stop the rest
            summary["errors"].append({"table": table, "error": str(exc)})
            continue
        if count:
            summary["tables"].append(table)
            summary["rows"] += count
    return summary
