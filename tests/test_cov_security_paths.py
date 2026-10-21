"""Branch coverage for security-path helpers, billing gate/seed/redemption, api-keys.

TDD: behavior assertions only — no snapshot locking. Every test targets an
uncovered branch from the coverage report:

- api/security_paths.py — sanitize_upload_name (None/empty/forbidden/
  absolute/``..``), resolve_under (happy/traversal/escape/target==base),
  validate_table_name (None/empty/bad chars/too long).
- api/billing/gate.py — require_feature allow/deny, dev backdoor on/off,
  _is_frozen try/except branches.
- api/billing/schema.py — get_connection shared/fallback branches,
  _ensure_schema, init_billing_tables error swallow, current_period.
- api/billing/seed.py — seed idempotency, _env_int fallback, currency
  fallback, plan_price hit/miss.
- api/billing/redemption.py — local claim roundtrip/first-wins/miss/error,
  redeem_remote all status branches.
- api/routes/api_keys.py — scopes-as-string (line 33), rotate 404 (87-88),
  create 422, revoke 404.
"""

from __future__ import annotations

import asyncio
import re
import sys
import types
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

# ─── security_paths: sanitize_upload_name ────────────────────────────────────


def test_sanitize_none_rejected():
    from api.security_paths import sanitize_upload_name

    with pytest.raises(HTTPException) as ei:
        sanitize_upload_name(None)
    assert ei.value.status_code == 400


@pytest.mark.parametrize("bad", ["", "   ", "\t\n "])
def test_sanitize_empty_rejected(bad):
    from api.security_paths import sanitize_upload_name

    with pytest.raises(HTTPException) as ei:
        sanitize_upload_name(bad)
    assert ei.value.status_code == 400


@pytest.mark.parametrize("bad", ["a/b.csv", "a\\b.csv", "a\x00b", "/abs.csv"])
def test_sanitize_forbidden_chars_rejected(bad):
    from api.security_paths import sanitize_upload_name

    with pytest.raises(HTTPException) as ei:
        sanitize_upload_name(bad)
    assert ei.value.status_code == 400


@pytest.mark.parametrize("bad", [".", "..", "  ..  "])
def test_sanitize_dot_segments_rejected(bad):
    from api.security_paths import sanitize_upload_name

    with pytest.raises(HTTPException) as ei:
        sanitize_upload_name(bad)
    assert ei.value.status_code == 400


def test_sanitize_absolute_path_rejected(monkeypatch):
    """Path.is_absolute() branch: bypass the char filter so the absolute
    check itself is exercised (covers line 39 on every platform)."""
    import api.security_paths as sp

    monkeypatch.setattr(sp, "_FORBIDDEN_CHARS", ())
    with pytest.raises(HTTPException) as ei:
        sp.sanitize_upload_name("/abs.csv")
    assert ei.value.status_code == 400


def test_sanitize_dotdot_parts_rejected(monkeypatch):
    """".." inside path.parts branch (covers line 39 without the char filter)."""
    import api.security_paths as sp

    monkeypatch.setattr(sp, "_FORBIDDEN_CHARS", ())
    with pytest.raises(HTTPException) as ei:
        sp.sanitize_upload_name("a/../b")
    assert ei.value.status_code == 400


def test_sanitize_valid_and_stripped():
    from api.security_paths import sanitize_upload_name

    assert sanitize_upload_name("report.csv") == "report.csv"
    assert sanitize_upload_name("  data.parquet  ") == "data.parquet"
    assert sanitize_upload_name("...") == "..."


# ─── security_paths: resolve_under ───────────────────────────────────────────


def test_resolve_under_happy_path(tmp_path):
    from api.security_paths import resolve_under

    assert resolve_under(tmp_path, "f.csv") == tmp_path.resolve() / "f.csv"


@pytest.mark.parametrize("bad", ["..", "../e.csv", "a/b", "a\\b"])
def test_resolve_under_traversal_rejected(tmp_path, bad):
    from api.security_paths import resolve_under

    with pytest.raises(HTTPException) as ei:
        resolve_under(tmp_path, bad)
    assert ei.value.status_code == 400


def test_resolve_under_escape_rejected(tmp_path, monkeypatch):
    """Resolved target outside base (e.g. symlink escape) raises
    (covers line 52)."""
    from pathlib import Path

    from api.security_paths import resolve_under

    monkeypatch.setattr(Path, "is_relative_to", lambda self, other: False)
    with pytest.raises(HTTPException) as ei:
        resolve_under(tmp_path, "f.csv")
    assert ei.value.status_code == 400


def test_resolve_under_target_equals_base_rejected(tmp_path, monkeypatch):
    """Degenerate resolve where target collapses onto base raises
    (covers the ``target == base`` arm of line 52)."""
    from pathlib import Path

    import api.security_paths as sp

    monkeypatch.setattr(
        Path, "resolve", lambda self, *a, **k: Path(str(tmp_path))
    )
    with pytest.raises(HTTPException) as ei:
        sp.resolve_under(tmp_path, "f.csv")
    assert ei.value.status_code == 400


# ─── security_paths: validate_table_name ─────────────────────────────────────


def test_validate_table_name_valid():
    from api.security_paths import validate_table_name

    assert validate_table_name("sales") == "sales"
    assert validate_table_name("_x1") == "_x1"
    assert validate_table_name("A" * 63) == "A" * 63


@pytest.mark.parametrize(
    "bad",
    [None, "", "a b", "t;DROP", '"x"', "a-b", "9lives", "a/b", "sales\n", "tâble"],
)
def test_validate_table_name_invalid(bad):
    from api.security_paths import validate_table_name

    with pytest.raises(HTTPException) as ei:
        validate_table_name(bad)
    assert ei.value.status_code == 400


def test_validate_table_name_too_long():
    from api.security_paths import validate_table_name

    with pytest.raises(HTTPException) as ei:
        validate_table_name("x" * 64)
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        validate_table_name(123)


# ─── billing/gate.py ─────────────────────────────────────────────────────────


def _request():
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _user(uid="u1"):
    return SimpleNamespace(user_id=uid)


def test_is_frozen_import_failure_returns_false(monkeypatch):
    """Except branch: None in sys.modules makes the import raise."""
    from api.billing import gate

    monkeypatch.setitem(sys.modules, "keygen_app.config", None)
    assert gate._is_frozen() is False


def test_is_frozen_true_branch(monkeypatch):
    from api.billing import gate

    fake = types.ModuleType("keygen_app.config")
    fake.is_frozen = lambda: True
    monkeypatch.setitem(sys.modules, "keygen_app", types.ModuleType("keygen_app"))
    monkeypatch.setitem(sys.modules, "keygen_app.config", fake)
    assert gate._is_frozen() is True


def test_gate_allow_branch(monkeypatch):
    from api.billing import gate

    monkeypatch.setattr(gate, "has_feature", lambda uid, key, plan: True)
    checker = gate.require_feature("dashboard_export")
    assert asyncio.run(checker(_request(), _user())) is None


def test_gate_deny_branch(monkeypatch):
    from api.billing import gate

    monkeypatch.setattr(gate, "has_feature", lambda uid, key, plan: False)
    checker = gate.require_feature("dashboard_export")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(checker(_request(), _user()))
    assert ei.value.status_code == 403
    assert ei.value.headers["X-Upgrade-Required"] == "true"
    assert ei.value.headers["X-Required-Feature"] == "dashboard_export"


def test_gate_dev_backdoor_skips_has_feature(monkeypatch):
    """DATAGENT_DEV_FEATURES=1 grants recreate_from_image without consulting
    entitlements (has_feature must not even be called)."""
    from api.billing import gate

    def _boom(uid, key, plan):
        raise AssertionError("has_feature must not be consulted")

    monkeypatch.setattr(gate, "has_feature", _boom)
    monkeypatch.setenv("DATAGENT_DEV_FEATURES", "1")
    monkeypatch.setattr(gate, "_is_frozen", lambda: False)
    checker = gate.require_feature("recreate_from_image")
    assert asyncio.run(checker(_request(), _user())) is None


def test_gate_backdoor_disabled_when_frozen(monkeypatch):
    """Frozen build: dev backdoor ignored, falls through to deny."""
    from api.billing import gate

    monkeypatch.setattr(gate, "has_feature", lambda uid, key, plan: False)
    monkeypatch.setenv("DATAGENT_DEV_FEATURES", "1")
    monkeypatch.setattr(gate, "_is_frozen", lambda: True)
    checker = gate.require_feature("recreate_from_image")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(checker(_request(), _user()))
    assert ei.value.status_code == 403


def test_gate_backdoor_only_applies_to_recreate(monkeypatch):
    """Dev backdoor does not leak to other features."""
    from api.billing import gate

    monkeypatch.setattr(gate, "has_feature", lambda uid, key, plan: False)
    monkeypatch.setenv("DATAGENT_DEV_FEATURES", "1")
    monkeypatch.setattr(gate, "_is_frozen", lambda: False)
    checker = gate.require_feature("dashboard_export")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(checker(_request(), _user()))
    assert ei.value.status_code == 403


# ─── billing/schema.py ───────────────────────────────────────────────────────


class _FakeCtxCon:
    """Minimal context-manager connection recording executed SQL."""

    def __init__(self):
        self.sql = []

    def execute(self, sql, *a, **k):
        self.sql.append(sql)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeLoader:
    def __init__(self, con):
        self._con = con

    def get_connection(self):
        return self._con


def test_get_connection_uses_the_shared_connection():
    """The billing connection is the process-wide shared one.

    The loader opened a *fresh* DuckDB handle per call, which is the
    "a different configuration than existing connections" failure against
    the same warehouse file.
    """
    from api.db import get_connection as shared_connection
    from api.billing import schema

    with schema.get_connection() as con:
        assert con is shared_connection()


def test_get_connection_never_builds_a_loader(monkeypatch):
    """No fallback loader connection: one connection, one configuration."""
    import api.state as state
    from api.billing import schema

    monkeypatch.setattr(state, "loader", None)
    with schema.get_connection() as con:
        assert con is not None
    assert state.loader is None


def test_ensure_schema_runs_create():
    from api.billing import schema

    con = _FakeCtxCon()
    schema._ensure_schema(con)
    assert any("CREATE SCHEMA" in s for s in con.sql)


def test_init_billing_tables_error_swallowed(monkeypatch):
    """DB failure during init is logged + skipped, never raised."""
    from api.billing import schema

    def _boom():
        raise RuntimeError("warehouse locked")

    monkeypatch.setattr(schema, "get_connection", _boom)
    assert schema.init_billing_tables() is None


def test_init_billing_tables_alter_except_covered(monkeypatch):
    """ADD COLUMN that raises still leaves init successful (covers the
    Google-identity column except-pass, lines 187-188)."""

    from api.billing import schema

    class _SelectiveCon(_FakeCtxCon):
        def execute(self, sql, *a, **k):
            self.sql.append(sql)
            if sql.lstrip().upper().startswith("ALTER"):
                raise RuntimeError("no such column")
            return self

    monkeypatch.setattr(schema, "get_connection", lambda: _SelectiveCon())
    assert schema.init_billing_tables() is None


def test_seed_migrates_legacy_premium(billing_db):
    """Legacy analytics.user_subscriptions rows move premium -> pro
    (covers the legacy migration UPDATE)."""
    from api.billing.schema import SCHEMA, get_connection
    from api.billing.seed import seed_billing_data

    with get_connection() as con:
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA}.user_subscriptions (plan VARCHAR)"
        )
        con.execute(f"DELETE FROM {SCHEMA}.user_subscriptions")
        con.execute(f"INSERT INTO {SCHEMA}.user_subscriptions VALUES ('premium')")
    seed_billing_data()
    with get_connection() as con:
        rows = con.execute(
            f"SELECT plan FROM {SCHEMA}.user_subscriptions"
        ).fetchall()
    assert rows and all(r[0] == "pro" for r in rows)


def _ai_cap(con, schema_mod, plan_id):
    return con.execute(
        f"""SELECT limit_value FROM {schema_mod}.plan_limits
            WHERE plan_id = ? AND metric = 'ai_requests'""",
        [plan_id],
    ).fetchone()[0]


def test_seed_ai_caps_insert_path(billing_db, monkeypatch):
    """Missing limit rows are inserted with the env-configured caps."""
    from api.billing.schema import SCHEMA, get_connection
    from api.billing.seed import seed_billing_data

    with get_connection() as con:
        con.execute(f"DELETE FROM {SCHEMA}.plan_limits")
    monkeypatch.setenv("BILLING_FREE_AI_DAILY_CAP", "10")
    monkeypatch.setenv("BILLING_PRO_AI_DAILY_CAP", "7")
    seed_billing_data()
    with get_connection() as con:
        assert _ai_cap(con, SCHEMA, "plan_free") == 10
        assert _ai_cap(con, SCHEMA, "plan_pro") == 7


def test_seed_ai_caps_update_path(billing_db, monkeypatch):
    """Existing limit rows are kept in sync with the env-configured caps."""
    from api.billing.schema import SCHEMA, get_connection
    from api.billing.seed import seed_billing_data

    monkeypatch.delenv("BILLING_FREE_AI_DAILY_CAP", raising=False)
    monkeypatch.delenv("BILLING_PRO_AI_DAILY_CAP", raising=False)
    seed_billing_data()
    monkeypatch.setenv("BILLING_FREE_AI_DAILY_CAP", "11")
    monkeypatch.setenv("BILLING_PRO_AI_DAILY_CAP", "8")
    seed_billing_data()
    with get_connection() as con:
        assert _ai_cap(con, SCHEMA, "plan_free") == 11
        assert _ai_cap(con, SCHEMA, "plan_pro") == 8


def test_current_period_format():
    from api.billing.schema import current_period

    assert re.fullmatch(r"\d{4}-\d{2}", current_period())


# ─── billing/seed.py ─────────────────────────────────────────────────────────


def test_env_int_fallback_and_bad_value(monkeypatch):
    from api.billing import seed

    monkeypatch.delenv("BILLING_FREE_AI_DAILY_CAP", raising=False)
    assert seed.get_free_ai_daily_cap() == 25
    monkeypatch.setenv("BILLING_FREE_AI_DAILY_CAP", "notanint")
    assert seed.get_free_ai_daily_cap() == 25
    monkeypatch.setenv("BILLING_FREE_AI_DAILY_CAP", "10")
    assert seed.get_free_ai_daily_cap() == 10
    monkeypatch.setenv("BILLING_PRO_AI_DAILY_CAP", "7")
    assert seed.get_pro_ai_daily_cap() == 7


def test_get_currency_fallbacks(monkeypatch):
    from api.billing import seed

    monkeypatch.delenv("BILLING_CURRENCY", raising=False)
    assert seed.get_currency() == "USD"
    monkeypatch.setenv("BILLING_CURRENCY", "")
    assert seed.get_currency() == "USD"
    monkeypatch.setenv("BILLING_CURRENCY", "EUR")
    assert seed.get_currency() == "EUR"


def test_seed_idempotent(billing_db):
    """Second + third seed runs change nothing (covers insert-skip and
    currency/amount + AI-cap update branches)."""
    from api.billing.schema import SCHEMA, get_connection
    from api.billing.seed import seed_billing_data

    def counts():
        with get_connection() as con:
            return {
                t: con.execute(
                    f"SELECT count(*) FROM {SCHEMA}.{t}"
                ).fetchone()[0]
                for t in ("plans", "plan_prices", "plan_features", "plan_limits")
            }

    before = counts()
    seed_billing_data()
    seed_billing_data()
    after = counts()
    assert before == after
    assert after == {
        "plans": 3,
        "plan_prices": 4,
        "plan_features": 60,
        "plan_limits": 9,
    }


def test_seed_error_swallowed(monkeypatch):
    from api.billing import seed

    def _boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(seed, "get_connection", _boom)
    assert seed.seed_billing_data() is None


def test_plan_price_hit_and_miss(billing_db):
    from api.billing.seed import plan_price

    row = plan_price("pro", "MONTHLY")
    assert row is not None
    assert row["plan_id"] == "plan_pro"
    assert row["amount"] == 600
    assert row["currency"]
    assert plan_price("nope", "MONTHLY") is None


# ─── billing/redemption.py ───────────────────────────────────────────────────


def test_local_claim_roundtrip_first_wins(billing_db):
    from api.billing import redemption

    assert redemption.local_claim("h-absent") is None
    redemption.record_local_claim("h1", "user-a", "User@Example.COM")
    row = redemption.local_claim("h1")
    assert row["user_id"] == "user-a"
    assert row["email"] == "user@example.com"
    # Second account cannot steal the key: first write wins.
    redemption.record_local_claim("h1", "user-b", "b@x.com")
    assert redemption.local_claim("h1")["user_id"] == "user-a"


def test_local_claim_error_returns_none(monkeypatch):
    from api.billing import redemption

    def _boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(redemption, "get_connection", _boom)
    assert redemption.local_claim("anything") is None


def test_is_remote_configured_passthrough(monkeypatch):
    from api.billing import redemption
    from api.billing import cloud_sync

    monkeypatch.setattr(cloud_sync, "is_configured", lambda: True)
    assert redemption.is_remote_configured() is True
    monkeypatch.setattr(cloud_sync, "is_configured", lambda: False)
    assert redemption.is_remote_configured() is False


class _Resp:
    def __init__(self, status, payload=None, bad_json=False):
        self.status_code = status
        self._payload = payload or {}
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


def _remote(monkeypatch, base, secret, resp=None, exc=None):
    from api.billing import redemption
    from api.billing import cloud_sync
    import httpx

    monkeypatch.setattr(cloud_sync, "billing_sync_base_url", lambda: base)
    monkeypatch.setattr(cloud_sync, "billing_sync_secret", lambda: secret)
    if exc is not None:
        def _raise(*a, **k):
            raise exc

        monkeypatch.setattr(httpx, "post", _raise)
    else:
        monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)
    return redemption


def test_redeem_remote_not_configured(monkeypatch):
    mod = _remote(monkeypatch, "", "")
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "unreachable"}


def test_redeem_remote_post_error(monkeypatch):
    mod = _remote(monkeypatch, "https://bill.example", "s3cret",
                  exc=ConnectionError("down"))
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "unreachable"}


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"status": "redeemed"}, "redeemed"),
        ({"status": "already_owner"}, "already_owner"),
    ],
)
def test_redeem_remote_grants(monkeypatch, payload, expected):
    mod = _remote(monkeypatch, "https://bill.example", "s3cret",
                  resp=_Resp(200, payload))
    assert mod.redeem_remote("k", "h", " E@X.com ", "u") == {"status": expected}


@pytest.mark.parametrize("resp", [_Resp(200, {"status": "weird"}), _Resp(200, bad_json=True)])
def test_redeem_remote_200_fail_closed(monkeypatch, resp):
    mod = _remote(monkeypatch, "https://bill.example", "s3cret", resp=resp)
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "unreachable"}


def test_redeem_remote_409_variants(monkeypatch):
    mod = _remote(monkeypatch, "https://bill.example", "s3cret",
                  resp=_Resp(409, {"detail": {"status": "refunded"}}))
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "refunded"}
    mod = _remote(monkeypatch, "https://bill.example", "s3cret",
                  resp=_Resp(409, {"detail": {"status": "other"}}))
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "conflict"}
    mod = _remote(monkeypatch, "https://bill.example", "s3cret",
                  resp=_Resp(409, bad_json=True))
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "conflict"}


def test_redeem_remote_404_and_500(monkeypatch):
    mod = _remote(monkeypatch, "https://bill.example", "s3cret", resp=_Resp(404))
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "unknown"}
    mod = _remote(monkeypatch, "https://bill.example", "s3cret", resp=_Resp(500))
    assert mod.redeem_remote("k", "h", "e@x.com", "u") == {"status": "unreachable"}


# ─── routes/api_keys.py ──────────────────────────────────────────────────────


def _key_user():
    return SimpleNamespace(user_id="u1", tenant_id="t1")


def test_create_key_missing_name_422():
    from api.routes import api_keys as ak

    with pytest.raises(HTTPException) as ei:
        asyncio.run(ak.create_api_key({}, user=_key_user()))
    assert ei.value.status_code == 422


def test_create_key_scopes_string_split(monkeypatch):
    """Comma-string scopes are split into a list (covers line 33)."""
    from api.routes import api_keys as ak

    seen = {}

    def _fake_create(**kwargs):
        seen.update(kwargs)
        return (
            SimpleNamespace(id="k1", expires_at=datetime(2027, 1, 1)),
            "raw-123",
        )

    monkeypatch.setattr(ak.api_key_manager, "create_key", _fake_create)
    monkeypatch.setattr(ak.audit_logger, "log_api_key_created",
                        lambda **k: None)
    body = asyncio.run(
        ak.create_api_key(
            {"name": "k", "scopes": "read, write ,", "expires_days": 30},
            user=_key_user(),
        )
    )
    assert body["success"] is True
    assert body["scopes"] == ["read", "write"]
    assert seen["scopes"] == ["read", "write"]
    assert body["api_key"] == "raw-123"
    assert body["expires_at"] == datetime(2027, 1, 1).isoformat()


def test_create_key_no_expiry(monkeypatch):
    from api.routes import api_keys as ak

    def _fake_create(**kwargs):
        return (SimpleNamespace(id="k2", expires_at=None), "raw-x")

    monkeypatch.setattr(ak.api_key_manager, "create_key", _fake_create)
    monkeypatch.setattr(ak.audit_logger, "log_api_key_created",
                        lambda **k: None)
    body = asyncio.run(ak.create_api_key({"name": "k"}, user=_key_user()))
    assert body["expires_at"] is None


def test_list_keys(monkeypatch):
    from api.routes import api_keys as ak

    monkeypatch.setattr(ak.api_key_manager, "list_keys",
                        lambda uid: [{"id": "k1"}])
    body = asyncio.run(ak.list_api_keys(user=_key_user()))
    assert body == {"keys": [{"id": "k1"}]}


def test_revoke_missing_key_404(monkeypatch):
    from api.routes import api_keys as ak

    monkeypatch.setattr(ak.api_key_manager, "revoke_key",
                        lambda uid, kid: False)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ak.revoke_api_key("nope", user=_key_user()))
    assert ei.value.status_code == 404


def test_revoke_ok(monkeypatch):
    from api.routes import api_keys as ak

    monkeypatch.setattr(ak.api_key_manager, "revoke_key",
                        lambda uid, kid: True)
    monkeypatch.setattr(ak.audit_logger, "log_api_key_revoked",
                        lambda **k: None)
    body = asyncio.run(ak.revoke_api_key("k1", user=_key_user()))
    assert body == {"success": True, "message": "API key revoked"}


def test_rotate_missing_key_404(monkeypatch):
    """Rotate ValueError maps to 404 (covers lines 87-88)."""
    from api.routes import api_keys as ak

    def _boom(uid, kid):
        raise ValueError("Key not found")

    monkeypatch.setattr(ak.api_key_manager, "rotate_key", _boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ak.rotate_api_key("nope", user=_key_user()))
    assert ei.value.status_code == 404


def test_rotate_ok(monkeypatch):
    from api.routes import api_keys as ak

    def _fake_rotate(uid, kid):
        return (
            SimpleNamespace(id="k9", name="rk",
                            scopes={"read"}, expires_at=None),
            "raw-new",
        )

    monkeypatch.setattr(ak.api_key_manager, "rotate_key", _fake_rotate)
    monkeypatch.setattr(ak.audit_logger, "log_api_key_created",
                        lambda **k: None)
    body = asyncio.run(ak.rotate_api_key("k1", user=_key_user()))
    assert body["success"] is True
    assert body["api_key"] == "raw-new"
    assert body["scopes"] == ["read"]
