"""Coverage push for api/main.py + api/routes/{org,data,enterprise}.py + api/dependencies.py.

Fast TestClient tests only (no network, no real warehouse — `billing_db`
isolates DuckDB, dashboard connections are stubbed to tmp files).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import api.main as main
from api.main import app
from config import settings
from tests.conftest import auth_headers, ensure_user

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


# ── helpers ──────────────────────────────────────────────────────────────

def _tag(prefix: str = "t") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _headers_for(tag: str, role: str = "admin", plan=None):
    """Auth headers for a unique user; optionally grant a billing plan."""
    from api.auth import user_manager

    username = f"cov_{tag}_{uuid.uuid4().hex[:6]}"
    headers = auth_headers(username, "Str0ng!Passw0rd", role=role)
    uid = user_manager.get_user_info(username)["user_id"]
    if plan:
        from api.billing.subs import ensure_billing_row

        ensure_billing_row(uid, plan, never_expires=True)
    return headers, uid


@pytest.fixture
def wired_loader(billing_db, monkeypatch):
    """Point api.main.loader at the isolated billing-db loader."""
    import api.state as state

    monkeypatch.setattr(main, "loader", state.loader)
    return state.loader


@pytest.fixture
def wired_etl(billing_db, monkeypatch):
    """Real extractor/cleaner wired into api.state (isolated tmp warehouse)."""
    import api.state as state
    from etl.clean import DataCleaner
    from etl.extract import DataExtractor

    ex = DataExtractor(config=settings.data.model_dump())
    cl = DataCleaner(config=settings.data.model_dump())
    monkeypatch.setattr(state, "extractor", ex)
    monkeypatch.setattr(state, "cleaner", cl)
    monkeypatch.setattr(main, "loader", state.loader)
    return ex, cl


def _isolated_managers(monkeypatch, tmp_path):
    import duckdb

    with duckdb.connect(str(settings.duckdb.database_path)) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    import api.cost_usage as cost_mod
    import api.sla as sla_mod

    for name in ("sla.duckdb", "cost.duckdb"):
        with duckdb.connect(str(tmp_path / name)) as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    monkeypatch.setattr(
        sla_mod, "sla_manager",
        sla_mod.SLAManager(db_path=str(tmp_path / "sla.duckdb")),
    )
    monkeypatch.setattr(
        cost_mod, "cost_usage_manager",
        cost_mod.CostUsageManager(db_path=str(tmp_path / "cost.duckdb")),
    )


@pytest.fixture
def dash_conn(monkeypatch, tmp_path):
    """Stub the dashboards shared connection to a tmp DuckDB file."""
    import duckdb

    import api.dashboards as dash_mod

    con = duckdb.connect(str(tmp_path / "dash.duckdb"))
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    monkeypatch.setattr(dash_mod, "get_connection", lambda: con)
    yield con
    con.close()


def _raw_write(name: str, text: str) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    p = RAW / name
    p.write_text(text, encoding="utf-8")
    return p


# ── health / config / auth ───────────────────────────────────────────────

class TestHealthConfig:
    def test_health_ok(self):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "healthy"
        assert body["components"]["database"] == "connected"
        assert "version" in body and "llm_model" in body

    def test_health_system_ok(self, billing_db):
        headers, _ = _headers_for("hsys")
        r = client.get("/health/system", headers=headers)
        assert r.status_code == 200
        assert r.json()["components"]["api"]["status"] == "healthy"

    def test_config_public_ok(self):
        r = client.get("/config")
        assert r.status_code == 200
        body = r.json()
        assert "features" in body and body["ads"]["enabled"] is False
        assert body["billing"]["enabled"] is False

    def test_config_authed_has_entitlements(self, billing_db):
        headers, _ = _headers_for("cfg")
        r = client.get("/config", headers=headers)
        assert r.status_code == 200
        assert "entitlements" in r.json()["billing"]

    def test_auth_status_requires_login(self):
        assert client.get("/auth/status").status_code == 401

    def test_auth_status_ok(self):
        headers, _ = _headers_for("st")
        r = client.get("/auth/status", headers=headers)
        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    def test_logout_ok(self):
        headers, _ = _headers_for("lo")
        r = client.post("/auth/logout", headers=headers)
        assert r.status_code == 200
        assert r.json()["success"] is True


# ── upload / clean / load ────────────────────────────────────────────────

class TestIngest:
    def test_upload_missing_filename(self, wired_etl):
        headers, _ = _headers_for("up0")
        r = client.post("/upload", files={"file": ("", b"a,b\n1,2\n")},
                        headers=headers)
        assert r.status_code in (400, 422)

    def test_upload_bad_extension(self, wired_etl):
        headers, _ = _headers_for("up1")
        r = client.post("/upload",
                        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
                        headers=headers)
        assert r.status_code == 400
        assert "Unsupported" in r.json()["detail"]

    def test_upload_too_large(self, wired_etl, monkeypatch):
        monkeypatch.setattr(settings.data, "max_upload_size_mb", 0)
        headers, _ = _headers_for("up2")
        r = client.post("/upload", files={"file": ("big.csv", b"a,b\n1,2\n")},
                        headers=headers)
        assert r.status_code == 400

    def test_upload_extractor_not_initialized(self, billing_db, monkeypatch):
        import api.state as state

        monkeypatch.setattr(state, "extractor", None)
        headers, _ = _headers_for("up3")
        r = client.post("/upload", files={"file": ("x.csv", b"a\n1\n")},
                        headers=headers)
        assert r.status_code == 503

    def test_upload_success_csv(self, wired_etl):
        headers, _ = _headers_for("up4")
        name = _tag("covup") + ".csv"
        r = client.post("/upload", files={"file": (name, b"a,b\n1,2\n3,4\n")},
                        headers=headers)
        try:
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["success"] is True
            assert body["sheets"] == ["default"]
            assert body["rows"] == 2
        finally:
            (RAW / name).unlink(missing_ok=True)

    def test_upload_extractor_failure_is_500(self, wired_etl, monkeypatch):
        import api.state as state

        def _boom(_path):
            raise RuntimeError("parse exploded")

        monkeypatch.setattr(state.extractor, "extract", _boom)
        headers, _ = _headers_for("up5")
        name = _tag("covupfail") + ".csv"
        r = client.post("/upload", files={"file": (name, b"a\n1\n")},
                        headers=headers)
        try:
            assert r.status_code == 500
        finally:
            (RAW / name).unlink(missing_ok=True)

    def test_clean_missing_file(self, wired_etl):
        headers, _ = _headers_for("cl0")
        r = client.post("/clean", json={"filename": "no_such_file_xyz.csv"},
                        headers=headers)
        assert r.status_code == 404

    def test_clean_success_with_plan_branches(self, wired_etl):
        headers, _ = _headers_for("cl1")
        name = _tag("covcl") + ".csv"
        _raw_write(name, "a,b\n1,x\n2,\n2,\n")
        try:
            r = client.post("/clean", json={
                "filename": name, "sheet": "default",
                "plan": {
                    "deduplication": "bogus_value",
                    "deduplication_subset": ["alpha"],
                    "global_null_strategy": "bogus_fallback",
                    "drop_columns": ["b"],
                    "rename_columns": {"a": "alpha"},
                    "column_rules": {
                        "alpha": {"strategy": "fill_constant", "fill_value": 0},
                        "zzz": {"strategy": "bogus_strategy"},
                        "skip": "notadict",
                    },
                },
            }, headers=headers)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["success"] is True
            assert "preview" in body and "cleaning_code" in body
        finally:
            (RAW / name).unlink(missing_ok=True)

    def test_clean_auto_infer_plan(self, wired_etl):
        headers, _ = _headers_for("cl2")
        name = _tag("covcl") + ".csv"
        _raw_write(name, "a\n1\n2\n")
        try:
            r = client.post("/clean", json={"filename": name}, headers=headers)
            assert r.status_code == 200
            assert r.json()["success"] is True
        finally:
            (RAW / name).unlink(missing_ok=True)

    def test_clean_preview_invalid_name(self, wired_etl):
        headers, _ = _headers_for("cp0")
        r = client.post("/clean/preview",
                        json={"table_name": "bad;name"}, headers=headers)
        assert r.json() == {"success": False, "error": "Invalid table name"}

    def test_clean_preview_missing_table(self, wired_etl):
        headers, _ = _headers_for("cp1")
        r = client.post("/clean/preview",
                        json={"table_name": "cov_nosuch_xyz"}, headers=headers)
        body = r.json()
        assert body["success"] is False and "not found" in body["error"]

    def test_clean_preview_success_and_drop_column(self, wired_etl):
        import api.state as state

        headers, _ = _headers_for("cp2")
        state.loader.load_dataframe(
            pd.DataFrame({"a": [1.0, None, 1.0], "b": ["x", "y", "x"]}),
            "covprev_t", if_exists="replace")
        r = client.post("/clean/preview",
                        json={"table_name": "covprev_t", "dedup": True,
                              "null_strategy": "fill_mean", "drop_columns": ["b"]},
                        headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["before"]["rows"] == 3
        assert "b" in body["columns_removed"]

    def test_load_missing_file(self, wired_etl):
        headers, _ = _headers_for("ld0")
        r = client.post("/load", json={"filename": "no_such_file_xyz.csv"},
                        headers=headers)
        assert r.status_code == 404

    def test_load_empty_df_is_400(self, wired_etl):
        headers, _ = _headers_for("ld1")
        name = _tag("covldempty") + ".csv"
        _raw_write(name, "id,name\n")
        try:
            r = client.post("/load", json={"filename": name}, headers=headers)
            assert r.status_code == 400
            assert "empty" in r.json()["detail"].lower()
        finally:
            (RAW / name).unlink(missing_ok=True)

    def test_load_bad_table_name(self, wired_etl):
        headers, _ = _headers_for("ld2")
        name = _tag("covld") + ".csv"
        _raw_write(name, "a\n1\n")
        try:
            r = client.post("/load",
                            json={"filename": name, "table_name": "bad;name"},
                            headers=headers)
            assert r.status_code == 400
        finally:
            (RAW / name).unlink(missing_ok=True)

    def test_load_success(self, wired_etl):
        import api.state as state

        headers, _ = _headers_for("ld3")
        name = _tag("covld") + ".csv"
        table = _tag("covload")
        _raw_write(name, "a\n1\n2\n")
        try:
            r = client.post("/load",
                            json={"filename": name, "table_name": table},
                            headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["rows_loaded"] == 2
            assert state.loader.table_exists(table)
        finally:
            (RAW / name).unlink(missing_ok=True)


# ── tables / sql / merge / schema / ask / query-builder / legacy dash ────

class TestMainQuerySurface:
    def test_tables_empty_without_loader(self, billing_db, monkeypatch):
        monkeypatch.setattr(main, "loader", None)
        headers, _ = _headers_for("tbl0")
        r = client.get("/tables", headers=headers)
        assert r.json() == {"tables": {}}

    def test_tables_list_ok(self, wired_loader):
        headers, _ = _headers_for("tbl1")
        r = client.get("/tables", headers=headers)
        assert r.status_code == 200
        assert "tables" in r.json() and "system_tables" in r.json()

    def test_table_info_invalid_name(self, wired_loader):
        headers, _ = _headers_for("tbl2")
        assert client.get("/tables/bad;name", headers=headers).status_code == 400

    def test_table_info_missing_is_404(self, wired_loader):
        headers, _ = _headers_for("tbl3")
        assert client.get("/tables/cov_nosuch_xyz", headers=headers).status_code == 404

    def test_table_sample_success(self, wired_loader):
        headers, _ = _headers_for("tbl4")
        wired_loader.load_dataframe(pd.DataFrame({"a": [1, 2, 3]}),
                                    "covsample_t", if_exists="replace")
        r = client.post("/tables/covsample_t/sample?n=2", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["rows"]) == 2

    def test_table_sample_invalid_name(self, wired_loader):
        headers, _ = _headers_for("tbl5")
        assert client.post("/tables/bad;name/sample", headers=headers).status_code == 400

    def test_drop_table_non_admin_forbidden(self, wired_loader):
        headers, _ = _headers_for("drop0", role="viewer")
        assert client.delete("/tables/cov_x", headers=headers).status_code == 403

    def test_drop_table_missing_is_404(self, wired_loader):
        headers, _ = _headers_for("drop1")
        r = client.delete("/tables/cov_nosuch_xyz", headers=headers)
        assert r.status_code == 404

    def test_drop_system_table_forbidden(self, wired_loader):
        import warehouse.system_tables as st

        headers, _ = _headers_for("drop2")
        syst = sorted(st.SYSTEM_TABLES)[0]
        assert client.delete(f"/tables/{syst}", headers=headers).status_code == 403

    def test_drop_all_requires_confirm(self, wired_loader):
        headers, _ = _headers_for("drop3")
        assert client.request("DELETE", "/tables", headers=headers).status_code in (400, 422)

    def test_merge_bad_join_type(self, wired_loader):
        headers, _ = _headers_for("mg0")
        r = client.post("/merge", json={"left_table": "a", "right_table": "b",
                                        "left_key": "id", "right_key": "id",
                                        "join_type": "sideways"}, headers=headers)
        assert r.status_code == 400

    def test_merge_missing_tables_404(self, wired_loader):
        headers, _ = _headers_for("mg1")
        r = client.post("/merge", json={"left_table": "cov_nope1",
                                        "right_table": "cov_nope2",
                                        "left_key": "id", "right_key": "id"},
                        headers=headers)
        assert r.status_code == 404

    def test_sql_forbidden_statement(self, wired_loader):
        headers, _ = _headers_for("sql0")
        r = client.post("/sql", json={"query": "DROP TABLE analytics.sales"},
                        headers=headers)
        assert r.status_code == 400
        assert r.json()["success"] is False

    def test_sql_empty_is_422(self, wired_loader):
        headers, _ = _headers_for("sql1")
        assert client.post("/sql", json={"query": ""}, headers=headers).status_code == 422

    def test_sql_alias_and_success(self, wired_loader):
        wired_loader.load_dataframe(pd.DataFrame({"a": [1]}),
                                    "covsql_t", if_exists="replace")
        headers, _ = _headers_for("sql2")
        r = client.post("/sql", json={"sql": "SELECT * FROM covsql_t"},
                        headers=headers)
        assert r.status_code == 200
        assert r.json()["row_count"] == 1

    def test_sql_missing_table_hint(self, wired_loader):
        headers, _ = _headers_for("sql3")
        r = client.post("/sql", json={"query": "SELECT * FROM cov_missing_xyz"},
                        headers=headers)
        body = r.json()
        assert body["success"] is False
        assert "Available tables" in body.get("hint", "")

    def test_schema_ok(self, wired_loader):
        headers, _ = _headers_for("sch")
        assert client.get("/schema", headers=headers).status_code == 200

    def test_ask_disabled_with_fallback(self, billing_db, monkeypatch):
        monkeypatch.setattr(settings.features, "enable_llm_agent", False)
        monkeypatch.setattr(settings.features, "fallback_to_sql", True)
        headers, _ = _headers_for("ask0")
        r = client.post("/ask", json={"question": "hi"}, headers=headers)
        assert r.status_code == 400

    def test_ask_disabled_no_fallback_404(self, billing_db, monkeypatch):
        monkeypatch.setattr(settings.features, "enable_llm_agent", False)
        monkeypatch.setattr(settings.features, "fallback_to_sql", False)
        headers, _ = _headers_for("ask1")
        r = client.post("/ask", json={"question": "hi"}, headers=headers)
        assert r.status_code == 404

    def test_ask_no_orchestrator_503(self, billing_db, monkeypatch):
        monkeypatch.setattr(settings.features, "enable_llm_agent", True)
        monkeypatch.setattr(main, "orchestrator", None)
        headers, _ = _headers_for("ask2")
        r = client.post("/ask", json={"question": "hi"}, headers=headers)
        assert r.status_code == 503

    def test_ask_sql_disabled_404(self, billing_db, monkeypatch):
        monkeypatch.setattr(settings.features, "fallback_to_sql", False)
        headers, _ = _headers_for("asq0")
        r = client.post("/ask-sql", json={"question": "hi"}, headers=headers)
        assert r.status_code == 404

    def test_ask_sql_no_tables(self, billing_db, monkeypatch):
        monkeypatch.setattr(settings.features, "fallback_to_sql", True)
        monkeypatch.setattr(main, "loader", SimpleNamespace(list_tables=lambda: []))
        headers, _ = _headers_for("asq1")
        r = client.post("/ask-sql", json={"question": "hi"}, headers=headers)
        assert r.json()["success"] is False

    def test_ask_sql_no_orchestrator(self, billing_db, monkeypatch):
        monkeypatch.setattr(settings.features, "fallback_to_sql", True)
        monkeypatch.setattr(settings.features, "enable_llm_agent", False)
        monkeypatch.setattr(main, "orchestrator", None)
        import api.state as state

        monkeypatch.setattr(main, "loader", state.loader)
        state.loader.load_dataframe(pd.DataFrame({"a": [1]}),
                                    "covask_t", if_exists="replace")
        headers, _ = _headers_for("asq2")
        r = client.post("/ask-sql", json={"question": "hi"}, headers=headers)
        body = r.json()
        assert body["success"] is False
        assert "Orchestrator" in body["answer"]

    def test_visual_build_empty(self, billing_db):
        headers, _ = _headers_for("qb0")
        ok = client.post("/query/visual/build", json={}, headers=headers).json()
        assert ok["success"] is True and "sql" in ok
        bad = client.post("/query/visual/build", json={
            "from_table": "t", "filters": "boom",
        }, headers=headers).json()
        assert bad["success"] is False

    def test_visual_validate_empty(self, billing_db):
        headers, _ = _headers_for("qb1")
        body = client.post("/query/visual/validate", json={}, headers=headers).json()
        assert "success" in body

    def test_visual_execute_empty(self, billing_db):
        headers, _ = _headers_for("qb2")
        assert client.post("/query/visual/execute", json={}, headers=headers).json()["success"] is False

    def test_visual_autojoin_missing_params(self, billing_db):
        headers, _ = _headers_for("qb3")
        body = client.post("/query/visual/auto-join", json={}, headers=headers).json()
        assert body == {"success": False, "error": "table1 and table2 are required"}

    def test_visual_suggest_missing_table(self, billing_db):
        headers, _ = _headers_for("qb4")
        body = client.post("/query/visual/suggest", json={}, headers=headers).json()
        assert body == {"success": False, "error": "table is required"}

    def test_legacy_dashboard_empty(self, billing_db, monkeypatch):
        monkeypatch.setattr(main, "loader", SimpleNamespace(list_tables=lambda: []))
        headers, _ = _headers_for("legd")
        for path in ("/dashboard/kpi", "/dashboard/revenue-trend",
                     "/dashboard/revenue-by-dimension", "/dashboard/top-products",
                     "/dashboard/growth"):
            r = client.get(path, headers=headers)
            assert r.status_code == 200, path
            assert r.json()["success"] is True

    def test_detect_best_table_text_dates(self, billing_db, monkeypatch):
        import api.state as state

        monkeypatch.setattr(main, "loader", state.loader)
        state.loader.load_dataframe(
            pd.DataFrame({
                "order_date": ["2026-01-05", "2026-02-11"],
                "region": ["North", "South"],
                "revenue": [10.0, 20.0],
            }),
            "cov_textdates", if_exists="replace",
        )
        table, date_col, value_col, dim_col = main._detect_best_table()
        assert table == "cov_textdates"
        assert date_col == "order_date"
        assert value_col == "revenue"
        assert dim_col == "region"

    def test_dashboard_text_date_series(self, billing_db, monkeypatch):
        import api.state as state

        monkeypatch.setattr(main, "loader", state.loader)
        state.loader.load_dataframe(
            pd.DataFrame({
                "order_date": ["2026-01-05", "2026-02-11", "2026-02-20"],
                "region": ["North", "South", "North"],
                "revenue": [10.0, 20.0, 5.0],
            }),
            "cov_textseries", if_exists="replace",
        )
        headers, _ = _headers_for("legtxt")

        trend = client.get("/dashboard/revenue-trend", headers=headers).json()
        assert trend["success"] is True
        assert [m[:7] for m in trend["months"]] == ["2026-01", "2026-02"]
        assert trend["revenue"] == [10.0, 25.0]

        growth = client.get("/dashboard/growth", headers=headers).json()
        assert growth["success"] is True
        assert [p[:7] for p in growth["periods"]] == ["2026-01", "2026-02"]
        assert growth["revenue"] == [10.0, 25.0]
        assert growth["growth_pct"][0] is None
        assert growth["growth_pct"][1] == 150.0

    def test_profile_missing_table(self, wired_loader):
        headers, _ = _headers_for("prof")
        assert client.get("/profile/cov_nosuch_xyz", headers=headers).json()["success"] is False

    def test_quality_missing_table(self, wired_loader):
        headers, _ = _headers_for("qual")
        body = client.get("/quality/cov_nosuch_xyz", headers=headers).json()
        assert body["success"] is False


# ── org routes ───────────────────────────────────────────────────────────

class TestOrgHttp:
    def test_org_gated_for_free(self, billing_db):
        headers, _ = _headers_for("orgfree")
        assert client.get("/org/tree", headers=headers).status_code == 403

    def test_department_full_crud(self, billing_db):
        headers, _ = _headers_for("orgd", plan="lifetime")
        created = client.post("/org/departments", json={"name": _tag("dept")},
                              headers=headers)
        assert created.status_code == 200, created.text
        dept_id = created.json()["department"]["id"]

        assert any(d["id"] == dept_id
                   for d in client.get("/org/departments", headers=headers).json()["departments"])
        assert client.get(f"/org/departments/{dept_id}", headers=headers).json()["success"] is True
        assert client.put(f"/org/departments/{dept_id}",
                           json={"description": "upd"}, headers=headers).json()["success"] is True
        assert client.get("/org/tree", headers=headers).json()["success"] is True
        assert client.delete(f"/org/departments/{dept_id}", headers=headers).json()["success"] is True
        assert client.get(f"/org/departments/{dept_id}", headers=headers).json()["success"] is False

    def test_department_missing_name_422(self, billing_db):
        headers, _ = _headers_for("orgd2", plan="lifetime")
        assert client.post("/org/departments", json={}, headers=headers).status_code == 422

    def test_department_update_delete_missing(self, billing_db):
        headers, _ = _headers_for("orgd3", plan="lifetime")
        assert client.put("/org/departments/nope", json={"name": "x"},
                          headers=headers).json()["success"] is False
        assert client.delete("/org/departments/nope", headers=headers).json()["success"] is False

    def test_team_crud(self, billing_db):
        headers, _ = _headers_for("orgt", plan="lifetime")
        dept_id = client.post("/org/departments", json={"name": _tag("dept")},
                              headers=headers).json()["department"]["id"]
        assert client.post("/org/teams", json={}, headers=headers).status_code == 422
        team_id = client.post("/org/teams",
                              json={"name": _tag("team"), "department_id": dept_id},
                              headers=headers).json()["team"]["id"]
        assert client.get("/org/teams", headers=headers).json()["success"] is True
        assert client.get(f"/org/teams/{team_id}", headers=headers).json()["success"] is True
        assert client.put(f"/org/teams/{team_id}", json={"description": "u"},
                          headers=headers).json()["success"] is True
        assert client.get("/org/teams/nope", headers=headers).json()["success"] is False
        assert client.delete(f"/org/teams/{team_id}", headers=headers).json()["success"] is True
        assert client.delete("/org/teams/nope", headers=headers).json()["success"] is False

    def test_group_crud_and_members(self, billing_db):
        headers, _ = _headers_for("orgg", plan="lifetime")
        _, member_uid = _headers_for("orgmember")
        assert client.post("/org/groups", json={}, headers=headers).status_code == 422
        group_id = client.post("/org/groups", json={"name": _tag("grp")},
                               headers=headers).json()["group"]["id"]
        assert client.get("/org/groups", headers=headers).json()["success"] is True
        assert client.get(f"/org/groups/{group_id}", headers=headers).json()["success"] is True
        assert client.put(f"/org/groups/{group_id}", json={"description": "u"},
                          headers=headers).json()["success"] is True
        assert client.get("/org/groups/nope", headers=headers).json()["success"] is False

        assert client.post(f"/org/groups/{group_id}/members", json={},
                           headers=headers).status_code == 422
        added = client.post(f"/org/groups/{group_id}/members",
                            json={"user_id": member_uid}, headers=headers)
        assert added.json()["success"] is True
        members = client.get(f"/org/groups/{group_id}/members", headers=headers).json()
        assert members["success"] is True and len(members["members"]) == 1
        ugs = client.get(f"/org/users/{member_uid}/groups", headers=headers).json()
        assert ugs["success"] is True and len(ugs["groups"]) == 1
        assert client.delete(f"/org/groups/{group_id}/members/{member_uid}",
                             headers=headers).json()["success"] is True
        assert client.delete(f"/org/groups/{group_id}/members/{member_uid}",
                             headers=headers).json()["success"] is False
        assert client.delete(f"/org/groups/{group_id}", headers=headers).json()["success"] is True
        assert client.delete("/org/groups/nope", headers=headers).json()["success"] is False
        assert client.put("/org/groups/nope", json={"name": "x"},
                          headers=headers).json()["success"] is False


# ── enterprise routes ────────────────────────────────────────────────────

class TestEnterpriseHttp:
    def test_enterprise_gated_for_free(self, billing_db, tmp_path, monkeypatch):
        _isolated_managers(monkeypatch, tmp_path)
        headers, _ = _headers_for("entfree")
        assert client.get("/sla/definitions", headers=headers).status_code == 403
        assert client.get("/cost/summary", headers=headers).status_code == 403
        assert client.post("/cost/budgets", json={"name": "x"},
                           headers=headers).status_code == 403
        assert client.get("/pipeline/health", headers=headers).status_code == 403

    def test_sla_crud(self, billing_db, tmp_path, monkeypatch):
        _isolated_managers(monkeypatch, tmp_path)
        headers, _ = _headers_for("sla", plan="lifetime")
        def_id = client.post("/sla/definitions",
                             json={"name": _tag("sla"), "table_name": "sales"},
                             headers=headers).json()["definition"]["id"]
        assert client.get("/sla/definitions", headers=headers).json()["success"] is True
        assert client.get("/sla/status", headers=headers).json()["success"] is True
        assert client.get("/sla/breaches", headers=headers).json()["success"] is True
        assert client.post(f"/sla/check/{def_id}", headers=headers).json()["success"] is True
        assert client.put(f"/sla/definitions/{def_id}", json={"severity": "critical"},
                          headers=headers).json()["success"] is True
        assert client.delete(f"/sla/definitions/{def_id}", headers=headers).json()["success"] is True

    def test_sla_missing(self, billing_db, tmp_path, monkeypatch):
        _isolated_managers(monkeypatch, tmp_path)
        headers, _ = _headers_for("sla2", plan="lifetime")
        assert client.put("/sla/definitions/nope", json={"severity": "critical"},
                          headers=headers).json()["success"] is False
        assert client.delete("/sla/definitions/nope", headers=headers).json()["success"] is False
        assert client.post("/sla/check/nope", headers=headers).json()["success"] is False

    def test_cost_crud(self, billing_db, tmp_path, monkeypatch):
        _isolated_managers(monkeypatch, tmp_path)
        headers, uid = _headers_for("cost", plan="lifetime")
        rec = client.post("/cost/record",
                          json={"user_id": uid, "query_text": "SELECT 1",
                                "duration_ms": 5.0}, headers=headers)
        assert rec.json()["success"] is True
        assert client.get("/cost/queries", headers=headers).json()["total"] >= 1
        assert client.get("/cost/summary", headers=headers).json()["success"] is True
        budget_id = client.post("/cost/budgets", json={"name": _tag("b")},
                                headers=headers).json()["budget"]["id"]
        assert client.get("/cost/budgets", headers=headers).json()["success"] is True
        assert client.get("/cost/budgets/status", headers=headers).json()["success"] is True
        assert client.put(f"/cost/budgets/{budget_id}", json={"limit_amount": 9.0},
                          headers=headers).json()["success"] is True
        assert client.delete(f"/cost/budgets/{budget_id}", headers=headers).json()["success"] is True
        assert client.put("/cost/budgets/nope", json={"limit_amount": 1.0},
                          headers=headers).json()["success"] is False
        assert client.delete("/cost/budgets/nope", headers=headers).json()["success"] is False

    def test_pipeline_endpoints(self, billing_db, tmp_path, monkeypatch):
        _isolated_managers(monkeypatch, tmp_path)
        headers, _ = _headers_for("pipe", plan="lifetime")
        assert client.get("/pipeline/health", headers=headers).status_code == 200
        assert client.get("/pipeline/runs", headers=headers).status_code == 200
        # start_pipeline_run uses DELETE+INSERT (upsert-free write) so the
        # heartbeat start branch records a run and reports success.
        started = client.post("/pipeline/run/heartbeat",
                              json={"pipeline_name": "covpipe"}, headers=headers).json()
        assert started["success"] is True and started.get("run_id")
        finished = client.post("/pipeline/run/heartbeat",
                               json={"run_id": "cov-noop-run", "status": "success",
                                     "rows_processed": 3},
                               headers=headers).json()
        assert finished["success"] is True
        listed = client.get("/pipeline/runs", headers=headers).json()
        assert listed["total"] >= 1
        assert any(r["pipeline_name"] == "covpipe" for r in listed["runs"])


# ── dashboards CRUD + dashboard export jobs ──────────────────────────────

class TestDashboardsHttp:
    def test_dashboard_crud(self, billing_db, dash_conn):
        headers, _ = _headers_for("dash", plan="lifetime")
        assert client.get("/dashboards", headers=headers).json() == {"success": True, "dashboards": []}
        created = client.post("/dashboards", json={"name": _tag("dash")},
                              headers=headers)
        assert created.status_code == 200, created.text
        dash_id = created.json()["dashboard"]["id"]
        assert client.get(f"/dashboards/{dash_id}", headers=headers).json()["success"] is True
        assert client.get("/dashboards/nope", headers=headers).status_code == 404
        upd = client.put(f"/dashboards/{dash_id}", json={"name": "renamed"},
                         headers=headers)
        assert upd.json()["dashboard"]["name"] == "renamed"
        assert client.put("/dashboards/nope", json={"name": "x"},
                          headers=headers).status_code == 404
        assert client.delete(f"/dashboards/{dash_id}", headers=headers).json() == {"success": True}
        assert client.delete("/dashboards/nope", headers=headers).status_code == 404

    def test_dashboard_export_errors(self, billing_db, dash_conn):
        headers, _ = _headers_for("dashe", plan="lifetime")
        bad = client.post("/dashboards/nope/export/bogus", headers=headers)
        assert bad.status_code in (400, 403)
        missing = client.post("/dashboards/nope/export/pdf", headers=headers)
        assert missing.status_code == 404
        assert client.get("/dashboards/export/nojob", headers=headers).status_code == 404
        assert client.get("/dashboards/export/download/nojob", headers=headers).status_code == 404
        assert client.post("/dashboards/nope/share", headers=headers).status_code == 404
        assert client.get("/dashboards/shared/bogus", headers=headers).status_code == 404

    def test_dashboard_export_gated_for_free(self, billing_db, dash_conn):
        headers, _ = _headers_for("dashfree")
        assert client.get("/dashboards/export/nojob", headers=headers).status_code == 403


# ── /export/* files ──────────────────────────────────────────────────────

class TestFileExports:
    @pytest.mark.parametrize("path", ["csv", "parquet", "xlsx", "markdown", "html", "pptx", "pdf"])
    def test_export_gated_for_free(self, billing_db, path):
        headers, _ = _headers_for(f"expfree{path}")
        assert client.get(f"/export/{path}/cov_nosuch", headers=headers).status_code == 403

    def test_export_zip_gated_for_free(self, billing_db):
        headers, _ = _headers_for("expfreezip")
        assert client.get("/export/zip", headers=headers).status_code == 403

    @pytest.mark.parametrize("path", ["csv", "parquet", "xlsx", "markdown", "html", "pptx", "pdf"])
    def test_export_missing_table_404(self, billing_db, path, wired_loader):
        headers, _ = _headers_for(f"exp404{path}", plan="lifetime")
        assert client.get(f"/export/{path}/cov_nosuch_xyz", headers=headers).status_code == 404

    def test_export_zip_unknown_table_400(self, billing_db, wired_loader):
        headers, _ = _headers_for("expzip400", plan="lifetime")
        r = client.get("/export/zip?tables=cov_nosuch_xyz", headers=headers)
        assert r.status_code == 400

    def test_export_csv_success(self, wired_loader):
        wired_loader.load_dataframe(pd.DataFrame({"a": [1, 2]}),
                                    "covexp_t", if_exists="replace")
        headers, _ = _headers_for("expcsv", plan="lifetime")
        r = client.get("/export/csv/covexp_t", headers=headers)
        assert r.status_code == 200
        assert "a" in r.text

    def test_export_zip_success(self, wired_loader):
        wired_loader.load_dataframe(pd.DataFrame({"a": [1]}),
                                    "covzip_t", if_exists="replace")
        headers, _ = _headers_for("expzip", plan="lifetime")
        r = client.get("/export/zip?tables=covzip_t", headers=headers)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"


# ── api/dependencies.py ──────────────────────────────────────────────────

def _user(role="admin", **kw):
    from api.middleware.auth_middleware import User

    kw.setdefault("user_id", f"u_{role}_{uuid.uuid4().hex[:4]}")
    kw.setdefault("username", "covdep")
    kw.setdefault("plan", "free")
    return User(role=role, **kw)


class TestDependencies:
    def test_get_current_user_401(self):
        with pytest.raises(Exception) as exc:
            asyncio.run(main.__dict__["get_current_user"](
                Request({"type": "http", "method": "GET", "path": "/",
                         "headers": []})))
        assert getattr(exc.value, "status_code", 401) == 401

    def test_get_current_user_ok(self):
        from api.dependencies import get_current_user

        req = Request({"type": "http", "method": "GET", "path": "/",
                       "headers": []})
        req.state.user = _user()
        assert asyncio.run(get_current_user(req)).role == "admin"

    def test_get_optional_user(self):
        from api.dependencies import get_optional_user

        req = Request({"type": "http", "method": "GET", "path": "/",
                       "headers": []})
        assert asyncio.run(get_optional_user(req)) is None

    def test_get_admin_user(self):
        from api.dependencies import get_admin_user

        assert asyncio.run(get_admin_user(_user())).role == "admin"
        with pytest.raises(Exception) as exc:
            asyncio.run(get_admin_user(_user(role="viewer")))
        assert exc.value.status_code == 403

    def test_require_permission(self):
        from api.dependencies import require_permission
        from api.rbac import Permission

        # require_permission returns the checker coroutine (not a Depends
        # object) so it composes correctly as Depends(require_permission(...)).
        checker = require_permission(Permission.DATA_READ)
        assert asyncio.run(checker(_user())) is not None
        scoped = _user(role="nobody", scopes={Permission.DATA_READ.value})
        assert asyncio.run(checker(scoped)).username == "covdep"
        with pytest.raises(Exception) as exc:
            asyncio.run(checker(_user(role="nobody")))
        assert exc.value.status_code == 403

    def test_require_role(self):
        from api.dependencies import require_role

        checker = require_role("admin").dependency
        assert asyncio.run(checker(_user())) is not None
        with pytest.raises(Exception) as exc:
            asyncio.run(checker(_user(role="viewer")))
        assert exc.value.status_code == 403

    def test_require_scope(self):
        from api.dependencies import require_scope

        checker = require_scope("whatever").dependency
        assert asyncio.run(checker(_user())) is not None
        with pytest.raises(Exception) as exc:
            asyncio.run(checker(_user(role="viewer")))
        assert exc.value.status_code == 403

    def test_resource_guards(self):
        from api.dependencies import (require_audit_read, require_data_read,
                                      require_data_write, require_export)

        admin = _user()
        for dep in (require_data_read, require_data_write, require_export,
                    require_audit_read):
            assert asyncio.run(dep(admin)) is admin

    def test_tenant(self):
        from api.dependencies import get_tenant_id, require_tenant

        assert asyncio.run(get_tenant_id(_user(tenant_id="t9"))) == "t9"
        assert asyncio.run(require_tenant("t9")) == "t9"
        with pytest.raises(Exception) as exc:
            asyncio.run(require_tenant(None))
        assert exc.value.status_code == 400

    def test_request_helpers(self):
        from api.dependencies import get_ip_address, get_request_id, get_user_agent

        assert get_request_id(Request({"type": "http", "headers": []})) == ""
        assert get_request_id(Request({"type": "http", "headers": [
            (b"x-request-id", b"r1")]})) == "r1"
        fwd = Request({"type": "http", "headers": [(b"x-forwarded-for", b"1.2.3.4")],
                       "client": ("127.0.0.1", 1)})
        assert get_ip_address(fwd) == "1.2.3.4"
        direct = Request({"type": "http", "headers": [(b"x-forwarded-for", b"1.2.3.4")],
                          "client": ("9.9.9.9", 1)})
        assert get_ip_address(direct) == "9.9.9.9"
        assert get_ip_address(Request({"type": "http", "headers": []})) == "unknown"
        ua = Request({"type": "http", "headers": [(b"user-agent", b"cov")]})
        assert get_user_agent(ua) == "cov"

    def test_audit_helpers(self, monkeypatch):
        import api.dependencies as deps

        calls = []
        monkeypatch.setattr(deps, "audit_logger", SimpleNamespace(
            log_query=lambda **kw: calls.append(("q", kw)),
            log_data_access=lambda **kw: calls.append(("d", kw))))
        asyncio.run(deps.audit_query("SELECT 1", 5, 1.5, _user()))
        asyncio.run(deps.audit_query("SELECT 1", 0, 0.5, _user(), error="boom"))
        asyncio.run(deps.audit_data_access("t", ["a"], 3, _user()))
        assert [c[0] for c in calls] == ["q", "q", "d"]

    def test_manager_getters(self):
        import api.dependencies as deps

        for fn in (deps.get_role_mgr, deps.get_audit_mgr, deps.get_rls_mgr,
                   deps.get_session_mgr, deps.get_api_key_mgr, deps.get_sso_mgr,
                   deps.get_org_mgr):
            assert fn() is not None

    def test_user_groups(self, monkeypatch):
        import api.dependencies as deps

        assert asyncio.run(deps.get_current_user_groups(_user(group_ids=["g1"]))) == ["g1"]
        monkeypatch.setattr(deps, "get_org_manager",
                            lambda: SimpleNamespace(
                                get_user_groups=lambda _u: [{"id": "g9"}]))
        assert asyncio.run(deps.get_current_user_groups(_user())) == ["g9"]

        def _boom():
            raise RuntimeError("down")

        monkeypatch.setattr(deps, "get_org_manager", _boom)
        assert asyncio.run(deps.get_current_user_groups(_user())) == []

    def test_org_catalog_guards(self):
        from api.dependencies import (require_catalog_manage,
                                      require_catalog_read,
                                      require_dashboard_manage,
                                      require_notification_manage,
                                      require_org_manage)

        admin = _user()
        for dep in (require_org_manage, require_catalog_read,
                    require_catalog_manage, require_dashboard_manage,
                    require_notification_manage):
            assert asyncio.run(dep(admin)) is admin
        for dep in (require_org_manage, require_catalog_read,
                    require_catalog_manage, require_dashboard_manage,
                    require_notification_manage):
            with pytest.raises(Exception) as exc:
                asyncio.run(dep(_user(role="nobody")))
            assert exc.value.status_code == 403


# ── main.py pure helpers ─────────────────────────────────────────────────

class TestMainHelpers:
    def test_normalize_table_names(self):
        assert main._normalize_table_names(
            ["analytics.sales", '"quoted"', "a.b.c", "", "sales", "sales "]
        ) == ["sales", "quoted", "c"]

    def test_scope_tables_pins_preferred_and_caps(self):
        tables = ["support_tickets", "sales_data"]
        out = main._scope_tables_for_question(
            tables, "how many tickets?", preferred=["sales_data"])
        assert out[0] == "sales_data"
        big = [f"t{i:02d}" for i in range(60)]
        assert len(main._scope_tables_for_question(big, "")) == 40
        assert main._scope_tables_for_question([], "") == []

    def test_q_and_norm_col(self):
        assert main._q('a"b') == '"a""b"'
        assert main._norm_col("Total Sales") == "totalsales"

    def test_scope_sql_tables(self, billing_db, monkeypatch):
        assert main._scope_sql_tables("q", ["t1", "t2"]) == ["t1", "t2"]
        monkeypatch.setattr(main, "loader", None)
        monkeypatch.setattr(main, "orchestrator", None)
        assert main._scope_sql_tables("q", []) is None

    def test_refresh_planner_noop_without_orchestrator(self, monkeypatch):
        monkeypatch.setattr(main, "orchestrator", None)
        assert main._refresh_planner_schema(["t"]) is None

    def test_no_user_metering_ip_llm(self, billing_db, monkeypatch):
        monkeypatch.setattr(main, "orchestrator", None)
        req = Request({"type": "http", "method": "GET", "path": "/",
                       "headers": []})
        assert main._meter_ai_request(req) is None
        assert main._set_request_ip_rotation(req) is None
        assert main._llm_available(force=True) is False

    def test_question_request_properties(self):
        from api.main import QuestionRequest

        assert QuestionRequest(question="q", table="t").resolved_table == "t"
        assert QuestionRequest(question="q", context={"table": "c"}).resolved_table == "c"
        assert QuestionRequest(question="q").resolved_table is None
        assert QuestionRequest(question="q", tables=["a"]).resolved_tables == ["a"]
        assert QuestionRequest(question="q", context={"tables": ["b"]}).resolved_tables == ["b"]
        assert QuestionRequest(question="q", table="t").resolved_tables == ["t"]
        assert QuestionRequest(question="q").resolved_tables is None
        assert QuestionRequest(question="q", context={"session_id": "s"}).request_session_id == "s"
        assert QuestionRequest(question="q").request_session_id is None
        assert QuestionRequest(question="q", context={"history": [{"a": "b"}]}).history == [{"a": "b"}]
        assert QuestionRequest(question="q").history == []

    def test_upload_response_schema_prop(self):
        from api.main import UploadResponse

        assert UploadResponse(success=True, filename="f",
                              schema={"a": 1}).schema == {"a": 1}

    def test_build_ask_response(self, billing_db, monkeypatch):
        monkeypatch.setattr(main, "orchestrator", None)
        from api.main import QuestionRequest

        df = pd.DataFrame({"a": [1, 2]})

        class Fig:
            def to_json(self):
                return '{"data": []}'

        viz_ok = SimpleNamespace(success=True, config={"chart_type": "bar"},
                                 html="<div/>", figure=Fig())
        viz_bad = SimpleNamespace(success=False)
        plan = SimpleNamespace(steps=[
            SimpleNamespace(step_id=1, params={"query": "SELECT  a FROM t"}),
            SimpleNamespace(step_id=2, params={"query": "SELECT a FROM t"}),
            SimpleNamespace(step_id=3, params={"query": "SELECT b FROM t"}),
        ])
        s1 = SimpleNamespace(step_id=1, tool=SimpleNamespace(value="sql"),
                             success=True, execution_time_ms=2.0, error=None,
                             metadata={"k": 1},
                             result=SimpleNamespace(data=df))
        s2 = SimpleNamespace(step_id=2, tool=SimpleNamespace(value="sql"),
                             success=True, execution_time_ms=1.0, error=None,
                             metadata=None, result=None)
        s3 = SimpleNamespace(step_id=3, tool=SimpleNamespace(value="python"),
                             success=False, execution_time_ms=1.0,
                             error="boom", metadata=None, result=None)
        resp = SimpleNamespace(success=True, visualizations=[viz_ok, viz_bad],
                               step_results=[s1, s2, s3], plan=plan,
                               final_answer="done", total_time_ms=5.0, error=None)
        out = main._build_ask_response(QuestionRequest(question="q"), resp, "sess-1")
        assert out["success"] is True
        assert len(out["visualizations"]) == 1
        assert out["visualizations"][0]["spec"] == {"data": []}
        assert [s["step_id"] for s in out["steps"]] == [1, 3]
        assert out["data_preview"]["shape"] == [2, 1]
        assert out["session_id"] == "sess-1"
        assert "rule-based fallback" in out["answer"]
        assert out["llm_used"] is False

    def test_detect_best_table_empty(self, monkeypatch):
        monkeypatch.setattr(main, "loader", SimpleNamespace(list_tables=lambda: []))
        assert main._detect_best_table() == (None, None, None, None)

    def test_ensure_user_idempotent(self):
        ensure_user("cov_dup_user", "Str0ng!Passw0rd")
        ensure_user("cov_dup_user", "Str0ng!Passw0rd")
        from api.auth import user_manager

        assert user_manager.get_user_info("cov_dup_user")
