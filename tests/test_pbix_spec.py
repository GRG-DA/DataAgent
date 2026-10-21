"""Deterministic Power BI (.pbix / .pbit) -> DashboardStudio spec parser tests.

The parser (`api.pbix_spec`) reads the legacy ``Report/Layout`` inside the
zip, normalizes each visual's 0-100% box against its page dimensions and
extracts a flat theme contract identical to the screenshot spec shape
(theme / background / cardBg / textColor / accent / palette / header).
"""

import io
import json
import zipfile

import pytest

from api import pbix_spec


# ───────────────────────── build fake .pbix files ─────────────────────


def _vc(vtype, title=None, text=None, box=None):
    """A stringified visual-container config, like the legacy Report/Layout."""
    pos = {"x": 10, "y": 10, "width": 300, "height": 200, "z": 0}
    if box:
        pos = dict(box)
    vis = {"visualType": vtype}
    if text is not None:
        vis["textRange"] = {"text": text, "fontSize": 15}
    if title is not None:
        vis["objects"] = {
            "general": [
                {"properties": {"title": {"expr": {"Literal": {"Value": json.dumps(title)}}}}},
                {"properties": {"title": {"show": False}}},
            ]
        }
    vis["layouts"] = [{"position": pos}]
    return json.dumps({
        "singleVisual": vis,
        "layout": {"x": pos["x"], "y": pos["y"], "width": pos["width"],
                   "height": pos["height"], "z": pos.get("z", 0)},
    })


def _section(name, width, height, containers):
    return {
        "displayName": name,
        "name": name,
        "layout": {
            "margin": {"bottom": 8, "left": 8, "right": 8, "top": 8},
            "width": width,
            "height": height,
        },
        "visualContainers": containers,
    }


def _rgb_theme(filename="CustomTheme.json"):
    return json.dumps({
        "name": "CustomTheme",
        "dataColors": ["#01B8AA", "#374649", "#FD625E", "#F2C80F", "#5F6B6D", "#8AD4EB"],
        "foreground": "#252423",
        "background": "#F3F3F3",
    })


def _pbix(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _layout_str(sections):
    return json.dumps({"sections": sections})


def _sample_sections():
    page1 = _section("Sales", 1440, 900, [
        _vc("cardVisual", title="Total Sales",
            box={"x": 10, "y": 0, "width": 300, "height": 180, "z": 0}),
        _vc("clusteredColumnChart", title="Revenue by region",
            box={"x": 10, "y": 190, "width": 500, "height": 680, "z": 1}),
        _vc("slicer", title="Country",
            box={"x": 520, "y": 0, "width": 200, "height": 180, "z": 0}),
        _vc("textbox", text="Total revenue grew 12% this quarter.",
            box={"x": 520, "y": 190, "width": 700, "height": 80, "z": 0}),
        _vc("tableEx", title="Sales by product",
            box={"x": 1240, "y": 0, "width": 675, "height": 870, "z": 0}),
    ])
    page2 = _section("Trend", 1280, 720, [
        _vc("lineChart", title="Sales trend",
            box={"x": 0, "y": 0, "width": 1200, "height": 620, "z": 0}),
    ])
    return [page1, page2]


def _sample_pbix():
    return _pbix({
        "Report/Layout": _layout_str(_sample_sections()),
        "Report/StaticResources/RegisteredResources/CustomTheme.json": _rgb_theme(),
    })


# ──────────────────────────────── tests ───────────────────────────────


def test_pbix_to_spec_parses_legacy_layout():
    spec = pbix_spec.pbix_to_spec(_sample_pbix())

    # Flat theme contract (same shape as the screenshot spec; hex lowercase).
    assert spec["theme"] == "light"
    assert spec["background"] == "#f3f3f3"
    assert spec["accent"] == "#01b8aa"
    assert spec["palette"][0:2] == ["#01b8aa", "#374649"]
    assert spec["cardBg"] == "#ffffff"
    assert spec["textColor"] == "#1e293b"
    assert spec["header"]["present"] is True
    assert spec["header"]["title"] == "Sales"
    assert spec["_meta"]["pageCount"] == 2
    assert spec["_meta"]["widgetCount"] == 6

    pages = spec["pages"]
    assert len(pages) == 2
    p1 = pages[0]
    assert p1["name"] == "Sales"
    assert p1["width"] == 1440 and p1["height"] == 900

    widgets = p1["widgets"]
    assert [w["family"] for w in widgets] == ["kpi", "column", "slicer", "text", "table"]

    w0 = widgets[0]
    assert w0["title"] == "Total Sales"          # Literal quoted value unquoted
    assert round(w0["x"], 2) == round(10 / 1440 * 100, 2)
    assert round(w0["y"], 2) == round(0 / 900 * 100, 2)
    assert round(w0["w"], 2) == round(300 / 1440 * 100, 2)
    assert round(w0["h"], 2) == round(180 / 900 * 100, 2)

    tw = widgets[3]
    assert tw["family"] == "text"
    assert tw["content"] == "Total revenue grew 12% this quarter."
    assert round(tw["x"], 2) == round(520 / 1440 * 100, 2)

    # Page 2 normalizes against its OWN dimensions.
    p2 = pages[1]
    w = p2["widgets"][0]
    assert w["family"] == "line"
    assert round(w["w"], 2) == round(1200 / 1280 * 100, 2)
    assert round(w["h"], 2) == round(620 / 720 * 100, 2)

    # Every widget carries the box keys the plan layer reads.
    for page in pages:
        for widg in page["widgets"]:
            assert all(k in widg for k in ("index", "family", "title", "x", "y", "w", "h"))


def test_visual_family_mapping():
    cases = [
        ("cardVisual", "kpi"),
        ("multiRowCard", "kpi"),
        ("textbox", "text"),
        ("slicer", "slicer"),
        ("clusteredColumnChart", "column"),
        ("stackedColumnChart", "column"),
        ("clusteredBarChart", "bar"),
        ("lineChart", "line"),
        ("areaChart", "area"),
        ("pieChart", "pie"),
        ("donutChart", "donut"),
        ("gaugeChart", "gauge"),
        ("tableEx", "table"),
        ("matrix", "table"),
        ("scatterChart", "scatter"),
        ("funnel", "funnel"),
        ("someCustomVisual", "placeholder"),
        ("", "placeholder"),
    ]
    for vtype, expected in cases:
        assert pbix_spec._visual_family(vtype) == expected, vtype


def test_report_json_fallback_when_no_legacy_layout():
    pbix = _pbix({
        "Report/Report.json": json.dumps({"version": 4, "sections": _sample_sections()}),
        "Report/StaticResources/RegisteredResources/CustomTheme.json": _rgb_theme(),
    })
    spec = pbix_spec.pbix_to_spec(pbix)
    assert spec["_meta"]["pageCount"] == 2
    assert spec["pages"][0]["widgets"][0]["family"] == "kpi"


def test_normalize_box_clamps_off_canvas():
    box = pbix_spec._normalize_box({"x": -40, "y": 300, "width": 2000, "height": 100}, 1440, 900)
    assert box["x"] == 0.0
    assert box["w"] == 100.0          # clamped to canvas edge
    assert box["y"] <= 100.0
    assert box["h"] >= 0.0


def test_ignores_hidden_and_empty_visuals():
    vc_hidden = json.loads(_vc("cardVisual", title="Hidden", box={"x": 50, "y": 50, "width": 100, "height": 100, "z": 0}))
    vc_hidden["hideVisual"] = True
    page = _section("P", 1280, 720, [json.dumps(vc_hidden)])
    with pytest.raises(ValueError) as ei:
        pbix_spec.pbix_to_spec(_pbix({"Report/Layout": _layout_str([page])}))
    assert "visual" in str(ei.value).lower()


def test_rejects_non_zip_bytes():
    with pytest.raises(ValueError) as ei:
        pbix_spec.pbix_to_spec(b"\x00\x01\x02GARBAGE")
    assert "Power BI" in str(ei.value)


def test_rejects_oversize_pbix():
    huge = b"PK\x03\x04" + (b"\x00" * (30 * 1024 * 1024))
    with pytest.raises(ValueError) as ei:
        pbix_spec.pbix_to_spec(huge)
    assert "larger than 30MB" in str(ei.value) or "30MB" in str(ei.value)


def test_rejects_missing_layout():
    pbix = _pbix({"Report/CustomVisuals/foo.json": "{}"})
    with pytest.raises(ValueError) as ei:
        pbix_spec.pbix_to_spec(pbix)
    assert "layout" in str(ei.value).lower()


def test_rejects_layout_without_visuals():
    pbix = _pbix({"Report/Layout": json.dumps({"sections": []})})
    with pytest.raises(ValueError) as ei:
        pbix_spec.pbix_to_spec(pbix)
    assert "visual" in str(ei.value).lower()


def test_rejects_corrupt_layout_json():
    pbix = _pbix({"Report/Layout": "{not json!!}"})
    with pytest.raises(ValueError):
        pbix_spec.pbix_to_spec(pbix)