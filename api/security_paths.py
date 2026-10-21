"""Filename and table-identifier safety helpers for API path parameters."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

_FORBIDDEN_CHARS = ("/", "\\", "\x00")

# DuckDB/SQL identifier: letter/underscore start, then alnum/underscore.
# Rejects spaces, quotes, semicolons, and multi-statement injection.
_VALID_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def sanitize_upload_name(filename: str) -> str:
    """Return a safe basename or raise 400.

    Rejects empty names, path separators, absolute paths, and any ``..``
    segment so callers cannot escape ``data/raw``.
    """
    if filename is None:
        raise HTTPException(status_code=400, detail="No filename provided")

    name = str(filename).strip()
    if not name:
        raise HTTPException(status_code=400, detail="No filename provided")

    if any(ch in name for ch in _FORBIDDEN_CHARS):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = Path(name)
    if path.is_absolute() or ".." in path.parts or path.name != name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    return name


def resolve_under(raw_dir: Path, filename: str) -> Path:
    """Sanitize ``filename`` and resolve it under ``raw_dir`` (must stay inside)."""
    safe = sanitize_upload_name(filename)
    base = Path(raw_dir).resolve()
    target = (base / safe).resolve()
    # Path-boundary check (not str.startswith) so ``…/raw`` cannot match
    # a sibling like ``…/raw_backup``.
    if not target.is_relative_to(base) or target == base:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return target


def validate_table_name(name: Optional[str]) -> str:
    """Return a safe SQL identifier or raise 400.

    Guards every route that interpolates ``table_name`` into SQL
    (``DESCRIBE``/``DROP``/``SELECT``). DuckDB executes multi-statement
    strings, so ``sales;DROP TABLE …`` must never reach the loader.
    """
    if not name or not _VALID_IDENT.fullmatch(str(name)):
        raise HTTPException(status_code=400, detail="Invalid table name")
    return str(name)


def validate_column_name(name: Optional[str]) -> str:
    """Return a safe SQL column identifier or raise 400.

    Same contract as :func:`validate_table_name`, applied to column
    identifiers. Column names are quoted in the generated SQL, so an
    unescaped ``"`` would break out of the quotes and allow a second
    statement (DuckDB executes multi-statement strings). Restricting the
    character set also prevents ``UNION SELECT`` against system tables.
    """
    if not name or not _VALID_IDENT.fullmatch(str(name)):
        raise HTTPException(status_code=400, detail="Invalid column name")
    return str(name)
