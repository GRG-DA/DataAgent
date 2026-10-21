"""PBIX radar visual → real zone (not placeholder) plan tests.

A PBIX radar chart (``visualType: radarChart`` with ``Category`` + ``Y``
projections — see ``test_pbix_fallback.test_radar_exports_native``) must
map to the natively rendered frontend radar (``chartType: "radar"`` in
``aiChartBuild._planWidgetType`` / ``charts.js`` / ``dashboardRenderer``)
instead of a sized placeholder.
"""

import pandas as pd

from api import dashstudio


def _df():
    return pd.DataFrame({
        "region": ["north", "south", "east"],
        "amount": [10.0, 20.0, 30.0],
        "qty": [1.0, 2.0, 3.0],
    })


def _profile(columns):
    return {"row_count": 3, "columns": columns}


def _measure(*names):
    return {n: {"is_measure": True} for n in names}


def _widget(idx=0, family="radar", title="Amount by region", fields=None):
    return {"index": idx, "family": family, "title": title,
            "x": 5, "y": 5, "w": 40, "h": 40, "content": None,
            "fields": fields if fields is not None else []}


def _spec(widgets):
    return {"pages": [{"name": "Sales", "width": 1280, "height": 720,
                       "widgets": widgets}]}


def _allow_sql(monkeypatch):
    """Plan SQL validation hits the real warehouse — stub it to True.

    Genuine SQL-shape proof comes from executing the emitted SQL against an
    in-memory DuckDB table in the positive test below.
    """
    monkeypatch.setattr("api.main._validate_sql_single", lambda sql: True)


def test_radar_with_projected_fields_yields_real_zone(monkeypatch):
    """Category + Y projections bind deterministically → real radar zone."""
    _allow_sql(monkeypatch)
    profile = _profile({"region": {"is_categorical": True}, **_measure("amount", "qty")})
    widgets = [_widget(fields=[{"ref": "Sales.Sum of Amount", "role": "Y"},
                               {"ref": "Sales.Region", "role": "Category"}])]
    plan = dashstudio._build_plan_from_spec(_df(), "sales", profile, _spec(widgets))

    assert plan is not None
    assert plan["placeholders"] == []
    assert len(plan["zones"]) == 1
    zone = plan["zones"][0]
    assert zone["zone"] == "special"
    assert zone["chartType"] == "radar"
    assert zone["x"] == "region"
    assert zone["y"] == "amount"
    assert '"region"' in zone["sql"] and '"amount"' in zone["sql"]

    # The emitted SQL really runs: label + one column per measure.
    import duckdb

    df = _df()
    df["region"] = df["region"].astype(object)
    with duckdb.connect() as conn:
        conn.register("sales", df)
        rows = conn.execute(zone["sql"]).fetchall()
        cols = [d[0] for d in conn.description]
    assert cols[0] == "label" and set(cols[1:]) == {"amount", "qty"}
    assert len(rows) == 3


def test_radar_falls_back_to_title_match(monkeypatch):
    """No projections → fuzzy title match still yields a radar zone."""
    _allow_sql(monkeypatch)
    profile = _profile({"region": {"is_categorical": True}, **_measure("amount")})
    plan = dashstudio._build_plan_from_spec(_df(), "sales", profile, _spec([_widget()]))

    assert plan is not None
    assert plan["placeholders"] == []
    assert len(plan["zones"]) == 1
    assert plan["zones"][0]["chartType"] == "radar"


def test_radar_prefers_projected_measure_over_title(monkeypatch):
    """A Y-role field beats the fuzzy title match when they disagree."""
    _allow_sql(monkeypatch)
    profile = _profile({"region": {"is_categorical": True}, **_measure("amount", "qty")})
    widgets = [_widget(title="Amount by region",
                        fields=[{"ref": "Sales.Sum of Qty", "role": "Y"},
                                {"ref": "Sales.Region", "role": "Category"}])]
    plan = dashstudio._build_plan_from_spec(_df(), "sales", profile, _spec(widgets))

    assert len(plan["zones"]) == 1
    assert plan["zones"][0]["y"] == "qty"


def test_radar_caps_measures_at_six(monkeypatch):
    """Up to 6 axes ride along as extra result columns (one dataset each)."""
    _allow_sql(monkeypatch)
    measures = [f"m{i}" for i in range(8)]
    profile = _profile({"region": {"is_categorical": True}, **_measure(*measures)})
    df = pd.DataFrame({"region": ["a", "b"], **{m: [1.0, 2.0] for m in measures}})
    widgets = [_widget(title="Metrics by region",
                        fields=[{"ref": "Sales.Region", "role": "Category"}])]
    plan = dashstudio._build_plan_from_spec(df, "sales", profile, _spec(widgets))

    assert len(plan["zones"]) == 1
    assert plan["zones"][0]["sql"].count("SUM(") == 6


def test_radar_without_matching_columns_yields_placeholder(monkeypatch):
    """No categorical dimension → sized placeholder, same as siblings."""
    _allow_sql(monkeypatch)
    profile = _profile(_measure("amount", "qty"))
    widgets = [_widget(title="Mystery radar")]
    plan = dashstudio._build_plan_from_spec(_df(), "sales", profile, _spec(widgets))

    assert plan is not None
    assert plan["zones"] == []
    assert len(plan["placeholders"]) == 1
    assert plan["placeholders"][0]["family"] == "radar"
