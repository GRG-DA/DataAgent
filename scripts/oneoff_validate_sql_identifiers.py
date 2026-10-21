"""One-off remediation: validate every table identifier that reaches SQL.

The AGENTS.md invariant is "identifiers from a path parameter never reach SQL
unvalidated" — use ``api.security_paths.validate_table_name``.  27 route
handlers in ``api/main.py`` interpolated a table name straight into a SQL
string.  DuckDB executes multi-statement strings, so ``orders; DROP TABLE ...``
is a live injection primitive.

The fix follows the house style (``api/dashstudio.py:229-230``): the
validation goes right after the handler's docstring, before its ``try:``, so
the ``HTTPException(400)`` it raises is NOT swallowed by the handler's broad
``except Exception`` and the caller gets a proper 400.

Idempotent: re-running it changes nothing.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path("api/main.py")
VALIDATORS = {"validate_table_name", "validate_column_name"}


def route_handlers(tree: ast.Module) -> dict:
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put", "delete", "patch"}
            for d in node.decorator_list
        ):
            out[node.name] = node
    return out


def body_source(tree: ast.Module) -> dict:
    """Handler name -> (variable, assign node) for a table read from a body."""
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put", "delete", "patch"}
            for d in node.decorator_list
        ):
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Assign) and len(sub.targets) == 1):
                continue
            tgt = sub.targets[0]
            if not isinstance(tgt, ast.Name) or tgt.id not in {"table", "table_name"}:
                continue
            val = sub.value
            if isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute) and val.func.attr == "get":
                out.setdefault(node.name, (tgt.id, sub))
            elif isinstance(val, ast.Attribute) and val.attr in {"table", "table_name"}:
                out.setdefault(node.name, (tgt.id, sub))
    return out


def already_validated(lines: list, fn, var: str) -> bool:
    start = fn.lineno - 1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].lstrip()
        if stripped.startswith("@app.") or stripped.startswith("async def ") or stripped.startswith("def "):
            if lines[i].startswith("@app.") or lines[i][:1] not in (" ", "\t"):
                end = i
                break
    needle = f"validate_table_name({var})"
    return any(needle in l for l in lines[start:end])


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    lines = source.split("\n")
    tree = ast.parse(source)
    handlers = route_handlers(tree)
    bodies = body_source(tree)

    insertions = []  # (at_line_1based, code, description)

    for fname, fn in handlers.items():
        args = [a.arg for a in (list(fn.args.args) + list(fn.args.kwonlyargs))]
        if "table_name" in args:
            var = "table_name"
            body = fn.body
            # Insert BEFORE the first non-docstring statement: that keeps the
            # docstring a docstring, keeps the statement in the function's own
            # block, and works for a multi-line signature.
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                first_stmt = body[1]
            else:
                first_stmt = body[0]
            anchor = first_stmt.lineno - 1      # 0-based index of that statement
            code = f"    {var} = validate_table_name({var})"
            if already_validated(lines, fn, var):
                print(f"  SKIP  {fname}: already validated")
                continue
            insertions.append((anchor, code, fname))

    for fname, (var, assign) in bodies.items():
        fn = handlers.get(fname)
        if fn is None or "table_name" in [a.arg for a in fn.args.args]:
            continue
        if already_validated(lines, fn, var):
            print(f"  SKIP  {fname}: already validated")
            continue
        anchor_line = lines[assign.end_lineno - 1]
        indent = anchor_line[: len(anchor_line) - len(anchor_line.lstrip())]
        code = f"{indent}{var} = validate_table_name({var})"
        insertions.append((assign.end_lineno, code, fname))

    # Apply bottom-up so the earlier line numbers stay valid.
    for at, code, fname in sorted(insertions, key=lambda t: -t[0]):
        lines.insert(at, code)
        print(f"  +     {fname}: {code.strip()}")

    TARGET.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{len(insertions)} insertion(s) applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
