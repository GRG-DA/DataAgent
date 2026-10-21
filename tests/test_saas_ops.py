"""ADR-009 D5 / D7 operational tests.

Covers the shared-backend rate limiter, the shared-backend audit sinks, the
release-zip data exclusion and the DuckDB connection reuse in the SQL tool.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import duckdb
import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parent.parent


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


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


class _FakeState:
    """Stand-in for ``starlette.datastructures.State``."""

    def __init__(self, user=None):
        if user is not None:
            self.user = user


class _FakeUser:
    def __init__(self, user_id: str, username: str = "someone"):
        self.user_id = user_id
        self.username = username
        self.role = "user"
        self.tenant_id = None


class _FakeRequest:
    def __init__(self, host: str = "1.2.3.4", headers=None, user=None):
        self.client = _FakeClient(host)
        self.headers = headers or {}
        self.state = _FakeState(user)


def _score_in_range(score: float, lo, hi) -> bool:
    """Redis ZRANGEBYSCORE semantics (only the forms we use)."""
    if lo != "-inf" and score < float(lo):
        return False
    if hi == "+inf":
        return True
    if hi.startswith("("):
        return score < float(hi[1:])
    return score <= float(hi)


class FakeRedis:
    """Minimal in-process stand-in for the Redis commands the sinks use."""

    def __init__(self):
        self.zsets: dict[str, dict[str, float]] = {}
        self.lists: dict[str, list] = {}
        self.expiries: dict[str, int] = {}
        self.deleted: list[str] = []

    # -- sorted sets --
    def zremrangebyscore(self, key, lo, hi):
        members = self.zsets.get(key, {})
        doomed = [m for m, s in members.items() if _score_in_range(s, lo, hi)]
        for m in doomed:
            del members[m]
        return len(doomed)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def expire(self, key, ttl):
        self.expiries[key] = int(ttl)
        return True

    def zrange(self, key, start, end, withscores=False):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        return items if withscores else [m for m, _ in items]

    def delete(self, key):
        self.deleted.append(key)
        self.zsets.pop(key, None)
        self.lists.pop(key, None)
        return 1

    # -- lists --
    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def ltrim(self, key, start, stop):
        values = self.lists.get(key, [])
        if stop == -1:
            self.lists[key] = values[start:]
        else:
            self.lists[key] = values[start:stop + 1]
        return True

    def llen(self, key):
        return len(self.lists.get(key, []))

    # -- pipeline --
    def pipeline(self, transaction=True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, client: FakeRedis):
        self._client = client
        self._ops: list[tuple] = []

    def rpush(self, key, value):
        self._ops.append(("rpush", key, value))
        return self

    def ltrim(self, key, start, stop):
        self._ops.append(("ltrim", key, start, stop))
        return self

    def execute(self):
        results = []
        for op in self._ops:
            name, args = op[0], op[1:]
            results.append(getattr(self._client, name)(*args))
        self._ops = []
        return results


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    from api.middleware.rate_limit import (
        reset_rate_limit_backend,
        reset_rate_limits,
    )

    reset_rate_limit_backend()
    reset_rate_limits()
    yield
    reset_rate_limits()
    reset_rate_limit_backend()


# ---------------------------------------------------------------------------
# D5 — rate limiting
# ---------------------------------------------------------------------------

class TestRateLimitBuckets:
    def test_expensive_paths_have_buckets(self):
        from api.middleware.rate_limit import current_limits

        limits = current_limits()
        for bucket in ("ask", "ask_sql", "upload", "load"):
            assert bucket in limits, f"{bucket} is not rate limited"
            assert limits[bucket][0] > 0
            assert limits[bucket][1] > 0

    def test_ask_bucket_blocks_over_the_limit(self):
        from api.middleware.rate_limit import check_rate_limit, current_limits

        limit = current_limits()["ask"][0]
        for _ in range(limit):
            check_rate_limit(_FakeRequest(), "ask")
        with pytest.raises(HTTPException) as exc:
            check_rate_limit(_FakeRequest(), "ask")
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers

    def test_upload_and_load_buckets_block(self):
        from api.middleware.rate_limit import check_rate_limit, current_limits

        for bucket in ("upload", "load"):
            limit = current_limits()[bucket][0]
            for _ in range(limit):
                check_rate_limit(_FakeRequest(), bucket)
            with pytest.raises(HTTPException) as exc:
                check_rate_limit(_FakeRequest(), bucket)
            assert exc.value.status_code == 429


class TestRateLimitPerUserDimension:
    def test_two_users_behind_one_nat_do_not_share_a_bucket(self):
        from api.middleware.rate_limit import check_rate_limit, current_limits

        limit = current_limits()["ask"][0]
        alice = _FakeRequest("10.0.0.1", user=_FakeUser("alice"))
        bob = _FakeRequest("10.0.0.1", user=_FakeUser("bob"))

        for _ in range(limit):
            check_rate_limit(alice, "ask")
        with pytest.raises(HTTPException):
            check_rate_limit(alice, "ask")
        # Bob shares the NAT but has his own budget.
        for _ in range(limit):
            check_rate_limit(bob, "ask")

    def test_one_user_cannot_hide_behind_many_ips(self):
        from api.middleware.rate_limit import check_rate_limit, current_limits

        limit = current_limits()["ask"][0]
        for i in range(limit):
            check_rate_limit(_FakeRequest(f"10.0.0.{i}", user=_FakeUser("alice")), "ask")
        with pytest.raises(HTTPException):
            check_rate_limit(_FakeRequest("203.0.113.9", user=_FakeUser("alice")), "ask")

    def test_anonymous_requests_fall_back_to_the_ip(self):
        from api.middleware.rate_limit import check_rate_limit, current_limits

        limit = current_limits()["ask"][0]
        for _ in range(limit):
            check_rate_limit(_FakeRequest("10.0.0.1"), "ask")
        with pytest.raises(HTTPException):
            check_rate_limit(_FakeRequest("10.0.0.1"), "ask")
        # A different IP is unaffected.
        check_rate_limit(_FakeRequest("10.0.0.2"), "ask")

    def test_login_bucket_stays_ip_keyed(self):
        """Pre-auth buckets must not be keyed on a claimed identity."""
        from api.middleware.rate_limit import check_rate_limit, current_limits

        limit = current_limits()["login"][0]
        for _ in range(limit):
            check_rate_limit(_FakeRequest("10.0.0.1", user=_FakeUser("alice")), "login")
        with pytest.raises(HTTPException):
            check_rate_limit(_FakeRequest("10.0.0.1", user=_FakeUser("bob")), "login")

    def test_rate_limit_key_is_user_scoped(self):
        from api.middleware.rate_limit import rate_limit_key

        key = rate_limit_key(_FakeRequest("10.0.0.1", user=_FakeUser("alice")), "ask")
        assert key.startswith("user:")
        assert "alice" in key
        anon = rate_limit_key(_FakeRequest("10.0.0.1"), "ask")
        assert anon.startswith("ip:")


class TestRateLimitBackends:
    def test_default_backend_is_in_memory(self):
        from api.middleware.rate_limit_backends import (
            InMemoryRateLimitBackend,
            build_rate_limit_backend,
        )

        backend = build_rate_limit_backend("")
        assert isinstance(backend, InMemoryRateLimitBackend)

    def test_redis_backend_selected_when_redis_url_is_set(self, monkeypatch):
        from api.middleware.rate_limit import (
            get_rate_limit_backend,
            reset_rate_limit_backend,
        )
        from api.middleware.rate_limit_backends import RedisRateLimitBackend

        monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6379")
        reset_rate_limit_backend()  # the backend is resolved once and cached
        backend = get_rate_limit_backend()
        assert isinstance(backend, RedisRateLimitBackend)

    def test_redis_backend_is_shared_between_instances(self):
        from api.middleware.rate_limit_backends import RedisRateLimitBackend

        shared = FakeRedis()
        a = RedisRateLimitBackend(client=shared)
        b = RedisRateLimitBackend(client=shared)

        for _ in range(3):
            decision = a.hit("user:alice", "ask", 3, 60.0)
            assert decision.allowed
        blocked = b.hit("user:alice", "ask", 3, 60.0)
        assert not blocked.allowed
        assert blocked.retry_after > 0

    def test_redis_backend_uses_the_atomic_script_when_available(self):
        from api.middleware.rate_limit_backends import RedisRateLimitBackend

        class ScriptRedis(FakeRedis):
            def __init__(self, result):
                super().__init__()
                self.result = result
                self.calls = []

            def eval(self, script, numkeys, *args):
                self.calls.append((script, numkeys, args))
                return self.result

        client = ScriptRedis([0, 5, "12.5"])
        backend = RedisRateLimitBackend(client=client)
        decision = backend.hit("user:alice", "ask", 5, 60.0)
        assert decision.allowed is False
        assert decision.count == 5
        assert decision.retry_after == pytest.approx(12.5)
        assert client.calls, "the atomic script was not used"

    def test_redis_outage_falls_back_to_local_counters(self, monkeypatch):
        """A dead Redis must not 500 the request — the limit still applies."""
        from api.middleware.rate_limit import check_rate_limit, current_limits

        class BrokenRedis(FakeRedis):
            def zcard(self, key):
                raise ConnectionError("redis is down")

        from api.middleware.rate_limit_backends import RedisRateLimitBackend

        monkeypatch.setattr(
            "api.middleware.rate_limit.get_rate_limit_backend",
            lambda: RedisRateLimitBackend(client=BrokenRedis()),
        )
        limit = current_limits()["ask"][0]
        for _ in range(limit):
            check_rate_limit(_FakeRequest(), "ask")
        with pytest.raises(HTTPException) as exc:
            check_rate_limit(_FakeRequest(), "ask")
        assert exc.value.status_code == 429

    def test_backend_reset_clears_counters(self):
        from api.middleware.rate_limit_backends import InMemoryRateLimitBackend

        backend = InMemoryRateLimitBackend()
        backend.hit("user:alice", "ask", 1, 60.0)
        assert backend.hit("user:alice", "ask", 1, 60.0).allowed is False
        backend.reset()
        assert backend.hit("user:alice", "ask", 1, 60.0).allowed is True


# ---------------------------------------------------------------------------
# D5 — audit sinks
# ---------------------------------------------------------------------------

class TestAuditSinks:
    def test_sqlite_sink_persists_the_event(self, tmp_path):
        from api.audit import AuditEvent, AuditEventType
        from api.audit_sinks import SQLiteAuditHandler

        db = tmp_path / "audit.db"
        handler = SQLiteAuditHandler(path=str(db))
        handler.handle(AuditEvent(
            event_type=AuditEventType.QUERY_EXECUTED,
            user_id="u-1",
            tenant_id="t-1",
            resource="sales",
            action="select",
            details={"row_count": 3},
        ))
        handler.close()

        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT event_type, user_id, tenant_id, resource, action, "
                "result, details FROM audit_events"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "query_executed"
        assert rows[0][1] == "u-1"
        assert rows[0][2] == "t-1"
        assert rows[0][3] == "sales"
        assert json.loads(rows[0][6]) == {"row_count": 3}

    def test_sqlite_sink_uses_wal(self, tmp_path):
        from api.audit_sinks import SQLiteAuditHandler

        db = tmp_path / "audit.db"
        handler = SQLiteAuditHandler(path=str(db))
        try:
            mode = handler._conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            handler.close()
        assert str(mode).lower() == "wal"

    def test_sqlite_sink_uses_parameterised_statements(self, tmp_path):
        from api.audit import AuditEvent
        from api.audit_sinks import SQLiteAuditHandler

        db = tmp_path / "audit.db"
        handler = SQLiteAuditHandler(path=str(db))
        # A value that would break a string-concatenated INSERT.
        handler.handle(AuditEvent(user_id="o'brien", resource="a'; DROP TABLE audit_events; --"))
        handler.close()

        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute("SELECT user_id, resource FROM audit_events").fetchall()
        assert rows == [("o'brien", "a'; DROP TABLE audit_events; --")]

    def test_redis_sink_appends_and_caps(self):
        from api.audit import AuditEvent
        from api.audit_sinks import RedisAuditHandler

        client = FakeRedis()
        handler = RedisAuditHandler(client=client, key="audit:events", max_entries=3)
        for i in range(5):
            handler.handle(AuditEvent(user_id=f"u-{i}"))
        assert client.llen("audit:events") == 3
        payloads = [json.loads(v) for v in client.lists["audit:events"]]
        assert [p["user_id"] for p in payloads] == ["u-2", "u-3", "u-4"]

    def test_audit_logger_uses_the_sqlite_sink(self, tmp_path, monkeypatch):
        from api.audit import AuditEvent, AuditLogger
        from api.audit_sinks import SQLiteAuditHandler

        db = tmp_path / "audit.db"
        monkeypatch.setenv("AUDIT_SINK", "sqlite")
        monkeypatch.setenv("DATAAGENT_AUDIT_DB", str(db))

        log = AuditLogger(config={"enabled": True, "destinations": []})
        assert any(isinstance(h, SQLiteAuditHandler) for h in log.handlers)
        log.log(AuditEvent(user_id="u-1"))
        log.close()

        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute("SELECT user_id FROM audit_events").fetchall()
        assert rows == [("u-1",)]

    def test_audit_logger_uses_the_redis_sink_when_redis_url_is_set(self, monkeypatch):
        from api.audit import AuditLogger
        from api.audit_sinks import RedisAuditHandler

        monkeypatch.delenv("AUDIT_SINK", raising=False)
        monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6379")
        log = AuditLogger(config={"enabled": True, "destinations": []})
        assert any(isinstance(h, RedisAuditHandler) for h in log.handlers)

    def test_default_audit_sink_stays_the_file_handler(self, monkeypatch):
        from api.audit import AuditLogger
        from api.audit_sinks import RedisAuditHandler, SQLiteAuditHandler

        monkeypatch.delenv("AUDIT_SINK", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        log = AuditLogger(config={"enabled": True, "destinations": [
            {"type": "file", "path": "logs/audit.log", "format": "json"},
        ]})
        assert not any(isinstance(h, (RedisAuditHandler, SQLiteAuditHandler))
                       for h in log.handlers)
        assert any(type(h).__name__ == "FileAuditHandler" for h in log.handlers)

    def test_audit_logger_survives_a_broken_sink(self, tmp_path, monkeypatch):
        from api.audit import AuditEvent, AuditLogger

        monkeypatch.setenv("AUDIT_SINK", "sqlite")
        monkeypatch.setenv("DATAAGENT_AUDIT_DB", str(tmp_path / "audit.db"))
        log = AuditLogger(config={"enabled": True, "destinations": []})

        class _Boom:
            def handle(self, event):
                raise RuntimeError("sink is down")

        log.handlers.append(_Boom())
        log.log(AuditEvent(user_id="u-1"))  # must not raise
        log.close()

    def test_audit_sink_env_resolution(self, monkeypatch):
        from api.audit_sinks import resolve_audit_sink

        monkeypatch.delenv("AUDIT_SINK", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert resolve_audit_sink() == "file"

        monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6379")
        assert resolve_audit_sink() == "redis"

        monkeypatch.setenv("AUDIT_SINK", "sqlite")
        assert resolve_audit_sink() == "sqlite"

        monkeypatch.setenv("AUDIT_SINK", "nonsense")
        assert resolve_audit_sink() == "file"


# ---------------------------------------------------------------------------
# D7 — release zip must not ship customer data
# ---------------------------------------------------------------------------

class TestSecureZip:
    def test_data_directory_is_excluded(self, tmp_path):
        bsz = _load_script("build_secure_zip")
        root = tmp_path / "tree"
        (root / "data" / "raw").mkdir(parents=True)
        (root / "data" / "raw" / "customer.csv").write_text("secret,data\n1,2\n")
        (root / "data" / "curated").mkdir(parents=True)
        (root / "data" / "curated" / "warehouse.duckdb").write_bytes(b"duck")
        (root / "data" / "users.db").write_bytes(b"sqlite")
        (root / "app.py").write_text("print('hi')\n")

        out = tmp_path / "out.zip"
        bsz.build(root, out, "tree")

        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert not any("/data/" in n or n.endswith("/data") for n in names), names
        assert not any(n.endswith("customer.csv") for n in names)
        assert not any(n.endswith("users.db") for n in names)
        assert any(n.endswith("app.py") for n in names)

    def test_secret_files_are_excluded(self, tmp_path):
        bsz = _load_script("build_secure_zip")
        root = tmp_path / "tree"
        (root / "config").mkdir(parents=True)
        (root / "config" / "users.json").write_text("{}")
        (root / ".env").write_text("SECRET=1")
        (root / "app.py").write_text("print('hi')\n")

        out = tmp_path / "out.zip"
        bsz.build(root, out, "tree")

        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert not any(n.endswith("users.json") for n in names)
        assert not any(n.endswith(".env") for n in names)

    def test_self_check_rejects_a_data_path(self):
        bsz = _load_script("build_secure_zip")
        violations = bsz.find_violations(["tree/data/raw/customer.csv"], "tree")
        assert violations == ["tree/data/raw/customer.csv"]

    def test_self_check_rejects_a_secret_file(self):
        bsz = _load_script("build_secure_zip")
        assert bsz.find_violations(["tree/.env"], "tree")
        assert bsz.find_violations(["tree/config/users.json"], "tree")
        assert bsz.find_violations(["tree/data/users.db"], "tree")

    def test_self_check_passes_a_clean_archive(self):
        bsz = _load_script("build_secure_zip")
        assert bsz.find_violations(["tree/app.py", "tree/api/main.py"], "tree") == []

    def test_static_web_geo_assets_still_ship(self):
        """``web/js/data`` is a static UI asset dir, not the runtime data tree."""
        bsz = _load_script("build_secure_zip")
        names = ["tree/web/js/data/cities.json.js"]
        assert bsz.find_violations(names, "tree") == []
        assert not bsz._is_excluded_dir(os.path.join("web", "js", "data"))

    def test_users_db_is_treated_as_secret(self):
        bsz = _load_script("build_secure_zip")
        assert bsz._is_sensitive_name("users.db")
        assert bsz._is_sensitive_name("users.json")

    def test_billing_order_store_is_treated_as_secret(self):
        bsz = _load_script("build_secure_zip")
        assert bsz._is_sensitive_name("orders.sqlite3")
        assert bsz._is_sensitive_name("orders.sqlite3-wal")
        assert bsz.find_violations(["tree/billing_server/orders.sqlite3"], "tree")

    def test_build_fails_when_the_archive_contains_a_data_path(self, tmp_path, monkeypatch):
        """The self-check is a hard gate, not a warning."""
        bsz = _load_script("build_secure_zip")
        root = tmp_path / "tree"
        root.mkdir()
        (root / "app.py").write_text("print('hi')\n")
        out = tmp_path / "out.zip"

        real_write = zipfile.ZipFile.write

        def _sneaky_write(self, filename, arcname=None, *args, **kwargs):
            if arcname and arcname.endswith("app.py"):
                arcname = os.path.join("tree", "data", "raw", "customer.csv")
            return real_write(self, filename, arcname, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "write", _sneaky_write)
        with pytest.raises(SystemExit):
            bsz.build(root, out, "tree")

    def test_backup_script_covers_the_identity_store(self):
        script = (REPO_ROOT / "scripts" / "backup.ps1").read_text(encoding="utf-8")
        assert "data/users.db" in script


# ---------------------------------------------------------------------------
# D5 — DuckDB connection reuse in the SQL tool
# ---------------------------------------------------------------------------

class TestSQLToolConnection:
    def test_app_warehouse_reuses_the_process_wide_connection(self, tmp_path, monkeypatch):
        import api.db as db_mod
        from agent.tools_sql import SQLTool
        from config import settings

        app_db = tmp_path / "app_warehouse.duckdb"
        # Seed the file first: the shared connection sets search_path to the
        # configured schema, which must already exist.
        seed = duckdb.connect(str(app_db))
        seed.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        seed.execute("CREATE TABLE analytics.sales (amount DOUBLE)")
        seed.execute("INSERT INTO analytics.sales VALUES (10.0)")
        seed.close()

        monkeypatch.setattr(settings.duckdb, "database_path", str(app_db))
        monkeypatch.setattr(db_mod, "_conn", None)

        conn = db_mod.get_connection()
        assert conn is not None

        opened: list = []
        real_connect = duckdb.connect

        def _counting_connect(*args, **kwargs):
            opened.append(args)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(duckdb, "connect", _counting_connect)
        try:
            tool = SQLTool(db_path=str(app_db), schema="analytics")
            result = tool.execute("SELECT SUM(amount) AS total FROM analytics.sales")
            assert result.success, result.error
            assert float(result.data.iloc[0, 0]) == 10.0
            assert opened == [], "a second connection was opened to the app warehouse"
        finally:
            db_mod.close_connection()

    def test_other_files_still_get_their_own_connection(self, tmp_path):
        from agent.tools_sql import SQLTool

        db = tmp_path / "other.duckdb"
        con = duckdb.connect(str(db))
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.t (x INTEGER)")
        con.execute("INSERT INTO analytics.t VALUES (7)")
        con.close()

        tool = SQLTool(db_path=str(db), schema="analytics")
        result = tool.execute("SELECT x FROM analytics.t")
        assert result.success, result.error
        assert int(result.data.iloc[0, 0]) == 7
