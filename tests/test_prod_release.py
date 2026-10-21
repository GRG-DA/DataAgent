"""Production 100% gates — TLS path, image hygiene, hardening, probes."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerignore_excludes_secrets():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for needle in (
        "billing_server/*.sqlite3",
        "*.pem",
        ".env",
        "config/users.json",
    ):
        assert needle in text, f".dockerignore missing {needle}"


def test_billing_db_not_present():
    leftovers = list((ROOT / "billing_server").glob("*.sqlite3*"))
    assert leftovers == [], f"runtime billing DB must not ship: {leftovers}"


def test_compose_prod_shape():
    import yaml

    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "version" not in data, "obsolete compose version key"
    assert "networks" in data and "appnet" in data["networks"]
    api = data["services"]["api"]
    vols = " ".join(str(v) for v in api.get("volumes", []))
    assert "/app/config:ro" in vols or "/app/config:ro" in vols.replace(" ", "")
    assert "deploy" in api and "limits" in api["deploy"]["resources"]
    assert api["healthcheck"]["test"][-1].endswith("/ready")
    web = data["services"]["web"]
    assert "appnet" in web.get("networks", [])


def test_nginx_tls_path_exists():
    text = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    assert "limit_req_zone" in text
    assert "443" in text  # commented template block counts
    assert "Strict-Transport-Security" in text
    assert (ROOT / "scripts" / "enable_tls.ps1").exists()


def test_cors_is_allowlisted():
    from api.main import app

    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert cors.kwargs.get("allow_methods") != ["*"]
    assert cors.kwargs.get("allow_headers") != ["*"]
    assert "Authorization" in cors.kwargs.get("allow_headers", [])


def test_trusted_host_and_handlers_wired():
    from api.main import app

    names = [m.cls.__name__ for m in app.user_middleware]
    assert "TrustedHostMiddleware" in names
    routes = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/live" in routes and "/ready" in routes


def test_ready_live_and_system_health_readonly():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/live").status_code == 200
    ready = client.get("/ready")
    assert ready.status_code in (200, 503)
    src = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
    assert "read_only=True" in src
    # The system health check must not leak paths or tracebacks. It lives in
    # api/routes/ops.py now (the deep, per-dependency version).
    ops_src = (ROOT / "api" / "routes" / "ops.py").read_text(encoding="utf-8")
    assert '"path": str(settings.duckdb.database_path)' not in ops_src
    # ... and the response must not carry the warehouse path either.
    resp = client.get("/health/system")
    assert resp.status_code in (200, 503)
    assert "database_path" not in resp.text


def test_firebase_fully_removed():
    """The release must not ship Firebase (auth/SDK/config/dependency)."""
    from config import settings

    assert not hasattr(settings, "firebase")
    assert not (ROOT / "api" / "firebase_auth.py").exists()
    assert not (ROOT / "api" / "billing" / "firestore_store.py").exists()
    assert not (ROOT / "web" / "firebase-config.js").exists()


def test_request_id_header_present():
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/live")
    assert res.headers.get("X-Request-ID")
