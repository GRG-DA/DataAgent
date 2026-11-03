"""TDD: headless dashboard build + save (Chat Pro Phase 2)."""

from __future__ import annotations

import pandas as pd
import pytest

PLAN = {
    "title": "Sales overview",
    "table": "sales",
    "kpis": [
        {
            "label": "Total amount",
            "sql": "SELECT SUM(amount) AS value FROM sales",
            "variant": "kpi",
        },
        {
            "label": "Avg amount",
            "sql": "SELECT AVG(amount) AS value FROM sales",
            "variant": "trendkpi",
        },
    ],
    "filters": [],
    "zones": [
        {
            "zone": "trend",
            "chartType": "line",
            "title": "Amount over time",
            "sql": "SELECT sold_at AS label, SUM(amount) AS value FROM sales GROUP BY 1",
            "x": "label",
            "y": "value",
        },
        {
            "zone": "breakdown",
            "chartType": "bar",
            "title": "By product",
            "sql": "SELECT product AS label, SUM(amount) AS value FROM sales GROUP BY 1",
            "x": "label",
            "y": "value",
        },
    ],
    "insights": {
        "summary": "Sales grew steadily.",
        "key_findings": ["Alpha leads revenue."],
        "recommendations": ["Double down on Alpha."],
    },
}


def test_build_widgets_contract():
    from api.dashboard_build import build_widgets

    widgets = build_widgets(PLAN, "sales")
    assert widgets[0]["type"] == "custom"
    assert widgets[0]["title"] == "Sales overview"

    kpi_types = [
        w["type"]
        for w in widgets
        if w["type"] in ("kpi", "trendkpi", "prevkpi", "percentagekpi")
    ]
    assert kpi_types == ["kpi", "trendkpi"]

    charts = [
        w
        for w in widgets
        if w["type"] == "chart" and w["config"].get("chartType") in ("line", "bar")
    ]
    assert {c["config"]["chartType"] for c in charts} == {"line", "bar"}
    assert all(c["config"]["sql"] for c in charts)

    assert any("insights" in w["title"].lower() for w in widgets)

    for w in widgets:
        assert w["x"] >= 0 and w["y"] >= 0
        assert w["x"] + w["width"] <= 1920
        assert w["y"] + w["height"] <= 1080


def test_build_config_blankdash_contract():
    from api.dashboard_build import build_config

    cfg = build_config("Chat dash", "sales", [])
    assert cfg["name"] == "Chat dash"
    assert cfg["table"] == "sales"
    assert cfg["currentPage"] == 0
    assert cfg["pages"][0]["id"] == "page_1"
    assert cfg["pages"][0]["widgets"] == []


def test_create_dashboard_from_plan_persists(billing_db):
    from api.dashboard_build import create_dashboard_from_plan
    from api.dashboards import DashboardManager

    out = create_dashboard_from_plan(
        PLAN, "sales", owner_id="", name="Chat Pro test"
    )
    assert out["id"]
    assert out["url"] == f"#blankdash/{out['id']}"
    assert out["widget_count"] > 0

    dash = DashboardManager().get(out["id"], allow_all=True)
    assert dash is not None
    cfg = dash["layout"]["_blankdash"]
    assert cfg["name"] == "Chat Pro test"
    assert cfg["pages"][0]["widgets"]


def _seed_sales(loader):
    dates = pd.date_range("2026-01-01", periods=12, freq="MS").repeat(2)
    df = pd.DataFrame(
        {
            "sold_at": dates,
            "region": ["east", "west"] * 12,
            "amount": [100.0 + i * 7 for i in range(24)],
        }
    )
    loader.load_dataframe(
        df, "chatpro_sales", if_exists="replace", tenant_id="default"
    )


def test_auto_plan_for_table_reads_warehouse(billing_db):
    import api.state as state
    from api.dashboard_build import auto_plan_for_table

    _seed_sales(state.loader)
    plan = auto_plan_for_table("chatpro_sales")
    assert plan.get("kpis")
    assert plan.get("zones")


def test_create_dashboard_for_table_end_to_end(billing_db):
    import api.state as state
    from api.dashboard_build import create_dashboard_for_table
    from api.dashboards import DashboardManager

    _seed_sales(state.loader)
    out = create_dashboard_for_table(
        "chatpro_sales", owner_id="", name="Seeded dash"
    )
    dash = DashboardManager().get(out["id"], allow_all=True)
    cfg = dash["layout"]["_blankdash"]
    assert cfg["table"] == "chatpro_sales"
    assert cfg["pages"][0]["widgets"]


def test_auto_plan_rejects_bad_identifier(billing_db):
    from fastapi import HTTPException

    from api.dashboard_build import auto_plan_for_table

    with pytest.raises(HTTPException) as exc:
        auto_plan_for_table("sales; DROP TABLE x")
    assert exc.value.status_code == 400
