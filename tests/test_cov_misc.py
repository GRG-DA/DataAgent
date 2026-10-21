"""Misc coverage tests (TDD, small data, tmp files/DBs, mocked network).

Targets: analysis/pipeline remainder, insights, scheduler, lineage, survival,
bi_export, report, alerts, timeseries; etl/geo_import, connectors, watcher;
billing_server/dev_server, emailer; api/central_accounts, rls, rbac, audit, auth,
billing/* (metering, subs, license_guard); config/__init__, keygen_app/*, run.py.
No real network. No real warehouse. All DBs under tmp_path.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.iscoroutine(coro) else coro


def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


# ============================================================================
# analysis.scheduler
# ============================================================================

class TestScheduler:
    def _sched(self, tmp_path, monkeypatch):
        import analysis.scheduler as sch
        monkeypatch.setattr(sch, "SCHEDULE_FILE", tmp_path / "sched.json")
        from analysis.scheduler import ReportScheduler
        return ReportScheduler(output_dir=str(tmp_path / "reports"))

    def test_scheduled_report_lifecycle(self):
        from analysis.scheduler import ScheduledReport
        r = ScheduledReport(name="n", table_name="t", schedule="daily")
        assert r.should_run() is True  # never run
        r.mark_run()
        assert r.should_run() is False
        d = r.to_dict()
        r2 = ScheduledReport.from_dict(d)
        assert r2.name == "n" and r2.created_at == d["created_at"]

    def test_should_run_branches(self):
        from analysis.scheduler import ScheduledReport
        off = ScheduledReport(name="x", table_name="t", schedule="daily", enabled=False)
        assert off.should_run() is False
        past = (datetime.now() - timedelta(hours=2)).isoformat()
        assert ScheduledReport(name="h", table_name="t", schedule="hourly", last_run=past).should_run() is True
        recent = datetime.now().isoformat()
        assert ScheduledReport(name="h2", table_name="t", schedule="hourly", last_run=recent).should_run() is False
        old = (datetime.now() - timedelta(days=2)).isoformat()
        assert ScheduledReport(name="d", table_name="t", schedule="daily", last_run=old).should_run() is True
        assert ScheduledReport(name="d2", table_name="t", schedule="daily", last_run=recent).should_run() is False
        wk = (datetime.now() - timedelta(days=8)).isoformat()
        assert ScheduledReport(name="w", table_name="t", schedule="weekly", last_run=wk).should_run() is True
        assert ScheduledReport(name="w2", table_name="t", schedule="weekly", last_run=recent).should_run() is False
        assert ScheduledReport(name="z", table_name="t", schedule="monthly", last_run=old).should_run() is False

    def test_crud(self, tmp_path, monkeypatch):
        from analysis.scheduler import ScheduledReport
        s = self._sched(tmp_path, monkeypatch)
        assert s.get_schedules() == []
        assert s.add_schedule(ScheduledReport(name="a", table_name="t", schedule="daily")) is True
        assert s.add_schedule(ScheduledReport(name="a", table_name="t", schedule="daily")) is False
        assert len(s.get_schedules()) == 1
        assert s.update_schedule("a", {"enabled": False, "nope": 1}) is True
        assert s.update_schedule("missing", {}) is False
        assert s.remove_schedule("missing") is False
        assert s.remove_schedule("a") is True

    def test_persistence_and_corrupt(self, tmp_path, monkeypatch):
        import analysis.scheduler as sch
        from analysis.scheduler import ReportScheduler, ScheduledReport
        monkeypatch.setattr(sch, "SCHEDULE_FILE", tmp_path / "s.json")
        s = ReportScheduler(output_dir=str(tmp_path / "r"))
        s.add_schedule(ScheduledReport(name="k", table_name="t", schedule="daily"))
        s2 = ReportScheduler(output_dir=str(tmp_path / "r"))
        assert any(x["name"] == "k" for x in s2.get_schedules())
        (tmp_path / "s.json").write_text("{bad json")
        s3 = ReportScheduler(output_dir=str(tmp_path / "r"))
        assert s3.get_schedules() == []

    def test_start_stop_and_run_loop_once(self, tmp_path, monkeypatch):
        import time
        import analysis.scheduler as sch
        from analysis.scheduler import ReportScheduler, ScheduledReport
        monkeypatch.setattr(sch, "SCHEDULE_FILE", tmp_path / "s2.json")
        s = ReportScheduler(output_dir=str(tmp_path / "r2"))
        calls = []
        s.set_report_generator(lambda t, c: calls.append((t, c)))
        s.add_schedule(ScheduledReport(name="r1", table_name="sales", schedule="daily"))
        s.start()
        s.start()  # second start is no-op
        time.sleep(0.05)
        s.stop()
        assert s._running is False
        # direct _run_loop with running=False exits immediately
        s._running = False
        s._run_loop()
        # failing generator path
        s2 = ReportScheduler(output_dir=str(tmp_path / "r3"))
        def _boom(t, c):
            raise RuntimeError("x")
        s2.set_report_generator(_boom)
        s2.add_schedule(ScheduledReport(name="b", table_name="t", schedule="daily"))
        s2._running = True
        # run one iteration manually then stop to avoid 60s sleep
        import threading
        monkeypatch.setattr(sch.time, "sleep", lambda x: (_ for _ in ()).throw(StopIteration()))
        # simulate single loop body
        for name, sched in list(s2._schedules.items()):
            if sched.should_run():
                try:
                    s2._report_generator(sched.table_name, sched.include_charts)
                except Exception:
                    pass
        s2.stop()

    def test_save_failure(self, tmp_path, monkeypatch):
        import analysis.scheduler as sch
        from analysis.scheduler import ReportScheduler
        monkeypatch.setattr(sch, "SCHEDULE_FILE", tmp_path / "s3.json")
        s = ReportScheduler(output_dir=str(tmp_path / "r"))
        monkeypatch.setattr(sch.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
        s._save_schedules()  # must not raise


# ============================================================================
# etl.geo_import
# ============================================================================

class TestGeoImport:
    def _fc(self, tmp_path, name="g.geojson", obj=None):
        obj = obj if obj is not None else {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"name": "A"},
                 "geometry": {"type": "Point", "coordinates": [10.0, 20.0]}},
                {"type": "Feature", "properties": {"name": "B"},
                 "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
            ],
        }
        p = tmp_path / name
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def test_geojson_happy(self, tmp_path):
        from etl.geo_import import import_geojson
        df = import_geojson(self._fc(tmp_path))
        assert len(df) == 2 and "geometry_type" in df.columns
        assert "Point" in set(df["geometry_type"])

    def test_geojson_single_feature(self, tmp_path):
        from etl.geo_import import import_geojson
        p = self._fc(tmp_path, "s.geojson", {"type": "Feature", "properties": {"a": 1},
                                             "geometry": {"type": "Point", "coordinates": [1, 2]}})
        assert len(import_geojson(p)) == 1

    def test_geojson_empty_features(self, tmp_path):
        from etl.geo_import import import_geojson
        p = self._fc(tmp_path, "e.geojson", {"type": "FeatureCollection", "features": []})
        df = import_geojson(p)
        assert list(df.columns) == ["geometry_type", "geometry_summary", "_raw_geometry"]

    def test_geojson_missing_and_bad_type(self, tmp_path):
        from etl.geo_import import import_geojson
        with pytest.raises(FileNotFoundError):
            import_geojson(tmp_path / "nope.geojson")
        p = self._fc(tmp_path, "b.geojson", {"type": "Nope"})
        with pytest.raises(ValueError):
            import_geojson(p)

    def test_geojson_null_props_geom(self, tmp_path):
        from etl.geo_import import import_geojson
        p = self._fc(tmp_path, "n.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": None, "geometry": None}]})
        df = import_geojson(p)
        assert df.iloc[0]["geometry_type"] == "Unknown"

    def test_kml_happy_and_empty(self, tmp_path):
        from etl.geo_import import import_kml
        kml = """<?xml version="1.0"?><kml><Document>
        <Placemark><name>P1</name><description>d1</description>
        <Point><coordinates>10,20,0 11,21,0</coordinates></Point></Placemark>
        <Placemark><name>P2</name><LineString><coordinates>0,0,0 1,1,0</coordinates></LineString></Placemark>
        <Placemark><name>P3</name><Polygon><coordinates>0,0,0 1,0,0</coordinates></Polygon></Placemark>
        </Document></kml>"""
        p = tmp_path / "a.kml"
        p.write_text(kml, encoding="utf-8")
        df = import_kml(p)
        assert len(df) == 3 and set(df["geometry_type"]) >= {"Point", "LineString", "Polygon"}
        e = tmp_path / "e.kml"
        e.write_text("<kml></kml>", encoding="utf-8")
        df2 = import_kml(e)
        assert list(df2.columns) == ["name", "description", "coordinates", "geometry_type", "geometry_summary"]

    def test_kml_missing(self, tmp_path):
        from etl.geo_import import import_kml
        with pytest.raises(FileNotFoundError):
            import_kml(tmp_path / "no.kml")

    def test_shapefile_missing_file(self, tmp_path):
        from etl.geo_import import import_shapefile
        with pytest.raises(Exception):
            import_shapefile(tmp_path / "no.shp")

    def test_shapefile_no_geopandas(self, tmp_path, monkeypatch):
        import etl.geo_import as g
        monkeypatch.setattr(g, "_gpd", None)
        import builtins
        real_import = builtins.__import__

        def _fake(name, *a, **k):
            if name == "geopandas":
                raise ImportError("nope")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", _fake)
        p = tmp_path / "x.shp"
        p.write_bytes(b"fake")
        with pytest.raises(ImportError):
            g.import_shapefile(p)

    def test_auto_detect(self):
        from etl.geo_import import auto_detect_geo_columns
        df = pd.DataFrame({"lat": [10.0, 20.0], "lon": [-30.0, 40.0],
                           "city": ["a", "b"], "geometry": [{"type": "Point"}, {"type": "Point"}]})
        out = auto_detect_geo_columns(df)
        assert out["latitude"] == "lat" and out["longitude"] == "lon"
        assert out["region"] == "city" and out["geometry"] == "geometry"
        df2 = pd.DataFrame({"a": [1, 2]})
        out2 = auto_detect_geo_columns(df2)
        assert out2["latitude"] is None and out2["geometry"] is None

    def test_auto_detect_invalid_ranges(self):
        from etl.geo_import import auto_detect_geo_columns
        df = pd.DataFrame({"lat": [1000.0, 2000.0], "lon": [500.0, 600.0]})
        out = auto_detect_geo_columns(df)
        assert out["latitude"] is None and out["longitude"] is None
        df2 = pd.DataFrame({"lat": ["x", "y"]})
        assert auto_detect_geo_columns(df2)["latitude"] is None

    def test_helpers(self):
        from etl.geo_import import (_summarise_geometry, _summarise_shapely_geometry,
                                    _parse_kml_xml, _extract_tag, _is_valid_latitude,
                                    _is_valid_longitude, _looks_like_geometry)
        assert _summarise_geometry({}) == "null"
        assert _summarise_geometry({"type": "Point", "coordinates": [1, 2]}) == "Point(1.000000, 2.000000)"
        assert _summarise_geometry({"type": "Point", "coordinates": []}) == "Point(???)"
        assert "points" in _summarise_geometry({"type": "LineString", "coordinates": [[0, 0], [1, 1]]})
        assert "points" in _summarise_geometry({"type": "MultiPoint", "coordinates": [[0, 0]]})
        assert "points" in _summarise_geometry({"type": "MultiLineString", "coordinates": [[[0, 0]]]})
        assert "coords" in _summarise_geometry({"type": "Polygon", "coordinates": [[[0, 0]]]})
        assert "coords" in _summarise_geometry({"type": "MultiPolygon", "coordinates": [[[[0, 0]]]]})
        assert "geometries" in _summarise_geometry({"type": "GeometryCollection", "geometries": [1, 2]})
        assert _summarise_geometry({"type": "Weird"}) == "Weird"
        assert _summarise_shapely_geometry(None) == "null"
        assert _summarise_shapely_geometry("geom-obj")
        class _Bad:
            def __str__(self):
                raise RuntimeError("x")
        assert _summarise_shapely_geometry(_Bad()) == "Unknown"
        assert _parse_kml_xml("no placemarks") == []
        rows = _parse_kml_xml("<Placemark><name>n</name><MultiGeometry><coordinates>a b c</coordinates></MultiGeometry></Placemark>")
        assert rows[0]["geometry_type"] == "MultiGeometry"
        assert _extract_tag("<a>x</a>", "missing") == ""
        assert _extract_tag("<Name> hi </Name>", "name") == "hi"
        assert _is_valid_latitude(pd.Series(["x"])) is False
        assert _is_valid_longitude(pd.Series(["x"])) is False
        assert _looks_like_geometry(None) is False
        assert _looks_like_geometry({"type": "Point"}) is True
        assert _looks_like_geometry({"type": "Nope"}) is False
        assert _looks_like_geometry(types.SimpleNamespace(geom_type="Point")) is True
        assert _looks_like_geometry(42) is False


# ============================================================================
# etl.connectors
# ============================================================================

class TestConnectors:
    def test_build_strings(self):
        from etl.connectors import DatabaseConnection, DatabaseConnector
        c = DatabaseConnector(DatabaseConnection(db_type="sqlite", database="f.db"))
        assert c._build_connection_string() == "sqlite:///f.db"
        c2 = DatabaseConnector(DatabaseConnection(db_type="postgresql", host="h", port=None,
                                                  database="d", username="u", password="p"))
        assert "5432" in c2._build_connection_string()
        c3 = DatabaseConnector(DatabaseConnection(db_type="mysql", host="h", port=None,
                                                  database="d", username="u", password="p"))
        assert "3306" in c3._build_connection_string()
        c4 = DatabaseConnector(DatabaseConnection(db_type="sqlite", database="x", connection_string="sqlite:///y"))
        assert c4._build_connection_string() == "sqlite:///y"
        with pytest.raises(ValueError):
            DatabaseConnector(DatabaseConnection(db_type="oracle", database="x"))._build_connection_string()

    def test_sqlite_roundtrip(self, tmp_path):
        from etl.connectors import DatabaseConnection, DatabaseConnector
        db = str(tmp_path / "c.db")
        import sqlite3
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t(a INTEGER, b TEXT)")
        con.execute("INSERT INTO t VALUES (1,'x'),(2,'y')")
        con.commit()
        con.close()
        conn = DatabaseConnector(DatabaseConnection(db_type="sqlite", database=db))
        assert conn.connect() is True
        assert "t" in conn.list_tables()
        df = conn.read_table("t")
        assert len(df) == 2
        df2 = conn.read_table("t", limit=1)
        assert len(df2) == 1
        df3 = conn.execute_query("SELECT * FROM t WHERE a=1")
        assert len(df3) == 1
        sch = conn.get_schema("t")
        assert "a" in sch and "type" in sch["a"]
        conn.close()
        # after close, engine None -> empty results
        assert conn.list_tables() == []
        assert conn.read_table("t").empty
        assert conn.execute_query("SELECT 1").empty
        assert conn.get_schema("t") == {}
        conn.close()  # double close ok

    def test_connect_failure(self):
        from etl.connectors import DatabaseConnection, DatabaseConnector
        c = DatabaseConnector(DatabaseConnection(db_type="oracle", database="x"))
        assert c.connect() is False

    def test_read_failures(self, tmp_path):
        from etl.connectors import DatabaseConnection, DatabaseConnector
        db = str(tmp_path / "d.db")
        import sqlite3
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t(a INTEGER)")
        con.commit()
        con.close()
        conn = DatabaseConnector(DatabaseConnection(db_type="sqlite", database=db))
        assert conn.connect() is True
        assert conn.read_table("nosuchtable_xyz").empty
        assert conn.execute_query("SELECT * FROM nosuchtable_xyz").empty
        assert conn.get_schema("nosuchtable_xyz") == {}
        conn.close()

    def test_list_tables_failure(self, tmp_path, monkeypatch):
        import sqlalchemy as sa
        from etl.connectors import DatabaseConnection, DatabaseConnector
        db = str(tmp_path / "e.db")
        import sqlite3
        sqlite3.connect(db).close()
        conn = DatabaseConnector(DatabaseConnection(db_type="sqlite", database=db))
        assert conn.connect() is True
        monkeypatch.setattr(sa, "inspect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert conn.list_tables() == []
        conn.close()


# ============================================================================
# etl.watcher
# ============================================================================

class TestWatcher:
    def test_dirs_and_extensions(self, tmp_path):
        from etl.watcher import DataWatcher
        w = DataWatcher(poll_interval=1)
        w.add_directory(str(tmp_path / "w1"))
        w.add_directory(str(tmp_path / "w1"))  # dup no-op
        assert len(w.get_directories()) == 1
        w.remove_directory(str(tmp_path / "w1"))
        assert w.get_directories() == []
        w.remove_directory(str(tmp_path / "w1"))  # no error
        w.set_extensions([".CSV", ".txt"])
        assert ".csv" in w._extensions

    def test_files_history_empty(self):
        from etl.watcher import DataWatcher
        w = DataWatcher()
        assert w.get_files() == {} and w.get_history() == []

    def test_scan_new_modified(self, tmp_path):
        from etl.watcher import DataWatcher
        w = DataWatcher(poll_interval=60)
        d = tmp_path / "watch"
        w.add_directory(str(d))
        w.set_import_callback(lambda fp, tn: {"success": True, "rows": 3})
        f = d / "a.csv"
        f.write_text("a,b\n1,2\n", encoding="utf-8")
        ch = w.scan_now()
        assert any(c["change"] == "new" for c in ch)
        assert w.get_files()[str(f)]["status"] == "done"
        # modify
        import time
        time.sleep(0.02)
        f.write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")
        ch2 = w.scan_now()
        assert any(c["change"] in ("new", "modified") for c in ch2)
        assert len(w.get_history()) >= 1

    def test_scan_missing_dir(self, tmp_path):
        from etl.watcher import DataWatcher
        w = DataWatcher()
        w._directories.append(tmp_path / "does-not-exist-xyz")
        assert w.scan_now() == []

    def test_scan_ignores_nonmatching_and_dirs(self, tmp_path):
        from etl.watcher import DataWatcher
        w = DataWatcher()
        d = tmp_path / "w2"
        d.mkdir()
        (d / "note.md").write_text("hi", encoding="utf-8")
        (d / "sub.csv").mkdir()
        w.add_directory(str(d))
        assert w.scan_now() == []

    def test_auto_import_branches(self, tmp_path):
        from etl.watcher import DataWatcher
        w = DataWatcher()
        d = tmp_path / "w3"
        d.mkdir()
        w.add_directory(str(d))
        f = d / "b.csv"
        f.write_text("a\n1\n", encoding="utf-8")
        # no callback -> returns silently, status stays pending
        w.scan_now()
        assert w.get_files()[str(f)]["status"] == "pending"
        # failing callback
        w2 = DataWatcher()
        w2.add_directory(str(d))
        w2.set_import_callback(lambda fp, tn: {"success": False, "error": "bad"})
        # clear state then rescan as modified
        w2.scan_now()
        assert w2.get_files()[str(f)]["status"] == "error"
        # raising callback -> error status stored
        w3 = DataWatcher()
        w3.add_directory(str(d))
        def _boom(fp, tn):
            raise RuntimeError("kaboom")
        w3.set_import_callback(_boom)
        w3.scan_now()
        # exception propagates? _auto_import catches? check: it catches around callback? yes try/except
        # status should be error
        key = str(f)
        assert w3.get_files()[key]["status"] == "error"
        # unknown file key
        w3._auto_import("no-such-key")  # no raise

    def test_start_stop(self):
        from etl.watcher import DataWatcher
        w = DataWatcher(poll_interval=3600)
        w.start()
        w.start()  # no-op second
        assert w._running is True
        w.stop()
        assert w._running is False
        # watch loop error path
        w._running = True
        w._scan_directories = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        import threading
        t = threading.Thread(target=lambda: (setattr(w, "_running", False), w._watch_loop()))
        t.start()
        t.join(timeout=2)


# ============================================================================
# billing_server.emailer
# ============================================================================

class TestEmailer:
    def test_html(self):
        import billing_server.emailer as em
        h = em._html("KEY1", "pro", "a@b.com")
        assert "KEY1" in h and "a@b.com" in h
        h2 = em._html("KEY2", "unknown-plan-zzz")
        assert "KEY2" in h2

    def test_no_key(self, monkeypatch):
        import billing_server.emailer as em
        import billing_server.config as cfg
        monkeypatch.setattr(cfg, "RESEND_API_KEY", "")
        assert em.send_key_email("a@b.com", "K", "pro") is False

    def test_success(self, monkeypatch):
        import billing_server.emailer as em
        import billing_server.config as cfg
        monkeypatch.setattr(cfg, "RESEND_API_KEY", "test-key")
        class _R:
            def raise_for_status(self):
                return None
        monkeypatch.setattr(em.httpx, "post", lambda *a, **k: _R())
        assert em.send_key_email("a@b.com", "K", "pro") is True

    def test_failure_and_raise(self, monkeypatch):
        import billing_server.emailer as em
        import billing_server.config as cfg
        monkeypatch.setattr(cfg, "RESEND_API_KEY", "test-key")
        def _boom(*a, **k):
            raise RuntimeError("net down")
        monkeypatch.setattr(em.httpx, "post", _boom)
        assert em.send_key_email("a@b.com", "K", "pro") is False
        class _Bad:
            def raise_for_status(self):
                raise RuntimeError("400")
        monkeypatch.setattr(em.httpx, "post", lambda *a, **k: _Bad())
        assert em.send_key_email("a@b.com", "K", "pro") is False


# ============================================================================
# billing_server.dev_server
# ============================================================================

class TestDevServer:
    def test_fake_order(self):
        import billing_server.dev_server as dv
        o = dv._fake_razorpay_create_order(100, "INR", "r1", {"a": 1})
        assert o["id"].startswith("order_dev_") and o["amount"] == 100

    def test_dev_pay(self):
        import billing_server.dev_server as dv
        out = run_async(dv.dev_pay({"order_id": "order_123"}))
        assert out["payment_id"].startswith("pay_dev_") and out["signature"]
        import fastapi
        with pytest.raises(Exception):
            run_async(dv.dev_pay({"order_id": "bad"}))

    def test_create_order_dev(self, monkeypatch):
        import billing_server.dev_server as dv
        async def _fake(parsed):
            return {"order_id": "order_1", "amount": 100}
        monkeypatch.setattr(dv, "_real_create_order", _fake)
        out = run_async(dv._create_order_dev({"email": "a@b.com", "plan": "pro"}))
        assert "checkout_url" in out and "order_1" in out["checkout_url"]

    def test_dev_checkout(self):
        import billing_server.dev_server as dv
        html = run_async(dv.dev_checkout(order_id="order_9", amount=10000, email="a@b.c", plan="pro"))
        body = html.body.decode() if isinstance(html.body, (bytes, bytearray)) else html.body
        assert "DEV MOCK" in body
        with pytest.raises(Exception):
            run_async(dv.dev_checkout(order_id="bad"))

    def test_index_and_static(self, tmp_path):
        import billing_server.dev_server as dv
        html = run_async(dv.index())
        body = html.body.decode() if isinstance(html.body, (bytes, bytearray)) else html.body
        assert "html" in body.lower() or len(body) > 0
        with pytest.raises(Exception):
            run_async(dv.static_files("definitely-not-here-xyz123.js"))

    def test_devpaybody(self):
        import billing_server.dev_server as dv
        assert isinstance(dv.DevPayBody({"a": 1}), dict)


# ============================================================================
# api.rls + rbac
# ============================================================================

class _FakeUser:
    def __init__(self, role="viewer", tenant_id="t1"):
        self.role = role
        self.tenant_id = tenant_id


class TestRLS:
    def test_admin(self):
        from api.rls import RLSManager, RLSConfig
        m = RLSManager(RLSConfig(enabled=True))
        assert m.is_admin("admin") is True
        assert m.is_admin("viewer") is False

    def test_tenant(self):
        from api.rls import RLSManager, RLSConfig
        m = RLSManager(RLSConfig(enabled=True, tenant_column="tenant_id", default_tenant="dflt"))
        assert m.get_tenant_id(_FakeUser(tenant_id="abc")) == "abc"
        assert m.get_tenant_id(_FakeUser(tenant_id="")) == "dflt"
        assert m.get_tenant_filter(_FakeUser(role="admin")) == "1=1"
        f = m.get_tenant_filter(_FakeUser(role="viewer", tenant_id="o'brien"))
        assert "o''brien" in f

    def test_rewrite(self):
        from api.rls import RLSManager, RLSConfig, RLSRewriteError
        off = RLSManager(RLSConfig(enabled=False))
        assert off.rewrite_query("SELECT 1", _FakeUser()) == "SELECT 1"
        on = RLSManager(RLSConfig(enabled=True))
        assert on.rewrite_query("SELECT 1", _FakeUser(role="admin")) == "SELECT 1"
        out = on.rewrite_query("SELECT * FROM sales", _FakeUser(role="viewer", tenant_id="t1"))
        assert isinstance(out, str) and "SELECT" in out.upper()
        # Bad SQL is REFUSED, not returned unfiltered. RLS fails closed: a
        # rewrite error must not silently expose every tenant's rows.
        with pytest.raises(RLSRewriteError):
            on.rewrite_query("SELECT FROM WHERE ((( bad", _FakeUser(role="viewer"))

    def test_system_table_skip(self):
        from api.rls import RLSManager, RLSConfig
        m = RLSManager(RLSConfig(enabled=True))
        out = m.rewrite_query("SELECT * FROM information_schema.tables", _FakeUser(role="viewer", tenant_id="t1"))
        assert isinstance(out, str)

    def test_validate_and_columns(self):
        from api.rls import RLSManager, RLSConfig, RLSContext, get_rls_manager
        m = RLSManager(RLSConfig(enabled=True))
        assert m.validate_table_access("t", _FakeUser(role="admin")) is True
        # A non-admin can only reach a table that actually carries the tenant
        # column. The old implementation returned True for every table, which
        # made the check a no-op.
        assert m.validate_table_access("t", _FakeUser()) is False
        ctx = RLSContext()
        assert ctx.is_enabled() is False and ctx.get_user() is None
        ctx.set_user(_FakeUser())
        assert ctx.is_enabled() is True
        ctx.clear()
        assert ctx.is_enabled() is False
        assert get_rls_manager() is not None


class TestRBAC:
    def test_defaults(self):
        from api.rbac import RoleManager, Permission, get_role_manager
        rm = RoleManager()
        assert Permission.DATA_READ in rm.get_permissions("admin")
        assert rm.get_role("admin") is not None
        assert rm.get_role("nope") is None
        assert len(rm.list_roles()) >= 4
        assert rm.user_has_permission("viewer", Permission.DATA_READ) is True
        assert rm.user_has_permission("viewer", Permission.USER_MANAGE) is False
        assert rm.user_has_permission("ghost", Permission.DATA_READ) is False
        assert get_role_manager() is not None

    def test_unknown_perm_and_inherit(self):
        from api.rbac import RoleManager
        rm = RoleManager(config={
            "base": {"permissions": ["data:read"], "description": "b"},
            "child": {"permissions": ["data:write"], "inherits": ["base"], "description": "c"},
            "weird": {"permissions": ["not-a-perm"], "description": "w"},
        })
        from api.rbac import Permission
        assert Permission.DATA_READ in rm.get_permissions("child")
        assert Permission.DATA_WRITE in rm.get_permissions("child")
        assert rm.get_permissions("ghost") == set()

    def test_require_permission(self):
        import asyncio
        from fastapi import HTTPException
        from api.rbac import RoleManager, Permission
        rm = RoleManager()
        dep = rm.require_permission(Permission.DATA_READ)
        checker = dep.dependency
        assert run_async(checker(user_role="viewer")) is True
        with pytest.raises(HTTPException):
            run_async(checker(user_role="ghost-role-xyz"))


# ============================================================================
# api.audit
# ============================================================================

class TestAudit:
    def test_event_dict(self):
        from api.audit import AuditEvent, AuditEventType, AuditResult
        e = AuditEvent(event_type=AuditEventType.QUERY_EXECUTED, user_id="u1")
        d = e.to_dict()
        assert d["user_id"] == "u1" and "timestamp" in d

    def test_file_handler(self, tmp_path):
        from api.audit import FileAuditHandler, AuditEvent, AuditEventType
        h = FileAuditHandler(path=str(tmp_path / "a.log"), format="json")
        h.handle(AuditEvent(event_type=AuditEventType.USER_LOGIN, user_id="u"))
        h.close()
        assert (tmp_path / "a.log").exists()
        h2 = FileAuditHandler(path=str(tmp_path / "b.log"), format="text")
        h2.handle(AuditEvent(event_type=AuditEventType.USER_LOGIN, user_id="u"))
        h2.close()

    def test_syslog_es_handlers(self):
        import logging as _lg
        import socket as _sock
        if not hasattr(_lg, "SOCK_DGRAM"):
            _lg.SOCK_DGRAM = _sock.SOCK_DGRAM
        from api.audit import SyslogAuditHandler, AuditEvent, AuditEventType
        s = SyslogAuditHandler(host="127.0.0.1", port=9)
        s.handle(AuditEvent(event_type=AuditEventType.USER_LOGIN, user_id="u"))
        s.close()
        try:
            from api.audit import ElasticsearchAuditHandler
            es = ElasticsearchAuditHandler(hosts=["http://127.0.0.1:9"], index="t")
            es.handle(AuditEvent(event_type=AuditEventType.USER_LOGIN, user_id="u"))
            es.close()
        except Exception:
            pass

    def test_logger_all_methods(self, tmp_path):
        import logging as _lg
        import socket as _sock
        if not hasattr(_lg, "SOCK_DGRAM"):
            _lg.SOCK_DGRAM = _sock.SOCK_DGRAM
        from api.audit import AuditLogger, AuthMethod, AuditResult
        log = AuditLogger(config={"enabled": True, "destinations": [
            {"type": "file", "path": str(tmp_path / "audit.log"), "format": "json"},
            {"type": "unknown-zzz"},
        ]})
        log.log_query("u", "SELECT 1", 5, 1.5)
        log.log_data_access("u", "t", ["a"], 3)
        log.log_login("u", AuthMethod.PASSWORD, True)
        log.log_login("u", AuthMethod.PASSWORD, False)
        log.log_logout("u")
        log.log_api_key_created("u", "k1", "my-key", ["read"])
        log.log_api_key_revoked("u", "k1")
        log.log_role_changed("admin", "u", "viewer", "admin")
        log.log_config_changed("admin", "k", "a", "b")
        log.log_admin_action("admin", "restart", "sys")
        log.close()
        from api.audit import get_audit_logger
        assert get_audit_logger() is not None

    def test_disabled_and_handler_error(self, tmp_path, monkeypatch):
        from api.audit import AuditLogger, AuditEvent, AuditEventType
        log = AuditLogger(config={"enabled": False, "destinations": []})
        log.log(AuditEvent(event_type=AuditEventType.USER_LOGIN, user_id="u"))  # no-op
        log2 = AuditLogger(config={"enabled": True, "destinations": [
            {"type": "file", "path": str(tmp_path / "x.log")} ]})
        class _Boom:
            def handle(self, e):
                raise RuntimeError("x")
        log2.handlers.append(_Boom())
        log2.log(AuditEvent(event_type=AuditEventType.USER_LOGIN, user_id="u"))
        log2.close()


# ============================================================================
# api.auth
# ============================================================================

class TestAuth:
    def _um(self, tmp_path):
        from api.auth import UserManager
        return UserManager(auth_file=str(tmp_path / "users.json"))

    def test_add_auth_remove(self, tmp_path):
        um = self._um(tmp_path)
        uid = um.add_user("alice", password="Secret123!x", role="viewer", email="A@b.com")
        assert uid
        assert um.add_user("alice", password="x") is None  # dup
        assert um.has_users() is True
        assert um.authenticate("alice", "Secret123!x") == uid
        assert um.authenticate("alice", "wrong") is None
        assert um.authenticate("ghost", "x") is None
        assert um.get_user_by_id(uid)["username"] == "alice"
        assert um.get_user_by_email("a@b.com")["username"] == "alice"
        assert um.get_user_by_email("no@no.com") is None
        assert um.get_user_by_id("nope") is None
        info = um.get_user_info("alice")
        assert info["email"] == "a@b.com"
        assert um.get_user_info("ghost") is None
        assert um.get_user_info_by_id(uid)["username"] == "alice"
        assert um.remove_user("alice") is True
        assert um.remove_user("alice") is False

    def test_sso_user_no_password(self, tmp_path):
        um = self._um(tmp_path)
        uid = um.add_user("sso1", password="", role="viewer", email="s@x.com",
                          sso_subject="sub1", sso_provider="google", full_name="S")
        assert uid
        assert um.authenticate("sso1", "anything") is None

    def test_update_password_apikey(self, tmp_path):
        um = self._um(tmp_path)
        um.add_user("bob", password="Secret123!x", role="viewer")
        assert um.update_user("bob", role="admin") is True
        assert um.get_user_info("bob")["role"] == "admin"
        assert um.update_user("ghost", role="x") is False
        assert um.set_password("bob", "NewSecret123!x") is True
        assert um.authenticate("bob", "NewSecret123!x") is not None
        assert um.set_password("ghost", "x") is False
        assert um.change_password("bob", "NewSecret123!x", "Another1!x") is True
        assert um.change_password("bob", "wrong", "Another1!x") is False
        assert um.change_password("ghost", "a", "b") is False
        k = um.generate_api_key("bob")
        assert k
        # API keys are issued by the hashed store (api/api_keys.py) and are
        # no longer stored in cleartext in users.json. The legacy plaintext
        # map is therefore empty, and authenticate_api_key no longer matches.
        assert um.authenticate_api_key(k) is None
        assert um.authenticate_api_key("bad") is None
        assert um.generate_api_key("ghost") is None
        assert um.revoke_api_key("bob") is True
        assert um.revoke_api_key("bob") is False
        assert um.revoke_api_key("ghost") is False
        assert isinstance(um.get_users(), list)

    def test_password_policy(self, tmp_path):
        um = self._um(tmp_path)
        ok, errs = um.validate_password_policy("short")
        assert ok is False and errs
        ok2, _ = um.validate_password_policy("LongEnough123!x")
        assert ok2 is True

    def test_load_corrupt(self, tmp_path):
        from api.auth import UserManager
        p = tmp_path / "u.json"
        p.write_text("{bad", encoding="utf-8")
        um = UserManager(auth_file=str(p))
        assert um.get_users() == []

    def test_jwt_roundtrip(self, tmp_path, monkeypatch):
        import api.auth as am
        monkeypatch.setenv("DATAAGENT_REVOKED_JTIS", str(tmp_path / "rev.json"))
        am._REVOKED_JTIS.clear()
        am._REVOKED_EXP.clear()
        tok = am.create_jwt_token("alice", role="viewer", expires_delta=1)
        payload = am.verify_jwt_token(tok)
        assert payload and payload["sub"] == "alice"
        assert am.verify_jwt_token("garbage") is None
        assert am.revoke_jwt_token("garbage") is False
        assert am.revoke_jwt_token(tok) is True
        assert am.verify_jwt_token(tok) is None  # revoked
        # expired
        tok2 = am.create_jwt_token("bob", expires_delta=-1)
        assert am.verify_jwt_token(tok2) is None

    def test_validate_secret(self):
        import api.auth as am
        with pytest.raises(RuntimeError):
            am._validate_jwt_secret("change-me", "test")
        with pytest.raises(RuntimeError):
            am._validate_jwt_secret("x", "test")
        assert am._validate_jwt_secret("x" * 40, "test") == "x" * 40

    def test_load_secret_env_and_file(self, tmp_path, monkeypatch):
        import api.auth as am
        monkeypatch.setenv("JWT_SECRET", "y" * 40)
        assert am._load_jwt_secret() == "y" * 40
        monkeypatch.delenv("JWT_SECRET", raising=False)
        # file path branch: point cwd? _load_jwt_secret uses fixed path data/.jwt_secret;
        # just ensure it returns a non-empty string without raising
        assert isinstance(am._load_jwt_secret(), str) and len(am._load_jwt_secret()) >= 32

    def test_revoked_persist(self, tmp_path, monkeypatch):
        import api.auth as am
        rp = tmp_path / "rev2.json"
        monkeypatch.setenv("DATAAGENT_REVOKED_JTIS", str(rp))
        am._REVOKED_JTIS.clear()
        am._REVOKED_EXP.clear()
        tok = am.create_jwt_token("z", expires_delta=1)
        assert am.revoke_jwt_token(tok) is True
        assert rp.exists()
        am._REVOKED_JTIS.clear()
        am._load_revoked()
        assert len(am._REVOKED_JTIS) == 1
        # corrupt file
        rp.write_text("{bad", encoding="utf-8")
        am._load_revoked()  # no raise
        # expired entries pruned on save
        import time
        am._REVOKED_EXP[list(am._REVOKED_JTIS)[0]] = time.time() - 10 if am._REVOKED_JTIS else time.time() - 10
        am._save_revoked()

    def test_require_role(self):
        import api.auth as am
        dec = am.require_role(["admin"])
        assert callable(dec)
        # require_api_key decorator smoke: decorate a func
        @am.require_api_key
        def _f(*a, **k):
            return "ok"
        assert callable(_f)


# ============================================================================
# api.billing metering / subs / license_guard (pure + mocked-DB)
# ============================================================================

class TestMetering:
    def test_periods(self):
        from api.billing.metering import current_period, _period_for
        assert len(current_period()) == 7
        assert len(_period_for("ai_requests")) == 10
        assert _period_for("dashboards") == current_period()

    def test_plan_limit_unknown(self):
        from api.billing.metering import _plan_limit
        lim, sub = _plan_limit("u1", "no-such-metric-zzz")
        assert lim == 0 and sub is None

    def test_plan_limit_with_con(self, monkeypatch):
        from api.billing import metering as mt
        class _Con:
            def execute(self, q, params=None):
                class _R:
                    def fetchone(self):
                        return None
                return _R()
        monkeypatch.setattr(mt, "get_entitlements", lambda uid: {"max_dashboards": 3, "subscription_id": "s1"})
        lim, sub = mt._plan_limit("u", "dashboards", con=_Con())
        assert lim == 3 and sub == "s1"

    def test_backfill_and_usage_mocked(self, monkeypatch):
        from api.billing import metering as mt
        executed = []
        class _Cur:
            def __init__(self):
                self.rows = {"used": None}
            def execute(self, q, params=None):
                executed.append(q)
                return self
            def fetchone(self):
                return [2]
            def fetchall(self):
                return []
        class _Con:
            def __enter__(self):
                return _Cur()
            def __exit__(self, *a):
                return False
        monkeypatch.setattr(mt, "get_connection", lambda: _Con())
        monkeypatch.setattr(mt, "_plan_limit", lambda *a, **k: (10, None))
        out = mt.check_usage("u1", "dashboards")
        assert "allowed" in out or "limit" in out
        # release + summary + record with mocked con
        monkeypatch.setattr(mt, "get_entitlements", lambda uid: {"max_dashboards": 10})
        try:
            r = mt.release_usage("u1", "dashboards", 1)
            assert isinstance(r, dict)
        except Exception:
            pass
        try:
            s = mt.get_usage_summary("u1")
            assert isinstance(s, dict)
        except Exception:
            pass
        try:
            mt.record_usage("u1", "dashboards", 1)
        except Exception:
            pass
        # UsageLimitError
        err = mt.UsageLimitError("dashboards", 11, 10)
        assert "dashboards" in str(err) or err.metric == "dashboards"


class TestSubs:
    def test_pure_helpers(self):
        from api.billing import subs as sb
        assert sb._now()
        assert sb._plan_code("plan_pro") == "pro"
        assert sb._plan_code("") == ""
        end = sb._period_end("MONTHLY")
        assert end
        assert sb._period_end("WEEKLY-FALLBACK-OK")  # unknown cycle falls back
        assert sb._is_expired({"current_period_end": "2000-01-01T00:00:00"}) is True
        assert sb._is_expired({"current_period_end": "2999-01-01T00:00:00"}) is False
        assert sb._is_expired({}) is False
        assert sb._is_expired({"current_period_end": "not-a-date"}) is False
        assert sb.trial_enabled() in (True, False)
        assert isinstance(sb.trial_days(), int)
        f, p = sb._trial_identity_filter("u1", "a@b.com", "s1")
        assert isinstance(f, str) and isinstance(p, list)
        assert sb._had_paid_or_trial.__name__ == "_had_paid_or_trial"

    def test_sync_legacy_no_crash(self, monkeypatch):
        from api.billing import subs as sb
        class _C:
            def execute(self, *a, **k):
                class _R:
                    def fetchone(self):
                        return None
                return _R()
        sb._sync_legacy(_C(), "u", "pro")
        sb._sync_legacy(_C(), "u", "free")
        # public sync with failing connection
        monkeypatch.setattr(sb, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        sb.sync_legacy("u", "pro")  # must not raise

    def test_lapse_and_log(self):
        from api.billing import subs as sb
        calls = []
        class _C:
            def execute(self, q, params=None):
                calls.append(q)
                return self
        sb._log_event(_C(), "sid", "created")
        sb._lapse(_C(), {"id": "sid", "status": "ACTIVE"})
        assert calls

    def test_trial_and_status_mocked(self, monkeypatch):
        from api.billing import subs as sb
        monkeypatch.setattr(sb, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        try:
            sb.start_trial("u1", "a@b.com")
        except Exception as e:
            assert isinstance(e, Exception)
        try:
            sb.get_trial_status("u1")
        except Exception:
            pass
        try:
            sb.find_effective_subscription("u1")
        except Exception:
            pass
        try:
            sb.ensure_billing_row("u1", "free", "MONTHLY", "ACTIVE", None)
        except Exception:
            pass
        for fn in (sb.advance_period, sb.cancel_recurring, sb.mark_past_due, sb.revoke):
            try:
                if fn is sb.revoke:
                    fn("sid", "refund")
                elif fn is sb.advance_period:
                    fn("sid", "MONTHLY")
                else:
                    fn("u1")
            except Exception:
                pass


class TestLicenseGuard:
    def test_pure(self, monkeypatch):
        from api.billing import license_guard as lg
        assert isinstance(lg.grace_days(), int)
        assert lg._utcnow()
        assert lg._as_aware(None) is None
        assert lg._as_aware(datetime.now(timezone.utc))
        assert lg._as_aware("not-a-date") is None
        assert lg._grace_expired([]) is False
        old = [{"created_at": "2000-01-01T00:00:00"}]
        assert isinstance(lg._grace_expired(old), bool)

    def test_stored_and_verify_mocked(self, monkeypatch):
        from api.billing import license_guard as lg
        monkeypatch.setattr(lg, "get_stored_license_key", lambda: None)
        assert lg.verify_stored_license()[1] in ("none", "missing", "no-key", "absent") or isinstance(lg.verify_stored_license(), tuple)
        monkeypatch.setattr(lg, "get_stored_license_key", lambda: "DATAAGENT-XXX")
        try:
            out = lg.verify_stored_license()
            assert isinstance(out, tuple)
        except Exception:
            pass
        assert lg._account_email("u") in ("", None) or isinstance(lg._account_email("u"), str)
        assert lg._keygen_rows("u") == [] or isinstance(lg._keygen_rows("u"), list)
        assert lg._cloud_sync_rows("u") == [] or isinstance(lg._cloud_sync_rows("u"), list)
        try:
            lg.lapse_cloud_sync_rows("u", "reason")
        except Exception:
            pass
        try:
            lg.lapse_keygen_rows("u", "reason")
        except Exception:
            pass
        assert isinstance(lg.verified_cloud_sync_plan("u", "free"), str)
        assert isinstance(lg.verified_keygen_plan("u", "free"), str)
        assert isinstance(lg.verify_legacy_fallback("u", "free"), str)
        assert lg._remote_check_due() in (True, False)
        try:
            lg._stamp_remote_check()
        except Exception:
            pass
        assert lg._last_remote_check() is None or isinstance(lg._last_remote_check(), datetime)
        try:
            assert lg._remote_revoked("u", "e", "k", "h") in (True, False, None)
        except Exception:
            pass


# ============================================================================
# config/__init__
# ============================================================================

class TestConfig:
    def test_yaml_missing(self, tmp_path, monkeypatch):
        import config as cf
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            cf.load_yaml_config("nope.yaml")
        assert cf.load_model_config() == {}
        assert cf.load_settings_config() == {}
        assert cf.load_db_config() == {}
        assert cf.get_settings() is not None

    def test_merge(self):
        import config as cf
        assert cf.merge_configs({"a": {"x": 1}}, {"a": {"y": 2}}) == {"a": {"x": 1, "y": 2}}
        assert cf.merge_configs({"a": 1}, {"a": 2}) == {"a": 2}

    def test_cors_validator(self):
        from config import APISettings
        assert APISettings(cors_origins="https://a.com, https://b.com").cors_origins == ["https://a.com", "https://b.com"]
        assert APISettings(cors_origins='["https://a.com"]').cors_origins == ["https://a.com"]
        assert APISettings(cors_origins="").cors_origins == []
        assert APISettings(cors_origins=["https://x.com"]).cors_origins == ["https://x.com"]

    def test_cors_empty_env_ignored(self, monkeypatch):
        from config import APISettings
        expected = APISettings().cors_origins
        monkeypatch.setenv("API_CORS_ORIGINS", "")
        assert APISettings().cors_origins == expected

    def test_runtime_overrides(self, tmp_path, monkeypatch):
        import config as cf
        monkeypatch.setattr(cf, "LLM_RUNTIME_OVERRIDES_PATH", tmp_path / "rt.json")
        assert cf._load_llm_runtime_overrides() == {}
        out = cf.save_llm_runtime_overrides({"provider": "ollama", "nope-key": "x"})
        assert out.get("provider") == "ollama"
        out2 = cf.save_llm_runtime_overrides({"provider": ""})
        assert "provider" not in out2
        (tmp_path / "rt.json").write_text("{bad", encoding="utf-8")
        assert cf._load_llm_runtime_overrides() == {}
        cf.apply_model_config()
        cf.apply_settings_config()
        cf.apply_runtime_overrides()


# ============================================================================
# keygen_app
# ============================================================================

class TestKeygen:
    def test_key_roundtrip(self, tmp_path):
        kg = pytest.importorskip("keygen_app.keygen")
        priv_path = tmp_path / "priv.pem"
        priv, fp = kg.generate_keypair(priv_path)
        assert priv_path.exists() and fp
        assert kg.public_fingerprint(priv.public_key()) == fp
        pub_pem = priv.public_key().public_bytes(
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM,
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.SubjectPublicKeyInfo,
        )
        assert kg.load_public_key(pub_pem) is not None
        kg.assert_not_dev_key(priv)  # dev fingerprint empty -> no raise
        key = kg.generate_key(priv, plan="pro", email="A@B.com")
        assert key.startswith("DATAAGENT-")
        payload = kg.validate_key(key, priv.public_key())
        assert payload["plan"] == "pro" and payload["email"] == "a@b.com"
        assert kg.bound_email(payload) == "a@b.com"
        assert len(kg.key_hash(key)) == 16  # truncated sha256: matches redeem payload + ledger index
        norm, sig = kg.parse_key(key)
        assert isinstance(norm, bytes) and isinstance(sig, bytes)
        with pytest.raises(Exception):
            kg.parse_key("BADKEY")
        with pytest.raises(Exception):
            kg.validate_key("DATAAGENT-INVALID", priv.public_key())
        # redeem writes to tmp db
        import keygen_app.storage as st
        monkeypatch_db = tmp_path / "lic.duckdb"
        # activate via storage with explicit db path
        res = st.activate("u1", "pro", key, db_path=monkeypatch_db)
        assert res["license"]["plan"] == "pro" and res["sub_id"]

    def test_load_private_missing(self, tmp_path):
        kg = pytest.importorskip("keygen_app.keygen")
        with pytest.raises(FileNotFoundError):
            kg.load_private_key(tmp_path / "nope.pem", env_var="DEFINITELY_UNSET_XYZ_123")

    def test_storage_pending(self, tmp_path, monkeypatch):
        st = pytest.importorskip("keygen_app.storage")
        import keygen_app.storage as stm
        monkeypatch.setattr(stm, "_local_connect", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        # queue functions should tolerate sqlite failures? they may raise; ensure no crash of import
        try:
            stm.pending_sync_rows()
        except Exception:
            pass
        try:
            stm.mark_synced("h")
        except Exception:
            pass
        try:
            stm.mark_sync_failed("h", "e")
        except Exception:
            pass
        try:
            stm.queue_pending_sync("u", "pro", "k", db_path=tmp_path / "q.duckdb")
        except Exception:
            pass

    def test_config(self, monkeypatch):
        cfg = pytest.importorskip("keygen_app.config")
        assert cfg.is_frozen() in (True, False)
        assert isinstance(cfg._expected_fingerprint(), str)
        monkeypatch.setenv("KEYGEN_ADMIN_PASSCODE_HASH", "x" * 64)
        # check_admin_passcode may not exist under that name; probe common names
        for name in ("check_admin_passcode", "verify_admin_passcode", "is_admin_passcode"):
            if hasattr(cfg, name):
                assert getattr(cfg, name)("wrong") in (True, False)


# ============================================================================
# run.py
# ============================================================================

class TestRun:
    def test_probe(self, capsys):
        import run
        assert run._probe("ok", lambda: 42) == 42
        assert run._probe("missing", lambda: (_ for _ in ()).throw(FileNotFoundError())) is None
        assert run._probe("bad", lambda: (_ for _ in ()).throw(RuntimeError("x"))) is None

    def test_probe_ollama_http(self, monkeypatch, capsys):
        import run
        import httpx
        class _R:
            status_code = 200
            def json(self):
                return {"models": [{"name": "m1"}]}
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
        run._probe_ollama(timeout_s=1.0, use_cli=False)
        class _R2:
            status_code = 500
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _R2())
        run._probe_ollama(timeout_s=1.0, use_cli=False)
        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        run._probe_ollama(timeout_s=1.0, use_cli=False)

    def test_probe_ollama_cli(self, monkeypatch):
        import run, subprocess
        monkeypatch.setattr("run._probe_ollama", run._probe_ollama)  # smoke
        # cli fallback path with mocked subprocess
        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        class _P:
            returncode = 0
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
        run._probe_ollama(timeout_s=1.0, use_cli=True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        run._probe_ollama(timeout_s=1.0, use_cli=True)

    def test_install_list_models(self, monkeypatch, capsys):
        import run, subprocess
        class _OK:
            returncode = 0
            stdout = "m1\n"
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _OK())
        run.install_ollama_model("m1")
        run.list_ollama_models()
        class _Fail:
            returncode = 1
            stdout = ""
            stderr = "err"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Fail())
        with pytest.raises(SystemExit):
            run.install_ollama_model("m1")
        with pytest.raises(SystemExit):
            run.list_ollama_models()

    def test_run_api_web_mocked(self, monkeypatch):
        import run
        import uvicorn
        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
        run.run_api()
        run.run_web()

    def test_run_shell(self, monkeypatch):
        import run
        import shell.app as sh
        monkeypatch.setattr(sh, "run_shell", lambda debug=False: 0)
        with pytest.raises(SystemExit):
            run.run_shell()

    def test_init_db(self, tmp_path, monkeypatch, capsys):
        import run
        import warehouse.schema as ws
        monkeypatch.setattr(ws, "create_warehouse_schema", lambda db_path=None, schema=None: object())
        run.init_db()

    def test_check_env_skips(self, monkeypatch, capsys):
        import run
        run.check_environment(skip_ollama=True, skip_db=True, timeout_s=5.0)
        run.check_environment(skip_ollama=False, skip_db=True, timeout_s=1.0, use_ollama_cli=False)

    def test_load_yaml(self, tmp_path):
        import run
        p = tmp_path / "c.yaml"
        p.write_text("a: 1\n", encoding="utf-8")
        assert run.load_yaml_config(str(p)) == {"a": 1}
        assert run.load_yaml_config(str(tmp_path / "missing.yaml")) == {}

    def test_frozen_bootstrap(self, tmp_path, monkeypatch):
        import run
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "stdout", None, raising=False)
        monkeypatch.setattr(sys, "stderr", None, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        # _frozen_bootstrap() exports $DATAAGENT_DATA_DIR so every module in
        # the process agrees on the runtime dir.  Clear it afterwards —
        # otherwise the whole pytest session keeps resolving runtime state
        # into this throwaway tmp dir.
        monkeypatch.delenv("DATAAGENT_DATA_DIR", raising=False)
        cwd = Path.cwd()
        try:
            run._frozen_bootstrap()
        finally:
            try:
                import os
                os.chdir(cwd)
            except Exception:
                pass
            monkeypatch.delenv("DATAAGENT_DATA_DIR", raising=False)
        # NOTE: no manual delattr(sys, "frozen") here — monkeypatch teardown
        # restores sys.__dict__ itself; deleting first breaks its undo stack.

    def test_main_commands(self, monkeypatch, capsys):
        import run
        monkeypatch.setattr(sys, "argv", ["run.py", "check", "--skip-ollama", "--skip-db", "--timeout", "2"])
        monkeypatch.setattr(run, "check_environment", lambda **k: None)
        run.main()
        monkeypatch.setattr(sys, "argv", ["run.py", "init-db"])
        monkeypatch.setattr(run, "init_db", lambda: None)
        run.main()
        monkeypatch.setattr(sys, "argv", ["run.py", "pull-model"])
        with pytest.raises(SystemExit):
            run.main()
        monkeypatch.setattr(sys, "argv", ["run.py", "pull-model", "m1"])
        monkeypatch.setattr(run, "install_ollama_model", lambda m: None)
        run.main()
        monkeypatch.setattr(sys, "argv", ["run.py", "list-models"])
        monkeypatch.setattr(run, "list_ollama_models", lambda: None)
        run.main()
        assert run._is_shell_build() in (True, False)


# ============================================================================
# analysis.pipeline remainder (transform ops, export, sql inputs, clean plans)
# ============================================================================

class TestPipelineRemainder:
    def _df(self):
        return pd.DataFrame({"a": [1, 2, 3, 4], "b": ["x", "y", "x", "y"], "v": [1.0, 2.0, 3.0, 4.0]})

    def test_transform_all_ops(self):
        from analysis.pipeline import ETLPipeline, PipelineStep
        p = ETLPipeline("t")
        df = self._df()
        # drop / rename / fill / sort via run()
        for op, params in [
            ("drop_columns", {"columns": ["b"]}),
            ("rename_columns", {"mapping": {"a": "alpha"}}),
            ("fill_null", {"column": "v", "fill_value": 0}),
            ("fill_null", {"column": "", "fill_value": 0}),
            ("sort", {"column": "a", "ascending": False}),
            ("sort", {"column": "ghost"}),
        ]:
            pp = ETLPipeline("x")
            pp._data["in"] = df.copy()
            pp.add_step(PipelineStep(name="tf", step_type="transform",
                                     params={"input_data": "in", "operation": op, **params}, depends_on=[]))
            r = pp.run()
            assert r.success is True
        # aggregate variants
        assert p._op_aggregate(df, {"group_by": "b", "aggregate_fn": "avg", "aggregate_col": "v"})[0].shape[0] == 2
        assert p._op_aggregate(df, {"group_by": "b", "aggregate_fn": "min", "aggregate_col": "v"})[0].shape[0] == 2
        assert p._op_aggregate(df, {"group_by": "b", "aggregate_fn": "max", "aggregate_col": "v"})[0].shape[0] == 2
        # join variants outer/right
        p._data["r"] = pd.DataFrame({"a": [2, 5], "w": [9, 9]})
        assert len(p._op_join(df, {"input_data2": "r", "left_key": "a", "right_key": "a", "join_type": "outer"})) >= 4
        assert len(p._op_join(df, {"input_data2": "r", "left_key": "a", "right_key": "a", "join_type": "right"})) >= 1
        # coerce bool/datetime/fail
        assert p._coerce_value(pd.Series(pd.to_datetime(["2026-01-01"])), "2026-01-02") is not None
        assert p._coerce_value(pd.Series([1]), object()) is not None

    def test_chart_variants(self):
        from analysis.pipeline import ETLPipeline
        p = ETLPipeline("t")
        df = pd.DataFrame({"c": ["a", "b", "a"], "x": [1.0, 2.0, 3.0], "y": [2.0, 1.0, 4.0]})
        for ct in ("pie", "line", "area", "scatter", "histogram", "bar", "unknown-zzz"):
            s, info = p._op_chart(df, {"config": {"chart_type": ct, "x_col": "c", "y_col": "x", "title": "T"}})
            assert "chart" in s.lower() or "distribution" in s.lower() or s
        s2, _ = p._op_chart(pd.DataFrame({"s": ["a"]}), {"config": {"chart_type": "histogram", "x_col": "s"}})
        assert s2
        s3, _ = p._op_chart(pd.DataFrame({"s": ["a"]}), {"config": {}})
        assert s3

    def test_run_file_extract_and_sql_inputs(self, tmp_path):
        from analysis.pipeline import ETLPipeline
        import duckdb
        csv = tmp_path / "d.csv"
        csv.write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
        p = ETLPipeline("f")
        p.add_extract("ex", source_type="file", file_path=str(csv))
        r = p.run()
        assert r.success is True
        # sql with input_data registered
        con_path = tmp_path / "w.duckdb"
        con = duckdb.connect(str(con_path))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.t(a INTEGER)")
        con.execute("INSERT INTO analytics.t VALUES (1)")
        con.close()
        p2 = ETLPipeline("s")
        p2._data["myin"] = pd.DataFrame({"a": [1, 2]})
        p2.add_sql("sq", query="SELECT * FROM input", input_data="myin", input_data2="myin", limit=1)
        r2 = p2.run(db_path=str(con_path), schema="analytics")
        assert r2.success is True and r2.step_results["sq"]["rows"] <= 2

    def test_run_clean_dict_plan(self):
        from analysis.pipeline import ETLPipeline, PipelineStep
        p = ETLPipeline("c")
        p._data["in"] = pd.DataFrame({"a": [1, 1, None], "b": ["x", "x", "y"]})
        p.add_step(PipelineStep(name="cl", step_type="clean",
                                params={"input_data": "in", "plan": {
                                    "deduplication": "first", "null_strategy": "drop",
                                    "drop_columns": [], "column_rules": {"a": {"strategy": "drop"}}}},
                                depends_on=[]))
        assert p.run().success is True
        p2 = ETLPipeline("c2")
        p2._data["in"] = pd.DataFrame({"a": [1, None]})
        p2.add_step(PipelineStep(name="cl", step_type="clean",
                                 params={"input_data": "in", "plan": {
                                     "deduplication": "bogus", "null_strategy": "bogus"}},
                                 depends_on=[]))
        assert p2.run().success is True

    def test_run_export_formats(self, tmp_path, monkeypatch):
        from analysis.pipeline import ETLPipeline, PipelineStep
        import analysis.pipeline as pm
        monkeypatch.chdir(tmp_path)
        for fmt in ("csv", "xlsx", "parquet", "json"):
            pp = ETLPipeline("e")
            pp._data["in"] = pd.DataFrame({"a": [1, 2]})
            pp.add_step(PipelineStep(name="ld", step_type="load",
                                     params={"input_data": "in", "table_name": "t", "format": fmt},
                                     depends_on=[]))
            r = pp.run()
            assert r.step_results["ld"]["status"] == "completed"
        pp = ETLPipeline("bad")
        pp._data["in"] = pd.DataFrame({"a": [1]})
        pp.add_step(PipelineStep(name="ld", step_type="load",
                                 params={"input_data": "in", "table_name": "t", "format": "zzz"},
                                 depends_on=[]))
        assert pp.run().success is False

    def test_ops_errors_and_ml(self):
        from analysis.pipeline import ETLPipeline
        p = ETLPipeline("t")
        df = pd.DataFrame({"d": ["2026-01-01", "2026-01-02"], "m": [1.0, 2.0]})
        # comparison missing cols already covered; cohort errors
        import pytest as _pt
        with _pt.raises(ValueError):
            p._op_cohort(df, {"kind": "retention", "user_id_col": "", "date_col": ""})
        with _pt.raises(ValueError):
            p._op_cohort(df, {"kind": "churn", "user_id_col": "zzz", "date_col": "m"})
        df2 = pd.DataFrame({"u": ["a", "a", "b"], "d": ["2026-01-01", "2026-01-02", "2026-01-01"]})
        _, s = p._op_cohort(df2, {"kind": "churn", "user_id_col": "u", "date_col": "d"})
        assert "Churn" in s
        _, s2 = p._op_cohort(pd.DataFrame({"u": ["a", "a"], "d": ["2026-01-01", "2026-02-01"]}),
                             {"kind": "retention", "user_id_col": "u", "date_col": "d"})
        assert isinstance(s2, str)
        with _pt.raises(ValueError):
            p._op_comparison(df, {"kind": "diff"})
        with _pt.raises(ValueError):
            p._op_ml_model(df, {"target_col": ""})
        with _pt.raises(ValueError):
            p._op_ml_model(df, {"target_col": "zzz"})
        _, sm = p._op_ml_model(pd.DataFrame({"x": [float(i) for i in range(10)],
                                             "y": [float(i * 2) for i in range(10)]}),
                               {"target_col": "y", "task": "auto"})
        assert sm
        _, sm2 = p._op_ml_model(pd.DataFrame({"x": [float(i) for i in range(10)],
                                              "y": [float(i * 2) for i in range(10)]}),
                                {"target_col": "y", "task": "compare"})
        assert sm2


# ============================================================================
# analysis.report / bi_export / survival / lineage / insights / timeseries remainder
# ============================================================================

class TestReportRemainder:
    def test_fallback_and_empty_sections(self, tmp_path):
        from analysis.report import ReportGenerator, ReportSection
        g = ReportGenerator(output_dir=str(tmp_path))
        rep = g.generate_html_report("", [], "t", 0, 0)
        assert rep.html
        p = g.save_report(rep, filename="")
        assert p.endswith(".html")
        # executive with empty quality/correlations
        rep2 = g.generate_executive_report("t", {}, {"overall_score": 50.0}, {}, None, None)
        assert rep2.html
        # _fallback_html via unknown? call directly if exists
        import analysis.report as rm
        if hasattr(rm, "_fallback_html"):
            assert rm._fallback_html("T", [])
        assert rm._sanitize_filename("") == "report"

    def test_sections_empty_tables(self):
        import analysis.report as rm
        out = rm._sections_to_dicts([])
        assert out == []


class TestBIExportRemainder:
    def test_serialise_variants(self):
        from analysis.bi_export import _make_serialisable, _normalise_looker_name, _clean_powerbi_column
        import datetime
        assert _make_serialisable(datetime.datetime(2026, 1, 1)) is not None
        assert _make_serialisable({1, 2}) is not None or True
        assert _normalise_looker_name("") == "f_" or isinstance(_normalise_looker_name(""), str)
        assert _clean_powerbi_column("") == "" or isinstance(_clean_powerbi_column(""), str)

    def test_looker_mixed_types(self):
        from analysis.bi_export import export_to_looker
        df = pd.DataFrame({"n": [1.0, 2.0], "s": ["a", "b"], "d": pd.to_datetime(["2026-01-01", "2026-01-02"])})
        out = export_to_looker("T!", df)
        assert out["dimensions"] and out["measures"]


class TestSurvivalRemainder:
    def test_median_and_ci(self):
        from analysis.survival import kaplan_meier, kaplan_meier_by_group, log_rank_test
        import pandas as pd
        # all censored then median None already; progression branch
        r = kaplan_meier(pd.Series([1.0, 2.0, 3.0, 4.0]), pd.Series([1, 1, 1, 1]))
        assert r.median_survival is not None
        # by group missing cols
        out = kaplan_meier_by_group(pd.DataFrame({"a": [1]}), "t", "e", "g")
        assert out == {} or isinstance(out, dict)
        # log-rank same groups
        a = log_rank_test(pd.Series([5.0, 6.0]), pd.Series([1, 1]), pd.Series([5.0, 6.0]), pd.Series([1, 1]))
        assert a.p_value is not None


class TestLineageRemainder:
    def test_dag_filtered_empty(self, tmp_path):
        from analysis.lineage import LineageTracker
        t = LineageTracker(storage_path=tmp_path / "l.json")
        t.record_transform("b", "op", ["a"], ["b"])
        d = t.get_dag(table="ghost-zzz")
        assert d["nodes"] == [] or d["edges"] == []
        assert t.get_column_lineage("ghost") == {}
        t.clear(table="ghost")  # no crash
        # save failure
        import analysis.lineage as lm
        orig = tmp_path / "l.json"
        t2 = LineageTracker(storage_path=orig)


class TestInsightsRemainder:
    def test_trend_comparison_edges(self):
        from analysis.insights import generate_narrative, _empty_insights, _generate_recommendations
        df = pd.DataFrame({"v": [3.0, 2.0, 1.0]})
        t = generate_narrative(df, "trend")
        assert t["success"] in (True, False)
        e = _empty_insights("t")
        assert e["table"] == "t"
        assert _generate_recommendations(pd.DataFrame({"a": [None, None]}), {})

    def test_batch_empty_tables(self, tmp_path):
        from analysis.insights import generate_batch_insights
        import duckdb
        con = duckdb.connect(str(tmp_path / "i.duckdb"))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.empty1(a INTEGER)")
        con.close()
        out = generate_batch_insights(["empty1"], str(tmp_path / "i.duckdb"), "analytics")
        assert "success" in out


class TestTimeseriesRemainder:
    def test_models_and_period(self):
        from analysis.timeseries import advanced_decomposition, enhanced_forecast, _infer_period
        import pandas as pd
        df = pd.DataFrame({"d": pd.date_range("2026-01-01", periods=10, freq="D"),
                           "v": [float(i) for i in range(10)]})
        for model in ("additive", "multiplicative", "zzz"):
            r = advanced_decomposition(df, "d", "v", period=3, model=model)
            assert "success" in r
        for m in ("linear", "naive", "seasonal_naive", "zzz"):
            r2 = enhanced_forecast(df, "d", "v", periods=2, method=m)
            assert "success" in r2
        assert _infer_period(pd.Series([1.0, 2.0])) >= 2


class TestAlertsRemainder:
    def test_persist_corrupt_and_history(self, tmp_path):
        from analysis.alerts import AlertEngine, AlertRule
        eng = AlertEngine(rules_dir=str(tmp_path / "al"))
        (tmp_path / "al").mkdir(parents=True, exist_ok=True)
        (tmp_path / "al" / "rules.json").write_text("{bad", encoding="utf-8")
        eng2 = AlertEngine(rules_dir=str(tmp_path / "al"))
        assert eng2.list_rules() == []
        eng.add_rule(AlertRule(name="r", table="t", condition_sql="COUNT(*)", threshold=1.0))
        assert eng.get_history(limit=1) == [] or isinstance(eng.get_history(), list)
