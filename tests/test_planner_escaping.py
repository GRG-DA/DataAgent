"""Planner literal escaping + alert-rule validation (P0 follow-ups).

Question text and warehouse cell values flow into generated SQL — quoting
must double single quotes (O'Brien) or the statement breaks out.
Alert condition_sql is stored and executed raw later, so creation time is
the enforcement point.
"""
from __future__ import annotations


def test_quote_literal_doubles_quotes():
    from agent.planner import _quote_literal

    assert _quote_literal("O'Brien") == "'O''Brien'"
    assert _quote_literal("plain") == "'plain'"
    assert _quote_literal("a'b'c") == "'a''b''c'"


def test_quote_ident_escapes_embedded_quotes():
    """Attacker-controlled CSV headers must not break out of identifiers."""
    from agent.planner import _quote_ident

    assert _quote_ident("Margin%") == '"Margin%"'
    assert _quote_ident('a"; DROP TABLE x; --') == '"a""; DROP TABLE x; --"'
    assert _quote_ident("sales") == "sales"


def test_conditional_count_escapes_apostrophe_end_to_end():
    """Planner SQL for apostrophe input must execute (no breakout)."""
    import duckdb

    from agent.planner import Planner

    planner = Planner()
    planner.set_schema_context(
        "TABLE analytics.t (3 rows):\n"
        "  - priority: VARCHAR\n"
        "  - amount: DOUBLE\n"
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE SCHEMA analytics")
        con.execute("CREATE TABLE analytics.t (priority VARCHAR, amount DOUBLE)")
        con.execute("INSERT INTO analytics.t VALUES ('O''Brien', 1.0), ('High', 2.0)")
        for question in (
            "how many orders are o'brien priority?",
            "how many tickets are high priority?",
            "total amount for o'brien priority",
        ):
            plan = planner.create_plan(question, ["t"])
            queries = [s.params.get("query", "") for s in plan.steps
                       if getattr(s.tool, "value", s.tool) == "sql"]
            assert queries, question
            for q in queries:
                # Every legitimate literal pairs its quotes; a lone quote
                # means a value broke out of its string context.
                assert "'" not in q.replace("''", ""), (question, q)
                con.execute(q).fetchall()  # must parse + run
    finally:
        con.close()


def test_alert_add_rejects_unsafe_condition(billing_db):
    """Stored condition_sql is validated before it can be persisted."""
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers
    from api.subscriptions import set_plan

    user_id = "alert_rule_tester"
    from api.auth import user_manager
    from tests.conftest import ensure_user

    ensure_user("alert_rule_tester", "Str0ng!Passw0rd", role="viewer")
    info = user_manager.get_user_info("alert_rule_tester")
    set_plan(info["user_id"], "lifetime")

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers("alert_rule_tester", role="viewer")
    bad = client.post("/alerts/add", headers=headers, json={
        "name": "evil_rule", "table": "sales",
        "condition_sql": "1=1; DROP TABLE analytics.sales"})
    assert bad.status_code == 422, bad.text

    bad_table = client.post("/alerts/add", headers=headers, json={
        "name": "evil_rule2", "table": "analytics.api_keys",
        "condition_sql": "COUNT(*) > 0"})
    assert bad_table.status_code in (400, 422), bad_table.text


def test_alert_check_rejects_injection(billing_db):
    """POST /alert/check validates table names (no raw interpolation)."""
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers, ensure_user
    from api.auth import user_manager
    from api.subscriptions import set_plan

    ensure_user("alert_check_tester", "Str0ng!Passw0rd", role="viewer")
    set_plan(user_manager.get_user_info("alert_check_tester")["user_id"], "lifetime")
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    headers = auth_headers("alert_check_tester", role="viewer")
    r = client.post("/alert/check", headers=headers, json={
        "table_name": 'sales"; DROP TABLE sales; --', "thresholds": {}})
    assert r.status_code == 200
    assert r.json()["success"] is False


def test_quality_trend_rejects_injection():
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers

    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    r = client.get("/quality/sales%22%3B%20DROP%20TABLE%20x%3B%20--/trend",
                   headers=auth_headers())
    assert r.status_code in (400, 404)
