"""Headless dashboard creation for Chat Pro tasks.

Builds the deterministic auto-plan for a warehouse table, converts it into
blankdash widgets (the same contract the Dashboard Studio wizard produces),
and saves the result through :class:`api.dashboards.DashboardManager` so it
opens from ``#blankdash/<id>``.

The plan comes from ``api.main._build_auto_plan`` (lazy import — api.main
imports the agent at module load, so this module must never import it at
top level).
"""

from __future__ import annotations

import html
import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CANVAS_W = 1920
CANVAS_H = 1080
_MARGIN = 16
_GAP = 12
_HEADER_H = 72
_KPI_H = 112
_INSIGHT_H = 150
_CHART_MIN_H = 180
_MAX_KPIS = 5
_MAX_ZONES = 9
_ACCENT = "#2563eb"

_MAP_TYPES = {
    "countrymap",
    "geomap",
    "shapemap",
    "dotmap",
    "heatmapmap",
    "flowmap",
    "glmap",
}

_ZONE_PRIORITY = {
    "trend": 0,
    "breakdown": 1,
    "comparison": 2,
    "distribution": 3,
    "correlation": 4,
    "special": 5,
    "geo": 6,
}


def _uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _widget_type(chart_type: Any) -> Dict[str, Any]:
    """Map a plan chartType to (blankdash type, canvas chartType, is_map)."""
    t = str(chart_type or "bar").lower()
    if t == "histogram":
        return {"type": "histogram", "chartType": "histogram"}
    if t in ("box", "boxplot"):
        return {"type": "boxplot", "chartType": "box"}
    if t == "pareto":
        return {"type": "pareto", "chartType": "pareto"}
    if t in _MAP_TYPES:
        return {"type": t, "chartType": t, "map": True}
    if t in ("donut", "pie"):
        return {"type": "chart", "chartType": "doughnut"}
    if t in (
        "area",
        "combo",
        "treemap",
        "funnel",
        "waterfall",
        "scatter",
        "bubble",
        "heatmap",
        "radar",
        "horizontalbar",
        "line",
        "gauge",
    ):
        return {"type": "chart", "chartType": t}
    if t in ("stackedbar", "stacked"):
        return {"type": "chart", "chartType": "stackedbar"}
    if t == "sunburst":
        return {"type": "sunburst", "chartType": "sunburst"}
    if t == "sankey":
        return {"type": "sankey", "chartType": "sankey"}
    return {"type": "chart", "chartType": "bar"}


def _header_widget(
    table: str, title: str, subtitle: str, *, width: int, index: int
) -> Dict[str, Any]:
    body = (
        '<div style="display:flex;align-items:center;justify-content:space-between;'
        "height:100%;padding:18px 26px;background:var(--w-theme-accent,#2563eb);"
        'border-radius:14px;color:#fff">'
        f'<div><div style="font-size:23px;font-weight:800">{_esc(title)}</div>'
        f'<div style="font-size:12px;opacity:.82;margin-top:4px">{_esc(subtitle)}</div></div>'
        '<div style="text-align:right;font-size:11px;opacity:.9;font-weight:700;'
        'letter-spacing:.5px;text-transform:uppercase"><div>Data Agent</div>'
        '<div style="opacity:.7;margin-top:2px">AI-generated overview</div></div></div>'
    )
    return {
        "id": _uid("hdr"),
        "type": "custom",
        "title": title,
        "x": _MARGIN,
        "y": _MARGIN,
        "width": width,
        "height": _HEADER_H,
        "zIndex": index,
        "locked": True,
        "plain": False,
        "showTitle": False,
        "config": {"table": table, "html": body, "label": title, "plain": True},
    }


def _kpi_widget(
    kpi: Dict[str, Any], table: str, *, x: int, y: int, width: int, index: int
) -> Dict[str, Any]:
    variant = str(kpi.get("variant") or "kpi")
    sql = kpi.get("sql") or f"SELECT COUNT(*) AS value FROM {_q(table)}"
    if variant in ("gauge", "radial", "progress"):
        cfg: Dict[str, Any] = {
            "table": table,
            "sql": sql,
            "xCol": "",
            "yCol": "value",
            "label": kpi.get("label") or "KPI",
            "format": kpi.get("format") or "auto",
            "chartType": "gauge" if variant == "gauge" else "radial",
        }
        wtype = "chart" if variant == "gauge" else variant
    else:
        cfg = {
            "table": table,
            "sql": sql,
            "xCol": "",
            "yCol": "value",
            "label": kpi.get("label") or "KPI",
            "format": kpi.get("format") or "auto",
            "kpiStyle": "plan",
        }
        if kpi.get("spark") and kpi.get("sparkSql"):
            cfg["sparkSql"] = kpi["sparkSql"]
        wtype = variant if variant in ("trendkpi", "prevkpi", "percentagekpi") else "kpi"
    return {
        "id": _uid("kpi"),
        "type": wtype,
        "title": kpi.get("label") or "KPI",
        "x": x,
        "y": y,
        "width": width,
        "height": _KPI_H,
        "zIndex": index,
        "locked": False,
        "config": cfg,
    }


def _chart_widget(
    zone: Dict[str, Any], table: str, *, x: int, y: int, width: int, height: int, index: int
) -> Dict[str, Any]:
    mapped = _widget_type(zone.get("chartType"))
    cfg: Dict[str, Any] = {
        "table": table,
        "sql": zone.get("sql") or "",
        "chartType": mapped["chartType"],
        "xCol": zone.get("x") or "label",
        "yCol": zone.get("y") or "value",
        "label": zone.get("title") or "Chart",
        "filterField": zone.get("title") or "",
        "agg": "SUM",
        "limit": 12,
    }
    extra = zone.get("config")
    if isinstance(extra, dict):
        cfg.update(extra)
    if mapped.get("map"):
        cfg["geoField"] = zone.get("x") or "label"
        cfg["measureField"] = zone.get("y") or "value"
        cfg["aggregate"] = True
    if mapped["chartType"] == "stackedbar":
        cfg.setdefault("stacked", True)
        cfg.setdefault("seriesCol", "series")
    if mapped["chartType"] == "horizontalbar":
        cfg["indexAxis"] = "y"
    return {
        "id": _uid("zn"),
        "type": mapped["type"],
        "title": zone.get("title") or "Chart",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "zIndex": index,
        "locked": False,
        "plain": False,
        "config": cfg,
    }


def _insight_widget(
    table: str, insights: Dict[str, Any], *, width: int, y: int, index: int
) -> Dict[str, Any]:
    parts: List[str] = []
    if insights.get("summary"):
        parts.append(f"<p><b>Summary.</b> {_esc(insights['summary'])}</p>")
    for finding in (insights.get("key_findings") or [])[:3]:
        parts.append(f"<p><b>Finding.</b> {_esc(finding)}</p>")
    for rec in (insights.get("recommendations") or [])[:3]:
        parts.append(f"<p><b>Recommendation.</b> {_esc(rec)}</p>")
    body = (
        '<div style="height:100%;padding:16px 24px;background:var(--w-theme-card-bg,#fff);'
        'border:1px solid var(--w-theme-card-border,#e2e8f0);border-radius:14px;overflow:hidden">'
        '<div style="font-weight:800;font-size:16px;margin-bottom:10px">'
        "AI Executive Insights</div>"
        f'<div style="font-size:13px;line-height:1.6">{"".join(parts)}</div></div>'
    )
    return {
        "id": _uid("ins"),
        "type": "custom",
        "title": "AI Executive Insights",
        "x": _MARGIN,
        "y": y,
        "width": width,
        "height": _INSIGHT_H,
        "zIndex": index,
        "locked": False,
        "plain": True,
        "showTitle": False,
        "config": {
            "table": table,
            "html": body,
            "label": "AI Executive Insights",
            "plain": True,
            "autoFit": True,
        },
    }


def _layout_charts(
    zones: List[Dict[str, Any]],
    table: str,
    *,
    y0: int,
    content_w: int,
    available_h: int,
    index_start: int,
) -> List[Dict[str, Any]]:
    zones = [z for z in zones if isinstance(z, dict) and z.get("sql")][:_MAX_ZONES]
    zones.sort(key=lambda z: _ZONE_PRIORITY.get(z.get("zone"), 3))
    if not zones:
        return []
    rows = (len(zones) + 1) // 2
    gap_h = _GAP * (rows - 1)
    row_h = max(_CHART_MIN_H, (available_h - gap_h) // rows)
    col_w = (content_w - _GAP) // 2
    widgets: List[Dict[str, Any]] = []
    for i, zone in enumerate(zones):
        r, c = divmod(i, 2)
        widgets.append(
            _chart_widget(
                zone,
                table,
                x=_MARGIN + c * (col_w + _GAP),
                y=y0 + r * (row_h + _GAP),
                width=col_w,
                height=row_h,
                index=index_start + i,
            )
        )
    return widgets


def build_widgets(
    plan: Dict[str, Any],
    table: str,
    *,
    canvas_w: int = CANVAS_W,
    canvas_h: int = CANVAS_H,
) -> List[Dict[str, Any]]:
    """Convert an auto-build plan into blankdash widgets."""
    content_w = canvas_w - _MARGIN * 2
    title = str(plan.get("title") or f"{table} overview")
    widgets = [
        _header_widget(
            table,
            title,
            f"Data source: {table} · AI data-driven layout",
            width=content_w,
            index=1,
        )
    ]
    y = _MARGIN + _HEADER_H + _GAP
    index = 2

    kpis = [k for k in (plan.get("kpis") or []) if isinstance(k, dict)][:_MAX_KPIS]
    if kpis:
        kpi_w = (content_w - _GAP * (len(kpis) - 1)) // len(kpis)
        for i, kpi in enumerate(kpis):
            widgets.append(
                _kpi_widget(
                    kpi,
                    table,
                    x=_MARGIN + i * (kpi_w + _GAP),
                    y=y,
                    width=kpi_w,
                    index=index,
                )
            )
            index += 1
        y += _KPI_H + _GAP

    insights = plan.get("insights") or {}
    has_insight = bool(
        isinstance(insights, dict)
        and (insights.get("summary") or insights.get("key_findings"))
    )
    if has_insight:
        widgets.append(
            _insight_widget(table, insights, width=content_w, y=y, index=index)
        )
        index += 1
        y += _INSIGHT_H + _GAP

    available = canvas_h - _MARGIN - y
    widgets.extend(
        _layout_charts(
            plan.get("zones") or [],
            table,
            y0=y,
            content_w=content_w,
            available_h=available,
            index_start=index,
        )
    )
    return widgets


def build_config(
    name: str,
    table: str,
    widgets: List[Dict[str, Any]],
    *,
    canvas_w: int = CANVAS_W,
    canvas_h: int = CANVAS_H,
) -> Dict[str, Any]:
    """Return the blankdash snapshot persisted under ``layout._blankdash``."""
    return {
        "name": name,
        "table": table,
        "pages": [{"id": "page_1", "name": "Overview", "widgets": widgets}],
        "currentPage": 0,
        "canvasMinW": canvas_w,
        "canvasMinH": canvas_h,
        "fixedCanvas": True,
    }


def create_dashboard_from_plan(
    plan: Dict[str, Any],
    table: str,
    *,
    owner_id: str,
    owner: str = "",
    name: Optional[str] = None,
    canvas_w: int = CANVAS_W,
    canvas_h: int = CANVAS_H,
) -> Dict[str, Any]:
    """Save a plan as a real dashboard and return its id + deep link."""
    title = name or f"Chat Pro — {table} — {date.today().isoformat()}"
    widgets = build_widgets(plan, table, canvas_w=canvas_w, canvas_h=canvas_h)
    config = build_config(title, table, widgets, canvas_w=canvas_w, canvas_h=canvas_h)

    from api.dashboards import DashboardManager, _config_to_layout

    dash = DashboardManager().create(
        name=title,
        description="Created by Chat Pro",
        owner=owner,
        owner_id=owner_id,
        layout=_config_to_layout(config),
    )
    dash_id = str(dash.get("id") or "")
    return {
        "id": dash_id,
        "name": title,
        "url": f"#blankdash/{dash_id}",
        "widget_count": len(widgets),
    }


def auto_plan_for_table(
    table_name: str, *, tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    """Build the deterministic auto-plan for a warehouse table."""
    from api.main import _build_auto_plan, _build_column_profile
    from api.security_paths import validate_table_name
    from api.tenancy import tenant_connection

    table = validate_table_name(table_name)
    with tenant_connection(tenant_id) as conn:
        df = conn.execute(f"SELECT * FROM {_q(table)}").df()
    if df.empty:
        raise ValueError(f"Table '{table}' has no rows to analyze")
    profile = _build_column_profile(df)
    return _build_auto_plan(df, table, profile, [], None)


def create_dashboard_for_table(
    table_name: str,
    *,
    owner_id: str,
    owner: str = "",
    name: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Auto-plan a table, build the dashboard, and save it."""
    plan = auto_plan_for_table(table_name, tenant_id=tenant_id)
    return create_dashboard_from_plan(
        plan, table_name, owner_id=owner_id, owner=owner, name=name
    )
