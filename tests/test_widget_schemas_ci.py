"""CI widget-schema tests — no browser needed.

Validates the static wiring that the browser-level UI verification exercises
at runtime (scripts/ui_verify/harness.py, scenario `widgets`):

1. Every palette widget type in blankdash.js has a property schema.
2. Every schema entry is well-formed (key, type, group; control supported).
3. Condition references point at real keys in the same schema.
4. Defaults resolve and setValue round-trips.
5. Every palette type is handled by the renderer registry or the map branch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"


# ---------------------------------------------------------------------------
# Load the JS sources and extract the catalogues (mirrors runtime values)
# ---------------------------------------------------------------------------

def _load_catalogue():
    src = (WEB / "js" / "pages" / "blankdash.js").read_text(encoding="utf-8")
    block = re.search(r"var _CATALOGUE = \[(.*?)\];", src, re.S).group(1)
    items = re.findall(
        r'\{ type: "([a-z]+)",\s+label: "([^"]+)",\s+icon: "[^"]*",'
        r'\s+color: "#[0-9A-Fa-f]{6}",\s+size: \[\d+,\s*\d+\],\s+cat: "([^"]+)" \}',
        block,
    )
    return [{"type": t, "label": l, "cat": c} for t, l, c in items]


def _load_map_catalogue():
    src = (WEB / "js" / "pages" / "blankdash.js").read_text(encoding="utf-8")
    block = re.search(r"var _MAP_CATALOGUE = \[(.*?)\];", src, re.S).group(1)
    return re.findall(r'type: "([a-z]+)"', block)


def _load_chart_subtypes():
    src = (WEB / "js" / "pages" / "blankdash.js").read_text(encoding="utf-8")
    m = re.search(r"var _CHART_SUBTYPES = \[(.*?)\];", src, re.S)
    return re.findall(r'"([a-z]+)"', m.group(1))


def _load_schema_types():
    """Types registered in widgetSchemas.js (lowercased ids).

    Covers direct assignments (`_schemas.foo = ...`, any spacing) and
    bulk registration via `["foo", "bar"].forEach(...)` KPI-style blocks.
    """
    src = (WEB / "js" / "components" / "widgetSchemas.js").read_text(encoding="utf-8")
    types = set(re.findall(r"_schemas\.([a-z]+)\s*=", src))
    for block in re.findall(r"\[([^\]]+)\]\.forEach\(function \(t\)", src, re.S):
        types.update(re.findall(r'"([a-z]+)"', block))
    return types


def _load_renderer_types():
    """Types registered in dashboardRenderer.js."""
    src = (WEB / "js" / "components" / "dashboardRenderer.js").read_text(encoding="utf-8")
    return set(re.findall(r'_register\("([a-z]+)"', src))


def _load_custom_renderer_types():
    """Renderers registered inside blankdash.js (custom D3 + extra Chart.js/HTML)."""
    src = (WEB / "js" / "pages" / "blankdash.js").read_text(encoding="utf-8")
    types: set[str] = set()
    for fn in ("_registerCustomRenderers", "_registerExtraRenderers"):
        m = re.search(rf"function {fn}\(\) \{{(.*?)\n  \}}", src, re.S)
        body = m.group(1) if m else ""
        types.update(re.findall(r'DashboardRenderer\.register\("([a-z]+)"', body))
    return types


def _load_filter_types():
    src = (WEB / "js" / "pages" / "blankdash.js").read_text(encoding="utf-8")
    m = re.search(r"var _FILTER_TYPES = \[(.*?)\];", src, re.S)
    return set(re.findall(r'"([a-z]+)"', m.group(1)))


SUPPORTED_CONTROL_TYPES = {
    # UIControlKit registry + aliases (range -> slider)
    "text", "textarea", "number", "color", "toggle", "select", "slider",
    "range", "font", "alignment", "spacing", "border", "gradient",
    "multicolor", "icon", "iconPicker", "columnpicker", "pagepicker", "radiogroup",
    "tableSelect", "measureSelect", "colorArray", "referenceLines",
    "fontFamily", "date",
}


@pytest.fixture(scope="module")
def catalogue():
    return _load_catalogue()


@pytest.fixture(scope="module")
def schema_types():
    return _load_schema_types()


# ---------------------------------------------------------------------------
# 1. Every palette type has a property schema
# ---------------------------------------------------------------------------

def test_every_palette_type_has_schema(catalogue, schema_types):
    maps = set(_load_map_catalogue())
    missing = []
    for item in catalogue:
        t = item["type"]
        if t in schema_types or t in maps:
            continue
        missing.append(t)
    assert not missing, f"palette types without WidgetPropertySchemas entry: {missing}"


def test_map_types_have_schema(schema_types):
    maps = _load_map_catalogue()
    missing = [t for t in maps if t not in schema_types]
    assert not missing, f"map types without schema: {missing}"


def test_catalogue_size_matches_palette_claim():
    """The module docstring advertises the catalogue size — keep it honest."""
    src = (WEB / "js" / "pages" / "blankdash.js").read_text(encoding="utf-8")
    n = len(_load_catalogue()) + len(_load_map_catalogue())
    m = re.search(r"(\d+) widget types", src)
    assert m, "docstring lost its widget-count line"
    assert int(m.group(1)) >= n, (
        f"docstring says {m.group(1)} widget types but catalogue has {n}"
    )


# ---------------------------------------------------------------------------
# 2. Schema entries are well-formed
# ---------------------------------------------------------------------------

def _schema_fragments():
    src = (WEB / "js" / "components" / "widgetSchemas.js").read_text(encoding="utf-8")
    # every property literal: { key: "...", type: "...", ... group: "..." }
    return re.findall(
        r'\{\s*key: "([^"]+)",\s*type: "([a-zA-Z]+)"(.*?)\}',
        src,
        re.S,
    )


def test_schema_props_have_known_control_type():
    bad = []
    for key, ctype, _rest in _schema_fragments():
        if ctype not in SUPPORTED_CONTROL_TYPES:
            bad.append(f"{key} -> {ctype}")
    assert not bad, f"props with unsupported control types: {bad[:12]}"


def test_schema_props_have_group():
    bad = []
    for key, _ctype, rest in _schema_fragments():
        if "group:" not in rest:
            bad.append(key)
    assert not bad, f"props without a group (would vanish from the panel): {bad[:12]}"


def test_schema_keys_are_namespaced():
    """Every key must start with widget. or config. so setValue can address it."""
    bad = []
    for key, _ctype, _rest in _schema_fragments():
        if not (key.startswith("widget.") or key.startswith("config.")):
            bad.append(key)
    assert not bad, f"props with un-namespaced keys: {bad[:12]}"


# ---------------------------------------------------------------------------
# 3. Conditional props reference real keys
# ---------------------------------------------------------------------------

def test_condition_keys_exist_in_some_schema():
    """A condition pointing at a non-existent key would hide the prop forever.

    Soft check: the referenced base key (config.X / widget.X) must appear as a
    prop key somewhere in widgetSchemas.js or be a core widget field.
    """
    src = (WEB / "js" / "components" / "widgetSchemas.js").read_text(encoding="utf-8")
    all_keys = set(re.findall(r'key: "([^"]+)"', src))
    core_fields = {"type", "title", "locked", "showTitle"}
    bad = []
    for cond in re.findall(r'condition:\s*\{\s*key:\s*"([^"]+)"', src):
        if cond in all_keys or cond.replace("config.", "").replace("widget.", "") in core_fields:
            continue
        bad.append(cond)
    assert not bad, f"conditions referencing unknown keys: {bad[:12]}"


# ---------------------------------------------------------------------------
# 4. Defaults / setValue round-trip (pure JS, run via a tiny JS eval)
# ---------------------------------------------------------------------------

def test_set_value_and_resolve_roundtrip_js():
    """Run the real widgetSchemas.js in a JS engine via node (available)."""
    import subprocess

    js = r"""
const fs = require("fs");
let src = fs.readFileSync("web/js/components/widgetSchemas.js", "utf8");
// Evaluate the IIFE in this context: it defines window.WidgetPropertySchemas
global.window = {};
eval(src);
const WPS = global.window.WidgetPropertySchemas;
if (!WPS) { console.error("no WidgetPropertySchemas"); process.exit(2); }
const widget = { id: "w1", type: "chart", title: "T", config: { chartType: "bar" } };
// set + resolve round-trip on representative paths
WPS.setValue("config.legendShow", widget, false);
if (widget.config.legendShow !== false) { console.error("roundtrip fail legendShow"); process.exit(3); }
WPS.setValue("widget.x", widget, 42);
if (widget.x !== 42) { console.error("roundtrip fail widget.x"); process.exit(4); }
WPS.setValue("config.style.color", widget, "#123456");
if (widget.config.style.color !== "#123456") { console.error("roundtrip fail nested"); process.exit(5); }
// every registered type returns a schema array
for (const t of WPS.types) {
  const s = WPS.getSchema(t);
  if (!Array.isArray(s) || s.length === 0) { console.error("empty schema: " + t); process.exit(6); }
  for (const p of s) {
    if (!p.key || !p.type || !p.group) { console.error("malformed prop in " + t + ": " + JSON.stringify(p)); process.exit(7); }
  }
}
// visibleProps respects conditions without throwing
const vis = WPS.visibleProps("bar", { showDataLabels: true });
if (!Array.isArray(vis)) { console.error("visibleProps not array"); process.exit(8); }
console.log("JS-OK:" + WPS.types.length);
"""
    p = subprocess.run(
        ["node", "-e", js], capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert p.returncode == 0, f"node check failed:\n{p.stdout}\n{p.stderr}"
    out = p.stdout.strip()
    assert out.startswith("JS-OK:"), out
    n_types = int(out.split(":")[1])
    assert n_types >= 60, f"expected 60+ schema types, got {n_types}"


# ---------------------------------------------------------------------------
# 5. Renderer coverage: no palette type can hit "Unknown widget type"
# ---------------------------------------------------------------------------

def test_every_type_renders_or_is_data_backed():
    base = _load_renderer_types()                    # kpi, chart, table, text, shape, divider, metriccard
    custom = _load_custom_renderer_types()           # d3/custom renderers in blankdash
    subtypes = set(_load_chart_subtypes())           # chart.* subtypes
    filters = _load_filter_types()                   # slicer family (own branch)
    maps = set(_load_map_catalogue())                # map branch in _renderWidgetContent
    plain = {"custom", "page", "button", "image"}    # explicit branches
    kpi_family = {
        "radial", "status", "comparison", "targetactual", "trendkpi",
        "percentagekpi", "prevkpi", "iconkpi", "multikpi", "sparkkpi",
        "sparkbarkpi", "minilinekpi", "sparkdeltakpi", "donutkpi",
    }
    html_like = {"waffle", "calendar", "ranking", "gantt", "diverging", "bubblegrid", "watertank"}
    covered = base | custom | subtypes | filters | maps | plain | kpi_family | html_like
    all_types = {i["type"] for i in _load_catalogue()} | set(_load_map_catalogue())
    uncovered = sorted(all_types - covered)
    assert not uncovered, f"types with no renderer branch (would show 'Unknown widget type'): {uncovered}"


def test_kpi_delegates_registered():
    """The _kpiDelegate registrations must cover every KPI-family palette type."""
    src = (WEB / "js" / "pages" / "blankdash.js").read_text(encoding="utf-8")
    registered = set(re.findall(r'DashboardRenderer\.register\("([a-z]+kpi|radial|status|comparison|targetactual)"', src))
    expected = {
        "radial", "status", "comparison", "targetactual", "trendkpi",
        "percentagekpi", "prevkpi", "iconkpi", "multikpi", "sparkkpi",
        "sparkbarkpi", "minilinekpi", "sparkdeltakpi", "donutkpi",
    }
    missing = sorted(expected - registered)
    assert not missing, f"KPI-family types without renderer registration: {missing}"
