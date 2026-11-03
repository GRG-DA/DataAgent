"""Tenant-scoped storytelling cache + hard table scoping (TDD).

Two scoping bugs are guarded here:

1. **The storytelling cache was not tenant-scoped.**  The in-memory cache was
   keyed by ``entry["table"]`` — the table *name* only — and the whole cache
   was persisted in ONE file (``storytelling_cache.json``) next to the shared
   warehouse.  Two tenants that each own a table called ``sales`` therefore
   shared a cache slot, and one tenant's file was read by the other.  The
   cache is now namespaced per tenant: the key carries the tenant id and
   every tenant owns its own file.

2. **An explicit table scope was a hint, not a restriction.**  A request
   that named a table (``table`` / ``tables``) only *pinned* that table to
   the front of the planner's schema context — the rest of the tenant's
   tables stayed in scope, so the planner could still answer from a table
   the user never named.  A named scope is now a hard scope: the planner's
   schema context holds **only** the named tables.

Everything runs against a throwaway DuckDB file with a monkeypatched tenant
context, so the real warehouse is never touched.
"""
from __future__ import annotations

import copy
import json
import os

import duckdb
import pytest

from api.tenancy import tenant_schema, use_tenant

#: The two tenants used throughout.  They own tables with the SAME name.
ALICE = "u_scope_alice"
BOB = "u_scope_bob"

#: The table both tenants own.
TABLE = "sales"

#: A table only Alice owns - used to prove a named scope excludes the rest.
OTHER_TABLE = "orders"

#: A cheap content signature (row count + hash of the first rows).
SIG = "12:deadbeef"


def _llm_story() -> dict:
    """A cache entry that counts as an LLM-written story."""
    return {
        "type": "trend",
        "title": f"AI Analysis: {TABLE}",
        "narrative": "Paid orders dominate the book.",
        "confidence": "high",
        "source": "llm",
    }


def _entry(table: str = TABLE) -> dict:
    """A finished storytelling result, as the background job would store it."""
    return {
        "table": table,
        "signature": SIG,
        "stories": [_llm_story()],
        "question": "how are sales?",
        "created_at": "2026-10-22T11:00:16Z",
    }


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

def _build_warehouse(path: str) -> None:
    """A warehouse where both tenants own a table called ``sales``.

    Alice additionally owns ``orders`` - the table a hard table scope must
    keep out of the planner's schema context.
    """
    alice = tenant_schema(ALICE)
    bob = tenant_schema(BOB)
    con = duckdb.connect(path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        for schema in (alice, bob):
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        con.execute(
            f'CREATE TABLE "{alice}".{TABLE} (region VARCHAR, amount DOUBLE)'
        )
        con.execute(
            f"INSERT INTO \"{alice}\".{TABLE} VALUES ('east',10.0),('west',20.0)"
        )
        con.execute(
            f'CREATE TABLE "{alice}".{OTHER_TABLE} (region VARCHAR, amount DOUBLE)'
        )
        con.execute(
            f"INSERT INTO \"{alice}\".{OTHER_TABLE} VALUES ('east',5.0)"
        )
        con.execute(
            f'CREATE TABLE "{bob}".{TABLE} (region VARCHAR, amount DOUBLE)'
        )
        con.execute(
            f"INSERT INTO \"{bob}\".{TABLE} VALUES ('east',999.0)"
        )
    finally:
        con.close()


@pytest.fixture()
def scope_env(tmp_path, monkeypatch):
    """A throwaway warehouse plus an isolated storytelling cache directory."""
    import api.main as main_mod
    from config import settings

    db_path = str(tmp_path / "scope.duckdb")
    _build_warehouse(db_path)

    # The cache lives next to the warehouse file, so pointing the warehouse at
    # the temp directory keeps the cache out of data/curated as well.
    monkeypatch.setattr(settings.duckdb, "database_path", db_path)

    saved = copy.deepcopy(dict(main_mod._STORYTELLING_CACHE))
    main_mod._STORYTELLING_CACHE.clear()
    try:
        yield {"main": main_mod, "db_path": db_path, "dir": tmp_path}
    finally:
        main_mod._STORYTELLING_CACHE.clear()
        main_mod._STORYTELLING_CACHE.update(saved)


@pytest.fixture()
def iso_app(tmp_path, monkeypatch):
    """The app wired onto a two-tenant warehouse (real planner, no LLM)."""
    from fastapi.testclient import TestClient

    import api.db as db_mod
    import api.main as main_mod
    import api.state as state_mod
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from agent.tools_pandas import PandasTool
    from agent.tools_sql import SQLTool
    from agent.tools_viz import VizTool
    from config import settings
    from etl.load import WarehouseLoader

    # The suite's shared identity store gives the *oldest* account the
    # platform ("default") tenant; create it first so the accounts below do
    # not take that slot.
    from tests.conftest import ensure_user

    ensure_user("test_auth_user", "Str0ng!Passw0rd", role="admin")

    db_path = str(tmp_path / "iso.duckdb")
    _build_warehouse(db_path)

    monkeypatch.setattr(settings.duckdb, "database_path", db_path)
    db_mod.close_connection()

    loader = WarehouseLoader(
        db_path=db_path,
        schema=settings.duckdb.schema,
        config=settings.duckdb.config,
    )
    monkeypatch.setattr(state_mod, "loader", loader)
    monkeypatch.setattr(main_mod, "loader", loader)

    orchestrator = Orchestrator(
        llm_client=None,
        sql_tool=SQLTool(db_path=db_path, schema=settings.duckdb.schema),
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
    try:
        yield {"client": client, "orchestrator": orchestrator}
    finally:
        db_mod.close_connection()


def _headers(tenant_id: str, username: str) -> dict:
    """Bearer headers for a user that belongs to *tenant_id*."""
    from api.auth import user_manager
    from tests.conftest import auth_headers

    headers = auth_headers(username, "Str0ng!Passw0rd", role="user")
    user_manager.update_user(username, tenant_id=tenant_id)
    return headers


# ─────────────────────────────────────────────────────────────
# 1. The storytelling cache is tenant-scoped
# ─────────────────────────────────────────────────────────────

def test_cache_key_includes_the_tenant_id(scope_env):
    """The same table name must produce a different key per tenant."""
    main_mod = scope_env["main"]

    key_alice = main_mod._storytelling_cache_key(TABLE, ALICE)
    key_bob = main_mod._storytelling_cache_key(TABLE, BOB)

    assert key_alice != key_bob, (
        "two tenants owning a table with the same name share a cache slot"
    )
    assert ALICE in key_alice, key_alice
    assert BOB in key_bob, key_bob
    assert TABLE in key_alice, key_alice


def test_cache_file_path_is_per_tenant(scope_env):
    """Every tenant persists its cache in its own file."""
    main_mod = scope_env["main"]

    with use_tenant(ALICE):
        path_alice = main_mod._storytelling_cache_path()
    with use_tenant(BOB):
        path_bob = main_mod._storytelling_cache_path()

    assert path_alice != path_bob, (
        "tenants share one storytelling cache file"
    )
    assert os.path.basename(path_alice).startswith("storytelling_cache_"), path_alice
    assert path_alice.endswith(".json"), path_alice
    assert os.path.basename(path_bob).startswith("storytelling_cache_"), path_bob


def test_cache_written_by_one_tenant_is_not_readable_by_another(scope_env):
    """A story written for Alice must never be served to Bob."""
    main_mod = scope_env["main"]

    with use_tenant(ALICE):
        main_mod._storytelling_cache_put(_entry())
        assert main_mod._storytelling_cache_hit(TABLE, SIG, "how are sales?") is not None

    with use_tenant(BOB):
        assert main_mod._storytelling_cache_hit(TABLE, SIG, "how are sales?") is None


def test_cache_still_works_for_one_tenant(scope_env):
    """The cache keeps doing its job: write -> hit, for the same tenant."""
    main_mod = scope_env["main"]

    with use_tenant(ALICE):
        main_mod._storytelling_cache_put(_entry())
        hit = main_mod._storytelling_cache_hit(TABLE, SIG, "how are sales?")

        assert hit is not None, "the tenant's own cache entry was not found"
        assert hit["stories"] == _entry()["stories"]
        assert hit["table"] == TABLE


def test_each_tenant_persists_its_own_cache_file(scope_env):
    """Alice's file holds Alice's story; Bob's file stays untouched."""
    main_mod = scope_env["main"]

    with use_tenant(ALICE):
        main_mod._storytelling_cache_put(_entry())
        alice_path = main_mod._storytelling_cache_path()
    with use_tenant(BOB):
        bob_path = main_mod._storytelling_cache_path()

    assert alice_path != bob_path
    assert os.path.exists(alice_path)
    assert not os.path.exists(bob_path), "Bob's cache file was written by Alice"

    persisted = json.loads(open(alice_path, encoding="utf-8").read())
    assert TABLE in persisted
    assert persisted[TABLE]["stories"][0]["source"] == "llm"


def test_legacy_shared_cache_file_is_never_imported(scope_env):
    """The old shared ``storytelling_cache.json`` must not seed a tenant.

    Reading it into a tenant's namespace would re-introduce the very leak
    this fixes: whoever wrote that file last would be served to everyone.
    """
    main_mod = scope_env["main"]
    legacy = os.path.join(str(scope_env["dir"]), "storytelling_cache.json")
    with open(legacy, "w", encoding="utf-8") as fh:
        json.dump({TABLE: _entry()}, fh)

    with use_tenant(ALICE):
        assert main_mod._storytelling_cache_hit(TABLE, SIG, "how are sales?") is None


@pytest.mark.asyncio
async def test_background_job_writes_into_the_creating_tenants_namespace(scope_env):
    """The job keeps the tenant it was created for.

    The job runs after the request is gone, so it must not fall back to
    whatever tenant happens to be current when it writes the cache.
    """
    main_mod = scope_env["main"]

    job_id = "job-scope"
    main_mod._STORYTELLING_JOBS[job_id] = {
        "job_id": job_id,
        "table": TABLE,
        "status": "running",
        "tenant_id": ALICE,
    }
    # The ambient tenant is BOB when the job runs - the job must ignore it.
    with use_tenant(BOB):
        await main_mod._run_storytelling_job(job_id, TABLE, "how are sales?", 5)

    assert TABLE in main_mod._STORYTELLING_CACHE.get(ALICE, {}), (
        "the job did not write into the namespace of the tenant that created it"
    )
    assert TABLE not in main_mod._STORYTELLING_CACHE.get(BOB, {}), (
        "the job wrote into another tenant's cache namespace"
    )


# ─────────────────────────────────────────────────────────────
# 2. A named table scope is a restriction, not a hint
# ─────────────────────────────────────────────────────────────

def test_scope_tables_returns_only_the_named_tables(scope_env):
    """The scoping helper returns exactly the requested tables."""
    main_mod = scope_env["main"]
    tables = [TABLE, OTHER_TABLE, "customers"]

    scoped = main_mod._scope_tables_for_question(
        tables, "how many orders?", preferred=[TABLE]
    )

    assert scoped == [TABLE], scoped


def test_named_table_request_limits_the_planner_schema(iso_app):
    """A request that names one table gives the planner ONLY that table."""
    client = iso_app["client"]
    headers = _headers(ALICE, "scope_alice")

    r = client.post(
        "/ask",
        json={"question": "what is the total amount?", "table": TABLE},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    context = iso_app["orchestrator"].planner.schema_context
    assert f"TABLE {TABLE}" in context, context
    assert OTHER_TABLE not in context, (
        "the planner's schema context still holds tables the user did not "
        "name:\n" + context
    )


def test_request_without_a_table_still_sees_every_tenant_table(iso_app):
    """Without an explicit scope, the tenant's tables stay in scope."""
    client = iso_app["client"]
    headers = _headers(ALICE, "scope_alice")

    r = client.post(
        "/ask",
        json={"question": "what is the total amount?"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    context = iso_app["orchestrator"].planner.schema_context
    assert f"TABLE {TABLE}" in context, context
    assert f"TABLE {OTHER_TABLE}" in context, context
