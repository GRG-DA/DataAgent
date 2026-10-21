"""PPTX export workability — dashboard render must produce a valid deck.

Covers both paths in export/dashboard_export.py:
- export_pptx (classic widgets list): title + KPI + 2-per-slide chart
  images + data tables for every visual type incl. exotics
  (gauge/funnel/waterfall/treemap/combo/radar/scatter).
- export_dash_pptx (blankdash pages[].widgets[] canvas): layout-preserving
  slides with shapes inside slide bounds.
"""

from __future__ import annotations

import io

import pandas as pd

from export import dashboard_export as de


def _df(kind: str) -> pd.DataFrame:
    cats = ["A", "B", "C", "D", "E"]
    nums = [10, 20, 30, 40, 50]
    if kind in ("kpi", "gauge"):
        return pd.DataFrame({"value": [123.45]})
    if kind == "table":
        return pd.DataFrame({"region": cats, "amount": nums})
    if kind in ("scatter", "bubble"):
        return pd.DataFrame({"price": nums, "weight": [5, 15, 25, 35, 45]})
    if kind == "combo":
        return pd.DataFrame({"region": cats, "qty": nums, "revenue": [100, 200, 300, 400, 500]})
    return pd.DataFrame({"label": cats, "value": nums})


def _run_query(sql: str) -> pd.DataFrame:
    import re

    m = re.search(r"--(\\w+)", sql or "")
    return _df(m.group(1) if m else "bar").copy()


def _dashboard(types) -> dict:
    widgets = []
    for t in types:
        if t == "text":
            widgets.append({"type": "text", "title": "Note", "config": {"content": "hi"}})
        elif t == "table":
            widgets.append({"type": "table", "title": "Detail", "config": {"sql": "SELECT x --table"}})
        elif t == "kpi":
            widgets.append({"type": "kpi", "title": "KPI", "config": {"sql": "SELECT x --kpi"}})
        else:
            widgets.append({"type": t, "title": t, "config": {"sql": f"SELECT x --{t}", "xCol": "label"}})
    return {"name": "Render check", "widgets": widgets, "layout": {"insight": "up"}}


def test_export_pptx_all_types_render():
    from pptx import Presentation

    types = ["kpi", "bar", "line", "pie", "donut", "area", "gauge", "funnel",
             "waterfall", "treemap", "combo", "radar", "scatter", "table", "text"]
    data = de.export_pptx(_dashboard(types), _run_query, lambda t, l: _df("bar"))
    assert len(data) > 20000
    prs = Presentation(io.BytesIO(data))
    # title + KPI + ceil(12 charts / 2) + 1 table slide
    assert len(prs.slides) == 1 + 1 + 6 + 1
    pictures = sum(1 for s in prs.slides for sh in s.shapes
                   if sh.shape_type.name == "PICTURE")
    assert pictures == 12  # every chart widget incl. exotics has an image
    tables = sum(1 for s in prs.slides for sh in s.shapes if getattr(sh, "has_table", False))
    assert tables >= 12


def test_export_pptx_table_widget_not_duplicated():
    from pptx import Presentation

    dash = {"name": "t", "widgets": [{"type": "table", "title": "T", "config": {"sql": "x"}}], "layout": {}}
    prs = Presentation(io.BytesIO(de.export_pptx(dash, lambda s: _df("table"), lambda t, l: _df("table"))))
    assert len(prs.slides) == 2  # title + exactly one table slide
    tables = sum(1 for s in prs.slides for sh in s.shapes if getattr(sh, "has_table", False))
    assert tables == 1


def test_export_pptx_empty_data_never_crashes():
    from pptx import Presentation

    dash = _dashboard(["bar", "kpi", "table"])
    data = de.export_pptx(dash, lambda s: pd.DataFrame(), lambda t, l: pd.DataFrame())
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) >= 1


def test_export_dash_pptx_layout_preserving():
    from pptx import Presentation

    cfg = {"name": "Studio", "theme": "auto", "pages": [
        {"name": "Overview", "widgets": [
            {"id": "k1", "type": "kpi", "config": {"sql": "SELECT 1 --kpi"}, "x": 20, "y": 20, "width": 300, "height": 160},
            {"id": "c1", "type": "bar", "config": {"sql": "SELECT 1 --bar"}, "x": 340, "y": 20, "width": 800, "height": 400},
            {"id": "t1", "type": "table", "config": {"sql": "SELECT 1 --table"}, "x": 20, "y": 440, "width": 600, "height": 400},
        ]},
    ]}
    data = de.export_dash_pptx(cfg, _run_query, lambda t, l: _df("bar"))
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) == 2  # title + 1 page
    slide = prs.slides[1]
    assert len(list(slide.shapes)) >= 4
    # shapes inside slide bounds (13.333 x 7.5 in)
    for sh in slide.shapes:
        try:
            right = (sh.left + sh.width) / 914400.0
            bottom = (sh.top + sh.height) / 914400.0
        except Exception:
            continue
        assert right <= 13.333 * 1.02, right
        assert bottom <= 7.5 * 1.02, bottom
