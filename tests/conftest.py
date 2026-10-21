"""Shared fixtures for the DataAgent test suite.

Runs against a throwaway DuckDB file (never the real warehouse) and a
sample "AI-image" plan that mirrors what `_build_plan_from_spec` produces.
"""

import copy
import os
import sqlite3
import sys
import tempfile
import types
import uuid
from pathlib import Path

import duckdb
import pytest

# Isolate the default warehouse path before any `api.billing` import can run
# `init_billing_tables()` against the real data/curated warehouse.
_TEST_WH_DIR = Path(tempfile.mkdtemp(prefix="dataagent_test_wh_"))
_TEST_WH = str(_TEST_WH_DIR / "warehouse.duckdb")

# The identity store is SQLite by default (ADR-009 D2). Point it at a
# throwaway database for the whole session so the developer's real
# `data/users.db` is never created or written to, and keep the legacy JSON
# import out of the way (the tests start from an empty directory).
os.environ.setdefault("DATAAGENT_USER_DB", str(_TEST_WH_DIR / "users.db"))
os.environ.setdefault("DATAAGENT_USER_LEGACY_JSON", str(_TEST_WH_DIR / "no_users.json"))

from config import settings as _settings  # noqa: E402

# Only redirect the *real* warehouse default on first import. `tests.conftest`
# can be re-imported under a different module name (e.g. `from tests.conftest
# import auth_headers`); re-running this must not clobber a path a fixture has
# already monkeypatched (e.g. `billing_db`'s temp file), or later requests
# would silently reconnect to the session warehouse.
_path_now = _settings.duckdb.database_path.replace("\\", "/")
if "dataagent_test_wh" not in _path_now and "curated" in _path_now:
    _settings.duckdb.database_path = _TEST_WH


@pytest.fixture
def billing_db(tmp_path, monkeypatch):
    """Fresh DuckDB warehouse with billing tables + seed data.

    Swaps `api.state.loader` so billing helpers only touch the temp file.
    """
    import api.state as state
    from etl.load import WarehouseLoader
    from api.billing.schema import init_billing_tables
    from api.billing.seed import seed_billing_data
    from api.billing.cache import clear_request_cache

    db_path = str(tmp_path / "billing_test.duckdb")
    monkeypatch.setattr(_settings.duckdb, "database_path", db_path)
    old_loader = state.loader
    state.loader = WarehouseLoader(
        db_path=db_path,
        schema=_settings.duckdb.schema,
        config=_settings.duckdb.config,
    )
    clear_request_cache()
    init_billing_tables()
    seed_billing_data()
    yield state.loader
    state.loader = old_loader
    clear_request_cache()


@pytest.fixture
def tcon(tmp_path):
    """A temp DuckDB connection with an `analytics.sales` table."""
    con = duckdb.connect(str(tmp_path / "test_warehouse.duckdb"))
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute(
        "CREATE TABLE analytics.sales ("
        " product VARCHAR, region VARCHAR, amount DOUBLE, sold_at DATE)"
    )
    con.execute(
        "INSERT INTO analytics.sales VALUES "
        "('Alpha','west',10.0,DATE '2026-01-01'),"
        "('Alpha','east',20.0,DATE '2026-01-02'),"
        "('Beta','west',5.0,DATE '2026-01-03'),"
        "('Beta','east',15.0,DATE '2026-01-04')"
    )
    yield con
    con.close()


def ensure_user(username: str, password: str, role: str = "admin") -> dict:
    """Create a local user if missing; return user info."""
    from api.auth import user_manager

    info = user_manager.get_user_info(username)
    if not info:
        user_manager.add_user(username, password, role)
        info = user_manager.get_user_info(username)
    return info or {}


def auth_headers(username: str = "test_auth_user", password: str = "Str0ng!Passw0rd",
                 role: str = "admin") -> dict:
    """Bearer JWT headers so AuthMiddleware authenticates TestClient requests.

    Local-mode auto-admin was removed (Phase 1 auth hardening); tests that
    hit protected routes on the main app must present real credentials.
    """
    from api.auth import create_jwt_token

    ensure_user(username, password, role=role)
    token = create_jwt_token(username, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def billing_env(tmp_path, monkeypatch):
    """Isolated billing_server config: temp sqlite + fresh Ed25519 keypair + test secrets."""
    cryptography = pytest.importorskip("cryptography")
    pytest.importorskip("httpx")
    from cryptography.hazmat.primitives import serialization
    from keygen_app.keygen import generate_keypair

    import billing_server.config as cfg
    import billing_server.keymint as keymint

    priv_path = tmp_path / "ed25519_private.pem"
    priv, _fp = generate_keypair(priv_path)
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "billing.sqlite3"))
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY_PATH", str(priv_path))
    monkeypatch.setattr(cfg, "LICENSE_PRIVATE_KEY", "")
    monkeypatch.setattr(cfg, "RAZORPAY_KEY_ID", "rzp_test_123")
    monkeypatch.setattr(cfg, "RAZORPAY_KEY_SECRET", "test_secret")
    monkeypatch.setattr(cfg, "RAZORPAY_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(cfg, "ENTITLEMENTS_SYNC_SECRET", "sync_secret")
    monkeypatch.setattr(cfg, "RESEND_API_KEY", "")
    monkeypatch.setattr(cfg, "ADMIN_TOKEN", "admin_test")

    yield {
        "cfg": cfg,
        "keymint": keymint,
        "public_pem": pub_pem.decode(),
        "private": priv,
    }


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    """Isolated billing store config: temp sqlite file + no Turso env + fresh init set."""
    import billing_server.config as cfg
    import billing_server.store as store

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "billing.sqlite3"))
    monkeypatch.setattr(cfg, "TURSO_DATABASE_URL", "", raising=False)
    monkeypatch.setattr(cfg, "TURSO_AUTH_TOKEN", "", raising=False)
    store._initialized_targets.clear()
    return {"store": store, "cfg": cfg}


@pytest.fixture()
def fake_turso(monkeypatch):
    """A fake `libsql` module; every connect() lands on one shared in-memory DB.

    Records connect() calls and executed CREATE statements for assertions.
    """
    state = {"connects": [], "ddls": []}
    uri = f"file:turso_fake_{uuid.uuid4().hex}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)

    class FakeCursor:
        def __init__(self, cur):
            self._cur = cur

        @property
        def description(self):
            return self._cur.description

        @property
        def rowcount(self):
            return self._cur.rowcount

        def execute(self, sql, parameters=()):
            if parameters:
                self._cur.execute(sql, tuple(parameters))
            else:
                self._cur.execute(sql)
            if sql.lstrip().lower().startswith("create"):
                state["ddls"].append(sql)
            return self

        def fetchone(self):
            row = self._cur.fetchone()
            return tuple(row) if row is not None else None

        def fetchall(self):
            return [tuple(r) for r in self._cur.fetchall()]

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self._con = sqlite3.connect(uri, uri=True)

        def execute(self, sql, parameters=()):
            return FakeCursor(self._con.cursor()).execute(sql, parameters)

        def commit(self):
            self._con.commit()

        def rollback(self):
            self._con.rollback()

        def close(self):
            self._con.close()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
            return False

    module = types.ModuleType("libsql")

    def connect(database, auth_token=None, **kwargs):
        state["connects"].append({"database": database, "auth_token": auth_token})
        return FakeConnection()

    module.connect = connect
    monkeypatch.setitem(sys.modules, "libsql", module)
    yield state
    anchor.close()


@pytest.fixture(autouse=True)
def isolated_user_store(tmp_path, monkeypatch):
    """Never write test users into the real identity store.

    The user directory lives in the SQLite store (api/user_store.py). The
    whole session shares one throwaway database (DATAAGENT_USER_DB, set
    above), and `auth_file` — still used for the revoked-JTI list — is
    redirected to a per-test temp path.
    """
    from api.auth import user_manager as um

    monkeypatch.setattr(um, "auth_file", tmp_path / "users.json")
    yield um


@pytest.fixture
def sample_plan():
    """A validated plan with three executable zones (kpi / bar / table)."""
    return {
        "used": "ai-image",
        "spec": {
            "theme": "light",
            "background": "#f8fafc",
            "cardBg": "#ffffff",
            "textColor": "#0f172a",
            "accent": "#2563eb",
            "palette": ["#2563eb", "#ef4444"],
            "header": {"present": True, "title": "Sales Overview"},
        },
        "zones": [
            {
                "zone": "z1", "chartType": "kpi", "title": "Total orders",
                "metric": "amount", "x": "product", "y": "amount",
                "sql": "SELECT COUNT(*) AS cnt FROM analytics.sales",
                "cols": 3, "rows": 1, "config": {},
                "box": {"x": 2, "y": 12, "w": 20, "h": 14}, "widx": 0,
            },
            {
                "zone": "z2", "chartType": "bar", "title": "Amount by region",
                "metric": "amount", "x": "region", "y": "amount",
                "sql": ("SELECT region, SUM(amount) AS total FROM analytics.sales "
                        "GROUP BY region ORDER BY total DESC"),
                "cols": 6, "rows": 3, "config": {},
                "box": {"x": 24, "y": 12, "w": 50, "h": 30}, "widx": 1,
            },
            {
                "zone": "z3", "chartType": "table", "title": "All rows",
                "metric": "amount", "x": "product", "y": "amount",
                "sql": ("SELECT product, region, amount FROM analytics.sales "
                        "ORDER BY amount"),
                "cols": 6, "rows": 9, "config": {},
                "box": {"x": 2, "y": 44, "w": 72, "h": 30}, "widx": 2,
            },
        ],
        "kpis": [],
        "placeholders": [],
    }


@pytest.fixture
def sample_kpi_plan():
    """A plan with a dedicated KPI band plus one chart zone.

    Mirrors what `_build_plan_from_spec` returns for a screenshot with two
    KPI cards and one bar chart: KPI entries carry runnable single-scalar
    SQL under shared `widx` values with the zones.
    """
    return {
        "used": "ai-image",
        "spec": {
            "theme": "light",
            "background": "#f8fafc",
            "cardBg": "#ffffff",
            "textColor": "#0f172a",
            "accent": "#2563eb",
            "palette": ["#2563eb", "#ef4444"],
            "header": {"present": True, "title": "Sales Overview"},
        },
        "zones": [
            {
                "zone": "z2", "chartType": "bar", "family": "bar",
                "title": "Amount by region",
                "metric": "amount", "x": "region", "y": "amount",
                "sql": ("SELECT region, SUM(amount) AS total FROM analytics.sales "
                        "GROUP BY region ORDER BY total DESC"),
                "cols": 6, "rows": 3, "config": {},
                "box": {"x": 24, "y": 44, "w": 50, "h": 30}, "widx": 2,
            }
        ],
        "kpis": [
            {
                "label": "Gross revenue", "variant": "kpi", "agg": "SUM",
                "column": "amount", "expression": "SUM(amount)", "spark": False,
                "sql": "SELECT SUM(amount) AS value FROM analytics.sales",
                "box": {"x": 2, "y": 12, "w": 20, "h": 14}, "widx": 0,
            },
            {
                "label": "Avg order", "variant": "kpi", "agg": "SUM",
                "column": "amount/cnt", "expression": "SUM(amount)/NULLIF(COUNT(*),0)",
                "spark": False,
                "sql": "SELECT SUM(amount)/NULLIF(COUNT(*),0) AS value FROM analytics.sales",
                "box": {"x": 24, "y": 12, "w": 20, "h": 14}, "widx": 1,
            },
        ],
        "placeholders": [],
    }


@pytest.fixture
def sample_plan_copy(sample_plan):
    """Deep copy so tests can mutate SQL without touching the fixture."""
    return copy.deepcopy(sample_plan)