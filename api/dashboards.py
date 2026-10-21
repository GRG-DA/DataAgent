"""
Dashboards Module — widget/layout dashboards + Power BI (.pbit) export.

Stores dashboards in the existing ``analytics.dashboards`` table (widgets,
layout and filters as JSON columns) and exposes:

* a ``DashboardManager`` CRUD layer (used by the web UI and by the
  ``pbix_export`` / ``verify_export`` CLIs via ``get_dashboard_manager()``),
* a FastAPI router at ``/dashboards`` with the REST endpoints plus
  ``GET /dashboards/{id}/export/pbit`` which builds a real Power BI Template
  through ``export.pbix_export.build_powerbi_pbit``.
"""

import io
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.billing.gate import require_feature
from api.billing.schema import FEATURE_DASHBOARD_EXPORT

from config import settings
from api.db import get_connection
from api.billing.metering import UsageLimitError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DashboardManager:
    """CRUD over the ``analytics.dashboards`` table (widget/layout JSON)."""

    def __init__(self, db_path: Optional[str] = None, schema: str = "analytics"):
        self.db_path = db_path or str(settings.duckdb.database_path)
        self.schema = schema
        self._init_tables()

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        # Shared process-wide connection (api/db.py) — NOT a fresh connect per
        # request. Opening a new read-write connection to warehouse.duckdb on
        # every call collided with the app's own handle on Windows and caused
        # intermittent "file in use" 500s. DuckDB is autocommit, so DDL/DML on
        # the shared connection persist immediately.
        return get_connection()

    def _init_tables(self) -> None:
        conn = self._get_conn()
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.schema}.dashboards (
                    id VARCHAR,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    owner VARCHAR DEFAULT '',
                    owner_id VARCHAR DEFAULT '',
                    layout JSON DEFAULT '{{"columns":12,"row_height":80}}',
                    widgets JSON DEFAULT '[]',
                    filters JSON DEFAULT '[]',
                    tags VARCHAR[] DEFAULT [],
                    is_shared BOOLEAN DEFAULT FALSE,
                    is_template BOOLEAN DEFAULT FALSE,
                    shared_with_users VARCHAR[] DEFAULT [],
                    shared_with_groups VARCHAR[] DEFAULT [],
                    refresh_interval INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    @staticmethod
    def _parse_row(row: Any, cols: List[str]) -> Dict[str, Any]:
        dash = dict(zip(cols, row))
        for field in ("layout", "widgets", "filters"):
            val = dash.get(field)
            if isinstance(val, str):
                try:
                    dash[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
            if field == "layout" and not isinstance(dash.get(field), dict):
                dash[field] = {"columns": 12, "row_height": 80}
            if field in ("widgets", "filters") and not isinstance(dash.get(field), list):
                dash[field] = []
        return dash

    def list(self, owner_id: str = "", allow_all: bool = False) -> List[Dict[str, Any]]:
        """List dashboards visible to *owner_id*.

        Without ``owner_id`` and without ``allow_all`` the result is empty
        rather than "every dashboard in the warehouse" — an unfiltered list
        exposed every user's dashboards (queries + data previews) to any
        authenticated caller.
        """
        if not owner_id and not allow_all:
            return []
        conn = self._get_conn()
        # Shared connection is not thread-safe (execute can step another
        # thread's pending result) — same lock as /bi/query.
        from api.db import _bi_query_lock
        with _bi_query_lock:
            if allow_all:
                rows = conn.execute(
                    "SELECT * FROM analytics.dashboards ORDER BY updated_at DESC"
                ).fetchall()
            elif owner_id:
                rows = conn.execute(
                    "SELECT * FROM analytics.dashboards WHERE owner_id = ? ORDER BY updated_at DESC",
                    [owner_id],
                ).fetchall()
            else:
                rows = []
        cols = [d[0] for d in conn.description]
        return [self._parse_row(r, cols) for r in rows]

    def get(self, dash_id: str, owner_id: str = "", allow_all: bool = False) -> Optional[Dict[str, Any]]:
        """Fetch a dashboard, enforcing ownership.

        A dashboard is visible to its owner, or to an admin
        (``allow_all``).  Legacy rows with no owner are admin-only — see
        :meth:`_owns`.
        """
        conn = self._get_conn()
        # Shared connection is not thread-safe (execute can step another
        # thread's pending result) — same lock as /bi/query.
        from api.db import _bi_query_lock
        with _bi_query_lock:
            row = conn.execute(
                "SELECT * FROM analytics.dashboards WHERE id = ?", [dash_id]
            ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.description]
        dash = self._parse_row(row, cols)
        if not allow_all and not self._owns(dash, owner_id):
            return None
        return dash

    @staticmethod
    def _owns(dash: Dict[str, Any], owner_id: str) -> bool:
        """True when *owner_id* may access *dash*.

        Legacy dashboards (``owner_id = ''``) are **admin-only**.  They used
        to be visible to every authenticated caller, which in a multi-tenant
        deployment handed every customer every dashboard created before
        ownership existed (ADR-009 D1).  An admin still sees them through
        ``allow_all``.
        """
        if not owner_id:
            return False
        return (dash.get("owner_id") or "") == owner_id

    def create(self, name: str, description: str = "", owner: str = "",
               owner_id: str = "", widgets: Optional[List[Dict]] = None,
               filters: Optional[List[Dict]] = None,
               layout: Optional[Dict] = None) -> Dict[str, Any]:
        # Enforce the plan's dashboard cap for authenticated users.
        if owner_id:
            from api.billing.metering import check_usage, UsageLimitError
            usage = check_usage(owner_id, "dashboards")
            if not usage.get("allowed", True):
                raise UsageLimitError(
                    "dashboards", usage.get("used", 0), usage.get("limit", 0)
                )
        dash_id = str(uuid.uuid4())
        now = _now_iso()
        widgets_json = json.dumps(widgets or [])
        filters_json = json.dumps(filters or [])
        layout_json = json.dumps(layout or {"columns": 12, "row_height": 80})
        conn = self._get_conn()
        from api.db import _bi_query_lock
        with _bi_query_lock:
            conn.execute(
                """INSERT INTO analytics.dashboards
                   (id, name, description, owner, owner_id, layout, widgets, filters,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, CAST(? AS JSON), CAST(? AS JSON), CAST(? AS JSON), ?, ?)""",
                [dash_id, name, description, owner, owner_id, layout_json,
                 widgets_json, filters_json, now, now],
            )
        if owner_id:
            from api.billing.metering import record_usage
            record_usage(owner_id, "dashboards")
        # Read the row back without an ownership filter: this is the row we
        # just created, and the caller is its owner.
        dash = self.get(dash_id, owner_id=owner_id, allow_all=True)
        return dash or {"id": dash_id, "name": name}

    def update(self, dash_id: str, allow_all: bool = False, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Update a dashboard (ownership-checked).

        ``owner_id`` may arrive in *kwargs* (the session owner, injected by
        the route). A caller may not reassign a dashboard to somebody else:
        the stored owner_id is preserved unless the caller is an admin.
        """
        owner_id = kwargs.get("owner_id") or ""
        current = self.get(dash_id, owner_id=owner_id, allow_all=allow_all)
        if not current:
            return None
        col_map: Dict[str, str] = {
            "name": "name",
            "description": "description",
            "owner": "owner",
            "owner_id": "owner_id",
            "layout": "layout",
            "widgets": "widgets",
            "filters": "filters",
        }
        # Rewrite the row wholesale via a FRESH connection (autocommit per
        # statement). An in-place UPDATE on the shared process-wide connection
        # trips a DuckDB ART-index PK violation under concurrent use; a clean
        # connection plus DELETE→INSERT avoids both the stale-index state and
        # the same-key-delete+insert-in-one-transaction limitation.
        merged = {k: current.get(k) for k in
                  ("name", "description", "owner", "owner_id",
                   "layout", "widgets", "filters")}
        for k, v in kwargs.items():
            if k in col_map:
                merged[k] = v
        # Ownership is not transferable by a non-admin: keep the stored owner.
        if not allow_all:
            merged["owner_id"] = current.get("owner_id") or ""
        now = _now_iso()
        created_at = current.get("created_at") or now
        # Write on the SAME process-wide shared connection (under the query lock).
        # Opening a second duckdb.connect() to the same file while the shared
        # connection is alive makes the fresh connection's writes invisible /
        # inconsistent to get(), which intermittently returned a 404 after a save,
        # and is the same root cause as the ART-index corruption the other helpers
        # avoid. PK/UNIQUE constraints are already stripped and secondary indexes
        # healed, so DELETE→INSERT on the single shared connection is safe.
        from api.db import _bi_query_lock

        conn = self._get_conn()
        with _bi_query_lock:
            conn.execute("DELETE FROM analytics.dashboards WHERE id = ?", [dash_id])
            conn.execute(
                """INSERT INTO analytics.dashboards
                   (id, name, description, owner, owner_id, layout, widgets, filters,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, CAST(? AS JSON), CAST(? AS JSON), CAST(? AS JSON),
                           CAST(? AS TIMESTAMP), ?)""",
                [dash_id,
                 merged["name"] or "",
                 merged["description"] or "",
                 merged["owner"] or "",
                 merged["owner_id"] or "",
                 json.dumps(merged["layout"] or {"columns": 12, "row_height": 80}),
                 json.dumps(merged["widgets"] or []),
                 json.dumps(merged["filters"] or []),
                 created_at,
                 now],
            )
        return self.get(dash_id, owner_id=owner_id, allow_all=True)

    def delete(self, dash_id: str, owner_id: str = "", allow_all: bool = False) -> bool:
        """Delete a dashboard (ownership-checked).

        Returns False when the dashboard does not exist *or* belongs to
        somebody else — the caller cannot distinguish, so an attacker cannot
        probe for the existence of another user's dashboards.
        """
        if not self.get(dash_id, owner_id=owner_id, allow_all=allow_all):
            return False
        # Run the DELETE on the shared process-wide connection under the lock.
        # A second duckdb.connect() to the same file while the shared connection
        # is alive is the documented root cause of the ART-index bug that this
        # comment mentions; routing through the single shared connection avoids
        # it and keeps the deletion immediately visible to subsequent reads.
        from api.db import _bi_query_lock

        conn = self._get_conn()
        row_owner = None
        with _bi_query_lock:
            row = conn.execute(
                "SELECT owner_id FROM analytics.dashboards WHERE id = ?", [dash_id]
            ).fetchone()
            if row:
                row_owner = row[0]
            conn.execute("DELETE FROM analytics.dashboards WHERE id = ?", [dash_id])
        if row_owner:
            try:
                from api.billing.metering import release_usage
                release_usage(row_owner, "dashboards")
            except Exception:  # noqa: BLE001
                pass
        return True

    def set_shared(self, dash_id: str, is_shared: bool) -> Optional[Dict[str, Any]]:
        """Toggle public shareability of a dashboard."""
        # Internal helper: no ownership filter (callers check access first).
        current = self.get(dash_id, allow_all=True)
        if not current:
            return None
        # Write on the shared process-wide connection under the lock (see update()).
        now = _now_iso()
        created_at = current.get("created_at") or now
        merged = {k: current.get(k) for k in
                  ("name", "description", "owner", "owner_id",
                   "layout", "widgets", "filters")}
        from api.db import _bi_query_lock

        conn = self._get_conn()
        with _bi_query_lock:
            conn.execute("DELETE FROM analytics.dashboards WHERE id = ?", [dash_id])
            conn.execute(
                """INSERT INTO analytics.dashboards
                   (id, name, description, owner, owner_id, layout, widgets, filters,
                    is_shared, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, CAST(? AS JSON), CAST(? AS JSON), CAST(? AS JSON),
                           ?, CAST(? AS TIMESTAMP), ?)""",
                [dash_id,
                 merged["name"] or "",
                 merged["description"] or "",
                 merged["owner"] or "",
                 merged["owner_id"] or "",
                 json.dumps(merged["layout"] or {"columns": 12, "row_height": 80}),
                 json.dumps(merged["widgets"] or []),
                 json.dumps(merged["filters"] or []),
                 bool(is_shared),
                 created_at,
                 now],
            )
        return self.get(dash_id, allow_all=True)


def get_dashboard_manager() -> DashboardManager:
    """Factory used by the web API, ``pbix_export`` CLI and ``verify_export``."""
    return DashboardManager()


# ── REST router ───────────────────────────────────────────────────────────

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


class DashboardBody(BaseModel):
    name: str = "Untitled Dashboard"
    description: str = ""
    owner: str = ""
    owner_id: str = ""
    layout: Optional[Dict[str, Any]] = Field(default=None)
    widgets: Optional[List[Dict[str, Any]]] = Field(default=None)
    filters: Optional[List[Dict[str, Any]]] = Field(default=None)
    # Full engine-agnostic dashboard config (e.g. the blank dashboard's whole
    # `_dash` object: pages, theme, refresh, canvas settings, share flags...).
    # Persisted inside the `layout` JSON column so it round-trips losslessly.
    config: Optional[Dict[str, Any]] = Field(default=None)
    is_shared: Optional[bool] = Field(default=None)


def _config_to_layout(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Store a dashboard config together with any layout, namespaced so the
    existing PBI/exporter consumers (which expect layout.slicers/palette etc.)
    are unaffected."""
    if config is None:
        return {}
    return {"_blankdash": config}


def _session_owner(request: Request) -> Optional[str]:
    """Return the authenticated user's id, or None when unauthenticated.

    Ownership is always derived from the *session*, never from the request
    body: a client-supplied ``owner_id`` let any user claim (and then read
    or overwrite) another user's dashboards.
    """
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "user_id", None):
        return str(user.user_id)
    return None


def _is_admin(request: Request) -> bool:
    user = getattr(request.state, "user", None)
    return bool(user is not None and getattr(user, "is_admin", False))


@router.get("")
async def list_dashboards(request: Request):
    try:
        mgr = get_dashboard_manager()
        owner_id = _session_owner(request)
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return {"success": True, "dashboards": mgr.list(
            owner_id=owner_id, allow_all=_is_admin(request))}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list dashboards failed")
        return {"success": False, "error": str(e)}


@router.post("")
async def create_dashboard(body: DashboardBody, request: Request):
    try:
        mgr = get_dashboard_manager()
        # Metering must track the AUTHENTICATED user, not a client-supplied
        # owner_id (which the frontend fills with "anonymous" when no
        # username is stored). Override with the server-side identity so the
        # plan's dashboard cap cannot be bypassed.
        auth_user = getattr(request.state, "user", None)
        if auth_user is not None and getattr(auth_user, "user_id", None):
            owner_id = auth_user.user_id
            owner = getattr(auth_user, "username", None) or body.owner
        else:
            owner_id = body.owner_id
            owner = body.owner
        layout = body.layout or _config_to_layout(body.config)
        if body.config and body.layout is None:
            layout = _config_to_layout(body.config)
        last = mgr.create(name=body.name, description=body.description,
                          owner=owner, owner_id=owner_id,
                          widgets=body.widgets, filters=body.filters,
                          layout=layout)
        if last and (body.is_shared is not None):
            mgr.set_shared(last.get("id"), body.is_shared)
        return {"success": True, "dashboard": last}
    except UsageLimitError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("create dashboard failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{dash_id}")
async def get_dashboard(dash_id: str, request: Request):
    mgr = get_dashboard_manager()
    owner_id = _session_owner(request)
    if owner_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    dash = mgr.get(dash_id, owner_id=owner_id, allow_all=_is_admin(request))
    if not dash:
        raise HTTPException(status_code=404, detail="dashboard not found")
    return {"success": True, "dashboard": dash}


@router.put("/{dash_id}")
async def update_dashboard(dash_id: str, body: DashboardBody, request: Request):
    mgr = get_dashboard_manager()
    owner_id = _session_owner(request)
    if owner_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    layout = body.layout
    if body.config is not None:
        layout = _config_to_layout(body.config)
    # The owner is taken from the session, never from the body: accepting a
    # client-supplied owner_id let an attacker name the victim's id to pass
    # the ownership check and overwrite their dashboard.
    dash = mgr.update(dash_id, name=body.name, description=body.description,
                      owner=body.owner, owner_id=owner_id,
                      layout=layout, widgets=body.widgets, filters=body.filters,
                      allow_all=_is_admin(request))
    if not dash:
        raise HTTPException(status_code=404, detail="dashboard not found")
    if body.is_shared is not None:
        mgr.set_shared(dash_id, body.is_shared)
        dash = mgr.get(dash_id, owner_id=owner_id, allow_all=_is_admin(request))
    return {"success": True, "dashboard": dash}


@router.delete("/{dash_id}")
async def delete_dashboard(dash_id: str, request: Request):
    mgr = get_dashboard_manager()
    owner_id = _session_owner(request)
    if owner_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not mgr.get(dash_id, owner_id=owner_id, allow_all=_is_admin(request)):
        raise HTTPException(status_code=404, detail="dashboard not found")
    mgr.delete(dash_id, owner_id=owner_id, allow_all=_is_admin(request))
    return {"success": True}


@router.post("/{dash_id}/share")
async def share_dashboard(dash_id: str, request: Request,
                           body: Optional[Dict[str, Any]] = None):
    """Mark a dashboard as shareable and mint a view-only share token.

    Returns ``{ token, shareUrl }``. The token can be resolved by anyone via
    ``GET /dashboards/shared/{token}`` (or the generic ``GET /share/{token}``).
    """
    body = body or {}
    mgr = get_dashboard_manager()
    owner_id = _session_owner(request)
    if owner_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not mgr.get(dash_id, owner_id=owner_id, allow_all=_is_admin(request)):
        raise HTTPException(status_code=404, detail="dashboard not found")
    mgr.set_shared(dash_id, True)
    from api.collaboration import get_collaboration_manager
    cmgr = get_collaboration_manager()
    # `shared_by` is the session user, not a client-supplied string.
    user = getattr(request.state, "user", None)
    shared_by = getattr(user, "username", None) or "anonymous"
    link = cmgr.create_share_link(
        target_type="dashboard",
        target_id=dash_id,
        shared_by=shared_by,
        permission="view",
        shared_with=body.get("shared_with", ""),
        expires_in_days=body.get("expires_in_days"),
    )
    return {"success": True, "token": link.token, "shareUrl": f"/dashboards/shared/{link.token}"}


@router.get("/shared/{token}")
async def get_shared_dashboard(token: str):
    """Public, view-only access to a dashboard via a share token."""
    from api.collaboration import get_collaboration_manager
    cmgr = get_collaboration_manager()
    link = cmgr.get_share_link(token)
    if not link or link.get("target_type") != "dashboard":
        raise HTTPException(status_code=404, detail="shared dashboard not found or expired")
    mgr = get_dashboard_manager()
    dash = mgr.get(link["target_id"])
    if not dash or not dash.get("is_shared"):
        raise HTTPException(status_code=404, detail="shared dashboard not found")
    return {"success": True, "dashboard": dash}


@router.get("/{dash_id}/export/pbit")
async def export_dashboard_pbit(
    dash_id: str,
    request: Request,
    _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
):
    """Build and download a Power BI Report (.pbix) for a saved dashboard.

    The download is a ``.pbix`` — NOT a ``.pbit`` template. Power BI Desktop
    opens ``.pbit`` files in "no data" mode, so the charts render empty; a
    ``.pbix`` report opens with the model's DATATABLE data populated.
    """
    mgr = get_dashboard_manager()
    dash = mgr.get(dash_id, owner_id=_session_owner(request) or "",
                   allow_all=_is_admin(request))
    if not dash:
        raise HTTPException(status_code=404, detail="dashboard not found")
    try:
        from export import pbix_export
        # Shared process-wide connection — never opened/closed per request.
        con = get_connection()

        def run_query(sql: str):
            return con.execute(sql).df()

        def load_table(table: str, limit: int):
            return con.execute(f'SELECT * FROM "{table}" LIMIT {max(1, int(limit))}').df()

        data = pbix_export.build_powerbi_pbit(dash, run_query, load_table)
    except Exception as e:
        logger.exception("pbit export failed")
        raise HTTPException(status_code=500, detail=f"pbit export failed: {e}")

    fname = re.sub(r'[\\/:*?"<>|]+', "_", str(dash.get("name") or "dashboard")).strip() or "dashboard"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}.pbix"'},
    )


# ── Async Dashboard Export (PDF / PPTX / XLSX) ─────────────────────────
# Follows the Power BI 3-step pattern from the Deep Research Report:
#   1. POST  /dashboards/{id}/export/{format}  → {jobId, status: "queued"}
#   2. GET   /dashboards/export/{jobId}         → poll status
#   3. GET   /dashboards/export/download/{jobId} → download file

@router.post("/{dash_id}/export/{fmt}")
async def export_dashboard(
    dash_id: str,
    fmt: str,
    request: Request,
    _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
):
    """Create an async export job for a dashboard.

    Supported formats: pdf, pptx, xlsx.  Returns a job ID that can be
    polled via GET /dashboards/export/{jobId} until status = "succeeded",
    then the file is downloaded via GET /dashboards/export/download/{jobId}.

    For blankdash (Dashboard Studio) dashboards whose config is stored in
    the layout column with pages[].widgets[] and x/y/w/h positions, the
    PPTX/XLSX exports preserve the visual layout on each page.
    """
    if fmt not in ("pdf", "pptx", "xlsx"):
        raise HTTPException(status_code=400, detail=f"unsupported format: {fmt}")
    mgr = get_dashboard_manager()
    dash = mgr.get(dash_id, owner_id=_session_owner(request) or "",
                   allow_all=_is_admin(request))
    if not dash:
        raise HTTPException(status_code=404, detail="dashboard not found")
    try:
        from export.dashboard_export import create_export_job
        con = get_connection()

        def run_query(sql: str):
            return con.execute(sql).df()

        def load_table(table: str, limit: int = 200):
            return con.execute(f'SELECT * FROM "{table}" LIMIT {max(1, int(limit))}').df()

        job_id = create_export_job(dash_id, fmt, dash, run_query, load_table)
        return {"success": True, "jobId": job_id}
    except Exception as e:
        logger.exception("export job creation failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/{job_id}")
async def get_export_status(
    job_id: str,
    _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
):
    """Poll an export job's status (Power BI's GetExportToFileStatus pattern)."""
    from export.dashboard_export import get_job_status
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return {"success": True, "job": status}


@router.get("/export/download/{job_id}")
async def download_export(
    job_id: str,
    _: None = Depends(require_feature(FEATURE_DASHBOARD_EXPORT)),
):
    """Download the file produced by a completed export job."""
    from export.dashboard_export import get_job_data, get_job_filename
    data = get_job_data(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found or not yet completed")
    fname = get_job_filename(job_id)
    ext = fname.rsplit(".", 1)[-1] if "." in fname else "bin"
    content_types = {
        "pdf": "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    ct = content_types.get(ext, "application/octet-stream")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=ct,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
