"""Observability + platform operations tests (ADR-009 follow-up workstream).

Covers four deliverables:

* the dependency-free Prometheus registry (``api/metrics.py``);
* the operational endpoints (``api/routes/ops.py``);
* the hosted backup / restore tool (``scripts/hosted_backup.py``);
* the static legal pages (``web/legal/*.html``).

The rules the metrics registry has to keep are the interesting part: a
metric endpoint is a public surface, so it must never leak a customer
identifier, an email, a token or a raw SQL string, and it must never create
an unbounded number of time series.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_LEGAL = REPO_ROOT / "web" / "legal"
LEGAL_PAGES = ("privacy", "terms", "dpa", "security")

#: Every fill-in marker the legal pages are allowed to carry.  The set is
#: closed on purpose: an operator must be able to enumerate the work.
ALLOWED_FILLINS = frozenset({
    "COMPANY_NAME",
    "COMPANY_ADDRESS",
    "CONTACT_EMAIL",
    "SECURITY_EMAIL",
    "DPO_EMAIL",
    "JURISDICTION",
    "EFFECTIVE_DATE",
    "DATA_REGION",
    "RETENTION_DAYS",
    "SUBPROCESSOR_HOSTING",
    "SUBPROCESSOR_STORAGE",
    "SUBPROCESSOR_EMAIL",
    "SUBPROCESSOR_LLM",
    "SUBPROCESSOR_MONITORING",
})

#: Text that must never appear in a shipped legal page.
_LOREM_MARKERS = (
    "lorem ipsum",
    "dolor sit amet",
    "consectetur adipiscing",
    "sed do eiusmod",
    "excepteur sint",
    "nulla tempus",
)

#: A reference to an off-box asset.  The legal pages are served from the
#: app's own origin and must render offline and under a strict CSP, so they
#: may not pull a script, style, font or image from the internet.
_EXTERNAL_REF = re.compile(
    r"""<(?:script|link|img|iframe|source|embed|video|audio)\b[^>]*\b"""
    r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""",
    re.IGNORECASE,
)
_EXTERNAL_CSS_URL = re.compile(r"""url\(\s*["']?(?:https?:)?//""", re.IGNORECASE)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_script(name: str):
    """Import a standalone script from ``scripts/`` (it is not a package)."""
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_scripts_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_fake_install(tmp_path: Path) -> Path:
    """Build a throwaway installation tree the backup tool can copy."""
    import duckdb

    root = tmp_path / "install"
    (root / "data" / "curated").mkdir(parents=True)
    (root / "data" / "raw" / "u_demo").mkdir(parents=True)
    (root / "config").mkdir(parents=True)

    warehouse = root / "data" / "curated" / "warehouse.duckdb"
    con = duckdb.connect(str(warehouse))
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute("CREATE TABLE analytics.sales (amount DOUBLE)")
    con.execute("INSERT INTO analytics.sales VALUES (1.0)")
    con.close()

    with_context = sqlite3.connect(str(root / "data" / "users.db"))
    try:
        with_context.execute("CREATE TABLE users (username TEXT)")
        with_context.commit()
    finally:
        with_context.close()

    (root / "data" / ".jwt_secret").write_text("jwt-secret-value", encoding="utf-8")
    (root / "data" / ".grant_key").write_text("grant-key-value", encoding="utf-8")
    (root / "config" / "settings.yaml").write_text("app:\n  name: x\n", encoding="utf-8")
    (root / "config" / "users.json").write_text('{"u": {"password": "hash"}}', encoding="utf-8")
    (root / "data" / "raw" / "u_demo" / "sales.csv").write_text(
        "a,b\n1,2\n", encoding="utf-8"
    )
    return root


@pytest.fixture()
def ops_client():
    """A FastAPI app that mounts the ops router (no auth middleware)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routes.ops import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_metrics():
    from api.metrics import reset_metrics

    reset_metrics()
    yield
    reset_metrics()


# ---------------------------------------------------------------------------
# A. the metrics registry
# ---------------------------------------------------------------------------

class TestMetricsExposition:
    def test_render_uses_the_prometheus_exposition_format(self):
        from api.metrics import record_http_request, render_prometheus

        record_http_request("GET", "/tables/sales", 200, 0.12)
        text = render_prometheus()

        assert "# HELP dataagent_http_requests_total" in text
        assert "# TYPE dataagent_http_requests_total counter" in text
        assert (
            'dataagent_http_requests_total{method="GET",path="/tables/{id}",status="200"} 1'
            in text
        )

    def test_every_declared_metric_is_present(self):
        from api.metrics import (
            record_ai_request,
            record_http_request,
            record_rate_limited,
            record_rows_returned,
            record_sql_query,
            record_tenant_request,
            render_prometheus,
            set_tenant_count,
        )

        record_http_request("POST", "/sql", 200, 0.4)
        record_sql_query("success")
        record_ai_request("ask")
        record_rows_returned(12)
        record_rate_limited("ask")
        record_tenant_request("u_abc123")
        set_tenant_count(3)
        text = render_prometheus()

        for name in (
            "dataagent_http_requests_total",
            "dataagent_http_request_duration_seconds",
            "dataagent_sql_queries_total",
            "dataagent_ai_requests_total",
            "dataagent_warehouse_rows_returned",
            "dataagent_rate_limited_total",
            "dataagent_tenants",
            "dataagent_tenant_requests_total",
            "dataagent_uptime_seconds",
            "dataagent_build_info",
        ):
            assert f"# TYPE {name} " in text, name

    def test_histogram_exposes_buckets_sum_and_count(self):
        from api.metrics import observe_http_duration, render_prometheus

        observe_http_duration("/tables/{id}", 0.03)
        text = render_prometheus()

        assert "# TYPE dataagent_http_request_duration_seconds histogram" in text
        assert 'dataagent_http_request_duration_seconds_bucket{le="0.05",path="/tables/{id}"} 1' in text
        assert 'dataagent_http_request_duration_seconds_bucket{le="+Inf",path="/tables/{id}"} 1' in text
        assert 'dataagent_http_request_duration_seconds_count{path="/tables/{id}"} 1' in text
        assert "dataagent_http_request_duration_seconds_sum{path=\"/tables/{id}\"}" in text

    def test_build_info_carries_the_version(self):
        from api.metrics import render_prometheus

        text = render_prometheus()
        assert re.search(r'dataagent_build_info\{version="[^"]+"\} 1', text)

    def test_uptime_is_a_number(self):
        from api.metrics import render_prometheus

        text = render_prometheus()
        match = re.search(r"^dataagent_uptime_seconds ([0-9.]+)$", text, re.MULTILINE)
        assert match, text
        assert float(match.group(1)) >= 0.0


class TestMetricsNeverLeak:
    def test_a_tenant_id_is_never_used_as_a_label(self):
        from api.metrics import record_tenant_request, render_prometheus

        record_tenant_request("acme-corporation")
        text = render_prometheus()

        assert "acme" not in text.lower()
        assert "dataagent_tenant_requests_total{tenant=" in text

    def test_an_email_is_never_used_as_a_label(self):
        from api.metrics import record_tenant_request, render_prometheus

        record_tenant_request("ops@example.com")
        text = render_prometheus()

        assert "example.com" not in text
        assert "@" not in text

    def test_a_token_in_a_path_is_normalised_away(self):
        from api.metrics import record_http_request, render_prometheus

        record_http_request("GET", "/auth/verify/eyJhbGciOiJIUzI1NiJ9.payload.signature", 200)
        text = render_prometheus()

        assert "eyJhbGciOiJIUzI1NiJ9" not in text
        assert 'path="/auth/verify/{id}"' in text

    def test_sql_text_never_reaches_a_label(self):
        from api.metrics import record_sql_query, render_prometheus

        record_sql_query("success")
        text = render_prometheus()

        assert "SELECT" not in text
        assert 'dataagent_sql_queries_total{result="success"} 1' in text

    def test_an_unknown_feature_falls_back_to_other(self):
        from api.metrics import record_ai_request, render_prometheus

        record_ai_request("something-odd-with-a-secret")
        text = render_prometheus()

        assert "something-odd-with-a-secret" not in text
        assert 'dataagent_ai_requests_total{feature="other"} 1' in text


class TestPathNormalisation:
    def test_a_table_name_collapses_to_an_id(self):
        from api.metrics import normalize_path

        assert normalize_path("/tables/sales") == "/tables/{id}"
        assert normalize_path("/tables/ppc_ads") == "/tables/{id}"

    def test_a_tenant_id_collapses_to_an_id(self):
        from api.metrics import normalize_path

        assert normalize_path("/tenants/u_4uze5ruwq0nqlwgmz32c") == "/tenants/{id}"

    def test_static_paths_are_left_alone(self):
        from api.metrics import normalize_path

        assert normalize_path("/health/system") == "/health/system"
        assert normalize_path("/metrics") == "/metrics"
        assert normalize_path("/sql") == "/sql"
        assert normalize_path("/ask-sql") == "/ask-sql"

    def test_the_query_string_is_dropped(self):
        from api.metrics import normalize_path

        assert normalize_path("/tables/sales?token=abc123") == "/tables/{id}"

    def test_a_route_template_collapses_to_an_id(self):
        from api.metrics import normalize_path

        assert normalize_path("/tables/{table_name}") == "/tables/{id}"

    def test_the_trailing_slash_is_ignored(self):
        from api.metrics import normalize_path

        assert normalize_path("/tables/") == "/tables"


# ---------------------------------------------------------------------------
# B. the operational endpoints
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_the_endpoint_serves_prometheus_text(self, ops_client, monkeypatch):
        """The exposition is served when the caller is authorised.

        It is deliberately **not** anonymous: it names every API path,
        per-path request counts and the tenant count, which is a free map
        of the product's internals. A scraper presents ``METRICS_TOKEN``.
        """
        monkeypatch.setenv("METRICS_TOKEN", "test-scraper-token")
        response = ops_client.get(
            "/metrics", headers={"Authorization": "Bearer test-scraper-token"}
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "version=0.0.4" in response.headers["content-type"]
        assert "# TYPE dataagent_http_requests_total counter" in response.text

    def test_anonymous_callers_are_refused(self, ops_client, monkeypatch):
        """Without a token (or an admin session) the metrics are not public."""
        monkeypatch.setenv("METRICS_TOKEN", "test-scraper-token")
        response = ops_client.get("/metrics")

        assert response.status_code == 403
        assert "# TYPE dataagent_http_requests_total" not in response.text


class TestDeepHealthCheck:
    def test_every_component_is_reported_individually(self):
        from api.routes.ops import deep_health_check

        report = deep_health_check()

        assert report["status"] in {"healthy", "degraded", "unhealthy"}
        components = report["components"]
        for name in (
            "api",
            "warehouse",
            "identity_store",
            "audit_sink",
            "llm_provider",
            "billing",
            "opencode_server",
        ):
            assert name in components, name
            entry = components[name]
            assert isinstance(entry["ok"], bool), name
            assert isinstance(entry["critical"], bool), name
            assert isinstance(entry["reason"], str) and entry["reason"], name

    def test_a_broken_dependency_does_not_raise(self, monkeypatch):
        from api.routes import ops

        def _boom():
            raise RuntimeError("the warehouse probe exploded")

        monkeypatch.setattr(ops, "_probe_warehouse", _boom)
        report = ops.deep_health_check()

        assert report["components"]["warehouse"]["ok"] is False
        assert "exploded" in report["components"]["warehouse"]["reason"]
        assert report["status"] == "unhealthy"

    def test_a_broken_dependency_does_not_hide_the_others(self, monkeypatch):
        from api.routes import ops

        def _boom():
            raise RuntimeError("the warehouse probe exploded")

        monkeypatch.setattr(ops, "_probe_warehouse", _boom)
        report = ops.deep_health_check()

        assert report["components"]["api"]["ok"] is True
        assert report["components"]["identity_store"]["ok"] in (True, False)
        assert len(report["components"]) == len(ops.PROBE_NAMES)

    def test_the_warehouse_probe_cleans_up_after_itself(self):
        from api.routes.ops import _probe_warehouse

        result = _probe_warehouse()
        assert "ok" in result
        # A failed probe must not leave a scratch table behind.
        from api.db import get_connection

        rows = get_connection().execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE '%health_probe%'"
        ).fetchall()
        assert rows == []

    def test_the_endpoint_returns_the_breakdown(self, ops_client):
        response = ops_client.get("/health/system")

        assert response.status_code in (200, 503)
        body = response.json()
        assert body["status"] in {"healthy", "degraded", "unhealthy"}
        assert "warehouse" in body["components"]

    def test_the_deep_check_can_be_restricted_to_admins(self, ops_client, monkeypatch):
        from api.routes import ops

        monkeypatch.setenv(ops.ADMIN_ONLY_ENV, "1")
        response = ops_client.get("/health/system")
        assert response.status_code == 403
        assert "administrator" in response.json()["detail"].lower()

        monkeypatch.delenv(ops.ADMIN_ONLY_ENV)
        assert ops_client.get("/health/system").status_code in (200, 503)


class TestMetricsMiddleware:
    def test_the_middleware_counts_a_request(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.metrics import MetricsMiddleware, render_prometheus

        app = FastAPI()
        app.add_middleware(MetricsMiddleware)

        @app.get("/tables/{table_name}")
        async def _tables(table_name: str):
            return {"ok": True}

        client = TestClient(app)
        assert client.get("/tables/sales").status_code == 200

        text = render_prometheus()
        assert 'dataagent_http_requests_total{method="GET",path="/tables/{id}",status="200"} 1' in text
        assert "sales" not in text

    def test_the_middleware_does_not_break_a_failing_request(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.metrics import MetricsMiddleware, render_prometheus

        app = FastAPI()
        app.add_middleware(MetricsMiddleware)

        @app.get("/boom")
        async def _boom():
            raise RuntimeError("handler exploded")

        client = TestClient(app)
        with pytest.raises(RuntimeError):
            client.get("/boom")

        text = render_prometheus()
        assert 'dataagent_http_requests_total{method="GET",path="/boom",status="500"} 1' in text


class TestLivenessAndReadiness:
    def test_liveness_never_touches_a_dependency(self, ops_client, monkeypatch):
        from api.routes import ops

        def _boom():
            raise RuntimeError("the dependency is down")

        monkeypatch.setattr(ops, "_probe_warehouse_read", _boom)
        monkeypatch.setattr(ops, "_probe_warehouse", _boom)

        response = ops_client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    def test_readiness_fails_when_the_warehouse_is_unusable(self, ops_client, monkeypatch):
        from api.routes import ops

        def _boom():
            raise RuntimeError("the dependency is down")

        monkeypatch.setattr(ops, "_probe_warehouse_read", _boom)
        response = ops_client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"

    def test_the_existing_routes_are_not_collided_with(self, ops_client):
        """``/health``, ``/live`` and ``/ready`` stay owned by api/main.py."""
        paths = set(ops_client.app.openapi()["paths"])
        assert "/health" not in paths
        assert "/live" not in paths
        assert "/ready" not in paths
        assert "/health/system" in paths
        assert "/health/live" in paths
        assert "/health/ready" in paths
        assert "/metrics" in paths


# ---------------------------------------------------------------------------
# C. the hosted backup / restore tool
# ---------------------------------------------------------------------------

class TestHostedBackup:
    def test_backup_writes_a_manifest(self, tmp_path):
        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)

        manifest = backup.create_backup(str(root), str(tmp_path / "out"))

        backup_dir = Path(manifest["backup_dir"])
        assert (backup_dir / "manifest.json").exists()
        assert manifest["files"], "the manifest lists no files"
        assert manifest["warehouse"]["method"] == "export_database"
        on_disk = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        assert on_disk["created_at"]

    def test_backup_excludes_secrets_by_default(self, tmp_path):
        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)

        manifest = backup.create_backup(str(root), str(tmp_path / "out"))
        backup_dir = Path(manifest["backup_dir"])
        names = {p.name for p in backup_dir.rglob("*") if p.is_file()}

        assert ".jwt_secret" not in names
        assert ".grant_key" not in names
        assert "users.json" not in names
        assert "jwt-secret-value" not in (backup_dir / "manifest.json").read_text(
            encoding="utf-8"
        )
        assert "data/.jwt_secret" in manifest["excluded_secrets"]
        assert "data/.grant_key" in manifest["excluded_secrets"]
        assert manifest["includes_secrets"] is False

    def test_include_secrets_needs_the_flag_and_warns_loudly(self, tmp_path, capsys):
        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)

        manifest = backup.create_backup(
            str(root), str(tmp_path / "out"), include_secrets=True
        )
        backup_dir = Path(manifest["backup_dir"])
        names = {p.name for p in backup_dir.rglob("*") if p.is_file()}

        assert ".jwt_secret" in names
        assert manifest["includes_secrets"] is True
        printed = capsys.readouterr()
        message = (printed.out + printed.err).upper()
        assert "WARNING" in message
        assert "SECRET" in message

    def test_restore_refuses_without_confirmation(self, tmp_path):
        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)
        manifest = backup.create_backup(str(root), str(tmp_path / "out"))

        with pytest.raises(backup.ConfirmationRequired):
            backup.restore_backup(str(manifest["backup_dir"]), str(root))

    def test_restore_puts_the_data_back(self, tmp_path):
        import duckdb

        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)
        manifest = backup.create_backup(str(root), str(tmp_path / "out"))

        # Damage the live warehouse, then restore it.
        warehouse = root / "data" / "curated" / "warehouse.duckdb"
        warehouse.unlink()
        con = duckdb.connect(str(warehouse))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.close()

        result = backup.restore_backup(
            str(manifest["backup_dir"]), str(root), confirm=True
        )
        assert result["restored"]

        con = duckdb.connect(str(warehouse), read_only=True)
        rows = con.execute("SELECT amount FROM analytics.sales").fetchall()
        con.close()
        assert rows == [(1.0,)]

    def test_verify_accepts_an_intact_backup(self, tmp_path):
        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)
        manifest = backup.create_backup(str(root), str(tmp_path / "out"))

        report = backup.verify_backup(str(manifest["backup_dir"]))
        assert report["ok"] is True
        assert report["checked"] > 0

    def test_verify_detects_a_tampered_file(self, tmp_path):
        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)
        manifest = backup.create_backup(str(root), str(tmp_path / "out"))
        backup_dir = Path(manifest["backup_dir"])

        target = next(
            p for p in sorted(backup_dir.rglob("*"))
            if p.is_file() and p.name != "manifest.json"
        )
        target.write_text("tampered", encoding="utf-8")

        report = backup.verify_backup(str(backup_dir))
        assert report["ok"] is False
        assert report["mismatched"]

    def test_the_cli_exits_non_zero_without_a_subcommand(self, capsys):
        backup = _load_script("hosted_backup")
        assert backup.main([]) != 0

    def test_the_cli_backup_writes_to_the_requested_directory(self, tmp_path):
        backup = _load_script("hosted_backup")
        root = _make_fake_install(tmp_path)
        out = tmp_path / "backups"

        assert backup.main(["backup", "--root", str(root), "--out", str(out)]) == 0
        assert list(out.glob("*/manifest.json"))


# ---------------------------------------------------------------------------
# D. the legal pages
# ---------------------------------------------------------------------------

class TestLegalPages:
    @pytest.mark.parametrize("name", LEGAL_PAGES)
    def test_the_page_exists(self, name):
        path = WEB_LEGAL / f"{name}.html"
        assert path.exists(), path
        assert path.stat().st_size > 2000, path

    @pytest.mark.parametrize("name", LEGAL_PAGES)
    def test_the_page_is_self_contained(self, name):
        html = (WEB_LEGAL / f"{name}.html").read_text(encoding="utf-8")

        assert not _EXTERNAL_REF.search(html), f"{name}.html pulls an off-box asset"
        assert not _EXTERNAL_CSS_URL.search(html), f"{name}.html pulls an off-box asset"
        assert "<style" in html, f"{name}.html has no inline stylesheet"

    @pytest.mark.parametrize("name", LEGAL_PAGES)
    def test_the_page_has_no_lorem_ipsum(self, name):
        html = (WEB_LEGAL / f"{name}.html").read_text(encoding="utf-8").lower()

        for marker in _LOREM_MARKERS:
            assert marker not in html, f"{name}.html contains {marker!r}"

    @pytest.mark.parametrize("name", LEGAL_PAGES)
    def test_the_page_covers_its_subject(self, name):
        html = (WEB_LEGAL / f"{name}.html").read_text(encoding="utf-8").lower()

        expected = {
            "privacy": ("personal data", "retention", "subprocessor"),
            "terms": ("service", "liability", "terminat"),
            "dpa": ("controller", "processor", "transfer"),
            "security": ("encryption", "incident", "vulnerabilit"),
        }[name]
        for word in expected:
            assert word in html, f"{name}.html does not mention {word!r}"

    @pytest.mark.parametrize("name", LEGAL_PAGES)
    def test_the_fill_in_markers_are_documented(self, name):
        html = (WEB_LEGAL / f"{name}.html").read_text(encoding="utf-8")

        found = set(re.findall(r"\[\[([A-Z_]+)\]\]", html))
        assert found, f"{name}.html has no operator fill-in marker"
        assert found <= ALLOWED_FILLINS, found - ALLOWED_FILLINS
