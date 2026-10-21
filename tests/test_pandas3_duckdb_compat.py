"""RED: CSV import must work when pandas gives `str`-dtype columns.

duckdb==1.1.3 (pinned) cannot convert pandas 3's default string dtype
(``dtype('str')`` / ``StringDtype``): ``con.register`` raises
``NotImplementedException: Data type 'str' not recognized`` and the
``/dashstudio/import-paste`` + ``/dashstudio/import-file`` endpoints 500.

The compat layer must normalise only those columns to ``object`` (DuckDB's
classic VARCHAR path) and leave numeric/datetime dtypes untouched.
"""

import io

import duckdb
import pandas as pd
from fastapi.testclient import TestClient

from etl.pandas_compat import register_dataframe

from api.main import app

CSV_TEXT = "date,source,spend,impressions\n2025-04-06,Google Ads,12.5,4000\n2025-04-07,Meta,8.25,2100\n"


def _csv_frame() -> pd.DataFrame:
    return pd.read_csv(io.StringIO(CSV_TEXT))


def test_register_dataframe_stages_read_csv_frame(tmp_path):
    con = duckdb.connect(str(tmp_path / "compat.duckdb"))
    try:
        register_dataframe(con, "staging_compat", _csv_frame())
        rows = con.execute("SELECT date, source, spend FROM staging_compat ORDER BY date").fetchall()
    finally:
        con.close()
    assert rows == [("2025-04-06", "Google Ads", 12.5), ("2025-04-07", "Meta", 8.25)]


def test_register_dataframe_preserves_numeric_types(tmp_path):
    con = duckdb.connect(str(tmp_path / "types.duckdb"))
    try:
        register_dataframe(con, "staging_types", _csv_frame())
        types = {r[0]: r[1] for r in con.execute("DESCRIBE staging_types").fetchall()}
    finally:
        con.close()
    assert types["spend"] == "DOUBLE"
    assert types["impressions"] == "BIGINT"
    assert types["source"] == "VARCHAR"


def test_register_dataframe_accepts_explicit_string_dtype(tmp_path):
    """pandas 3 also materialises `dtype='str'` frames from dict constructors."""
    try:
        df = pd.DataFrame({"label": pd.Series(["a", "b"], dtype="str")})
    except TypeError:  # pragma: no cover - pandas without the str alias
        pytest.skip("pandas build has no str dtype alias")
    con = duckdb.connect(str(tmp_path / "str_dtype.duckdb"))
    try:
        register_dataframe(con, "staging_str", df)
        assert con.execute("SELECT label FROM staging_str ORDER BY label").fetchall() == [("a",), ("b",)]
    finally:
        con.close()


def test_import_paste_stages_csv():
    """End-to-end: the endpoint that 500s today must stage rows."""
    from tests.conftest import auth_headers

    client = TestClient(app)
    resp = client.post(
        "/dashstudio/import-paste",
        headers=auth_headers("pandas3_user", "Str0ng!Passw0rd", role="admin"),
        json={"table_name": "pandas3_compat", "csv_text": CSV_TEXT},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["row_count"] == 2
    assert body["table"] == "pandas3_compat"


def test_import_file_stages_csv():
    from tests.conftest import auth_headers

    client = TestClient(app)
    resp = client.post(
        "/dashstudio/import-file",
        headers=auth_headers("pandas3_file_user", "Str0ng!Passw0rd", role="admin"),
        files={"file": ("ppc_ads.csv", CSV_TEXT.encode(), "text/csv")},
        data={"table_name": "pandas3_file"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2
