"""POST /load must return 400 when cleaned data has zero rows."""

from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd

# Project root — /load resolves Path("data/raw") against the process cwd.
_ROOT = Path(__file__).resolve().parents[1]
_RAW = _ROOT / "data" / "raw"


def test_load_empty_returns_400(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.main as main
    from etl.clean import DataCleaner
    from etl.extract import DataExtractor
    from etl.load import WarehouseLoader

    db_path = str(tmp_path / "load_test.duckdb")
    loader = WarehouseLoader(db_path=db_path, schema="analytics")
    monkeypatch.setattr(main, "loader", loader)

    def _empty_extract(_path):
        return {"default": pd.DataFrame(columns=["id", "name"])}

    name = f"_tmp_empty_load_{uuid.uuid4().hex}.csv"
    # Each tenant uploads into its own directory (ADR-009 D1), so the
    # fixture file has to land where this caller's tenant can find it.
    # The tenant is assigned when the app starts, so read it from the
    # identity store after startup.
    from api.tenancy import DEFAULT_TENANT_ID, tenant_slug

    try:
        from tests.conftest import auth_headers

        with TestClient(main.app, headers=auth_headers(
            "load_empty_test", "Str0ng!Passw0rd", role="admin"
        )) as client:
            from api.auth import user_manager

            info = user_manager.get_user_info("load_empty_test") or {}
            tenant_id = str(info.get("tenant_id") or "")
            if tenant_id and tenant_id != DEFAULT_TENANT_ID:
                raw_dir = Path("data/raw") / tenant_slug(tenant_id)
            else:
                raw_dir = Path("data/raw")
            file_path = raw_dir / name
            raw_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text("id,name\n", encoding="utf-8")
            assert file_path.exists()

            # Lifespan re-inits globals; restore our isolated doubles.
            # /load now lives in api.routes.data and reads api.state, so both
            # the legacy main.* globals and api.state must point at doubles.
            import api.state as _state

            extractor = DataExtractor(config={})
            monkeypatch.setattr(extractor, "extract", _empty_extract)
            cleaner = DataCleaner(config={})
            monkeypatch.setattr(main, "extractor", extractor)
            monkeypatch.setattr(main, "cleaner", cleaner)
            monkeypatch.setattr(main, "loader", loader)
            monkeypatch.setattr(_state, "extractor", extractor)
            monkeypatch.setattr(_state, "cleaner", cleaner)
            monkeypatch.setattr(_state, "loader", loader)

            r = client.post(
                "/load",
                json={"filename": name, "table_name": "empty_should_fail"},
            )
        assert r.status_code == 400, r.text
        body = r.json()
        assert "empty" in str(body.get("detail", "")).lower()
        assert not loader.table_exists("empty_should_fail")
    finally:
        for stale in (_RAW / name, *Path("data/raw").glob(f"*/{name}")):
            stale.unlink(missing_ok=True)
