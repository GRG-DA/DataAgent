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
