"""End-to-end HTTP isolation test for the AI agent pipeline (ADR-009 D1).

ADR-009 §"Verification" claims:

    ``tests/test_saas_tenancy.py`` — cross-tenant isolation: tenant A cannot
    read, write, list or drop tenant B's table; the same logical table name
    coexists per tenant.

and LAUNCH-TECHNICAL-REVIEW P0-3 fix #3 adds:

    **Then write the end-to-end HTTP isolation test** ADR-009 claims: two
    accounts, two tenants, same table name, asserting through ``POST /ask``,
    ``POST /sql``, ``GET /tables``, an export and a Dashboard Studio visual
    that neither can see the other.

The existing ``tests/test_saas_tenancy.py`` only exercises the *helpers*
(schema naming, the SQL policy, a raw DuckDB connection).  It never drives
the **real endpoints**, so the defect it was supposed to catch survived: the
agent pipeline hard-coded the platform schema, so ``POST /ask`` read the
operator's table instead of the caller's own — simultaneously a broken
headline feature *and* a cross-tenant read.

This module is that missing end-to-end proof.  It creates two tenants that
own a table with the **same name** and different data, then drives the real
HTTP endpoints (``POST /ask``, ``POST /ask-sql``, ``POST /llm/chat/stream``,
``POST /sql``, ``GET /tables``) and asserts that neither tenant ever sees the
other's rows.  It also reproduces the shared-connection leak: a Dashboard
Studio visual used to leave the caller's schema on the *process-wide*
connection, so the next tenant's ``/ask`` resolved the previous tenant's
table.
"""
from __future__ import annotations

import json

import duckdb
import pytest

from api.tenancy import tenant_schema

#: The logical table name both tenants own.
TABLE = "shared_sales"

#: Region values that only ever exist in ONE tenant's rows.  If an answer
#: contains the other tenant's marker, a cross-tenant read happened.
ALICE_MARKER = "ALICE_ONLY_REGION"
BOB_MARKER = "BOB_ONLY_REGION"

ALICE_TENANT = "u_iso_alice"
BOB_TENANT = "u_iso_bob"


# ─────────────────────────────────────────────────────────────
# Fixture: a warehouse with the same table in two tenant schemas
# ─────────────────────────────────────────────────────────────

def _build_warehouse(path: str) -> None:
    """Create the platform schema plus one copy of *TABLE* per tenant."""
    alice = tenant_schema(ALICE_TENANT)
    bob = tenant_schema(BOB_TENANT)
    con = duckdb.connect(path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{alice}"')
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{bob}"')
        for schema, region, amount in (
            (alice, ALICE_MARKER, 111.5),
            (bob, BOB_MARKER, 999.25),
        ):
            con.execute(f'DROP TABLE IF EXISTS "{schema}"."{TABLE}"')
            con.execute(
                f'CREATE TABLE "{schema}"."{TABLE}" ('
                " region VARCHAR, product VARCHAR, amount DOUBLE)"
            )
            con.execute(
                f'INSERT INTO "{schema}"."{TABLE}" VALUES '
                f"('{region}', 'Widget', {amount}), "
                f"('{region}', 'Gadget', {amount})"
            )
    finally:
        con.close()


@pytest.fixture()
def iso_app(tmp_path, monkeypatch):
    """Wire the app onto a two-tenant warehouse and return the test app.

    The app's *real* code paths are used — the real loader, the real
    ``SQLTool``, the real planner and the real orchestrator — only the
    warehouse file and the (absent) LLM are swapped, so the test proves the
    production wiring rather than a mock of it.
    """
    from fastapi.testclient import TestClient

    import api.db as db_mod
    import api.main as main_mod
    import api.state as state_mod
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from agent.tools_sql import SQLTool
    from agent.tools_pandas import PandasTool
    from agent.tools_viz import VizTool
    from config import settings
    from etl.load import WarehouseLoader

    # The suite's shared identity store gives the *oldest* account the
    # platform ("default") tenant and every later account a private one.
    # Create the standard test account FIRST so this file's two tenant
    # accounts do not take that slot and push every later account (which
    # expects the platform tenant) into a private tenant of its own.
    from tests.conftest import ensure_user

    ensure_user("test_auth_user", "Str0ng!Passw0rd", role="admin")

    db_path = str(tmp_path / "iso.duckdb")
    _build_warehouse(db_path)

    # Point the whole app at the throwaway warehouse.
    monkeypatch.setattr(settings.duckdb, "database_path", db_path)
    # The shared connection must not keep pointing at the previous file.
    db_mod.close_connection()

    loader = WarehouseLoader(
        db_path=db_path,
        schema=settings.duckdb.schema,
        config=settings.duckdb.config,
    )
    monkeypatch.setattr(state_mod, "loader", loader)
    monkeypatch.setattr(main_mod, "loader", loader)

    # A real orchestrator, without an LLM: the rule-based planner plus the
    # SQL tool are exactly the code path that used to read the wrong schema.
    sql_tool = SQLTool(db_path=db_path, schema=settings.duckdb.schema)
    orchestrator = Orchestrator(
        llm_client=None,
        sql_tool=sql_tool,
        pandas_tool=PandasTool(),
        viz_tool=VizTool(),
        planner=Planner(llm_client=None, schema_context=""),
    )
    monkeypatch.setattr(state_mod, "orchestrator", orchestrator)
    monkeypatch.setattr(main_mod, "orchestrator", orchestrator)

    # The feature gates that would otherwise short-circuit /ask.
    monkeypatch.setattr(settings.features, "enable_llm_agent", True)
    monkeypatch.setattr(settings.features, "fallback_to_sql", True)
    monkeypatch.setattr(main_mod, "_set_request_ip_rotation", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_meter_ai_request", lambda *a, **k: None)

    client = TestClient(main_mod.app)

    yield {"client": client, "loader": loader, "orchestrator": orchestrator}

    db_mod.close_connection()


def _headers(tenant_id: str, username: str) -> dict:
    """Bearer headers for a user that belongs to *tenant_id*."""
    from api.auth import user_manager
    from tests.conftest import auth_headers

    headers = auth_headers(username, "Str0ng!Passw0rd", role="user")
    user_manager.update_user(username, tenant_id=tenant_id)
    return headers


def _blob(payload) -> str:
    """Flatten a response body into a searchable string."""
    return json.dumps(payload, default=str)


# ─────────────────────────────────────────────────────────────
# 1. GET /tables — the inventory is tenant-scoped
# ─────────────────────────────────────────────────────────────

def test_tables_endpoint_is_tenant_scoped(iso_app):
    """Each tenant sees its own copy of the table, with real columns/counts."""
    client = iso_app["client"]

    alice = client.get("/tables", headers=_headers(ALICE_TENANT, "iso_alice"))
    assert alice.status_code == 200, alice.text
    alice_info = alice.json()["tables"][TABLE]
    assert not alice_info.get("error"), alice_info
    assert alice_info["row_count"] == 2, alice_info
    assert {c["column_name"] for c in alice_info["columns"]} == {
        "region", "product", "amount",
    }
    assert ALICE_MARKER in _blob(alice_info)
    assert BOB_MARKER not in _blob(alice_info)

    bob = client.get("/tables", headers=_headers(BOB_TENANT, "iso_bob"))
    assert bob.status_code == 200, bob.text
    bob_info = bob.json()["tables"][TABLE]
    assert not bob_info.get("error"), bob_info
    assert bob_info["row_count"] == 2, bob_info
    assert BOB_MARKER in _blob(bob_info)
    assert ALICE_MARKER not in _blob(bob_info)


# ─────────────────────────────────────────────────────────────
# 2. POST /sql — raw SQL resolves in the caller's schema
# ─────────────────────────────────────────────────────────────

def test_sql_endpoint_reads_only_the_callers_rows(iso_app):
    """An unqualified table name resolves to the caller's own copy."""
    client = iso_app["client"]
    query = f"SELECT region, amount FROM {TABLE}"

    alice = client.post("/sql", json={"query": query},
                        headers=_headers(ALICE_TENANT, "iso_alice"))
    assert alice.status_code == 200, alice.text
    body = alice.json()
    assert body["success"] is True, body
    assert body["row_count"] == 2, body
    assert ALICE_MARKER in _blob(body)
    assert BOB_MARKER not in _blob(body)

    bob = client.post("/sql", json={"query": query},
                      headers=_headers(BOB_TENANT, "iso_bob"))
    assert bob.status_code == 200, bob.text
    body = bob.json()
    assert body["success"] is True, body
    assert BOB_MARKER in _blob(body)
    assert ALICE_MARKER not in _blob(body)


def test_sql_endpoint_refuses_the_platform_schema_for_a_tenant(iso_app):
    """The safety net: a tenant may not qualify the platform schema.

    ``analytics`` is the *platform* schema (and the ``default`` tenant's own
    schema).  A hosted tenant must not be able to point a query at it
    explicitly — otherwise a stale hard-coded ``analytics.<table>`` would
    silently read the operator's data instead of failing closed
    (LAUNCH-TECHNICAL-REVIEW P0-3 fix #2).
    """
    client = iso_app["client"]
    r = client.post("/sql", json={"query": f"SELECT * FROM analytics.{TABLE}"},
                    headers=_headers(ALICE_TENANT, "iso_alice"))
    body = r.json()
    assert body["success"] is False, body
    assert "Access denied" in str(body.get("error")), body
    assert ALICE_MARKER not in _blob(body)
    assert BOB_MARKER not in _blob(body)


def test_sql_endpoint_refuses_another_tenants_schema(iso_app):
    """ADR-009 D1 layer 2: an explicit cross-tenant schema is refused."""
    client = iso_app["client"]
    bob_schema = tenant_schema(BOB_TENANT)
    r = client.post("/sql", json={"query": f"SELECT * FROM {bob_schema}.{TABLE}"},
                    headers=_headers(ALICE_TENANT, "iso_alice"))
    body = r.json()
    assert body["success"] is False, body
    assert "Access denied" in str(body.get("error")), body
    assert BOB_MARKER not in _blob(body)


# ─────────────────────────────────────────────────────────────
# 3. POST /ask-sql — the SQL fallback path
# ─────────────────────────────────────────────────────────────

def test_ask_sql_reads_only_the_callers_rows(iso_app):
    """``/ask-sql`` executes the planner's query in the caller's schema."""
    client = iso_app["client"]
    question = f"What is the total amount by region in {TABLE}?"

    alice = client.post("/ask-sql", json={"question": question},
                        headers=_headers(ALICE_TENANT, "iso_alice"))
    assert alice.status_code == 200, alice.text
    body = alice.json()
    assert body["success"] is True, body
    assert ALICE_MARKER in _blob(body)
    assert BOB_MARKER not in _blob(body)

    bob = client.post("/ask-sql", json={"question": question},
                      headers=_headers(BOB_TENANT, "iso_bob"))
    assert bob.status_code == 200, bob.text
    body = bob.json()
    assert body["success"] is True, body
    assert BOB_MARKER in _blob(body)
    assert ALICE_MARKER not in _blob(body)


# ─────────────────────────────────────────────────────────────
# 4. POST /ask — the headline agent pipeline
# ─────────────────────────────────────────────────────────────

def test_ask_reads_only_the_callers_rows(iso_app):
    """``/ask`` answers from the caller's own table.

    This is the regression ADR-009's verification section claimed to cover
    but never did: the planner emitted ``analytics.<table>``, the agent
    answered "the table does not exist", and a tenant that *did* have a
    same-named table in the platform schema would have read the operator's
    rows instead.
    """
    client = iso_app["client"]
    question = f"What is the total amount by region in {TABLE}?"

    alice = client.post("/ask", json={"question": question},
                        headers=_headers(ALICE_TENANT, "iso_alice"))
    assert alice.status_code == 200, alice.text
    body = alice.json()
    assert body["success"] is True, body
    answer = str(body.get("answer"))
    assert "does not exist" not in answer.lower(), answer
    assert ALICE_MARKER in _blob(body), answer
    assert BOB_MARKER not in _blob(body), answer

    bob = client.post("/ask", json={"question": question},
                      headers=_headers(BOB_TENANT, "iso_bob"))
    assert bob.status_code == 200, bob.text
    body = bob.json()
    assert body["success"] is True, body
    answer = str(body.get("answer"))
    assert "does not exist" not in answer.lower(), answer
    assert BOB_MARKER in _blob(body), answer
    assert ALICE_MARKER not in _blob(body), answer


def test_ask_query_does_not_qualify_the_platform_schema(iso_app):
    """The planner's SQL is unqualified, so it cannot name a schema.

    A hard-coded ``analytics.`` prefix is what made the agent read the
    operator's table (and what made it fail outright for a tenant that had
    no such table in the platform schema).
    """
    from agent.planner import ToolType

    planner = iso_app["orchestrator"].planner
    planner.set_schema_context(
        f"TABLE {tenant_schema(ALICE_TENANT)}.{TABLE} (2 rows):\n"
        "  - region: VARCHAR\n"
        "  - product: VARCHAR\n"
        "  - amount: DOUBLE\n"
    )
    plan = planner.create_plan(f"total amount by region in {TABLE}", [TABLE])
    queries = [
        s.params.get("query", "")
        for s in plan.steps
        if s.tool == ToolType.SQL and s.params
    ]
    assert queries
    for q in queries:
        assert "analytics" not in q, q
        assert TABLE in q, q


# ─────────────────────────────────────────────────────────────
# 5. POST /llm/chat/stream — the streaming chat path
# ─────────────────────────────────────────────────────────────

def test_chat_stream_reads_only_the_callers_rows(iso_app):
    """The SSE chat endpoint streams the caller's own data."""
    client = iso_app["client"]
    question = f"What is the total amount by region in {TABLE}?"

    with client.stream(
        "POST", "/llm/chat/stream", json={"question": question},
        headers=_headers(ALICE_TENANT, "iso_alice"),
    ) as resp:
        assert resp.status_code == 200, resp.read()
        alice_stream = resp.read().decode("utf-8", "replace")

    assert "does not exist" not in alice_stream.lower(), alice_stream
    assert ALICE_MARKER in alice_stream, alice_stream
    assert BOB_MARKER not in alice_stream, alice_stream

    with client.stream(
        "POST", "/llm/chat/stream", json={"question": question},
        headers=_headers(BOB_TENANT, "iso_bob"),
    ) as resp:
        assert resp.status_code == 200, resp.read()
        bob_stream = resp.read().decode("utf-8", "replace")

    assert "does not exist" not in bob_stream.lower(), bob_stream
    assert BOB_MARKER in bob_stream, bob_stream
    assert ALICE_MARKER not in bob_stream, bob_stream


# ─────────────────────────────────────────────────────────────
# 6. The shared-connection leak (Dashboard Studio)
# ─────────────────────────────────────────────────────────────

def _dashboard_payload(table: str) -> dict:
    """A one-visual dashboard bound to *table* (Dashboard Studio shape)."""
    return {
        "id": "iso-dash",
        "name": "iso",
        "report": {
            "pages": [
                {
                    "name": "page1",
                    "visuals": [
                        {
                            "type": "bar",
                            "table": table,
                            "measure": "SUM(amount)",
                            "dimension": "region",
                        },
                    ],
                },
            ],
        },
    }


def test_dashboard_studio_visual_does_not_leak_into_the_next_tenant(iso_app):
    """A Dashboard Studio visual must not leave its schema on the shared
    connection.

    ``api/dashstudio.py`` used to run ``SET search_path = '<tenant schema>'``
    on the **process-wide** connection and never restore it.  Because the
    agent's SQL tool reuses that same connection, the next request — from
    any tenant — inherited the previous tenant's schema and resolved the
    previous tenant's table (docs/LAUNCH-READINESS.md §6 defect #6,
    docs/LAUNCH-TECHNICAL-REVIEW.md P0-3).
    """
    client = iso_app["client"]

    # Tenant A loads a Dashboard Studio visual built on ITS table.
    visual = client.post(
        "/dashstudio/visual-data",
        json={"dashboard": _dashboard_payload(TABLE), "page": 0, "visual": 0},
        headers=_headers(ALICE_TENANT, "iso_alice"),
    )
    assert visual.status_code == 200, visual.text
    visual_body = visual.json()
    assert visual_body["success"] is True, visual_body
    assert ALICE_MARKER in _blob(visual_body)

    # Tenant B now asks the agent about the SAME table name.  It must get
    # B's rows — not A's, and not an error caused by A's schema.
    bob = client.post(
        "/ask", json={"question": f"What is the total amount by region in {TABLE}?"},
        headers=_headers(BOB_TENANT, "iso_bob"),
    )
    assert bob.status_code == 200, bob.text
    body = bob.json()
    assert body["success"] is True, body
    assert ALICE_MARKER not in _blob(body), body
    assert BOB_MARKER in _blob(body), body


def test_shared_connection_search_path_is_restored(iso_app):
    """The process-wide connection is left on the platform schema again."""
    from api.db import get_connection

    client = iso_app["client"]
    visual = client.post(
        "/dashstudio/visual-data",
        json={"dashboard": _dashboard_payload(TABLE), "page": 0, "visual": 0},
        headers=_headers(ALICE_TENANT, "iso_alice"),
    )
    assert visual.json().get("success") is True, visual.text

    path = get_connection().execute(
        "SELECT current_setting('search_path')"
    ).fetchone()[0]
    assert tenant_schema(ALICE_TENANT) not in str(path), path
