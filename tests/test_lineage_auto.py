"""TDD: automatic, tenant-scoped lineage recording.

Lineage is written by the backend whenever a table is persisted (upload/load
and clean/apply), never by the browser calling ``POST /lineage/record``.
Records are isolated per tenant and a lineage failure must never fail the
data operation.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest


def test_lineage_storage_is_tenant_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(tmp_path))

    from analysis.lineage import LineageTracker
    from api.tenancy import use_tenant

    with use_tenant("tenant_a"):
        LineageTracker().record_transform("table_a", "load", ["a.csv"], ["table_a"])

    with use_tenant("tenant_b"):
        assert LineageTracker().get_lineage("table_a") == []
        LineageTracker().record_transform("table_b", "load", ["b.csv"], ["table_b"])

    # The default tenant does not see either tenant's records.
    assert LineageTracker().get_lineage("table_a") == []
    assert LineageTracker().get_lineage("table_b") == []

    files = sorted((tmp_path / "data" / "lineage").glob("*.json"))
    assert len(files) == 2, files


def test_concurrent_records_are_not_lost(tmp_path):
    from analysis.lineage import LineageTracker

    path = tmp_path / "lineage.json"

    def record(i: int) -> None:
        LineageTracker(storage_path=path).record_transform(
            f"table_{i}", "clean", [f"src_{i}"], [f"table_{i}"]
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(24)))

    assert len(LineageTracker(storage_path=path).get_full_lineage()) == 24


def test_record_persisted_transform_is_best_effort(monkeypatch):
    import api.lineage_service as service

    class Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("lineage storage unavailable")

    monkeypatch.setattr("analysis.lineage.LineageTracker", Boom)

    assert (
        service.record_persisted_transform(
            operation="load",
            inputs=["file.csv"],
            outputs=["loaded"],
            table="loaded",
        )
        is None
    )


@pytest.fixture
def lineage_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.main as main
    import api.state as state
    from api.routes import data as data_routes
    from config import settings
    from etl.clean import DataCleaner
    from etl.extract import DataExtractor
    from etl.load import WarehouseLoader
    from tests.conftest import auth_headers

    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(tmp_path / "runtime"))
    db_path = str(tmp_path / "lineage.duckdb")
    loader = WarehouseLoader(db_path=db_path, schema="analytics")
    headers = auth_headers("lineage_auto_user", "Str0ng!Passw0rd", role="admin")

    with TestClient(main.app, headers=headers) as client:
        monkeypatch.setattr(state, "loader", loader)
        monkeypatch.setattr(main, "loader", loader)
        monkeypatch.setattr(settings.duckdb, "database_path", db_path)
        monkeypatch.setattr(
            state, "extractor", DataExtractor(config=settings.data.model_dump())
        )
        monkeypatch.setattr(
            state, "cleaner", DataCleaner(config=settings.data.model_dump())
        )
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        monkeypatch.setattr(data_routes, "_tenant_raw_dir", lambda: raw_dir)

        from api.auth import user_manager

        info = user_manager.get_user_info("lineage_auto_user") or {}
        tenant_id = str(info.get("tenant_id") or "default")
        yield client, loader, tenant_id, raw_dir


def test_clean_apply_records_lineage(lineage_app):
    from analysis.lineage import LineageTracker

    client, loader, tenant_id, _raw_dir = lineage_app
    loader.load_dataframe(
        pd.DataFrame({"id": [1, 2], "name": ["a", "b"]}),
        "lineage_src",
        if_exists="replace",
        tenant_id=tenant_id,
    )

    response = client.post("/clean/apply", json={"table_name": "lineage_src"})
    assert response.status_code == 200, response.text

    records = LineageTracker(tenant_id=tenant_id).get_lineage("lineage_src_clean")
    assert len(records) == 1
    record = records[0]
    assert record["operation"] == "clean"
    assert record["inputs"] == ["lineage_src"]
    assert record["outputs"] == ["lineage_src_clean"]
    assert record["metadata"]["rows_before"] == 2
    assert record["column_map"]["name"] == ["lineage_src.name"]


def test_chat_clean_table_records_lineage(lineage_app):
    from agent.tools_tasks import clean_table
    from analysis.lineage import LineageTracker

    _client, loader, tenant_id, _raw_dir = lineage_app
    loader.load_dataframe(
        pd.DataFrame({"id": [1, 2], "name": ["a", "b"]}),
        "lineage_chat_src",
        if_exists="replace",
        tenant_id=tenant_id,
    )

    result = clean_table("lineage_chat_src", tenant_id=tenant_id)
    assert result.success, result.error

    records = LineageTracker(tenant_id=tenant_id).get_lineage(
        result.data["output_table"]
    )
    assert records and records[-1]["operation"] == "clean"
    assert records[-1]["inputs"] == ["lineage_chat_src"]


def test_load_records_lineage(lineage_app):
    from analysis.lineage import LineageTracker

    client, _loader, tenant_id, raw_dir = lineage_app
    (raw_dir / "lineage_load.csv").write_text("id,name\n1,a\n2,b\n", encoding="utf-8")

    response = client.post(
        "/load",
        json={"filename": "lineage_load.csv", "table_name": "lineage_loaded"},
    )
    assert response.status_code == 200, response.text

    records = LineageTracker(tenant_id=tenant_id).get_lineage("lineage_loaded")
    assert len(records) == 1
    record = records[0]
    assert record["operation"] == "load"
    assert record["inputs"] == ["lineage_load.csv"]
    assert record["outputs"] == ["lineage_loaded"]
    assert record["metadata"]["rows"] == 2


def test_lineage_failure_does_not_fail_load(lineage_app, monkeypatch):
    client, _loader, _tenant_id, raw_dir = lineage_app
    (raw_dir / "lineage_fail.csv").write_text("id,name\n1,a\n", encoding="utf-8")

    class Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("lineage unavailable")

    monkeypatch.setattr("analysis.lineage.LineageTracker", Boom)

    response = client.post(
        "/load",
        json={"filename": "lineage_fail.csv", "table_name": "lineage_still_loaded"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows_loaded"] == 1


def test_record_endpoint_rejects_malformed_payload(lineage_app):
    client, _loader, _tenant_id, _raw_dir = lineage_app

    assert client.post("/lineage/record", json={}).status_code == 400
    assert (
        client.post(
            "/lineage/record",
            json={"table": "x", "operation": "manual", "inputs": "not-a-list"},
        ).status_code
        == 400
    )


def test_record_endpoint_writes_to_caller_tenant(lineage_app):
    from analysis.lineage import LineageTracker

    client, _loader, tenant_id, _raw_dir = lineage_app
    response = client.post(
        "/lineage/record",
        json={
            "table": "manual_out",
            "operation": "manual",
            "inputs": ["manual_in"],
            "outputs": ["manual_out"],
            "description": "operator annotation",
        },
    )
    assert response.status_code == 200, response.text

    records = LineageTracker(tenant_id=tenant_id).get_lineage("manual_out")
    assert records and records[-1]["operation"] == "manual"

def test_lineage_history_escapes_inputs_and_outputs():
    source = (
        Path(__file__).resolve().parents[1] / "web" / "js" / "pages" / "lineage.js"
    ).read_text(encoding="utf-8")
    assert "App.escapeHtml(r.inputs.join(', '))" in source
    assert "App.escapeHtml(r.outputs.join(', '))" in source
