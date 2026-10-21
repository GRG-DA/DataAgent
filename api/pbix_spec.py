"""Deterministic Power BI (.pbix / .pbit) parsing into a DashboardStudio spec.

Power BI desktop files are zips. The legacy ``Report/Layout`` member holds a
stringified JSON document whose ``sections`` drive the report: each section is
a page with a layout (width/height) and ``visualContainers`` — an array of
**stringified** visual configs. This module turns that into the same flat spec
shape the screenshot path uses (``theme`` / ``background`` / ``cardBg`` /
``textColor`` / ``accent`` / ``palette`` / ``header`` / ``pages``), with every
widget box normalized to 0-100% of its own page.

Pure zipfile + json: no FastAPI, no data layer, no LLM. Every failure is a
``ValueError`` with a user-facing message.
"""

import io
import json
import re
import zipfile

_MAX_PBIX_BYTES = 30 * 1024 * 1024  # upload cap

_LAYOUT_CANDIDATES = ("Report/Layout", "Report/Report.json", "Report/Layout/index.json")

# Registered (user) themes override the built-in CY24SU10 base theme.
_THEME_SEARCH_ORDER = (
    "Report/StaticResources/RegisteredResources/",
    "Report/StaticResources/SharedResources/BaseThemes/",
)

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Power BI visualType -> DashboardStudio widget family (kept to the set the
# plan layer can map to real SQL; anything else becomes a sized placeholder).
_FAMILY = {
    "cardVisual": "kpi",
    "multiRowCard": "kpi",
    "textbox": "text",
    "shape": "text",
    "slicer": "slicer",
    "clusteredColumnChart": "column",
    "stackedColumnChart": "column",
    "hundredPercentStackedColumnChart": "column",
    "lineClusteredColumnComboChart": "column",
    "lineStackedColumnComboChart": "column",
    "ribbonChart": "column",
    "clusteredBarChart": "bar",
    "stackedBarChart": "bar",
    "hundredPercentStackedBarChart": "bar",
    "lineChart": "line",
    "areaChart": "area",
    "stackedAreaChart": "area",
    "pieChart": "pie",
    "donutChart": "donut",
    "donutChartVisual": "donut",
    "radialGauge": "gauge",
    "gaugeChart": "gauge",
    "tableEx": "table",
    "matrix": "table",
    "pivotTable": "table",
    "table": "table",
    "scatterChart": "scatter",
    "bubbleChart": "scatter",
    "funnel": "funnel",
    "treemap": "treemap",
    "heatmap": "heatmap",

    # KPI variants shipped by newer PBI versions.
    "kpiVisual": "kpi",
    "kpi": "kpi",
    "card": "kpi",

    # Maps.
    "map": "map",
    "filledMap": "map",
    "shapeMap": "map",
    "azureMap": "map",
    "esriMap": "map",
}

_DEFAULT_PAGE_SIZE = (1280.0, 720.0)

# Guard against corrupt layouts: a page larger than this is untrustworthy, so
# every widget box would normalize to sub-pixel slivers. Fall back to default.
_MAX_PAGE_DIM = 20000.0

# Guard against corrupt layouts: a page larger than this is untrustworthy, so
# every widget box would normalize to sub-pixel slivers. Fall back to default.
_MAX_PAGE_DIM = 20000.0


def pbix_to_spec(raw) -> dict:
    """Parse an in-memory .pbix/.pbit into a DashboardStudio project spec."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("Upload a Power BI (.pbix) file.")
    if len(raw) > _MAX_PBIX_BYTES:
        raise ValueError("File is larger than 30MB — upload a smaller .pbix file.")
    if not raw[:2] in (b"PK",):
        raise ValueError("Not a valid Power BI file.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid Power BI file.") from exc

    with zf:
        layout_name, content = _find_layout(zf)
        pages = parse_layout(content)
        pages = [p for p in pages if p.get("widgets")]
        if not pages:
            raise ValueError("No dashboard visuals were found in this .pbix file.")
        theme = _theme_from_zip(zf)

    palette = theme.get("palette") or []
    accent = _hex_or(theme.get("accent")) or (palette[0] if palette else "#2563eb")
    background = _hex_or(theme.get("background")) or "#f1f5f9"
    darker = _is_dark(background)
    if darker:
        card_bg, text_color = "#0f172a", "#e2e8f0"
    else:
        card_bg, text_color = "#ffffff", "#1e293b"

    first = pages[0]
    return {
        "used": "pbix",
        "theme": "dark" if darker else "light",
        "background": background,
        "cardBg": card_bg,
        "textColor": text_color,
        "accent": accent,
        "palette": palette,
        "header": {"present": True, "title": str(first.get("name") or "Dashboard")[:200]},
        "canvas": {"w": int(first["width"]), "h": int(first["height"])},
        "pages": pages,
        "_meta": {
            "source": "pbix",
            "layout_member": layout_name,
            "theme_name": theme.get("name") or "",
            "pageCount": len(pages),
            "widgetCount": sum(len(p.get("widgets") or []) for p in pages),
        },
    }


def parse_layout(content) -> list:
    """Turn the decoded layout member into a list of page dicts with widgets."""
    try:
        data = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("This .pbix report layout could not be read.") from exc
    sections = data.get("sections") or []
    pages = []
    for si, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        sec = _split_virtual_pages(sec)
        pw = _fnum(sec.get("layout", {}).get("width"), 0.0) if isinstance(sec.get("layout"), dict) else 0.0
        ph = _fnum(sec.get("layout", {}).get("height"), 0.0) if isinstance(sec.get("layout"), dict) else 0.0
        if not pw or pw < 32 or pw > _MAX_PAGE_DIM:
            pw = _DEFAULT_PAGE_SIZE[0]
        if not ph or ph < 18 or ph > _MAX_PAGE_DIM:
            ph = _DEFAULT_PAGE_SIZE[1]
        vcs = sec.get("visualContainers")
        if not isinstance(vcs, list):
            vcs = sec.get("visual") or []
        widgets = _page_widgets(vcs, pw, ph)
        if widgets:
            pages.append({
                "name": str(sec.get("displayName") or sec.get("name") or ("Page %d" % (si + 1)))[:80],
                "width": pw,
                "height": ph,
                "widgets": widgets,
            })
    return pages


def _page_widgets(vcs, pw, ph) -> list:
    widgets = []
    for i, vc in enumerate(vcs):
        vc = _maybe_json(vc)
        if not isinstance(vc, dict):
            continue
        if vc.get("hideVisual") is True:
            continue
        vis = _maybe_json(vc.get("singleVisual"))
        if not isinstance(vis, dict):
            continue
        vtype = str(vis.get("visualType") or "")
        family = _visual_family(vtype)
        box = _visual_box(vc, vis)
        nbox = _normalize_box(box, pw, ph)
        if nbox["w"] <= 0 or nbox["h"] <= 0:
            continue
        title = _visual_title(vis) or ""
        if not title and family != "text":
            title = "Visual %d" % (i + 1)
        content = None
        if family == "text":
            content = str(_nested(vis, ("textRange", "text")) or "")[:4000] or None
        widgets.append({
            "index": i,
            "family": family,
            "title": title,
            "x": nbox["x"],
            "y": nbox["y"],
            "w": nbox["w"],
            "h": nbox["h"],
            "content": content,
            "fields": _visual_fields(vis),
        })
    return widgets


# Query-ref aggregation prefixes Power BI writes into projections, mapped to
# SQL aggregates. Order matters: "Count Distinct" must be tested before "Count".
_AGG_PREFIXES = (
    ("Sum of ", "SUM"),
    ("Average of ", "AVG"),
    ("Avg of ", "AVG"),
    ("Count Distinct of ", "COUNT DISTINCT"),
    ("Count of ", "COUNT"),
    ("Min of ", "MIN"),
    ("Max of ", "MAX"),
)

# PBIX JSON projections use a "Column:AGG" ref (e.g. "amount:SUM").
_COLON_AGGS = {
    "SUM": "SUM",
    "AVG": "AVG",
    "AVERAGE": "AVG",
    "COUNT": "COUNT",
    "COUNT DISTINCT": "COUNT DISTINCT",
    "DISTINCTCOUNT": "COUNT DISTINCT",
    "MIN": "MIN",
    "MAX": "MAX",
}


def _field_parts(query_ref):
    """Split a projection queryRef into (column, agg-or-None).

    ``Sales.Sum of Amount`` → ("Amount", "SUM"); ``Sales.Region`` →
    ("Region", None); ``amount:SUM`` → ("amount", "SUM"). Returns "" for
    non-string refs.
    """
    if not isinstance(query_ref, str):
        return ("", None)
    ref = query_ref.strip()
    if not ref:
        return ("", None)
    col = ref
    agg = None
    # The aggregate phrase sits between the table qualifier and the column:
    # "Table.Agg of Column". Only split at the LAST dot so dotted names survive.
    head, _, tail = ref.rpartition(".")
    if head and tail:
        for prefix, fn in _AGG_PREFIXES:
            if tail.startswith(prefix):
                col = tail[len(prefix):]
                agg = fn
                break
        else:
            col = tail
    # Colon form: "Column:AGG". Leave the ref untouched when the tail is not
    # a known aggregate token (dotted/timestamp-like names survive).
    if agg is None and ":" in col:
        name, _, agg_tail = col.rpartition(":")
        mapped = _COLON_AGGS.get(agg_tail.strip().upper())
        if mapped and name.strip():
            col, agg = name, mapped
    return (col.strip(), agg)


def _visual_fields(vis) -> list:
    """Extract a visual's data bindings from singleVisual.projections.

    Each projection maps a queryRef to roles (Y, Category, Values, Rows, X,
    Series...). Slicers project bare fields with an empty role list. Returns a
    compact list of {ref, role} in projection order; first role wins when a
    ref is projected into several roles.
    """
    projs = vis.get("projections")
    if not isinstance(projs, list):
        return []
    fields, seen = [], set()
    for p in projs:
        if not isinstance(p, dict):
            continue
        ref = p.get("queryRef")
        if not isinstance(ref, str) or not ref.strip() or ref in seen:
            continue
        seen.add(ref)
        roles = p.get("projections")
        role = ""
        if isinstance(roles, list) and roles:
            role = str(roles[0] or "")
        fields.append({"ref": ref.strip(), "role": role})
    return fields


def _visual_box(vc, vis) -> dict:
    default = {"x": 0, "y": 0, "width": 300, "height": 200}
    pos = _nested(vis, ("layouts", 0, "position"))
    if isinstance(pos, dict):
        return pos
    vlayout = vc.get("layout")
    if isinstance(vlayout, dict):
        return vlayout
    return default


def _normalize_box(pos, pw, ph) -> dict:
    x = max(0.0, _fnum(pos.get("x"), 0.0))
    y = max(0.0, _fnum(pos.get("y"), 0.0))
    w = max(0.0, _fnum(pos.get("width"), 0.0))
    h = max(0.0, _fnum(pos.get("height"), 0.0))
    pw = max(1.0, float(pw))
    ph = max(1.0, float(ph))
    x = min(100.0, round(x / pw * 100, 2))
    y = min(100.0, round(y / ph * 100, 2))
    w = min(round(w / pw * 100, 2), max(0.0, 100.0 - x))
    h = min(round(h / ph * 100, 2), max(0.0, 100.0 - y))
    return {"x": x, "y": y, "w": max(0.0, w), "h": max(0.0, h)}


def _visual_title(vis) -> str:
    general = _nested(vis, ("objects", "general"))
    if isinstance(general, list):
        for obj in general:
            props = _nested(obj, ("properties",))
            if not isinstance(props, dict):
                continue
            title = props.get("title")
            if isinstance(title, dict):
                lit = _nested(title, ("expr", "Literal", "Value"))
                cleaned = _literal_string(lit)
                if cleaned and cleaned.lower() != "(blank)":
                    return cleaned[:200]
    return ""


def _literal_string(value) -> str:
    if not isinstance(value, str):
        try:
            value = json.loads(value) if isinstance(value, (bytes, bytearray)) else value
        except Exception:  # noqa: BLE001
            return ""
    if not isinstance(value, str):
        return ""
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _visual_family(vtype: str) -> str:
    return _FAMILY.get(vtype, "placeholder")


# ─────────────────────────────── layout lookup ────────────────────────


def _find_layout(zf):
    names = set(zf.namelist())
    for cand in _LAYOUT_CANDIDATES:
        if cand in names:
            return cand, zf.read(cand)
    subs = sorted(n for n in names if n.startswith("Report/Layout/") and n.endswith(".json"))
    if subs:
        return subs[0], zf.read(subs[0])
    raise ValueError("This .pbix has no readable report layout.")


def _split_virtual_pages(sec) -> dict:
    """Report.json sections nest virtual pages; merge their visuals in place."""
    merged = dict(sec)
    virtual = merged.get("virtualPages") or merged.get("horizontal") or []
    if isinstance(virtual, list):
        vcs = list(merged.get("visualContainers") or [])
        for vp in virtual:
            if isinstance(vp, dict):
                vcs.extend(vp.get("visualContainers") or [])
        merged["visualContainers"] = vcs
    return merged


# ─────────────────────────────── theme ────────────────────────────────


def _theme_from_zip(zf) -> dict:
    merged = {"palette": [], "background": None, "foreground": None, "name": None, "accent": None}
    for prefix in _THEME_SEARCH_ORDER:
        for name in zf.namelist():
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(name))
            except Exception:  # noqa: BLE001
                continue
            _walk_theme(merged, data)
    return {
        "palette": merged["palette"][:10],
        "accent": merged["palette"][0] if merged["palette"] else _hex_or(merged["accent"]),
        "background": _hex_or(merged["background"]),
        "foreground": _hex_or(merged["foreground"]),
        "name": merged["name"],
    }


def _walk_theme(merged, node):
    if isinstance(node, dict):
        if merged["name"] is None and isinstance(node.get("name"), str) and node["name"].strip():
            merged["name"] = node["name"]
        colors = node.get("dataColors")
        if isinstance(colors, list):
            for c in colors:
                hexc = _hex_or(c)
                if hexc and hexc not in merged["palette"]:
                    merged["palette"].append(hexc)
        if merged["background"] is None and isinstance(node.get("background"), str):
            merged["background"] = node["background"]
        if merged["foreground"] is None and isinstance(node.get("foreground"), str):
            merged["foreground"] = node["foreground"]
        for value in node.values():
            _walk_theme(merged, value)
    elif isinstance(node, list):
        for value in node:
            _walk_theme(merged, value)


# ─────────────────────────────── helpers ──────────────────────────────


def _fnum(value, default):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f == f else default  # NaN guard


def _hex_or(value):
    if isinstance(value, str) and _HEX_RE.match(value.strip()):
        return value.strip().lower()
    return None


def _is_dark(bg: str) -> bool:
    if not bg or len(bg) != 7:
        return False
    try:
        r, g, b = (int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16))
    except ValueError:
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) < 110


def _maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return value
    return value


def _nested(obj, path):
    """Robust deep getter that lazily parses stringified JSON at each step."""
    cur = obj
    for step in path:
        if cur is None:
            return None
        cur = _maybe_json(cur)
        try:
            cur = cur[step]
        except (KeyError, IndexError, TypeError):
            return None
    return cur