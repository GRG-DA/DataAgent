"""Dependency-combination guards for the packaged desktop build."""

import duckdb
import pandas


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in value.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def test_duckdb_supports_the_installed_pandas():
    """DuckDB before 1.4.4 cannot safely convert pandas 3.x frames."""
    duckdb_version = _version_tuple(duckdb.__version__)
    pandas_major = _version_tuple(pandas.__version__)[0]
    if duckdb_version < (1, 4, 4):
        assert pandas_major < 3, (
            f"duckdb {duckdb.__version__} predates pandas 3.x support; "
            f"pandas {pandas.__version__} can corrupt the heap during .df()"
        )
