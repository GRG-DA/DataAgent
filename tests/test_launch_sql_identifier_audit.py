"""The SQL-identifier audit must stay green.

``scripts/audit_sql_identifiers.py`` walks every route handler in
``api/main.py`` and reports the table identifiers that reach a SQL string
without passing through ``validate_table_name``.  It exists because the
AGENTS.md invariant — "identifiers from a path parameter never reach SQL
unvalidated" — is easy to state and easy to break: 44 handlers were
interpolating a table name straight into a query.

Run as a test so it cannot rot: a new route that forgets to register its
permission or to validate its identifier fails the suite.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "audit_sql_identifiers.py"


def test_the_audit_script_exists():
    assert SCRIPT.is_file(), "scripts/audit_sql_identifiers.py is missing"


def test_no_identifier_reaches_sql_unvalidated():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "an identifier reaches SQL without validate_table_name:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "UNVALIDATED IDENTIFIERS REACHING SQL: 0" in result.stdout, result.stdout


# ── the helper gate must actually gate ─────────────────────────────────
#
# A "local" identifier (one that comes from a server-side helper) used
# to be skipped UNCONDITIONALLY: both branches of the check were
# ``continue``, so the list of validating helpers had no effect at all.
# A route that took its table from a helper that does NOT validate it
# therefore produced ZERO findings.
#
# The rule is: a helper-derived identifier is skipped ONLY when the
# helper itself validates what it returns; otherwise it is REPORTED.


UNVALIDATED_HELPER = '''
from api.security_paths import validate_table_name


def _derive(table_name):
    """A helper that does NOT validate what it returns."""
    return table_name + "_copy"


@app.get("/synthetic")
async def synthetic(table_name: str):
    table = _derive(table_name)
    conn.execute(f"SELECT * FROM {table}")
    return {"table": table}
'''


VALIDATED_HELPER = '''
from api.security_paths import validate_table_name


def _safe_table(table_name):
    """A helper that validates the identifier it returns."""
    return validate_table_name(table_name)


@app.get("/synthetic-ok")
async def synthetic_ok(table_name: str):
    table = _safe_table(table_name)
    conn.execute(f"SELECT * FROM {table}")
    return {"table": table}
'''


def _load_audit_module():
    """Import ``scripts/audit_sql_identifiers.py`` as a module."""
    spec = importlib.util.spec_from_file_location("audit_sql_identifiers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit(source: str):
    """Run the audit over a synthetic module and return its findings."""
    module = _load_audit_module()
    tree = ast.parse(source)
    helpers = module.HandlerAudit()._validated_helpers(tree)
    audit = module.HandlerAudit(helper_validated=helpers)
    audit.visit(tree)
    return audit.issues


def test_a_helper_that_does_not_validate_is_reported():
    """The dead branch swallowed this finding."""
    issues = _audit(UNVALIDATED_HELPER)

    assert issues, (
        "a table identifier derived from a helper that does not validate it "
        "reaches SQL unvalidated and was not reported"
    )
    handler, ident, source, _line = issues[0]
    assert handler == "synthetic"
    assert ident == "table"
    assert source == "local"


def test_a_helper_that_validates_is_not_reported():
    """The legitimate case stays clean."""
    assert _audit(VALIDATED_HELPER) == []


def test_the_helper_list_names_the_validating_helpers():
    """The helper list must hold the HELPER names, not its local variables."""
    module = _load_audit_module()
    tree = ast.parse(VALIDATED_HELPER + "\n\n" + UNVALIDATED_HELPER)

    helpers = module.HandlerAudit()._validated_helpers(tree)

    assert "_safe_table" in helpers
    assert "_derive" not in helpers


def test_an_in_handler_validation_is_still_reported_as_clean():
    """``table = validate_table_name(...)`` inside the handler is enough."""
    source = '''
from api.security_paths import validate_table_name


@app.get("/synthetic-inline")
async def synthetic_inline(table_name: str):
    table = validate_table_name(table_name)
    conn.execute(f"SELECT * FROM {table}")
    return {"table": table}
'''
    assert _audit(source) == []

