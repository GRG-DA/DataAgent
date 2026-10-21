"""pandas -> DuckDB conversion helpers.

duckdb==1.1.3 (pinned in requirements.txt) predates pandas 3's default
string dtype: registering a frame whose columns are ``dtype('str')`` /
``StringDtype`` raises ``NotImplementedException: Data type 'str' not
recognized``, which 500s ``/dashstudio/import-paste`` and
``/dashstudio/import-file``. Casting only those columns to ``object``
restores DuckDB's classic VARCHAR path and leaves every other dtype (and
therefore every inferred DuckDB type) untouched.
"""

import pandas as pd

#: dtype names duckdb 1.1.3 cannot convert on its own.
_UNSUPPORTED_STRING_DTYPES = frozenset({"str", "string", "string[python]", "string[pyarrow]"})


def needs_duckdb_cast(dtype) -> bool:
    """True when ``dtype`` must be normalised before handing the frame to DuckDB."""
    if dtype == object:
        return False
    if isinstance(dtype, pd.StringDtype):
        return True
    return getattr(dtype, "name", str(dtype)) in _UNSUPPORTED_STRING_DTYPES


def duckdb_ready(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame DuckDB 1.1.3 can convert; the input is never mutated.

    Frames without unsupported string columns are returned unchanged (no copy).
    """
    str_cols = [c for c in df.columns if needs_duckdb_cast(df[c].dtype)]
    if not str_cols:
        return df
    return df.astype({c: "object" for c in str_cols})


def register_dataframe(con, name: str, df: pd.DataFrame) -> None:
    """``con.register(name, df)`` with pandas-3-safe dtype normalisation."""
    con.register(name, duckdb_ready(df))
