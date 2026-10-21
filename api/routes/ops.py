"""Operational endpoints: Prometheus metrics and a deep health check.

Three families of route, all additive -- the existing ``/health``, ``/live``
and ``/ready`` routes in ``api/main.py`` are untouched:

* ``GET /metrics``            the Prometheus text exposition.
* ``GET /health/system``      the **deep** check: every dependency is probed
                              individually and reported with a boolean and a
                              short reason.
* ``GET /health/live``        liveness.  Never touches a dependency.
* ``GET /health/ready``       readiness.  The warehouse must be usable.

``api/main.py`` owns the app object, so this module only exports a router::

    from api.routes.ops import router
    app.include_router(router)

or, in one call, the router *and* the metrics middleware::

    from api.routes.ops import install_ops
    install_ops(app)

Authentication
--------------

The auth middleware's ``public_paths`` list (which this workstream may not
edit) lists ``/health``, ``/live`` and ``/ready`` but not the new paths, so
**the new endpoints require an authenticated caller** until the operator
adds them.  A Prometheus scraper should therefore present a bearer token,
and a Kubernetes probe should either use the existing public ``/live`` and
``/ready`` routes or be given a service-account token.

Set ``DATAAGENT_OPS_ADMIN_ONLY=1`` to restrict the deep health check to
administrators; the shallow ``/health`` route stays public either way, so a
probe never loses its liveness signal.

Nothing here raises: a broken dependency is a *report*, not a crash.  The
health endpoints are the first thing an operator looks at, so they have to
keep answering when the thing they describe is broken.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import time
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from api.metrics import PROMETHEUS_CONTENT_TYPE, render_prometheus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["operations"])

#: Components whose failure makes the deployment **unhealthy** (not ready).
CRITICAL_COMPONENTS = frozenset({"warehouse", "identity_store"})

#: When this environment variable is set, the deep health check is
#: restricted to administrators.  The shallow ``/health`` route stays
#: available to everyone, so a probe never loses its liveness signal.
ADMIN_ONLY_ENV = "DATAAGENT_OPS_ADMIN_ONLY"

#: A path that is safe to interpolate into DDL.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Absolute paths are deployment detail and must not reach a client.
_ABS_PATH = re.compile(r"(?:[A-Za-z]:\\|/)[^\s'\"]{2,}")

#: When the process started, for the uptime gauge.
_PROCESS_START = time.time()


# ── helpers ───────────────────────────────────────────────────────────────

def _safe_reason(exc: BaseException) -> str:
    """A short, non-leaking reason for a failed probe."""
    text = str(exc).strip()
    line = text.splitlines()[0] if text else type(exc).__name__
    line = _ABS_PATH.sub("<path>", line)
    return (line or type(exc).__name__)[:140]


def _component(
    ok: bool,
    reason: str,
    critical: bool = False,
    **details: Any,
) -> Dict[str, Any]:
    """One entry of the health report.

    ``status`` mirrors ``ok`` in the vocabulary the old shallow
    ``/health/system`` used ("healthy" / "unavailable"), so the existing
    contract still holds for callers that read ``components.<name>.status``.
    """
    return {
        "ok": bool(ok),
        "status": "healthy" if ok else "unavailable",
        "critical": bool(critical),
        "reason": str(reason),
        "details": details,
    }


def _tcp_reachable(url: str, timeout: float = 1.5) -> bool:
    """True when a TCP connection to the URL's host succeeds."""
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
    except Exception:  # noqa: BLE001 - a probe never raises
        return False


# ── component probes ──────────────────────────────────────────────────────

def _probe_api() -> Dict[str, Any]:
    """The API process itself."""
    return _component(
        True,
        "process is serving requests",
        critical=True,
        pid=os.getpid(),
    )


def _warehouse_schema() -> str:
    """The platform schema, validated as a bare identifier."""
    try:
        from config import settings

        schema = str(getattr(settings.duckdb, "schema", "") or "analytics")
    except Exception:  # noqa: BLE001
        schema = "analytics"
    return schema if _IDENTIFIER.match(schema) else "analytics"


def _probe_warehouse_read() -> Dict[str, Any]:
    """Is the warehouse readable?  ``SELECT 1`` on the shared connection.

    The shared connection is used on purpose: opening a *second* DuckDB
    connection to the same file fails once this process holds it, so a
    read-only side connection would report a false negative forever.
    """
    try:
        from api.db import _bi_query_lock, get_connection

        with _bi_query_lock:
            get_connection().execute("SELECT 1").fetchone()
        return _component(True, "warehouse is readable", critical=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("warehouse read probe failed: %s", exc)
        return _component(False, _safe_reason(exc), critical=True)


def _probe_warehouse_write() -> Dict[str, Any]:
    """Is the warehouse writable?  Scratch table, then clean it up."""
    schema = _warehouse_schema()
    probe = "_dataagent_health_probe"
    try:
        from api.db import _bi_query_lock, get_connection

        with _bi_query_lock:
            conn = get_connection()
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{schema}"."{probe}" (id INTEGER)')
            try:
                conn.execute(f'INSERT INTO "{schema}"."{probe}" VALUES (1)')
            finally:
                conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{probe}"')
        return _component(True, "warehouse is writable", critical=True, schema=schema)
    except Exception as exc:  # noqa: BLE001
        logger.warning("warehouse write probe failed: %s", exc)
        return _component(False, _safe_reason(exc), critical=True, schema=schema)


def _probe_warehouse() -> Dict[str, Any]:
    """The warehouse must be readable *and* writable."""
    read = _probe_warehouse_read()
    if not read["ok"]:
        return read
    write = _probe_warehouse_write()
    if not write["ok"]:
        return write
    return _component(
        True,
        "warehouse is readable and writable",
        critical=True,
        schema=write["details"].get("schema", _warehouse_schema()),
    )


def _probe_identity_store() -> Dict[str, Any]:
    """The SQLite identity store must exist and be readable."""
    try:
        import sqlite3

        from api.user_store import identity_db_path

        path = Path(str(identity_db_path()))
        if not path.exists():
            return _component(
                False, "identity database is missing", critical=True, path=path.name
            )
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            tenants = _count_tenants(conn)
        finally:
            conn.close()
        details: Dict[str, Any] = {"path": path.name}
        if tenants is not None:
            details["tenants"] = tenants
            _publish_tenant_count(tenants)
        return _component(True, "identity store is readable", critical=True, **details)
    except Exception as exc:  # noqa: BLE001
        logger.warning("identity store probe failed: %s", exc)
        return _component(False, _safe_reason(exc), critical=True)


def _count_tenants(conn) -> Optional[int]:
    """How many distinct tenants the identity store knows about."""
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT tenant_id) FROM users WHERE tenant_id <> ''"
        ).fetchone()
        return int(row[0]) if row else None
    except Exception:  # noqa: BLE001 - an older store has no tenant column
        return None


def _publish_tenant_count(count: int) -> None:
    try:
        from api.metrics import set_tenant_count

        set_tenant_count(count)
    except Exception:  # noqa: BLE001
        return


def _probe_audit_sink() -> Dict[str, Any]:
    """Is the configured audit sink reachable and writable?"""
    try:
        from api.audit_sinks import audit_redis_url, audit_sqlite_path, resolve_audit_sink

        sink = resolve_audit_sink()
        if sink == "redis":
            url = audit_redis_url()
            if not url:
                return _component(True, "redis audit sink is not configured", sink=sink)
            if _tcp_reachable(url):
                return _component(True, "redis audit sink is reachable", sink=sink)
            return _component(False, "redis audit sink is unreachable", sink=sink)
        if sink == "sqlite":
            path = Path(audit_sqlite_path())
            parent = path.parent if str(path.parent) else Path(".")
            if not parent.exists():
                return _component(False, "audit database directory is missing", sink=sink)
            if not os.access(str(parent), os.W_OK):
                return _component(False, "audit database directory is not writable", sink=sink)
            return _component(True, "sqlite audit sink is writable", sink=sink)
        return _component(True, "file audit sink is configured", sink=sink)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit sink probe failed: %s", exc)
        return _component(False, _safe_reason(exc))


def _llm_base_url() -> str:
    """The base URL of the configured LLM provider."""
    try:
        from config import settings

        provider = str(getattr(settings.llm, "provider", "") or "").lower()
        attribute = {
            "ollama": "base_url",
            "openai": "openai_base_url",
            "anthropic": "anthropic_base_url",
            "lmstudio": "lmstudio_base_url",
            "openrouter": "openrouter_base_url",
            "groq": "groq_base_url",
            "gemini": "gemini_base_url",
            "xai": "xai_base_url",
            "deepseek": "deepseek_base_url",
            "opencode": "opencode_base_url",
        }.get(provider, "")
        return str(getattr(settings.llm, attribute, "") or "") if attribute else ""
    except Exception:  # noqa: BLE001
        return ""


def _probe_llm_provider() -> Dict[str, Any]:
    """Cheap reachability probe -- never a model call.

    An unreachable provider means the app falls back to rule-based answers,
    which is **degraded**, not unhealthy: the data plane still works.
    """
    try:
        from config import settings

        provider = str(getattr(settings.llm, "provider", "") or "unknown")
        url = _llm_base_url()
        if not url:
            return _component(
                True, "no llm provider is configured", provider=provider
            )
        if _tcp_reachable(url, timeout=1.5):
            return _component(
                True, "llm provider is reachable", provider=provider
            )
        return _component(
            False,
            "llm provider is unreachable (rule-based fallback)",
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm provider probe failed: %s", exc)
        return _component(False, _safe_reason(exc))


def _probe_billing() -> Dict[str, Any]:
    """The external billing / entitlement service."""
    try:
        from config import settings

        external = str(getattr(settings.billing, "external_url", "") or "").strip()
        sync = str(getattr(settings.billing, "sync_url", "") or "").strip()
        if not external and not sync:
            return _component(
                True,
                "billing is not configured (free-plan deployment)",
                configured=False,
            )
        target = sync or external
        if _tcp_reachable(target):
            return _component(
                True,
                "billing service is reachable",
                configured=True,
                source="sync" if sync else "checkout",
            )
        return _component(
            False,
            "billing service is unreachable",
            configured=True,
            source="sync" if sync else "checkout",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("billing probe failed: %s", exc)
        return _component(False, _safe_reason(exc))


def _probe_opencode_server() -> Dict[str, Any]:
    """The local opencode server, when one is configured."""
    try:
        from config import settings

        url = str(getattr(settings.llm, "opencode_server_url", "") or "").strip()
        if not url:
            return _component(True, "opencode server is not configured", configured=False)
        if _tcp_reachable(url, timeout=1.5):
            return _component(True, "opencode server is reachable", configured=True)
        return _component(False, "opencode server is unreachable", configured=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("opencode probe failed: %s", exc)
        return _component(False, _safe_reason(exc))


#: The components the deep health check probes, in report order.
PROBE_NAMES = (
    "api",
    "warehouse",
    "identity_store",
    "audit_sink",
    "llm_provider",
    "billing",
    "opencode_server",
)


# ── the deep health check ─────────────────────────────────────────────────

def _probe_components() -> Dict[str, Any]:
    """Run every probe; a probe that raises is reported as a failure."""
    probes = (
        ("api", _probe_api),
        ("warehouse", _probe_warehouse),
        ("identity_store", _probe_identity_store),
        ("audit_sink", _probe_audit_sink),
        ("llm_provider", _probe_llm_provider),
        ("billing", _probe_billing),
        ("opencode_server", _probe_opencode_server),
    )
    components: Dict[str, Any] = {}
    for name, probe in probes:
        try:
            components[name] = probe()
        except Exception as exc:  # noqa: BLE001 - a probe must not crash the report
            logger.warning("health probe %r raised: %s", name, exc)
            components[name] = _component(
                False,
                _safe_reason(exc),
                critical=name in CRITICAL_COMPONENTS,
            )
    return components


def _overall_status(components: Dict[str, Any]) -> str:
    """``unhealthy`` when a critical component failed, else ``degraded``."""
    if any(not entry["ok"] and entry["critical"] for entry in components.values()):
        return "unhealthy"
    if any(not entry["ok"] for entry in components.values()):
        return "degraded"
    return "healthy"


def deep_health_check() -> Dict[str, Any]:
    """Probe every dependency and summarise the result.

    Never raises: a probe that blows up is reported as a failed component.
    The probe functions are resolved at call time so that a test (or an
    operator) can replace one of them.
    """
    components = _probe_components()
    # Publish the per-dependency verdict so the shipped Prometheus alert
    # rules (`dataagent_health_component_ok`) fire even when nobody is
    # scraping /health/system.
    try:
        from api.metrics import set_health_component

        for name, entry in components.items():
            set_health_component(name, bool(entry.get("ok")))
    except Exception:  # noqa: BLE001
        pass
    return {
        "status": _overall_status(components),
        "version": _version(),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uptime_seconds": round(time.time() - _PROCESS_START, 3),
        "components": components,
    }


def _version() -> str:
    try:
        from config import settings

        return str(getattr(settings.app, "version", "") or "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _admin_only() -> bool:
    """True when the deep health check is restricted to administrators."""
    raw = os.environ.get(ADMIN_ONLY_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_admin(request) -> bool:
    """True when the request's user holds the administrator role."""
    user = getattr(request.state, "user", None)
    if user is None:
        return False
    return bool(getattr(user, "is_admin", False)) or (
        getattr(user, "role", "") == "admin"
    )


#: Environment variable holding the bearer token a scraper presents.
METRICS_TOKEN_ENV = "METRICS_TOKEN"


def _metrics_authorized(request) -> bool:
    """True when the caller may read the metrics.

    The exposition names every API path, per-path request counts, response
    codes and the tenant count — that is a free map of the product's
    internals for anyone who can reach the port.  So it is **not** public
    by default:

    * ``METRICS_TOKEN`` set  → the caller must present that bearer token
      (this is how Prometheus scrapes it).
    * otherwise             → an authenticated administrator may read it.

    A deployment that genuinely wants it public (a trusted sidecar) can set
    ``METRICS_PUBLIC=1`` explicitly.
    """
    if (os.environ.get("METRICS_PUBLIC", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True

    expected = (os.environ.get(METRICS_TOKEN_ENV, "") or "").strip()
    if expected:
        header = request.headers.get("authorization", "")
        presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if presented:
            return hmac.compare_digest(presented, expected)
        # A session-authenticated admin may also read it.
        return _is_admin(request)

    # No token configured: fall back to admin-only rather than public.
    return _is_admin(request)


# ── routes ────────────────────────────────────────────────────────────────

@router.get("/metrics")
async def metrics(request: Request) -> PlainTextResponse:
    """The Prometheus exposition of the process-local registry.

    Requires ``METRICS_TOKEN`` (bearer) or an admin session — see
    :func:`_metrics_authorized`.
    """
    if not _metrics_authorized(request):
        return PlainTextResponse(
            content="# metrics require METRICS_TOKEN or an admin session\n",
            media_type=PROMETHEUS_CONTENT_TYPE,
            status_code=403,
        )
    return PlainTextResponse(
        content=render_prometheus(),
        media_type=PROMETHEUS_CONTENT_TYPE,
    )


@router.get("/health/system")
async def health_system(request: Request) -> JSONResponse:
    """The deep health check: every dependency, one boolean each."""
    if _admin_only() and not _is_admin(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "Administrator role required."},
        )
    report = deep_health_check()
    status_code = 503 if report["status"] == "unhealthy" else 200
    return JSONResponse(status_code=status_code, content=report)


@router.get("/health/live")
async def health_live() -> JSONResponse:
    """Liveness: the process is up.  Never touches a dependency."""
    return JSONResponse(
        status_code=200,
        content={"status": "alive", "version": _version()},
    )


@router.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness: the warehouse has to be usable."""
    try:
        probe = _probe_warehouse_read()
        ready = bool(probe.get("ok"))
        reason = str(probe.get("reason", ""))
    except Exception as exc:  # noqa: BLE001 - a probe never breaks the endpoint
        logger.warning("readiness probe failed: %s", exc)
        ready = False
        reason = _safe_reason(exc)
    if ready:
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "version": _version()},
        )
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "reason": reason},
    )


def install_ops(app) -> None:
    """Wire the ops router *and* the metrics middleware onto *app*.

    Equivalent to::

        app.include_router(router)
        app.add_middleware(MetricsMiddleware)

    but in one call, so the operator cannot forget the middleware.
    """
    from api.metrics import MetricsMiddleware

    app.include_router(router)
    app.add_middleware(MetricsMiddleware)
