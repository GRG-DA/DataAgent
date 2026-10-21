"""LLM refinement of PBIX plans tests.

``dashstudio._refine_pbix_plans_with_llm`` sends each planned widget's title /
projected fields plus the warehouse column profile to the LLM and applies the
returned per-widget mappings (dimension / measure / agg / chartType / title).
Only SQL-validated mappings are applied; anything the LLM gets wrong leaves
the deterministic mapping untouched. The LLM call is injectable so tests stay
hermetic (no provider, no warehouse).
"""

import pytest

from api import dashstudio


def _profile():
    return {
        "row_count": 4,
        "columns": {
            "region": {"is_categorical": True},
            "product": {"is_categorical": True},
            "amount": {"is_measure": True},
            "discount": {"is_measure": True},
            "sold_at": {"is_date": True, "range_days": 90},
        },
    }


def _plan_with_zone(widx=1, x="region", y="amount", chart="bar", title="Revenue by region"):
    return {
        "table": "sales",
        "canvas": {"w": 1900, "h": 1080},
        "name": "Sales",
        "used": "pbix",
        "spec": {"theme": "light", "header": {"present": False}},
        "kpis": [],
        "filters": [],
        "tables": [],
        "texts": [],
        "placeholders": [],
        "zones": [{
            "zone": "z1", "chartType": chart, "title": title,
            "metric": y, "x": x, "y": y,
            "sql": f"SELECT {x} AS label, SUM({y}) AS value FROM sales GROUP BY 1",
            "cols": 6, "rows": 4, "config": {}, "box": {"x": 10, "y": 10, "w": 400, "h": 300},
            "widx": widx,
        }],
    }


def _kpi_plan(widx=0, col="amount", agg="SUM"):
    plan = _plan_with_zone()
    plan["zones"] = []
    plan["kpis"] = [{
        "label": "Total amount", "variant": "kpi", "agg": agg, "column": col,
        "expression": f"{agg}({col})", "spark": False,
        "sql": f"SELECT {agg}({col}) AS value FROM sales",
        "box": {"x": 10, "y": 10, "w": 300, "h": 120}, "widx": widx,
    }]
    return plan


def _mapping(**kw):
    base = {"page": 0, "widx": 1, "dimension": None, "measure": None,
            "agg": None, "chartType": None, "title": None}
    base.update(kw)
    return base


def _reply(mappings):
    import json
    return "```json\n" + json.dumps({"mappings": mappings}) + "\n```"


SQL_OK = lambda sql: True  # noqa: E731 — validator stub: no warehouse in tests


@pytest.mark.asyncio
async def test_refine_applies_valid_mapping(monkeypatch):
    plan = _plan_with_zone(x="product", y="discount")  # wrong deterministic guess
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(),
        llm_call=lambda s, u: _reply([_mapping(dimension="region", measure="amount", chartType="line")]),
        validate_sql=SQL_OK,
    )
    z = plans[0]["zones"][0]
    assert z["x"] == "region"
    assert z["y"] == "amount"
    assert z["chartType"] == "line"
    assert "region" in z["sql"].replace('"', "") and "SUM(amount)" in z["sql"].replace('"', "")


@pytest.mark.asyncio
async def test_refine_rejects_unknown_columns(monkeypatch):
    plan = _plan_with_zone()
    before = dict(plan["zones"][0])
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(),
        llm_call=lambda s, u: _reply([_mapping(dimension="hack_col", measure="drop_table")]),
    )
    z = plans[0]["zones"][0]
    assert z["x"] == before["x"] and z["y"] == before["y"]


@pytest.mark.asyncio
async def test_refine_ignores_unsafe_chart_types():
    plan = _plan_with_zone(chart="bar")
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(),
        llm_call=lambda s, u: _reply([_mapping(chartType="3d-pyramid")]),
    )
    assert plans[0]["zones"][0]["chartType"] == "bar"


@pytest.mark.asyncio
async def test_refine_updates_zone_title():
    plan = _plan_with_zone()
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(),
        llm_call=lambda s, u: _reply([_mapping(title="Revenue by Region", dimension="region", measure="amount")]),
        validate_sql=SQL_OK,
    )
    assert plans[0]["zones"][0]["title"] == "Revenue by Region"


@pytest.mark.asyncio
async def test_refine_kpi_measure_and_agg():
    plan = _kpi_plan(col="discount", agg="SUM")
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(),
        llm_call=lambda s, u: _reply([_mapping(widx=0, measure="amount", agg="AVG")]),
        validate_sql=SQL_OK,
    )
    k = plans[0]["kpis"][0]
    assert k["column"] == "amount"
    assert k["agg"] == "AVG"
    assert "AVG(amount)" in k["sql"].replace('"', "").replace("(", "(")


@pytest.mark.asyncio
async def test_refine_survives_garbage_reply():
    plan = _plan_with_zone()
    before = dict(plan["zones"][0])
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(),
        llm_call=lambda s, u: "I cannot help with that. Sorry!",
    )
    z = plans[0]["zones"][0]
    assert z["x"] == before["x"] and z["chartType"] == before["chartType"]


@pytest.mark.asyncio
async def test_refine_survives_raising_llm():
    plan = _plan_with_zone()
    async def boom(system, user):
        raise RuntimeError("provider down")
    plans = await dashstudio._refine_pbix_plans_with_llm([plan], _profile(), llm_call=boom)
    assert plans[0]["zones"][0]["chartType"] == "bar"


@pytest.mark.asyncio
async def test_refine_without_widgets_is_noop():
    plan = _plan_with_zone()
    plan["zones"] = []
    called = []
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(), llm_call=lambda s, u: called.append(1) or _reply([]),
    )
    assert called == []          # LLM not even invoked
    assert plans[0]["zones"] == []


@pytest.mark.asyncio
async def test_refine_trend_sql_for_date_dimension():
    plan = _plan_with_zone(x="product", y="amount", chart="column")
    plans = await dashstudio._refine_pbix_plans_with_llm(
        [plan], _profile(),
        llm_call=lambda s, u: _reply([_mapping(dimension="sold_at", measure="amount")]),
        validate_sql=SQL_OK,
    )
    z = plans[0]["zones"][0]
    assert z["x"] == "sold_at"
    assert "strftime" in z["sql"]          # trend bucketing kicks in for dates
    assert "GROUP BY 1 ORDER BY 1" in z["sql"]
