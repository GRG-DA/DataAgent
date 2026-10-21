"""SQL table scoping: schema-qualified names must resolve to real tables.

Issue: `/llm/generate-code` accepted `context: "analytics.chocolate_sales"`
(a schema-qualified string, which DuckDB and the UI both reasonably produce)
but `_scope_sql_tables()` passed it through untouched. `_sql_schema_context()`
then ran `DESCRIBE "analytics"."analytics.chocolate_sales"`, failed, and
returned an empty schema — so the LLM generated SQL blind and `generate_sql`
quietly dropped back to the rule-based fallback (`llm_used: false`).

Guarantees:
  1. Schema-qualified and quoted identifiers are normalized to bare names.
  2. Bare names are left untouched.
  3. The generated-code endpoint passes bare table names to the schema
     context builder (so the LLM sees real columns).
"""

from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────
# 1. _scope_sql_tables normalization
# ─────────────────────────────────────────────────────────────

def test_scope_strips_schema_prefix():
    from api.main import _scope_sql_tables

    assert _scope_sql_tables("total sales by region",
                             ["analytics.chocolate_sales"]) == ["chocolate_sales"]


def test_scope_strips_quoted_identifiers():
    from api.main import _scope_sql_tables

    assert _scope_sql_tables("q", ['analytics."chocolate_sales"']) == ["chocolate_sales"]
    assert _scope_sql_tables("q", ['"analytics"."chocolate_sales"']) == ["chocolate_sales"]


def test_scope_keeps_bare_names():
    from api.main import _scope_sql_tables

    assert _scope_sql_tables("q", ["chocolate_sales"]) == ["chocolate_sales"]
    assert _scope_sql_tables("q", ["chocolate_sales", "retail_sales"]) == [
        "chocolate_sales", "retail_sales",
    ]


def test_scope_normalizes_mixed_lists():
    from api.main import _scope_sql_tables

    assert _scope_sql_tables("q", ["analytics.chocolate_sales", "retail_sales"]) == [
        "chocolate_sales", "retail_sales",
    ]


def test_scope_handles_schema_qualified_list_context():
    from api.main import _scope_sql_tables

    out = _scope_sql_tables("q", ["analytics.chocolate_sales"])
    assert out == ["chocolate_sales"]


# ─────────────────────────────────────────────────────────────
# 2. /llm/generate-code hands bare names to the schema builder
# ─────────────────────────────────────────────────────────────

def test_generate_code_scopes_schema_qualified_context(monkeypatch):
    from api.main import app, _sql_schema_context
    from tests.conftest import auth_headers

    captured: dict = {}
    real_ctx = _sql_schema_context

    def spy(table_names):
        captured["tables"] = table_names
        return real_ctx(table_names)

    async def fake_generate_sql(prompt, **kwargs):
        captured["kwargs"] = kwargs
        return "SELECT 1 AS ok", "llm"

    import api.main as main_mod
    monkeypatch.setattr(main_mod, "_sql_schema_context", spy)
    monkeypatch.setattr(main_mod, "generate_sql", fake_generate_sql)
    # Out of scope here: the daily-AI-cap gate (covered by test_ai_metering).
    monkeypatch.setattr(main_mod, "_meter_ai_request", lambda req: None)
    # The gateway is exercised through `generate_sql` above, so all the
    # endpoint needs from the orchestrator is a truthy client.
    class _StubOrchestrator:
        llm_client = object()
        planner = None

    monkeypatch.setattr(main_mod, "orchestrator", _StubOrchestrator())

    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)
    r = client.post(
        "/llm/generate-code",
        json={"prompt": "total sales by region", "language": "sql",
              "context": "analytics.chocolate_sales"},
        headers=auth_headers(),
        timeout=60,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, r.text
    assert body["llm_used"] is True
    assert captured.get("tables") == ["chocolate_sales"], (
        "schema context must be built from bare table names"
    )
    assert captured["kwargs"].get("table_names") == ["chocolate_sales"]
