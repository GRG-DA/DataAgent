"""Remote store backend: Turso (libSQL) via the optional `libsql` client.

``store.py`` is written against the sqlite3 shape (``row_factory``, context
managers, tuple-only parameter binding). The libsql client mirrors sqlite3
but leaves some surface unimplemented — notably ``row_factory`` and
``executescript`` — so this adapter wraps a connection with the minimal
compatibility layer the store relies on:

- ``execute(sql, params)`` returns a cursor whose ``fetchone``/``fetchall``
  yield row objects supporting ``row["col"]``, ``row[0]``, and ``dict(row)``.
- ``with connection:`` commits on success, rolls back on error, and closes.

Imported lazily (only when ``TURSO_DATABASE_URL`` is set), so local installs
never need the ``libsql`` package.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence


class AdapterError(RuntimeError):
    """Raised for local misconfiguration of the remote backend."""


class _LibsqlRow:
    """sqlite3.Row-like access over a plain tuple + column names."""

    __slots__ = ("_keys", "_values")

    def __init__(self, keys: Sequence[str], values: Sequence[Any]) -> None:
        self._keys = list(keys)
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        return self._values[self._keys.index(key)]

    def keys(self) -> list[str]:
        return list(self._keys)

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key) -> bool:
        return key in self._keys

    def __repr__(self) -> str:
        return f"<Row {dict(zip(self._keys, self._values))!r}>"


class _LibsqlCursor:
    __slots__ = ("_cur",)

    def __init__(self, cur) -> None:
        self._cur = cur

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def _row(self, raw):
        if raw is None:
            return None
        desc = self._cur.description or ()
        return _LibsqlRow([d[0] for d in desc], raw)

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self) -> list[_LibsqlRow]:
        return [self._row(r) for r in self._cur.fetchall()]


class _LibsqlConnection:
    __slots__ = ("_con",)

    def __init__(self, con) -> None:
        self._con = con

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> _LibsqlCursor:
        if parameters:
            return _LibsqlCursor(self._con.execute(sql, tuple(parameters)))
        return _LibsqlCursor(self._con.execute(sql))

    def commit(self) -> None:
        self._con.commit()

    def rollback(self) -> None:
        self._con.rollback()

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:  # noqa: BLE001 — close is best-effort
            pass

    def __enter__(self) -> "_LibsqlConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def connect(url: str, auth_token: str) -> _LibsqlConnection:
    """Open a wrapped connection to the remote database."""
    if not url:
        raise AdapterError("TURSO_DATABASE_URL is not configured")
    if not auth_token:
        raise AdapterError(
            "TURSO_AUTH_TOKEN is required when TURSO_DATABASE_URL is set"
        )
    try:
        import libsql  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AdapterError(
            "the 'libsql' package is required for TURSO_DATABASE_URL mode "
            "(pip install libsql)"
        ) from exc
    return _LibsqlConnection(libsql.connect(url, auth_token=auth_token))
