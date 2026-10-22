"""Audit: does every table identifier that reaches SQL get validated?

The AGENTS.md invariant is "identifiers from a path parameter never reach SQL
unvalidated" — use ``api.security_paths.validate_table_name``.

This audit is AST-based: for every route handler it finds the SQL strings
that interpolate a table identifier, works out where that identifier comes
from (path parameter, request body, or server-side), and reports the ones
that are never validated.

A server-side ("local") identifier is only accepted when the HELPER it came
from validates the identifier it returns (``api/main.py::_detect_best_table``
does). A helper that does not validate is reported like any other source.

Usage:  python scripts/audit_sql_identifiers.py [--apply]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path("api/main.py")

VALIDATORS = {"validate_table_name", "validate_column_name"}

#: The validator that makes a TABLE identifier safe. ``validate_column_name``
#: proves nothing about a table name, so it never qualifies a helper.
TABLE_VALIDATOR = "validate_table_name"

SQL_FUNCS = {"execute", "executemany", "sql"}
IDENT_NAMES = {"table_name", "table"}


def _is_validator_call(node: ast.Call) -> bool:
    """True when the call is a direct ``validate_*`` call."""
    return (
        isinstance(node.func, ast.Name) and node.func.id in VALIDATORS
    )


def _called_name(call: ast.Call) -> str | None:
    """The name of the function a call invokes (``None`` for a method call)."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _returned_names(value: ast.expr) -> set:
    """The names a return statement hands back."""
    if isinstance(value, ast.Name):
        return {value.id}
    if isinstance(value, ast.Tuple):
        return {e.id for e in value.elts if isinstance(e, ast.Name)}
    return set()


class HandlerAudit(ast.NodeVisitor):
    def __init__(self, helper_validated=None) -> None:
        self.issues = []
        self._fn = None
        self._validated = set()
        self._sources = {}
        self._helpers = {}
        self._helper_validated = helper_validated or set()

    # -- helpers ---------------------------------------------------------
    def _is_route(self, node) -> bool:
        return any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put", "delete", "patch"}
            for d in node.decorator_list
        )

    def _args(self, node):
        a = node.args
        return [x.arg for x in (list(a.args) + list(a.kwonlyargs))]

    def _validated_helpers(self, tree: ast.Module) -> set:
        """Names of the helpers that validate the table identifier they return.

        ``api/main.py::_detect_best_table()`` validates the name it returns,
        so a caller that takes the table from that helper is safe. The audit
        only tracks validations performed inside a handler body, so it needs
        to be told about the helpers too.

        A helper counts as validating when the identifier it RETURNS is the
        result of ``validate_table_name`` — either returned straight from the
        validator, or assigned from it and handed back afterwards.
        """
        helpers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if self._is_route(node):
                continue
            if self._helper_validates(node):
                helpers.add(node.name)
        return helpers

    def _helper_validates(self, fn) -> bool:
        """True when the helper validates the table identifier it returns."""
        validated = set()
        for sub in ast.walk(fn):
            # `name = validate_table_name(raw)`
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call) \
                    and isinstance(sub.value.func, ast.Name) \
                    and sub.value.func.id == TABLE_VALIDATOR:
                for target in sub.targets:
                    if isinstance(target, ast.Name):
                        validated.add(target.id)
            # `return validate_table_name(raw)`
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call) \
                    and isinstance(sub.value.func, ast.Name) \
                    and sub.value.func.id == TABLE_VALIDATOR:
                return True
        # the returned identifier must be one of the validated ones
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Return) and sub.value is not None:
                if _returned_names(sub.value) & validated:
                    return True
        return False

    def visit_FunctionDef(self, node):
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_fn(node)

    def _visit_fn(self, node):
        if not self._is_route(node):
            self.generic_visit(node)
            return
        prev_fn = self._fn
        prev_val = self._validated
        prev_src = self._sources
        prev_helpers = self._helpers
        self._fn = node
        self._validated = set()
        self._sources = {}
        self._helpers = {}

        # 1. where does each identifier come from?
        for name in self._args(node):
            self._sources[name] = "path"
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                tgt = sub.targets[0].id
                val = sub.value
                if isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute) and val.func.attr == "get":
                    self._sources[tgt] = "body"
                elif isinstance(val, ast.Attribute) and val.attr in IDENT_NAMES:
                    self._sources[tgt] = "body"
                elif isinstance(val, ast.Name):
                    self._sources[tgt] = self._sources.get(val.id, "local")
                elif isinstance(val, ast.Call):
                    if _is_validator_call(val):
                        # `table = validate_table_name(raw)` - the TARGET is
                        # the validated identifier.
                        self._validated.add(tgt)
                        self._sources[tgt] = "local"
                    else:
                        # `table = _derive(raw)` - the value comes from a
                        # server-side helper, which is only trusted when it
                        # validates what it returns.
                        self._sources[tgt] = "local"
                        self._helpers[tgt] = _called_name(val)
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 \
                    and isinstance(sub.targets[0], ast.Tuple):
                # A tuple unpack: `table, _, col, _ = helper()`.
                if isinstance(sub.value, ast.Call):
                    helper = _called_name(sub.value)
                    for elt in sub.targets[0].elts:
                        if isinstance(elt, ast.Name) and elt.id in IDENT_NAMES:
                            self._sources[elt.id] = "local"
                            self._helpers[elt.id] = helper
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id in {"validate_table_name", "validate_column_name"}:
                for a in sub.args:
                    if isinstance(a, ast.Name):
                        self._validated.add(a.id)
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call) \
                    and isinstance(sub.value.func, ast.Name) \
                    and sub.value.func.id in {"validate_table_name", "validate_column_name"}:
                for a in sub.value.args:
                    if isinstance(a, ast.Name):
                        self._validated.add(a.id)

        # 2. which identifiers reach a SQL string?
        for sub in ast.walk(node):
            template = None
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mod):
                if isinstance(sub.left, ast.Constant) and isinstance(sub.left.value, str):
                    template = sub.left.value
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr in SQL_FUNCS and sub.args:
                # an f-string is a JoinedStr, not a Constant
                first = sub.args[0]
                if isinstance(first, ast.JoinedStr):
                    # Rebuild the template WITH the placeholders: a
                    # FormattedValue is not a Constant, so joining only the
                    # constants would silently drop "{table_name}".
                    parts = []
                    for v in first.values:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            parts.append(v.value)
                        elif isinstance(v, ast.FormattedValue):
                            parts.append("{" + ast.unparse(v.value) + "}")
                    template = "".join(parts)
            if template is None:
                continue
            if not any(kw in template.upper() for kw in ("SELECT", "FROM", "DESCRIBE", "DROP", "INSERT", "UPDATE")):
                continue
            for name in IDENT_NAMES:
                if "{" + name + "}" not in template:
                    continue
                source = self._sources.get(name, "unknown")
                if name in self._validated:
                    continue
                if source == "local":
                    # The name comes from a server-side helper. That is only
                    # safe when the helper validates what it returns (e.g.
                    # _detect_best_table()) - a helper that does NOT validate
                    # is reported like any other source.
                    helper = self._helpers.get(name)
                    if helper and helper in self._helper_validated:
                        continue
                self.issues.append((node.name, name, source, sub.lineno))

        self._fn = prev_fn
        self._validated = prev_val
        self._sources = prev_src
        self._helpers = prev_helpers
        self.generic_visit(node)


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    audit = HandlerAudit(helper_validated=HandlerAudit()._validated_helpers(tree))
    audit.visit(tree)

    # de-duplicate
    seen = set()
    rows = []
    for name, ident, source, line in audit.issues:
        key = (name, ident, source)
        if key in seen:
            continue
        seen.add(key)
        rows.append((line, name, ident, source))

    print(f"UNVALIDATED IDENTIFIERS REACHING SQL: {len(rows)}")
    for line, name, ident, source in sorted(rows):
        print(f"  L{line:>6}  {name}(): {ident} ({source})")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
