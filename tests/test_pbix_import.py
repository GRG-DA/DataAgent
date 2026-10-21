"""PBIX import → blankdash replica pipeline tests.

Covers the glue that wires the deterministic ``api.pbix_spec`` parser into
the validated plan pipeline behind "Recreate from Image":

- ``dashstudio._pbix_canvas``                page size → canvas px
- ``dashstudio._validate_pbix_spec``         spec normalization / repair
- ``dashstudio._build_plans_from_pbix_spec`` one validated plan per page
- ``dashstudio.import_pbix``                 POST /dashstudio/import-pbix handler

SQL execution is never touched: the plan builder is stubbed for orchestration
tests and the endpoint handler only parses bytes.
"""

import asyncio
import io
import json
import zipfile

import pandas as pd
import pytest
from fastapi import HTTPException, UploadFile

from api import dashstudio, pbix_spec


# ─────────────────────────────── fixtures ────────────────────────────


def _pbix_page_widgets():
    """Widget shape produced by pbix_spec.parse_layout (see test_pbix_spec)."""
    return [
        {"index": 0, "family": "kpi", "title": "Total Sales",
         "x": 0.69, "y": 0.0, "w": 20.83, "h": 20.0, "content": None},
        {"index": 1, "family": "column", "title": "Revenue by region",
         "x": 0.69, "y": 21.11, "w": 34.72, "h": 75.56, "content": None},
        {"index": 2, "family": "slicer", "title": "Region",
         "x": 36.11, "y": 0.0, "w": 13.89, "h": 20.0, "content": None},
        {"index": 3, "family": "text", "title": "Note",
         "x": 36.11, "y": 21.11, "w": 48.61, "h": 8.89, "content": "Sales grew 12%"},
        {"index": 4, "family": "table", "title": "Sales by product",
         "x": 86.11, "y": 0.0, "w": 13.89, "h": 96.67, "content": None},
    ]


def _spec(pages=None):
    return {
        "used": "pbix",
        "theme": "light",
        "background": "#f1f5f9",
        "cardBg": "#ffffff",
        "textColor": "#1e293b",
        "accent": "#01b8aa",
        "palette": ["#01b8aa", "#374649"],
        "header": {"present": True, "title": "Sales"},
        "canvas": {"w": 1440, "h": 900},
        "pages": pages if pages is not None else [
            {"name": "Sales", "width": 1440, "height": 900, "widgets": _pbix_page_widgets()},
        ],
    }


PROFILE = {"row_count": 4, "columns": {}}


@pytest.fixture
def stub_plan(monkeypatch):
    """Replace _build_plan_from_spec with a spy echoing the canvas it got."""
    calls = []

    def spy(df, table, profile, spec, title="", canvas=None):
        calls.append({"title": title, "canvas": dict(canvas or {}), "spec": spec})
        return {
            "table": table,
            "canvas": dict(canvas or {}),
            "kpis": [], "filters": [], "zones": [], "tables": [], "texts": [],
            "placeholders": [{"family": "kpi", "title": "X", "box": {}, "widx": 0}],
        }

    monkeypatch.setattr(dashstudio, "_build_plan_from_spec", spy)
    return calls


# ───────────────────────────── _pbix_canvas ──────────────────────────


def test_pbix_canvas_scales_small_pages_up():
    assert dashstudio._pbix_canvas(1280, 720) == (1900, 1069)


def test_pbix_canvas_preserves_page_aspect():
    assert dashstudio._pbix_canvas(1440, 900) == (1900, 1188)


def test_pbix_canvas_keeps_large_pages():
    assert dashstudio._pbix_canvas(2560, 1440) == (2560, 1440)


def test_pbix_canvas_falls_back_when_missing():
    assert dashstudio._pbix_canvas(0, 0) == (1900, 1080)
    assert dashstudio._pbix_canvas(None, None) == (1900, 1080)


def test_pbix_canvas_caps_absurd_sizes():
    assert dashstudio._pbix_canvas(99999, 99999) == (1900, 1080)


# ───────────────────────── _validate_pbix_spec ───────────────────────


def test_validate_pbix_spec_passes_through_valid():
    out = dashstudio._validate_pbix_spec(_spec())
    assert out["theme"] == "light"
    assert out["pages"][0]["name"] == "Sales"
    assert len(out["pages"][0]["widgets"]) == 5


def test_validate_pbix_spec_repairs_bad_colors():
    s = _spec()
    s["accent"] = "not-a-color"
    s["palette"] = ["zzz", "#374649"]
    out = dashstudio._validate_pbix_spec(s)
    assert out["accent"] == "#2563eb"          # light-theme default
    assert out["palette"] == ["#374649"]       # junk dropped


def test_validate_pbix_spec_defaults_dark_theme_colors():
    s = _spec()
    s["theme"] = "dark"
    s["background"] = s["cardBg"] = s["textColor"] = s["accent"] = ""
    out = dashstudio._validate_pbix_spec(s)
    assert out["background"] == "#0f172a"
    assert out["cardBg"] == "#252540"
    assert out["textColor"] == "#e0e0e0"
    assert out["accent"] == "#818cf8"


def test_validate_pbix_spec_drops_empty_pages():
    s = _spec(pages=[
        {"name": "Empty", "width": 100, "height": 100, "widgets": []},
        {"name": "Sales", "width": 1440, "height": 900, "widgets": _pbix_page_widgets()},
    ])
    out = dashstudio._validate_pbix_spec(s)
    assert [p["name"] for p in out["pages"]] == ["Sales"]


def test_validate_pbix_spec_rejects_no_pages():
    with pytest.raises(ValueError):
        dashstudio._validate_pbix_spec({"pages": []})
    with pytest.raises(ValueError):
        dashstudio._validate_pbix_spec("nope")


def test_validate_pbix_spec_rejects_all_pages_empty():
    with pytest.raises(ValueError):
        dashstudio._validate_pbix_spec(_spec(pages=[{"name": "E", "width": 100, "height": 100, "widgets": []}]))


# ─────────────────── _build_plans_from_pbix_spec ─────────────────────


def test_build_plans_one_per_page_with_own_canvas(stub_plan):
    s = _spec(pages=[
        {"name": "Sales", "width": 1440, "height": 900, "widgets": _pbix_page_widgets()},
        {"name": "Trend", "width": 1280, "height": 720, "widgets": _pbix_page_widgets()[:2]},
    ])
    plans = dashstudio._build_plans_from_pbix_spec(s, "t", pd.DataFrame({"a": [1]}), PROFILE)
    assert len(plans) == 2
    assert plans[0]["name"] == "Sales"
    assert plans[1]["name"] == "Trend"
    # Each page's boxes normalize against its OWN aspect — no stretching.
    assert stub_plan[0]["canvas"] == {"w": 1900, "h": 1188}
    assert stub_plan[1]["canvas"] == {"w": 1900, "h": 1069}
    assert stub_plan[0]["spec"]["pages"][0]["widgets"][0]["title"] == "Total Sales"
    assert len(stub_plan[1]["spec"]["pages"][0]["widgets"]) == 2


def test_build_plans_injects_page_name_as_header_title(stub_plan):
    s = _spec(pages=[
        {"name": "Sales", "width": 1440, "height": 900, "widgets": _pbix_page_widgets()},
        {"name": "Trend", "width": 1280, "height": 720, "widgets": _pbix_page_widgets()[:2]},
    ])
    plans = dashstudio._build_plans_from_pbix_spec(s, "t", pd.DataFrame({"a": [1]}), PROFILE)
    assert plans[0]["spec"]["header"]["title"] == "Sales"
    assert plans[1]["spec"]["header"]["title"] == "Trend"
    assert plans[0]["spec"]["accent"] == "#01b8aa"


def test_build_plans_no_header_when_spec_has_none(stub_plan):
    s = _spec()
    s["header"] = {"present": False}
    plans = dashstudio._build_plans_from_pbix_spec(s, "t", pd.DataFrame({"a": [1]}), PROFILE)
    assert plans[0]["spec"]["header"]["present"] is False


def test_build_plans_stamps_page_meta(stub_plan):
    plans = dashstudio._build_plans_from_pbix_spec(_spec(), "t", pd.DataFrame({"a": [1]}), PROFILE)
    assert plans[0]["used"] == "pbix"
    assert plans[0]["table"] == "t"
    assert plans[0]["canvas"] == {"w": 1900, "h": 1188}


def test_build_plans_skips_empty_pages(stub_plan):
    s = _spec(pages=[
        {"name": "Empty", "width": 100, "height": 100, "widgets": []},
        {"name": "Sales", "width": 1440, "height": 900, "widgets": _pbix_page_widgets()},
    ])
    plans = dashstudio._build_plans_from_pbix_spec(s, "t", pd.DataFrame({"a": [1]}), PROFILE)
    assert len(plans) == 1 and plans[0]["name"] == "Sales"


def test_build_plans_skips_widgetless_plans(monkeypatch):
    """A page whose plan carries no widgets at all is dropped, not rendered."""
    monkeypatch.setattr(
        dashstudio, "_build_plan_from_spec",
        lambda *a, **k: {"table": "t", "canvas": {}, "kpis": [], "zones": [],
                         "filters": [], "tables": [], "texts": [], "placeholders": []},
    )
    plans = dashstudio._build_plans_from_pbix_spec(_spec(), "t", pd.DataFrame({"a": [1]}), PROFILE)
    assert plans == []


def test_build_plans_survives_none_plan(monkeypatch):
    monkeypatch.setattr(dashstudio, "_build_plan_from_spec", lambda *a, **k: None)
    plans = dashstudio._build_plans_from_pbix_spec(_spec(), "t", pd.DataFrame({"a": [1]}), PROFILE)
    assert plans == []


# ───────────────────────── import_pbix handler ───────────────────────


def _upload(raw, name="report.pbix"):
    return UploadFile(file=io.BytesIO(raw), filename=name)


def _pbix_bytes(sections, theme=True):
    entries = {"Report/Layout": json.dumps({"sections": sections})}
    if theme:
        entries["Report/StaticResources/RegisteredResources/CustomTheme.json"] = json.dumps({
            "name": "CustomTheme",
            "dataColors": ["#01B8AA", "#374649"],
            "background": "#F3F3F3",
        })
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


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
        "layout": {"margin": {}, "width": width, "height": height},
        "visualContainers": containers,
    }


def _one_page():
    return [_section("Sales", 1280, 720, [
        _vc("cardVisual", title="Total Sales", box={"x": 10, "y": 0, "width": 300, "height": 180, "z": 0}),
        _vc("clusteredColumnChart", title="Revenue by region",
            box={"x": 10, "y": 190, "width": 500, "height": 520, "z": 1}),
    ])]


def test_import_pbix_returns_spec():
    res = asyncio.run(dashstudio.import_pbix(_upload(_pbix_bytes(_one_page()))))
    assert res["success"] is True
    assert res["fileName"] == "report.pbix"
    spec = res["spec"]
    assert spec["used"] == "pbix"
    assert spec["_meta"]["widgetCount"] == 2
    assert spec["_meta"]["pageCount"] == 1
    assert spec["accent"] == "#01b8aa"


def test_import_pbix_rejects_wrong_extension():
    with pytest.raises(HTTPException) as ei:
        asyncio.run(dashstudio.import_pbix(_upload(b"PK\x03\x04notapbix", "notes.txt")))
    assert ei.value.status_code == 400


def test_import_pbix_rejects_corrupt_file():
    with pytest.raises(HTTPException) as ei:
        asyncio.run(dashstudio.import_pbix(_upload(b"\x00\x01garbage", "x.pbix")))
    assert ei.value.status_code == 400


def test_import_pbix_rejects_pbix_without_visuals():
    raw = _pbix_bytes([_section("Empty", 1280, 720, [])])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(dashstudio.import_pbix(_upload(raw)))
    assert ei.value.status_code == 400


# ───────────────────── parser additions (families) ───────────────────


def test_visual_family_mapping_extended():
    cases = [
        ("kpiVisual", "kpi"),
        ("card", "kpi"),
        ("pivotTable", "table"),
        ("filledMap", "map"),
        ("azureMap", "map"),
        ("map", "map"),
        ("advancedRadarChart", "placeholder"),
    ]
    for vtype, expected in cases:
        assert pbix_spec._visual_family(vtype) == expected, vtype


def test_absurd_page_size_falls_back_to_default():
    page = _section("Big", 99999, 99999, [
        _vc("cardVisual", title="K", box={"x": 0, "y": 0, "width": 100, "height": 100, "z": 0}),
    ])
    raw = _pbix_bytes([page], theme=False)
    spec = pbix_spec.pbix_to_spec(raw)
    assert spec["pages"][0]["width"] == 1280
    assert spec["pages"][0]["height"] == 720
