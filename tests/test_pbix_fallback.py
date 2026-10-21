"""PBIT native visuals — every widget type emits its true Power BI visualType.

Previously exotic widgets were substituted (gauge->card, funnel->bar, etc.)
because projections were unverified. Projections are now corrected against the
Fabric 2.7.0 schema + MS docs, so all types export natively and verify
structurally via export/verify_export.py.
"""

from __future__ import annotations


def _wm(wtype: str, num_cols=("amount",), x_col: str = "region"):
    return {
        "widget": {"type": wtype, "title": f"{wtype} widget", "config": {}},
        "table": "sales",
        "x_col": x_col,
        "num_cols": list(num_cols),
        "cols": [x_col, *num_cols],
        "_kpi_idx": 0,
    }


def test_no_fallbacks_all_native():
    from export.pbix_export import _PBI_SAFE_FALLBACK

    assert _PBI_SAFE_FALLBACK == {}


def test_gauge_exports_native():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("gauge"))
    assert sv["visualType"] == "gauge"
    assert set(sv["projections"]) == {"Y"}


def test_funnel_exports_native():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("funnel"))
    assert sv["visualType"] == "funnel"
    assert set(sv["projections"]) == {"Category", "Y"}


def test_waterfall_exports_native_with_sentiment():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("waterfall"))
    assert sv["visualType"] == "waterfallChart"
    assert set(sv["projections"]) == {"Category", "Y"}
    assert "sentimentColors" in (sv.get("objects") or {})


def test_treemap_exports_native_group_values():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("treemap"))
    assert sv["visualType"] == "treemap"
    assert set(sv["projections"]) == {"Group", "Values"}


def test_radar_exports_native():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("radar"))
    assert sv["visualType"] == "radarChart"
    assert set(sv["projections"]) == {"Category", "Y"}


def test_combo_exports_native_y_y2():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("combo", num_cols=("qty", "revenue")))
    assert sv["visualType"] == "lineClusteredColumnComboChart"
    assert set(sv["projections"]) == {"Category", "Y", "Y2"}
    assert len(sv["projections"]["Y"]) == 1
    assert len(sv["projections"]["Y2"]) == 1


def test_combo_single_measure_columns_only():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("combo", num_cols=("qty",)))
    assert sv["visualType"] == "lineClusteredColumnComboChart"
    assert set(sv["projections"]) == {"Category", "Y"}
    assert "Y2" not in sv["projections"]


def test_scatter_emits_size_role():
    from export.pbix_export import _chart_from_widget

    sv = _chart_from_widget(_wm("scatter", num_cols=("price", "weight")))
    assert sv["visualType"] == "scatterChart"
    assert set(sv["projections"]) == {"X", "Y", "Size"}
    objs = sv.get("objects") or {}
    assert "categoryLabels" in objs
    assert "labels" not in objs


def test_supported_types_unchanged():
    from export.pbix_export import _chart_from_widget

    assert _chart_from_widget(_wm("bar"))["visualType"] == "clusteredColumnChart"
    assert _chart_from_widget(_wm("line"))["visualType"] == "lineChart"
    assert _chart_from_widget(_wm("kpi"))["visualType"] == "cardVisual"


def test_native_visuals_query_and_transforms_resolve():
    """New roles (Group/Values, Y2, Size, TargetValue) resolve in query + dataTransforms."""
    import json

    from export.pbix_export import _chart_from_widget, _make_visual_query, _make_visual_data_transforms

    cases = {
        "treemap": _wm("treemap"),
        "combo": _wm("combo", num_cols=("qty", "revenue")),
        "gauge": _wm("gauge"),
        "funnel": _wm("funnel"),
        "waterfall": _wm("waterfall"),
        "radar": _wm("radar"),
        "scatter": _wm("scatter", num_cols=("price", "weight")),
    }
    for wtype, wm in cases.items():
        sv = _chart_from_widget(wm)
        q = json.loads(_make_visual_query(sv))
        dt = json.loads(_make_visual_data_transforms(sv))
        selects = q["Commands"][0]["SemanticQueryDataShapeCommand"]["Query"]["Select"]
        names = [s.get("Name") for s in selects]
        meta = [s.get("Name") for s in dt["queryMetadata"]["Select"]]
        assert set(meta) == set(names), wtype
        for role, refs in (sv.get("projections") or {}).items():
            for r in (refs if isinstance(refs, list) else [refs]):
                assert r["queryRef"] in names, (wtype, role)
        # treemap Group active, funnel/waterfall/combo Category active
        if wtype in ("treemap", "funnel", "waterfall", "combo", "radar"):
            active = dt.get("projectionActiveItems") or {}
            assert active, wtype
