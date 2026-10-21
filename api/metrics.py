"""Dependency-free Prometheus-style metrics for DataAgent.

The app deliberately ships **no** ``prometheus_client`` dependency: the
registry has to work inside a PyInstaller one-file build and inside a slim
container, and the Prometheus text exposition format is small enough to
implement directly (and to audit).

What this module guarantees
---------------------------

* **Bounded cardinality.**  A request path is *normalised* before it can
  become a label (``/tables/sales`` -> ``/tables/{id}``), so a customer
  cannot mint a new time series per table, per dashboard or per id.
* **No customer identifiers.**  A tenant is labelled with a *hash* of its
  id, never the id itself.  Emails, tokens, SQL text and file names never
  reach a label.
* **Allow-listed label values.**  Every free-form value (AI feature, query
  result, rate-limit bucket) is mapped through a fixed allow-list, so an
  unexpected input degrades to ``other`` instead of creating a series.
* **Never raises.**  Recording a metric must not be able to fail a request.

The registry is process-local.  A multi-replica deployment scrapes each
replica and sums in Prometheus, which is the normal arrangement.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Dict, Iterable, List, Tuple

__all__ = [
    "DURATION_BUCKETS",
    "MetricsMiddleware",
    "PROMETHEUS_CONTENT_TYPE",
    "install_metrics_middleware",
    "normalize_path",
    "observe_http_duration",
    "record_ai_request",
    "record_http_request",
    "record_rate_limited",
    "record_rows_returned",
    "record_sql_query",
    "record_tenant_request",
    "render_prometheus",
    "reset_metrics",
    "set_tenant_count",
    "tenant_hash",
]

#: The content type a Prometheus scraper expects.
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: Histogram bucket bounds (seconds) for HTTP request latency.
DURATION_BUCKETS: Tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

COUNTER = "counter"
GAUGE = "gauge"
HISTOGRAM = "histogram"

LabelKey = Tuple[Tuple[str, str], ...]


# ── Label value allow-lists ────────────────────────────────────────────────
# A value outside the allow-list becomes "other": the registry can never be
# talked into creating an unbounded number of series.

SQL_RESULTS = frozenset({"success", "error"})
AI_FEATURES = frozenset({"ask", "ask_sql", "insights", "dashboard_build"})
RATE_LIMIT_BUCKETS = frozenset({
    "login", "register", "accounts", "sql", "execute",
    "ask", "ask_sql", "upload", "load",
})

#: Path segments that are part of the route, not a customer identifier.
STATIC_SEGMENTS = frozenset({
    "account", "accounts", "activate", "activity", "agent", "agents", "alerts",
    "api-keys", "api-key", "ask", "ask-sql", "assets", "audit", "auth", "billing",
    "bi", "catalog", "clean", "collaboration", "config", "conversation",
    "conversations", "css", "csv", "dashboard", "dashboards", "data", "delete",
    "departments", "docs", "download", "email", "encrypt", "entitlements",
    "evaluate", "events", "excel", "execute", "experiment", "experiments",
    "export", "favicon.ico", "file", "files", "graph", "group", "groups",
    "health", "help", "history", "image", "images", "import", "info", "insights",
    "invite", "job", "jobs", "js", "key", "keys", "lib", "live", "load",
    "logs", "measures", "me", "merge", "metadata", "metrics", "model", "models",
    "notebook", "notebooks", "openapi", "openapi.json", "organization",
    "organizations", "org", "pipeline", "plan", "plans", "premium", "preview",
    "proxy", "proxies", "query", "redoc", "register", "reports", "reset",
    "schedule", "schedules", "schema", "settings", "signup", "sql", "start",
    "static", "status", "subscription", "subscriptions", "system", "table",
    "tables", "team", "teams", "tenant", "tenants", "trial", "upload", "user",
    "users", "v1", "v2", "version", "webhook", "workflows", "ws",
})

#: A segment following one of these parents is an identifier, never a route
#: word -- ``/tables/sales`` must not create a series per table name.
ID_PARENTS = frozenset({
    "agents", "alerts", "api-keys", "api-key", "catalog", "conversations",
    "dashboards", "experiments", "files", "jobs", "measures", "models",
    "notebooks", "organizations", "schedules", "subscriptions", "tables",
    "tenants", "users", "workflows",
})

#: Words that may follow an id-parent without being an identifier.
TYPE_SEGMENTS = frozenset({"bi", "csv", "excel", "json", "parquet", "xlsx", "zip"})

_HEXISH = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
_UUIDISH = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKENISH = re.compile(r"^[A-Za-z0-9_\-.:]{48,}$")


# ── Metric definitions ─────────────────────────────────────────────────────

#: name -> (kind, help text, ordered label names)
_METRIC_SPECS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "dataagent_http_requests_total": (
        COUNTER,
        "HTTP requests handled, by method, normalised path and response status.",
        ("method", "path", "status"),
    ),
    "dataagent_http_request_duration_seconds": (
        HISTOGRAM,
        "HTTP request duration in seconds, by normalised path.",
        ("path",),
    ),
    "dataagent_sql_queries_total": (
        COUNTER,
        "Warehouse SQL queries executed, by result.",
        ("result",),
    ),
    "dataagent_ai_requests_total": (
        COUNTER,
        "AI-assisted requests handled, by feature.",
        ("feature",),
    ),
    "dataagent_warehouse_rows_returned": (
        COUNTER,
        "Rows returned by warehouse queries.",
        (),
    ),
    "dataagent_rate_limited_total": (
        COUNTER,
        "Requests refused by the rate limiter, by bucket.",
        ("bucket",),
    ),
    "dataagent_tenants": (
        GAUGE,
        "Number of tenants known to this deployment.",
        (),
    ),
    "dataagent_tenant_requests_total": (
        COUNTER,
        "Requests served, by hashed tenant identifier.",
        ("tenant",),
    ),
    "dataagent_uptime_seconds": (
        GAUGE,
        "Seconds since the metrics registry was initialised.",
        (),
    ),
    "dataagent_build_info": (
        GAUGE,
        "Build information; the value is always 1.",
        ("version",),
    ),
    "dataagent_health_component_ok": (
        GAUGE,
        "Deep health check: 1 when a dependency is healthy, 0 otherwise.",
        ("component",),
    ),
}

#: The order the metrics are rendered in (stable, so diffs are readable).
_METRIC_ORDER: Tuple[str, ...] = tuple(_METRIC_SPECS)


# ── Registry state ─────────────────────────────────────────────────────────

class _HistogramState:
    """Cumulative bucket counts, sum and count for one label set."""

    __slots__ = ("counts", "sum", "count")

    def __init__(self) -> None:
        self.counts: List[int] = [0] * len(DURATION_BUCKETS)
        self.sum = 0.0
        self.count = 0

    def observe(self, value: float) -> None:
        for index, bound in enumerate(DURATION_BUCKETS):
            if value <= bound:
                self.counts[index] += 1
        self.sum += value
        self.count += 1

    def copy(self) -> "_HistogramState":
        clone = _HistogramState()
        clone.counts = list(self.counts)
        clone.sum = self.sum
        clone.count = self.count
        return clone


_lock = threading.Lock()
_counters: Dict[str, Dict[LabelKey, float]] = {}
_gauges: Dict[str, Dict[LabelKey, float]] = {}
_histograms: Dict[str, Dict[LabelKey, _HistogramState]] = {}
_started_at = time.time()


def _build_version() -> str:
    """The application version, or ``"unknown"`` when it cannot be read."""
    try:
        from config import settings

        return str(getattr(settings.app, "version", "") or "unknown")
    except Exception:  # noqa: BLE001 - the registry must never fail
        return "unknown"


def _seed_registry() -> None:
    """(Re)create the always-present series."""
    _counters.setdefault("dataagent_warehouse_rows_returned", {})
    _counters["dataagent_warehouse_rows_returned"][()] = 0.0
    _gauges["dataagent_tenants"] = {(): 0.0}
    _gauges["dataagent_build_info"] = {(("version", _build_version()),): 1.0}
    _gauges["dataagent_uptime_seconds"] = {(): 0.0}


def reset_metrics() -> None:
    """Drop every recorded sample (used by the test-suite)."""
    with _lock:
        _counters.clear()
        _gauges.clear()
        _histograms.clear()
        _seed_registry()


# ── Value helpers ─────────────────────────────────────────────────────────

def _safe_label_value(value: str) -> str:
    """Belt-and-braces guard: never emit an email or a token as a label."""
    text = str(value)
    if _EMAIL.match(text):
        return "redacted"
    if _TOKENISH.match(text) and not text.isalpha():
        return "redacted"
    return text


def _allowlisted(value: str, allowed: Iterable[str]) -> str:
    """Map *value* through an allow-list; anything else becomes ``other``."""
    text = str(value or "").strip().lower()
    return text if text in allowed else "other"


def _escape_label_value(value: str) -> str:
    """Escape a label value for the Prometheus text format."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _looks_like_id(segment: str) -> bool:
    """True when a path segment looks like a value rather than a route word."""
    if not segment or segment.lower() in STATIC_SEGMENTS:
        return False
    if _UUIDISH.match(segment) or _HEXISH.match(segment):
        return True
    if any(char.isdigit() for char in segment):
        return True
    if len(segment) >= 16:
        return True
    return "_" in segment


def normalize_path(path: str) -> str:
    """Collapse identifiers in *path* so the metric cardinality stays bounded.

    ``/tables/sales`` becomes ``/tables/{id}``; a FastAPI route template
    such as ``/tables/{table_name}`` becomes ``/tables/{id}``.  The query
    string is dropped entirely -- it can carry a token.
    """
    raw = str(path or "")
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    if not raw.startswith("/"):
        raw = "/" + raw

    segments = [segment for segment in raw.split("/") if segment]
    if not segments:
        return "/"

    normalised: List[str] = []
    previous = ""
    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            normalised.append("{id}")
        elif previous in ID_PARENTS and segment.lower() not in TYPE_SEGMENTS:
            normalised.append("{id}")
        elif _looks_like_id(segment):
            normalised.append("{id}")
        else:
            normalised.append(segment)
        previous = segment.lower()

    return "/" + "/".join(normalised)


def tenant_hash(tenant_id: str) -> str:
    """A stable, non-reversible label for a tenant id."""
    digest = hashlib.sha256(
        f"dataagent-metrics-tenant-v1:{tenant_id}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


# ── Recording ─────────────────────────────────────────────────────────────

def _increment(name: str, labels: LabelKey, amount: float = 1.0) -> None:
    with _lock:
        bucket = _counters.setdefault(name, {})
        bucket[labels] = bucket.get(labels, 0.0) + amount


def _set_gauge(name: str, labels: LabelKey, value: float) -> None:
    with _lock:
        _gauges.setdefault(name, {})[labels] = float(value)


def record_http_request(
    method: str,
    path: str,
    status: int,
    duration_seconds: float = 0.0,
) -> None:
    """Record one HTTP request (counter + latency histogram)."""
    try:
        normalised = normalize_path(path)
        verb = str(method or "GET").upper()[:16]
        code = str(int(status))
        labels: LabelKey = (
            ("method", verb),
            ("path", normalised),
            ("status", code),
        )
        _increment("dataagent_http_requests_total", labels)
        observe_http_duration(normalised, duration_seconds)
    except Exception:  # noqa: BLE001 - never fail a request over a metric
        return


def observe_http_duration(path: str, seconds: float) -> None:
    """Record a request duration into the latency histogram."""
    try:
        value = float(seconds)
        if value < 0:
            value = 0.0
        labels: LabelKey = (("path", normalize_path(path)),)
        with _lock:
            bucket = _histograms.setdefault(
                "dataagent_http_request_duration_seconds", {}
            )
            state = bucket.get(labels)
            if state is None:
                state = _HistogramState()
                bucket[labels] = state
            state.observe(value)
    except Exception:  # noqa: BLE001
        return


def record_sql_query(result: str) -> None:
    """Record a warehouse query outcome (``success`` / ``error``)."""
    try:
        outcome = _allowlisted(result, SQL_RESULTS)
        _increment("dataagent_sql_queries_total", (("result", outcome),))
    except Exception:  # noqa: BLE001
        return


def record_ai_request(feature: str) -> None:
    """Record an AI-assisted request by feature."""
    try:
        name = _allowlisted(feature, AI_FEATURES)
        _increment("dataagent_ai_requests_total", (("feature", name),))
    except Exception:  # noqa: BLE001
        return


def record_rows_returned(rows: int) -> None:
    """Record how many rows a warehouse query returned."""
    try:
        amount = max(0, int(rows))
        _increment("dataagent_warehouse_rows_returned", (), float(amount))
    except Exception:  # noqa: BLE001
        return


def record_rate_limited(bucket: str) -> None:
    """Record a request the rate limiter refused."""
    try:
        name = _allowlisted(bucket, RATE_LIMIT_BUCKETS)
        _increment("dataagent_rate_limited_total", (("bucket", name),))
    except Exception:  # noqa: BLE001
        return


def record_tenant_request(tenant_id: str) -> None:
    """Record a request for *tenant_id* -- under a hash, never the id."""
    try:
        label = tenant_hash(str(tenant_id or "default"))
        _increment("dataagent_tenant_requests_total", (("tenant", label),))
    except Exception:  # noqa: BLE001
        return


def set_tenant_count(count: int) -> None:
    """Publish how many tenants this deployment knows about."""
    try:
        _set_gauge("dataagent_tenants", (), max(0, int(count)))
    except Exception:  # noqa: BLE001
        return


def set_health_component(component: str, ok: bool) -> None:
    """Publish the deep health check's verdict for one dependency.

    ``dataagent_health_component_ok{component="warehouse"} 0`` is what the
    shipped alert rules fire on, so the metric has to exist even when the
    health endpoint is not being scraped.
    """
    try:
        name = _safe_label_value(str(component or "unknown"))
        _set_gauge("dataagent_health_component_ok", (("component", name),),
                   1.0 if ok else 0.0)
    except Exception:  # noqa: BLE001
        return


# ── Rendering ─────────────────────────────────────────────────────────────

def _format_number(value: float) -> str:
    """Render a number the way Prometheus expects."""
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    if 0 < abs(value) < 1e-6:
        return repr(value)
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_labels(labels: LabelKey) -> str:
    if not labels:
        return ""
    parts = [
        f'{name}="{_escape_label_value(_safe_label_value(value))}"'
        for name, value in labels
    ]
    return "{" + ",".join(parts) + "}"


def _render_series(name: str, samples: Dict[LabelKey, float]) -> List[str]:
    lines: List[str] = []
    for labels in sorted(samples):
        value = samples[labels]
        lines.append(f"{name}{_format_labels(labels)} {_format_number(value)}")
    return lines


def _render_histogram(name: str, samples: Dict[LabelKey, _HistogramState]) -> List[str]:
    lines: List[str] = []
    for labels in sorted(samples):
        state = samples[labels]
        for bound, count in zip(DURATION_BUCKETS, state.counts):
            bucket_labels = (("le", _format_number(bound)),) + labels
            lines.append(
                f"{name}_bucket{_format_labels(bucket_labels)} {count}"
            )
        inf_labels = (("le", "+Inf"),) + labels
        lines.append(f"{name}_bucket{_format_labels(inf_labels)} {state.count}")
        lines.append(
            f"{name}_sum{_format_labels(labels)} {_format_number(state.sum)}"
        )
        lines.append(f"{name}_count{_format_labels(labels)} {state.count}")
    return lines


def render_prometheus() -> str:
    """Render the whole registry in the Prometheus text exposition format."""
    with _lock:
        _gauges.setdefault("dataagent_uptime_seconds", {})[()] = (
            time.time() - _started_at
        )
        counters = {name: dict(values) for name, values in _counters.items()}
        gauges = {name: dict(values) for name, values in _gauges.items()}
        histograms = {
            name: {key: state.copy() for key, state in values.items()}
            for name, values in _histograms.items()
        }

    lines: List[str] = []
    for name in _METRIC_ORDER:
        spec = _METRIC_SPECS[name]
        lines.append(f"# HELP {name} {spec[1]}")
        lines.append(f"# TYPE {name} {spec[0]}")
        if spec[0] == COUNTER:
            lines.extend(_render_series(name, counters.get(name, {})))
        elif spec[0] == GAUGE:
            lines.extend(_render_series(name, gauges.get(name, {})))
        else:
            lines.extend(_render_histogram(name, histograms.get(name, {})))
    return "\n".join(lines) + "\n"


_seed_registry()


# ── Middleware ────────────────────────────────────────────────────────────

#: Path prefix -> rate-limit bucket, so a 429 can be attributed without the
#: rate limiter having to know about the metrics registry.
_PATH_BUCKETS: Tuple[Tuple[str, str], ...] = (
    ("/auth/login", "login"),
    ("/auth/register", "register"),
    ("/auth/users", "accounts"),
    ("/ask-sql", "ask_sql"),
    ("/ask", "ask"),
    ("/upload", "upload"),
    ("/load", "load"),
    ("/execute", "execute"),
    ("/sql", "sql"),
)


def bucket_for_path(path: str) -> str:
    """The rate-limit bucket a path belongs to (``other`` when unknown)."""
    normalised = normalize_path(path)
    for prefix, bucket in _PATH_BUCKETS:
        if normalised == prefix or normalised.startswith(prefix + "/"):
            return bucket
    return "other"


def _request_path(request) -> str:
    """The route template when one is known, else the raw path."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return template
    return request.url.path


try:  # pragma: no cover - starlette is a hard dependency of the app
    from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
    from starlette.responses import Response as _Response
except Exception:  # noqa: BLE001 - keep the module importable without starlette
    _BaseHTTPMiddleware = object  # type: ignore[assignment,misc]
    _Response = object  # type: ignore[assignment,misc]


class MetricsMiddleware(_BaseHTTPMiddleware):  # type: ignore[misc]
    """Count requests, time them and attribute them to a tenant.

    The middleware never raises: a broken metrics path must not turn into a
    broken request.
    """

    async def dispatch(self, request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            raise
        finally:
            try:
                duration = time.perf_counter() - started
                path = _request_path(request)
                record_http_request(request.method, path, status, duration)
                if status == 429:
                    record_rate_limited(bucket_for_path(path))
                self._record_tenant(request)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _record_tenant(request) -> None:
        user = getattr(request.state, "user", None)
        if user is None:
            return
        try:
            from api.tenancy import resolve_tenant_id

            record_tenant_request(resolve_tenant_id(user))
        except Exception:  # noqa: BLE001
            return


def install_metrics_middleware(app) -> None:
    """Attach :class:`MetricsMiddleware` to a FastAPI app."""
    app.add_middleware(MetricsMiddleware)
