"""A platform table must never be named in a user's answer.

Regression guard for the transparency footer. The agent used to append
``*Source: analytics.key_redemptions*`` to its answer: the platform's
coupon/license-key redemption table (it holds ``user_id``, key material and
redemption timestamps) was missing from the platform-table registry, so it
was treated as the user's own data, offered to the planner in the schema
context, queried, and then named back to the user in the footer.

Two halves have to hold for that to stay fixed:

* ``warehouse/system_tables.py`` knows every table the platform owns.
* ``agent/orchestrator.py`` builds the footer from the plan's SQL and
  refuses to name a platform table, with or without a schema qualifier.

The schema context the planner reads must name tables by their BARE name:
the executor resolves an unqualified name through the request's tenant
search path, so a qualified name only invites the model to write SQL that
reaches outside the caller's own workspace.
"""
from __future__ import annotations

import pandas as pd
import pytest

from agent.orchestrator import AgentResponse, Orchestrator, StepResult
from agent.planner import ExecutionPlan, PlanStep, ToolType
from agent.tools_sql import SQLResult
from warehouse.system_tables import (
    SYSTEM_TABLES,
    is_derived_system_table,
    is_system_table,
    user_tables,
)


#: Platform tables that live in the ``analytics`` schema and MUST be
#: recognised as platform-owned.  The value names the module that creates
#: the table, so a future rename is traceable from the registry.
PLATFORM_TABLE_OWNERS = {
    "key_redemptions": "api/billing/redemption.py",
    "premium_settings": "api/premium.py",
    "api_keys_legacy": "api/api_keys.py",
    "catalog_assets_new": "scripts/repair_warehouse.py",
    "_dataagent_health_probe": "api/routes/ops.py",
}


class _StubLLM:
    """Minimal stand-in for the LLM client (no network)."""

    async def chat(self, messages, options=None):  # pragma: no cover - unused
        raise AssertionError("the stub LLM must never be called")


def _orchestrator() -> Orchestrator:
    """An Orchestrator with no tools — only the answer-building paths."""
    return Orchestrator(
        llm_client=None,
        sql_tool=None,
        pandas_tool=None,
        viz_tool=None,
        planner=None,
    )


def _plan(*queries: str) -> ExecutionPlan:
    """An execution plan whose SQL steps reference *queries*."""
    return ExecutionPlan(
        question="how many redemptions?",
        steps=[
            PlanStep(i + 1, ToolType.SQL, "query", {"query": q})
            for i, q in enumerate(queries)
        ],
        estimated_steps=len(queries),
    )


def _sources(*queries: str) -> list:
    """The table names the transparency footer would name for *queries*."""
    return Orchestrator._plan_source_tables(_plan(*queries))


def _answer(*queries: str) -> str:
    """The answer text (footer included) for a plan over *queries*."""
    response = AgentResponse(
        question="how many redemptions?",
        plan=_plan(*queries),
        step_results=[
            StepResult(
                step_id=1,
                tool=ToolType.SQL,
                success=True,
                result=SQLResult(True, data=pd.DataFrame({"total": [1]})),
            )
        ],
        final_answer="",
    )
    return _orchestrator()._rule_answer(response)


# ── the registry ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("table", sorted(PLATFORM_TABLE_OWNERS))
def test_platform_table_is_registered(table: str):
    """Every table the platform creates in ``analytics`` is platform-owned."""
    assert is_system_table(table) is True
    assert table in SYSTEM_TABLES


def test_key_redemptions_is_a_platform_table():
    """The table that leaked: coupon/license-key redemptions."""
    assert is_system_table("key_redemptions") is True
    assert is_system_table("KEY_REDEMPTIONS") is True


def test_user_tables_filters_platform_tables_out():
    """``user_tables`` is what the API hands to the model as "your data"."""
    tables = ["sales", "key_redemptions", "premium_settings", "api_keys_legacy"]
    assert user_tables(tables) == ["sales"]
    assert "key_redemptions" not in user_tables(tables)


def test_user_tables_keeps_genuine_user_tables():
    """A user table must not be swallowed by the platform registry."""
    assert user_tables(["sales", "orders", "customer_reviews"]) == [
        "sales", "orders", "customer_reviews",
    ]


# ── the transparency footer ───────────────────────────────────────────────

def test_footer_never_names_a_platform_table():
    """A plan over a platform table must not name it."""
    assert _sources("SELECT * FROM analytics.key_redemptions") == []
    assert "key_redemptions" not in _sources("SELECT * FROM analytics.key_redemptions")


def test_footer_never_names_a_platform_table_among_user_tables():
    """A mixed plan names the user's table only."""
    sources = _sources(
        "SELECT * FROM analytics.key_redemptions",
        "SELECT COUNT(*) FROM analytics.sales",
    )
    assert sources == ["sales"]
    assert "key_redemptions" not in sources


def test_footer_uses_bare_table_names():
    """A schema qualifier is never echoed back to the user."""
    assert _sources("SELECT * FROM analytics_t_x.sales") == ["sales"]
    assert _sources('SELECT * FROM "analytics_t_x"."sales"') == ["sales"]
    assert _sources("SELECT * FROM analytics.sales") == ["sales"]


def test_footer_keeps_a_genuine_user_table():
    """The normal case still works."""
    assert _sources("SELECT COUNT(*) FROM sales") == ["sales"]
    assert _sources("SELECT * FROM sales JOIN orders ON sales.id = orders.id") == [
        "sales", "orders",
    ]


def test_footer_is_omitted_when_only_platform_tables_are_referenced():
    """No user table left -> no footer at all (the caller must be able to
    tell "no source" from "a source")."""
    assert _sources("SELECT * FROM analytics.key_redemptions") == []
    assert "Source:" not in _answer("SELECT * FROM analytics.key_redemptions")


def test_footer_is_present_for_a_user_table():
    """The footer still names the user's own table."""
    answer = _answer("SELECT COUNT(*) FROM analytics.sales")
    assert "*Source: sales*" in answer


# ── the schema context the planner reads ──────────────────────────────────

def test_schema_context_uses_bare_table_names(tmp_path):
    """The planner's schema context must not carry a schema qualifier."""
    import duckdb

    from agent.orchestrator import create_orchestrator

    dbp = str(tmp_path / "ctx.duckdb")
    con = duckdb.connect(dbp)
    con.execute("CREATE SCHEMA analytics")
    con.execute("CREATE TABLE analytics.sales (amount DOUBLE)")
    con.execute("INSERT INTO analytics.sales VALUES (1.0)")
    con.execute("CREATE TABLE analytics.key_redemptions (user_id VARCHAR)")
    con.close()

    orch = create_orchestrator(db_path=dbp, schema="analytics", llm_client=_StubLLM())
    context = orch.planner.schema_context

    assert "TABLE sales (" in context
    assert "analytics.sales" not in context
    # ... and the platform table is not offered to the planner at all.
    assert "key_redemptions" not in context


# ── derived platform-table names ───────────────────────────────────────────

#: Platform tables whose NAME is built from a variable, so no fixed set can
#: list them.  The value names the site that creates the table.
DERIVED_TABLE_CONVENTIONS = {
    "DateTable_sales_amount": "api/dashstudio.py:499",
    "ai_probe_sales__npk": "api/db.py:115",
}


class TestDerivedPlatformTableNames:
    """A platform table with a derived name is platform-owned.

    Two platform helpers build their name from a variable, so they can never
    appear in a fixed registry:

    * ``DateTable_<table>_<column>`` -- the auto date spine the Dashboard
      Studio builds for a user table's date column (``api/dashstudio.py``).
    * ``<table>__npk`` -- the transient full copy ``api/db.py`` writes while
      it rebuilds a table without its constraint indexes.  A leftover
      ``key_redemptions__npk`` holds the redemption table's key material.
    """

    @pytest.mark.parametrize("name", sorted(DERIVED_TABLE_CONVENTIONS))
    def test_derived_name_is_a_platform_table(self, name: str):
        assert is_system_table(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "datetable_sales_amount",
            "DATETABLE_SALES_AMOUNT",
            "DateTable_Sales_Amount",
            "ai_probe_sales__npk",
            "AI_PROBE_SALES__NPK",
        ],
    )
    def test_derived_name_match_is_case_insensitive(self, name: str):
        assert is_system_table(name) is True

    def test_the_npk_helper_of_a_platform_table_is_platform(self):
        """The rebuild helper of the redemption table is a full copy of it."""
        assert is_system_table("key_redemptions__npk") is True
        assert is_system_table("api_keys__npk") is True

    @pytest.mark.parametrize(
        "name",
        ["sales", "ai_probe_sales", "orders", "npk", "npk_data", "dates", "date_tables"],
    )
    def test_a_user_table_stays_a_user_table(self, name: str):
        """A user table must never be classified as platform."""
        assert is_system_table(name) is False

    def test_user_tables_filters_the_derived_names_out(self):
        """``user_tables`` is what the API hands to the model as "your data"."""
        tables = [
            "sales",
            "DateTable_sales_amount",
            "ai_probe_sales__npk",
            "ai_probe_sales",
        ]
        assert user_tables(tables) == ["sales", "ai_probe_sales"]

    def test_the_fixed_names_still_behave(self):
        """The registered names are unaffected by the derived-name rules."""
        assert is_system_table("key_redemptions") is True
        assert is_system_table("premium_settings") is True
        assert is_system_table("sales") is False
        assert user_tables(["sales", "key_redemptions", "premium_settings"]) == ["sales"]

    def test_the_registry_itself_honours_the_conventions(self):
        """``name in SYSTEM_TABLES`` is the platform's namespace check.

        The SQL policy asks that question, so the registry has to answer it
        for a derived name too.
        """
        assert "DateTable_sales_amount" in SYSTEM_TABLES
        assert "ai_probe_sales__npk" in SYSTEM_TABLES
        assert "sales" not in SYSTEM_TABLES

    def test_the_registry_still_iterates_the_fixed_names_only(self):
        """``sorted(SYSTEM_TABLES)`` is the UI's "system tables" list."""
        names = sorted(SYSTEM_TABLES)
        assert "key_redemptions" in names
        assert "DateTable_sales_amount" not in names
        assert "ai_probe_sales__npk" not in names

    def test_is_derived_system_table_is_the_narrow_form(self):
        """True only for a derived name, never for a registered one."""
        assert is_derived_system_table("DateTable_sales_amount") is True
        assert is_derived_system_table("ai_probe_sales__npk") is True
        assert is_derived_system_table("key_redemptions") is False
        assert is_derived_system_table("sales") is False


class TestDerivedNamesInTheSqlPolicy:
    """The SQL policy must refuse a derived platform table.

    The SQL tool's own table check is the last gate before a query runs, so
    a derived name that slips past it is readable by anyone who can ask a
    question.
    """

    @pytest.fixture()
    def tool(self, tmp_path):
        from agent.tools_sql import SQLTool

        return SQLTool(db_path=tmp_path / "derived.duckdb", schema="analytics")

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM DateTable_sales_amount",
            "SELECT * FROM ai_probe_sales__npk",
            "SELECT * FROM key_redemptions__npk",
            "SELECT * FROM analytics.DateTable_sales_amount",
            "SELECT * FROM analytics.ai_probe_sales__npk",
            "SELECT * FROM analytics.key_redemptions__npk",
        ],
    )
    def test_derived_platform_table_is_refused(self, tool, query: str):
        ok, err = tool.validate_query(query)
        assert not ok, query
        assert err

    def test_a_user_table_is_still_allowed(self, tool):
        ok, err = tool.validate_query("SELECT * FROM ai_probe_sales")
        assert ok, err


class TestDerivedNamesInTheFooter:
    """The transparency footer must not name a derived platform table."""

    def test_footer_never_names_a_derived_platform_table(self):
        assert _sources("SELECT * FROM analytics.DateTable_sales_amount") == []
        assert _sources("SELECT * FROM analytics.ai_probe_sales__npk") == []
        assert "DateTable_sales_amount" not in _answer(
            "SELECT * FROM analytics.DateTable_sales_amount"
        )

    def test_footer_keeps_the_user_table(self):
        assert _sources("SELECT * FROM ai_probe_sales") == ["ai_probe_sales"]


class TestDerivedNamesInTheSchemaContext:
    """The planner's schema context must not offer a derived platform table."""

    def test_derived_table_is_not_offered_to_the_planner(self, tmp_path):
        import duckdb

        from agent.orchestrator import create_orchestrator

        dbp = str(tmp_path / "derived_ctx.duckdb")
        con = duckdb.connect(dbp)
        con.execute("CREATE SCHEMA analytics")
        con.execute("CREATE TABLE analytics.sales (amount DOUBLE)")
        con.execute("CREATE TABLE analytics.DateTable_sales_amount (Date DATE)")
        con.execute("CREATE TABLE analytics.ai_probe_sales__npk (amount DOUBLE)")
        con.close()

        orch = create_orchestrator(db_path=dbp, schema="analytics", llm_client=_StubLLM())
        context = orch.planner.schema_context

        assert "TABLE sales (" in context
        assert "DateTable_sales_amount" not in context
        assert "ai_probe_sales__npk" not in context
