"""TDD: POST /clean/apply — persist a cleaned warehouse table.

Chat Pro's "clean the data" task needs the preview logic to actually write a
new table. These tests pin the endpoint contract: validation, collision-safe
naming, source immutability, real cleaning, and the empty-result guard.
"""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def apply_env(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.main as main
    import api.state as state
    from config import settings
    from etl.load import WarehouseLoader
    from tests.conftest import auth_headers

    db_path = str(tmp_path / "clean_apply.duckdb")
    loader = WarehouseLoader(db_path=db_path, schema="analytics")
    headers = auth_headers("clean_apply_user", "Str0ng!Passw0rd", role="admin")
    with TestClient(main.app, headers=headers) as client:
        # Lifespan re-inits the globals; point the route at the isolated file.
        monkeypatch.setattr(state, "loader", loader)
        monkeypatch.setattr(main, "loader", loader)
        monkeypatch.setattr(settings.duckdb, "database_path", db_path)
        from api.auth import user_manager

        info = user_manager.get_user_info("clean_apply_user") or {}
        tenant_id = str(info.get("tenant_id") or "default")
        yield client, loader, tenant_id


def _seed(loader, tenant_id, name="apply_src"):
    df = pd.DataFrame(
        {
            "id": [1, 2, 3, 4, 4],
            "name": [" Alice ", "Bob", "", "Bob", "Bob"],
            "amount": [10.0, None, 5.0, 20.0, 20.0],
        }
    )
    loader.load_dataframe(df, name, if_exists="replace", tenant_id=tenant_id)


def test_apply_persists_cleaned_table(apply_env):
    client, loader, tenant_id = apply_env
    _seed(loader, tenant_id)

    r = client.post("/clean/apply", json={"table_name": "apply_src"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["output_table"] == "apply_src_clean"
    assert body["rows_before"] == 5
    assert body["rows_after"] == 2  # null amount, empty name, exact duplicate
    assert body["cleaning_code"]
    assert loader.table_exists("apply_src_clean", tenant_id=tenant_id)

    with loader.get_connection(tenant_id) as conn:
        out = conn.execute('SELECT * FROM "apply_src_clean"').df()
        src_rows = conn.execute('SELECT COUNT(*) FROM "apply_src"').fetchone()[0]
    assert len(out) == 2
    assert not out.duplicated().any()
    assert (out["name"].dropna().str.strip() == out["name"].dropna()).all()
    # The source table is never touched.
    assert src_rows == 5


def test_apply_collision_uses_suffix(apply_env):
    client, loader, tenant_id = apply_env
    _seed(loader, tenant_id)
    loader.load_dataframe(
        pd.DataFrame({"x": [1]}), "apply_src_clean", tenant_id=tenant_id
    )

    r = client.post("/clean/apply", json={"table_name": "apply_src"})
    assert r.status_code == 200, r.text
    assert r.json()["output_table"] == "apply_src_clean2"


def test_apply_rejects_bad_and_missing_tables(apply_env):
    client, _loader, _tenant_id = apply_env
    for bad in ["t; DROP TABLE x; --", "sales --", '"sales"', "a b", ""]:
        r = client.post("/clean/apply", json={"table_name": bad})
        assert r.status_code in (400, 422), (bad, r.status_code, r.text[:200])

    r = client.post("/clean/apply", json={"table_name": "does_not_exist"})
    assert r.status_code == 404


def test_apply_refuses_source_as_output(apply_env):
    client, loader, tenant_id = apply_env
    _seed(loader, tenant_id)

    r = client.post(
        "/clean/apply",
        json={"table_name": "apply_src", "output_table": "apply_src"},
    )
    assert r.status_code == 400


def test_apply_empty_result_returns_400(apply_env):
    client, loader, tenant_id = apply_env
    df = pd.DataFrame({"a": [1], "b": [None]})
    loader.load_dataframe(df, "apply_null_only", tenant_id=tenant_id)

    r = client.post(
        "/clean/apply",
        json={"table_name": "apply_null_only", "null_strategy": "drop"},
    )
    assert r.status_code == 400
    assert "no rows" in str(r.json().get("detail", "")).lower()
    assert not loader.table_exists("apply_null_only_clean", tenant_id=tenant_id)


def test_apply_requires_data_write_permission(apply_env):
    from fastapi.testclient import TestClient

    import api.main as main
    from tests.conftest import auth_headers

    viewer_headers = auth_headers(
        "clean_apply_viewer", "Str0ng!Passw0rd", role="viewer"
    )
    with TestClient(main.app, headers=viewer_headers) as viewer:
        r = viewer.post("/clean/apply", json={"table_name": "apply_src"})
    assert r.status_code == 403, r.text[:200]


def test_clean_ops_helper_trims_and_nulls_empty_strings():
    from api.routes.data import _clean_dataframe_ops

    df = pd.DataFrame({"name": [" x ", ""], "v": [1.0, None]})
    out, _changes = _clean_dataframe_ops(
        df,
        dedup=False,
        null_strategy="keep",
        drop_columns=[],
        trim_whitespace=True,
        empty_to_null=True,
    )
    assert out["name"].iloc[0] == "x"
    assert pd.isna(out["name"].iloc[1])
    assert pd.isna(out["v"].iloc[1])


def test_clean_ops_helper_fill_mode_survives_string_columns():
    from api.routes.data import _clean_dataframe_ops

    df = pd.DataFrame({"name": ["a", "a", None], "v": [1.0, None, 3.0]})
    out, _changes = _clean_dataframe_ops(
        df,
        dedup=False,
        null_strategy="fill_mode",
        drop_columns=[],
        trim_whitespace=False,
        empty_to_null=False,
    )
    assert out["name"].iloc[2] == "a"
    assert out["v"].iloc[1] == 1.0
