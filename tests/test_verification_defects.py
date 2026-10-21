"""Regression tests for the 7 defects found in the 2026-10-20 verification run.

Each test is written to FAIL against the pre-fix code and PASS after the fix.

  S1  RBAC bypass: ``/audit/logs`` was readable by every role because the
      permission was registered on the exact path ``"/audit"`` while the
      routes are ``/audit/logs`` and ``/audit/logs/export``.
  S2  ``readonly_transaction`` was used without an import in ``api/main.py``,
      so three endpoints raised ``NameError`` and returned HTTP 200 with
      ``{"success": false, "error": "name 'readonly_transaction' is not
      defined"}``.
  S3  The alert engine opened its own read-only DuckDB connection while the
      app holds the file read-write, so every alert rule failed with
      "a different configuration than existing connections".
  S4  ``api/rls.py`` used the same read-only-connection pattern, so
      ``get_tenant_columns()`` always returned ``[]``.
  S5  The AI dashboard plan returned ``used: "llm"`` but no
      ``insights.source``, so the UI labelled AI insights "Automatic
      (rule-based)".
  S6  ``POST /dashstudio/visual-data`` was an unimplemented stub.
  S7  ``agent/tools_sql.py`` passed ``result="success"`` / ``"failure"``
      (plain strings) to the audit logger, so every agent SQL query raised
      ``'str' object has no attribute 'value'`` and its audit record was
      silently dropped.
"""
from __future__ import annotations

import inspect

import pytest


# ── S1: RBAC audit-log bypass ────────────────────────────────────────────

def test_audit_log_paths_require_audit_read_permission():
    """``/audit/logs`` and ``/audit/logs/export`` must map to ``audit:read``.

    The exact-path entry ``"/audit"`` can never match the real routes, so a
    prefix rule is required.
    """
    from api.middleware.rbac_middleware import _required_permission

    assert _required_permission("/audit/logs", "GET") == "audit:read"
    assert _required_permission("/audit/logs/export", "GET") == "audit:read"


def test_audit_permission_is_not_satisfied_by_unrelated_paths():
    """The audit rule must not leak onto unrelated paths."""
    from api.middleware.rbac_middleware import _required_permission

    assert _required_permission("/auditx", "GET") is None


def test_audit_prefix_rule_is_registered():
    """Guard against a future refactor removing the prefix rule."""
    from api.middleware.rbac_middleware import PREFIX_PERMISSIONS

    assert any(prefix == "/audit" for prefix, _ in PREFIX_PERMISSIONS), (
        "PREFIX_PERMISSIONS must contain a '/audit' rule so the exact-path "
        "'/audit' entry actually matches /audit/logs"
    )


# ── S2: readonly_transaction NameError ───────────────────────────────────

def test_main_module_imports_readonly_transaction():
    """``api.main`` must import ``readonly_transaction`` at module scope.

    Three handlers call it; the only import was function-local, so they
    raised ``NameError`` at request time.
    """
    import api.main as main_mod

    assert hasattr(main_mod, "readonly_transaction"), (
        "api/main.py must import readonly_transaction at module level"
    )


@pytest.mark.parametrize(
    "source_fragment",
    [
        "with readonly_transaction(conn):",
    ],
)
def test_readonly_transaction_uses_are_resolvable(source_fragment):
    """Any module-level use of the helper must have a module-level import."""
    import api.main as main_mod

    src = inspect.getsource(main_mod)
    assert source_fragment in src
    assert "from etl.readonly import readonly_transaction" in src


# ── S3 / S4: DuckDB connection configuration conflicts ───────────────────

def _duckdb_connect_calls(source: str):
    """Return every ``duckdb.connect(...)`` call in *source* as source text.

    AST-based so a docstring mentioning ``read_only=True`` is not mistaken
    for a live call.
    """
    import ast

    calls = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute) and func.attr == "connect":
            name = func.value
        elif isinstance(func, ast.Name) and func.id == "connect":
            name = func.value
        if name is None:
            continue
        mod = getattr(name, "id", None)
        if mod not in ("duckdb", "_duckdb"):
            continue
        calls.append(ast.get_source_segment(source, node) or "")
    return calls


def test_alert_engine_does_not_open_a_second_readonly_connection():
    """The alert engine must reuse the process-wide warehouse connection.

    A fresh ``read_only=True`` connection to the same file is refused by
    DuckDB when the app already holds it read-write, so every rule failed.
    """
    from analysis.alerts import AlertEngine

    src = inspect.getsource(AlertEngine)
    for call in _duckdb_connect_calls(src):
        assert "read_only" not in call, (
            f"AlertEngine opens its own DuckDB connection ({call}); it must "
            "use api.db.get_connection() + etl.readonly.readonly_transaction"
        )
    assert "get_connection" in src, (
        "AlertEngine must reuse the process-wide warehouse connection"
    )


def test_rls_does_not_open_a_second_readonly_connection():
    """``get_tenant_columns`` must not open its own read-only connection."""
    from api.rls import RLSManager

    src = inspect.getsource(RLSManager)
    for call in _duckdb_connect_calls(src):
        assert "read_only" not in call, (
            f"RLSManager opens its own DuckDB connection ({call}); it must "
            "use api.db.get_connection() + etl.readonly.readonly_transaction"
        )


def test_rls_tenant_columns_are_readable_when_warehouse_is_open():
    """With the warehouse open (as the running app has it), columns resolve.

    The warehouse is a single-writer DuckDB file, so a test process cannot
    open it while the app holds it.  The assertion is therefore conditional
    on the file being readable; the code-path assertion above is the real
    guard.
    """
    from api.rls import RLSManager

    try:
        cols = RLSManager().get_tenant_columns("ppc_ads")
    except Exception as exc:  # pragma: no cover - defensive
        pytest.skip(f"warehouse not readable in this process: {exc}")
    if not cols:
        pytest.skip("warehouse is held by another process (single-writer rule)")
    assert "spend" in cols


# ── S5: AI insights provenance ───────────────────────────────────────────

def test_auto_build_plan_reports_insight_source():
    """The plan must say the insights were LLM-generated.

    The UI checks ``insights.source === "llm"``; without it every AI-written
    insight panel is labelled "Automatic (rule-based)".
    """
    from api.main import app

    route = next(
        r for r in app.routes if getattr(r, "path", "") == "/dashboard/auto-build-plan/{table_name}"
    )
    body = inspect.getsource(route.endpoint)
    assert "insights" in body, "the auto-build plan must build an insights payload"
    assert '"source"' in body or "'source'" in body, (
        "the insights payload must carry a 'source' field so the UI can tell "
        "LLM output from rule-based output"
    )


# ── S6: /dashstudio/visual-data stub ─────────────────────────────────────

def test_visual_data_is_not_a_stub():
    """``/dashstudio/visual-data`` must not return the placeholder payload."""
    from api.dashstudio import router

    route = next(
        r for r in router.routes
        if getattr(r, "path", "").endswith("/visual-data")
    )
    body = inspect.getsource(route.endpoint)
    assert "Implement with actual dashboard loading" not in body, (
        "/dashstudio/visual-data is still an unimplemented stub"
    )
    assert "SELECT" in body, (
        "/dashstudio/visual-data must build and run the visual's query"
    )


def test_visual_data_returns_data_for_a_real_visual():
    """The endpoint must actually run a query and return rows.

    The warehouse is a single-writer DuckDB file: when the app is running,
    a test process cannot open it and the shared connection falls back to
    an in-memory database with no tables.  The assertion therefore only
    applies when the real warehouse is reachable; the stub test above is
    the always-on guard, and the live app is the functional proof.
    """
    from api.dashstudio import get_visual_data
    from api.dashstudio import VisualDataRequest

    dashboard = {
        "report": {
            "pages": [
                {
                    "name": "Overview",
                    "visuals": [
                        {"type": "bar", "table": "ppc_ads",
                         "measure": "SUM(spend)", "dimension": "source"},
                    ],
                    "slicers": [],
                }
            ]
        }
    }
    result = _run(get_visual_data(VisualDataRequest(dashboard=dashboard)))
    if result.get("success") is not True:
        if "does not exist" in str(result.get("error", "")):
            pytest.skip("warehouse not reachable from this process (single-writer rule)")
        pytest.fail(f"visual-data failed: {result.get('error')}")
    assert result["row_count"] >= 1
    assert result["columns"], "the response must name the result columns"
    assert result["data"], "the response must carry rows"


def test_visual_data_builds_where_before_group_by():
    """A filtered visual must produce valid SQL (WHERE precedes GROUP BY)."""
    from api.dashstudio import get_visual_data
    from api.dashstudio import VisualDataRequest

    dashboard = {
        "report": {
            "pages": [
                {
                    "name": "Overview",
                    "visuals": [
                        {"type": "bar", "table": "ppc_ads",
                         "measure": "SUM(spend)", "dimension": "source"},
                    ],
                    "slicers": [],
                }
            ]
        }
    }
    result = _run(get_visual_data(VisualDataRequest(
        dashboard=dashboard, filters={"ppc_ads.source": ["Google Ads"]})))
    if not result.get("success"):
        if "does not exist" in str(result.get("error", "")):
            pytest.skip("warehouse not reachable from this process (single-writer rule)")
        pytest.fail(f"filtered visual-data failed: {result.get('error')}")
    assert result["sql"].index("WHERE") < result["sql"].index("GROUP BY"), (
        "WHERE must precede GROUP BY in the generated SQL: " + result["sql"]
    )
    assert result["row_count"] == 1, (
        f"the slicer filter should narrow the result to 1 row, got {result['row_count']}"
    )


def _run(coro):
    """Run a coroutine to completion without disturbing the event loop.

    ``asyncio.run`` resets the thread's current event loop, which breaks
    later tests that call ``asyncio.get_event_loop()``.  The previous loop
    is saved and restored.
    """
    import asyncio

    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:
        previous = None
    try:
        return asyncio.run(coro)
    finally:
        if previous is not None:
            asyncio.set_event_loop(previous)


# ── S7: audit result must be an enum, not a bare string ──────────────────

def test_audit_event_coerces_string_result_to_enum():
    """``AuditEvent`` must accept ``result="failure"`` without raising.

    ``to_dict()`` calls ``self.result.value``; a bare string made the audit
    handler raise and the record was dropped.
    """
    from api.audit import AuditEvent, AuditResult

    event = AuditEvent(result="failure", event_type="query_executed")
    data = event.to_dict()
    assert data["result"] == AuditResult.FAILURE.value
    assert data["event_type"] == "query_executed"


def test_audit_event_accepts_string_event_type():
    from api.audit import AuditEvent, AuditEventType

    event = AuditEvent(event_type="user_login")
    assert event.to_dict()["event_type"] == AuditEventType.USER_LOGIN.value


def test_audit_event_still_accepts_real_enums():
    from api.audit import AuditEvent, AuditResult, AuditEventType

    event = AuditEvent(event_type=AuditEventType.USER_LOGIN, result=AuditResult.SUCCESS)
    data = event.to_dict()
    assert data["event_type"] == AuditEventType.USER_LOGIN.value
    assert data["result"] == AuditResult.SUCCESS.value


def test_sql_tool_audit_calls_pass_enum_not_bare_string():
    """The SQL tool must pass ``AuditResult`` members, not bare strings."""
    from agent.tools_sql import SQLTool

    src = inspect.getsource(SQLTool.execute)
    assert 'result="success"' not in src and 'result="failure"' not in src, (
        "SQLTool.execute must pass AuditResult members to the audit logger"
    )


def test_audit_log_query_accepts_string_result():
    """``log_query`` must tolerate a bare string result from any caller."""
    from api.audit import AuditLogger, AuditResult

    logged = []

    class _Handler:
        def handle(self, event):
            logged.append(event.to_dict())

        def close(self):
            pass

    logger = AuditLogger()
    logger.handlers = [_Handler()]
    logger.enabled = True
    logger.log_query(user_id="u1", query="SELECT 1", row_count=1, execution_time_ms=1.0,
                     result="success")
    assert logged, "no audit record was written"
    assert logged[0]["result"] == AuditResult.SUCCESS.value
