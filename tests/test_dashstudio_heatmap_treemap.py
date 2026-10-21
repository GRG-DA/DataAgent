"""DashStudio heatmap/treemap visuals map to real zones (not placeholders)."""

import pandas as pd

from api import dashstudio


def _df():
    return pd.DataFrame({
        "region": ["north", "south", "east", "north"],
        "channel": ["web", "web", "store", "store"],
        "amount": [10.0, 20.0, 30.0, 40.0],
    })


def _profile(columns):
    return {"row_count": 4, "columns": columns}


def _measure(*names):
    return {n: {"is_measure": True} for n in names}


def _widget(idx=0, family="heatmap", title="Amount by region", fields=None):
    return {"index": idx, "family": family, "title": title,
            "x": 5, "y": 5, "w": 40, "h": 40, "content": None,
            "fields": fields if fields is not None else []}


def _spec(widgets):
    return {"pages": [{"name": "Sales", "width": 1280, "height": 720,
                       "widgets": widgets}]}


def _allow_sql(monkeypatch):
    monkeypatch.setattr("api.main._validate_sql_single", lambda sql: True)


def test_heatmap_with_two_dims_yields_real_zone(monkeypatch):
    _allow_sql(monkeypatch)
    profile = _profile({
        "region": {"is_categorical": True},
        "channel": {"is_categorical": True},
        **_measure("amount"),
    })
    plan = dashstudio._build_plan_from_spec(_df(), "sales", profile, _spec([_widget()]))

    assert plan is not None
    assert plan["placeholders"] == []
    assert len(plan["zones"]) == 1
    zone = plan["zones"][0]
    assert zone["chartType"] == "heatmap"
    assert zone["x"] == "region"
    assert zone["y"] == "channel"
    assert '"region"' in zone["sql"] and '"channel"' in zone["sql"]

    import duckdb

    df = _df()
    df["region"] = df["region"].astype(object)
    df["channel"] = df["channel"].astype(object)
    with duckdb.connect() as conn:
        conn.register("sales", df)
        rows = conn.execute(zone["sql"]).fetchall()
        cols = [d[0] for d in conn.description]
    assert cols == ["label", "category", "value"]
    assert len(rows) > 0


def test_heatmap_without_two_dims_yields_placeholder(monkeypatch):
    _allow_sql(monkeypatch)
    profile = _profile({"region": {"is_categorical": True}, **_measure("amount")})
    plan = dashstudio._build_plan_from_spec(_df(), "sales", profile, _spec([_widget()]))

    assert plan is not None
    assert plan["zones"] == []
    assert len(plan["placeholders"]) == 1
    assert plan["placeholders"][0]["family"] == "heatmap"


def test_treemap_yields_real_zone(monkeypatch):
    _allow_sql(monkeypatch)
    profile = _profile({
        "region": {"is_categorical": True},
        **_measure("amount"),
    })
    plan = dashstudio._build_plan_from_spec(
        _df(), "sales", profile, _spec([_widget(family="treemap")])
    )

    assert plan is not None
    assert plan["placeholders"] == []
    assert len(plan["zones"]) == 1
    zone = plan["zones"][0]
    assert zone["chartType"] == "treemap"
    assert zone["x"] == "region"
    assert '"region"' in zone["sql"] and '"amount"' in zone["sql"]

    import duckdb

    df = _df()
    df["region"] = df["region"].astype(object)
    df["channel"] = df["channel"].astype(object)
    with duckdb.connect() as conn:
        conn.register("sales", df)
        rows = conn.execute(zone["sql"]).fetchall()
        cols = [d[0] for d in conn.description]
    assert cols == ["label", "value"]
    assert len(rows) == 3


def test_treemap_without_match_yields_placeholder(monkeypatch):
    _allow_sql(monkeypatch)
    profile = _profile(_measure("amount"))
    plan = dashstudio._build_plan_from_spec(
        _df(), "sales", profile, _spec([_widget(family="treemap", title="Mystery")])
    )

    assert plan is not None
    assert plan["zones"] == []
    assert len(plan["placeholders"]) == 1
