"""Regression tests for the launch-remediation items S6, S7, S8 and S9.

The findings come from ``docs/LAUNCH-FIX-PLAN.md`` (the P1 security table):

* **S6** — the RBAC middleware's docstring claimed "deny-by-default for
  mutating verbs", but ``MUTATING_METHODS`` was defined and never used:
  an unmapped ``POST``/``PUT``/``PATCH``/``DELETE`` was allowed through for
  *every* role, so ~100 mutating routes were reachable by ``viewer``.
* **S7** — API-key authentication built a ``User`` without a ``tenant_id``,
  so ``resolve_tenant_id()`` returned ``default`` and the key landed in the
  platform ``analytics`` schema: the operator's own data.
* **S8** — the ``query_history`` table was only ever
  ``CREATE TABLE IF NOT EXISTS``, so a table created by an older release
  kept its old shape and every query against the new columns raised a
  ``BinderException`` (order-dependent failures between two test files).
* **S9** — ``POST /clean`` read the flat ``data/raw`` directory while
  ``POST /upload`` wrote the caller's *tenant* directory, so a non-default
  tenant always got "File not found".

Every test here is written to fail against the pre-fix code.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


# ── helpers ────────────────────────────────────────────────────────────


def _request(path: str = "/probe", method: str = "GET", headers=None):
    """A minimal Starlette request (no server, no sockets)."""
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "headers": raw,
        "query_string": b"",
        "client": ("127.0.0.1", 5555),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "http_version": "1.1",
    }
    return Request(scope)


class _DummySessionManager:
    """A session manager that hands out a fixed session (or none)."""

    def __init__(self, session=None):
        self._session = session

    def get_session(self, session_id):
        return self._session

    def refresh_session(self, session_id):  # pragma: no cover - trivial
        return None


class _FakeKeyManager:
    """An API-key manager that accepts exactly one key."""

    def __init__(self, context=None, raw_key: str = "da_valid_key"):
        self._context = context
        self._raw_key = raw_key

    def validate_key(self, raw_key, required_scope=None):
        if self._context is None or raw_key != self._raw_key:
            return None
        return self._context


def _key_context():
    return SimpleNamespace(
        user_id="u-acme",
        key_id="key-1",
        scopes={"read", "write"},
        tenant_id=None,  # the context carries no tenant — see the report
    )


def _user_record(**overrides):
    record = {
        "user_id": "u-acme",
        "username": "acme_bot",
        "role": "user",
        "email": "bot@acme.invalid",
        "is_active": True,
        "is_admin": False,
    }
    record.update(overrides)
    return record


def _auth_middleware(api_key_manager=None, session_manager=None):
    from api.middleware.auth_middleware import AuthMiddleware

    return AuthMiddleware(
        app=None,
        session_manager=session_manager or _DummySessionManager(),
        api_key_manager=api_key_manager or _FakeKeyManager(_key_context()),
    )


# ── S7: API-key auth must bind a tenant ────────────────────────────────


def test_api_key_auth_resolves_to_the_key_owners_tenant(monkeypatch):
    """An API-key request must land in the key owner's tenant.

    Before the fix the API-key branch built a ``User`` with no
    ``tenant_id`` at all, so ``resolve_tenant_id()`` returned ``default``
    and ``tenant_schema()`` returned the *platform* ``analytics`` schema —
    the operator's own data.
    """
    import api.middleware.auth_middleware as auth_mod
    from api.tenancy import resolve_tenant_id, tenant_schema

    monkeypatch.setattr(
        auth_mod.user_manager,
        "get_user_info_by_id",
        lambda user_id: _user_record(tenant_id="acme"),
    )

    middleware = _auth_middleware()
    user = asyncio.run(
        middleware._authenticate_api_key(_request(headers={"X-API-Key": "da_valid_key"}))
    )

    assert user is not None, "the API key was not accepted"
    assert user.tenant_id == "acme", (
        f"the API-key user must carry the owner's tenant, got {user.tenant_id!r}"
    )
    assert resolve_tenant_id(user) == "acme"
    assert tenant_schema(resolve_tenant_id(user)) != "analytics", (
        "an API key must never resolve to the platform schema"
    )


def test_bearer_api_key_fallback_resolves_to_the_key_owners_tenant(monkeypatch):
    """The ``Authorization: Bearer <api-key>`` fallback must bind a tenant too."""
    import api.middleware.auth_middleware as auth_mod
    from api.tenancy import resolve_tenant_id, tenant_schema

    monkeypatch.setattr(
        auth_mod.user_manager,
        "get_user_info_by_id",
        lambda user_id: _user_record(tenant_id="acme"),
    )

    middleware = _auth_middleware()
    user = asyncio.run(
        middleware._authenticate_bearer(
            _request(headers={"Authorization": "Bearer da_valid_key"})
        )
    )

    assert user is not None, "the bearer API key was not accepted"
    assert user.auth_method == "api_key"
    assert user.tenant_id == "acme", (
        f"the bearer API-key user must carry the owner's tenant, got {user.tenant_id!r}"
    )
    assert resolve_tenant_id(user) == "acme"
    assert tenant_schema(resolve_tenant_id(user)) != "analytics"


def test_api_key_user_without_a_tenant_still_resolves_to_default(monkeypatch):
    """A user with no tenant must fall back to ``default`` — no crash."""
    import api.middleware.auth_middleware as auth_mod
    from api.tenancy import DEFAULT_TENANT_ID, resolve_tenant_id, tenant_schema

    monkeypatch.setattr(
        auth_mod.user_manager,
        "get_user_info_by_id",
        lambda user_id: _user_record(tenant_id=""),
    )

    middleware = _auth_middleware()
    user = asyncio.run(
        middleware._authenticate_api_key(_request(headers={"X-API-Key": "da_valid_key"}))
    )

    assert user is not None
    assert not user.tenant_id
    assert resolve_tenant_id(user) == DEFAULT_TENANT_ID
    assert tenant_schema(resolve_tenant_id(user)) == "analytics"


def test_session_path_still_binds_the_session_tenant(monkeypatch):
    """Regression: the session path keeps using ``session.tenant_id``."""
    import api.middleware.auth_middleware as auth_mod

    monkeypatch.setattr(
        auth_mod.user_manager,
        "get_user_info_by_id",
        lambda user_id: _user_record(tenant_id="ignored"),
    )
    session = SimpleNamespace(user_id="u-acme", tenant_id="session-tenant")

    middleware = _auth_middleware(session_manager=_DummySessionManager(session))
    user = asyncio.run(
        middleware._authenticate_session(
            _request(headers={"Cookie": "session_id=abc"})
        )
    )

    assert user is not None
    assert user.auth_method == "session"
    assert user.tenant_id == "session-tenant"


def test_jwt_path_still_binds_the_user_tenant(monkeypatch):
    """Regression: the JWT path keeps using the user record's tenant."""
    import api.middleware.auth_middleware as auth_mod

    monkeypatch.setattr(
        auth_mod.user_manager,
        "get_user_info",
        lambda username: _user_record(tenant_id="jwt-tenant"),
    )
    monkeypatch.setattr("api.auth.verify_jwt_token", lambda token: {"sub": "acme_bot"})

    middleware = _auth_middleware()
    user = asyncio.run(
        middleware._authenticate_bearer(
            _request(headers={"Authorization": "Bearer a.jwt.token"})
        )
    )

    assert user is not None
    assert user.auth_method == "jwt"
    assert user.tenant_id == "jwt-tenant"


# ── S6: RBAC must be deny-by-default for mutating verbs ────────────────


def _client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, raise_server_exceptions=False)


def _headers_for(username: str, role: str):
    from api.auth import create_jwt_token, user_manager

    if not user_manager.get_user_info(username):
        user_manager.add_user(username, "Str0ng!Passw0rd", role)
    return {"Authorization": f"Bearer {create_jwt_token(username, role=role)}"}


def test_unmapped_mutating_route_is_refused_for_viewer_and_user():
    """A mutating request on a path with no rule must be refused (403).

    The RBAC middleware runs before the router, so an unmapped ``POST`` is
    refused even when no route matches it.
    """
    from api.middleware.rbac_middleware import _required_permission

    client = _client()
    for role in ("viewer", "user"):
        headers = _headers_for(f"rbac_unmapped_{role}", role)
        r = client.post("/definitely-not-a-route", headers=headers)
        assert r.status_code == 403, (
            f"an unmapped POST must be refused for the {role!r} role, got {r.status_code}"
        )
        assert "Permission denied" in r.text

    # The lookup itself must report the refusal, not "no permission needed".
    assert _required_permission("/definitely-not-a-route", "POST") is not None


def test_unmapped_get_route_is_still_allowed():
    """Reads stay open where they are open today (404, not 403)."""
    client = _client()
    headers = _headers_for("rbac_unmapped_get", "viewer")
    r = client.get("/definitely-not-a-route", headers=headers)
    assert r.status_code == 404, (
        f"an unmapped GET must not be refused by RBAC, got {r.status_code}"
    )


def test_admin_role_still_has_full_access():
    """The admin bypass must survive the deny-by-default change."""
    client = _client()
    headers = _headers_for("rbac_admin_bypass", "admin")
    r = client.post("/definitely-not-a-route", headers=headers)
    assert r.status_code == 404, (
        f"an admin must not be refused by RBAC, got {r.status_code}"
    )


def test_every_mutating_route_has_a_permission_rule():
    """Structural guard: no mutating route may be left unregistered.

    The route table is walked (including the routers that are included into
    the app) and every ``POST``/``PUT``/``PATCH``/``DELETE`` route must
    resolve to a permission through the same matcher the middleware uses.
    """
    from api.main import app
    from api.middleware.rbac_middleware import (
        MUTATING_METHODS,
        OPEN_PATHS,
        _required_permission,
    )

    uncovered = []
    mutating = 0
    for path, methods in _iter_app_routes(app):
        if not (methods & MUTATING_METHODS):
            continue
        mutating += 1
        concrete = _concretise(path)
        for method in sorted(methods & MUTATING_METHODS):
            if concrete in OPEN_PATHS:
                continue
            permission = _required_permission(concrete, method)
            if not permission:
                uncovered.append(f"{method} {path}")
            elif permission == "rbac:unmapped":
                uncovered.append(f"{method} {path} (unmapped)")

    # The walk must actually see the app's routes — otherwise the guard
    # would pass vacuously.
    assert mutating >= 100, (
        f"the route walk found only {mutating} mutating routes — the "
        "enumeration is broken, so the guard would pass vacuously"
    )

    assert not uncovered, (
        "every mutating route must be registered in PATH_PERMISSIONS / "
        "PREFIX_PERMISSIONS (or in OPEN_PATHS). Unregistered:\n  "
        + "\n  ".join(sorted(set(uncovered)))
    )


def test_a_registered_mutating_route_still_works_for_the_user_role():
    """Regression: a registered route keeps working for the ``user`` role."""
    client = _client()
    headers = _headers_for("rbac_user_role", "user")
    r = client.post("/user/query-history", json={"query": "select 1"}, headers=headers)
    assert r.status_code == 200, (
        f"a registered mutating route must work for the 'user' role, got "
        f"{r.status_code}: {r.text[:200]}"
    )


def test_viewer_is_refused_the_data_plane():
    """A ``viewer`` must not be able to write data."""
    client = _client()
    headers = _headers_for("rbac_viewer_write", "viewer")
    r = client.post("/upload", headers=headers)
    assert r.status_code == 403, (
        f"a viewer must not be able to upload, got {r.status_code}"
    )


def test_viewer_is_refused_the_other_export_formats():
    """The html/pptx/pdf/markdown exports must not be a way around the map."""
    client = _client()
    headers = _headers_for("rbac_viewer_export", "viewer")
    for fmt in ("html", "pptx", "pdf", "markdown"):
        r = client.get(f"/export/{fmt}/sales", headers=headers)
        assert r.status_code == 403, (
            f"a viewer must not be able to export {fmt}, got {r.status_code}"
        )


def _iter_app_routes(app):
    """Yield ``(path, methods)`` for every route of *app*, included routers too."""
    def walk(router, prefix=""):
        for route in getattr(router, "routes", []):
            context = getattr(route, "include_context", None)
            if context is not None:
                included = getattr(context, "included_router", None)
                sub_prefix = prefix + (getattr(context, "prefix", "") or "")
                if included is not None:
                    yield from walk(included, sub_prefix)
                continue
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path and methods:
                yield prefix + path, set(methods)

    yield from walk(app.router)


def _concretise(path: str) -> str:
    """Turn a route pattern into the concrete path a request would carry.

    ``/tables/{table_name}`` becomes ``/tables/x`` — the path a client
    actually sends.  The parameter is *not* stripped: a rule that only
    matches the collection path would not protect the item path, and the
    guard test must see that.
    """
    return re.sub(r"\{[^}]+\}", "x", path)


# ── S9: /clean must read the caller's tenant directory ─────────────────


def _data_client(tmp_path, monkeypatch):
    """A client whose data-ingest state is wired to a temp working dir."""
    from fastapi.testclient import TestClient

    from api.main import app
    from config import settings
    import api.state as state
    from etl.clean import DataCleaner
    from etl.extract import DataExtractor

    monkeypatch.chdir(tmp_path)
    state.extractor = DataExtractor(config=settings.data.model_dump())
    state.cleaner = DataCleaner(config=settings.data.model_dump())
    return TestClient(app, raise_server_exceptions=False)


def _make_user(username: str, role: str = "user", tenant_id: str = ""):
    from api.auth import create_jwt_token, user_manager

    if not user_manager.get_user_info(username):
        user_manager.add_user(username, "Str0ng!Passw0rd", role, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {create_jwt_token(username, role=role)}"}


def _csv_bytes() -> bytes:
    return b"product,amount\nalpha,1\nbeta,2\n"


def test_clean_reads_the_tenant_directory(tmp_path, monkeypatch):
    """A non-default tenant can upload and then clean its own file."""
    client = _data_client(tmp_path, monkeypatch)
    headers = _make_user("s9_tenant_user", role="user", tenant_id="acme")

    upload = client.post(
        "/upload",
        files={"file": ("sales.csv", _csv_bytes(), "text/csv")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text[:300]

    clean = client.post("/clean", json={"filename": "sales.csv"}, headers=headers)
    assert clean.status_code == 200, (
        "a tenant must be able to clean the file it uploaded: "
        f"{clean.status_code} {clean.text[:300]}"
    )
    assert clean.json()["success"] is True

    # The file really landed in the tenant's own directory.
    from api.tenancy import tenant_slug

    tenant_dir = tmp_path / "data" / "raw" / tenant_slug("acme")
    assert (tenant_dir / "sales.csv").exists(), (
        f"the upload must land in {tenant_dir}, not in the flat data/raw"
    )


def test_clean_for_the_default_tenant_is_unchanged(tmp_path, monkeypatch):
    """Regression: the ``default`` tenant still uses the flat ``data/raw``."""
    client = _data_client(tmp_path, monkeypatch)
    headers = _make_user("s9_default_user", role="user", tenant_id="")

    upload = client.post(
        "/upload",
        files={"file": ("sales.csv", _csv_bytes(), "text/csv")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text[:300]

    clean = client.post("/clean", json={"filename": "sales.csv"}, headers=headers)
    assert clean.status_code == 200, clean.text[:300]
    assert (tmp_path / "data" / "raw" / "sales.csv").exists()


def test_clean_is_rate_limited(tmp_path, monkeypatch):
    """``/clean`` must be rate limited like its siblings (``/upload``, ``/load``)."""
    from api.middleware import rate_limit as rate_limit_mod

    client = _data_client(tmp_path, monkeypatch)
    headers = _make_user("s9_rate_limited", role="user", tenant_id="acme")

    monkeypatch.setitem(rate_limit_mod._LIMITS, "upload", (2, 60.0))
    rate_limit_mod.reset_rate_limits()
    try:
        statuses = [
            client.post("/clean", json={"filename": "sales.csv"}, headers=headers).status_code
            for _ in range(4)
        ]
    finally:
        rate_limit_mod.reset_rate_limits()

    assert 429 in statuses, (
        f"/clean must answer 429 once the bucket is exhausted, got {statuses}"
    )


# ── S8: query_history must not drift between schemas ───────────────────


#: The shape an older release created (``tests/test_saas_identity.py``).
OLD_QUERY_HISTORY_DDL = (
    "id VARCHAR, user_id VARCHAR, query VARCHAR"
)

#: The shape the app reads and writes (``api/user_data.py``).
NEW_QUERY_HISTORY_COLUMNS = ("id", "user_id", "query", "sql", "result_summary", "created_at")


@pytest.fixture()
def old_shape_warehouse(tmp_path, monkeypatch):
    """A warehouse whose ``query_history`` has the *old* three-column shape."""
    import duckdb

    from config import settings
    import api.state as state

    path = str(tmp_path / "old_shape.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    con.execute(
        f"CREATE TABLE analytics.query_history ({OLD_QUERY_HISTORY_DDL})"
    )
    con.close()

    monkeypatch.setattr(settings.duckdb, "database_path", path)
    state.loader = None
    yield path
    state.loader = None


def _columns_of(path: str, table: str = "query_history"):
    import duckdb

    con = duckdb.connect(path)
    try:
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'analytics' AND table_name = ? "
            "ORDER BY ordinal_position",
            [table],
        ).fetchall()
    finally:
        con.close()
    return tuple(str(r[0]) for r in rows)


def test_query_history_migration_adds_the_missing_columns(old_shape_warehouse):
    """The ensure/init path must add the columns the app reads.

    ``CREATE TABLE IF NOT EXISTS`` never changes an existing table, so a
    table created by an older release keeps its old shape and every query
    against the new columns raises a ``BinderException``.
    """
    from api.user_data import _ensure_tables

    _ensure_tables()

    columns = _columns_of(old_shape_warehouse)
    for column in NEW_QUERY_HISTORY_COLUMNS:
        assert column in columns, (
            f"the migration must add {column!r}; the table has {columns}"
        )


def test_query_history_is_readable_after_the_migration(old_shape_warehouse):
    """The app's own query must work against the migrated table."""
    from api.user_data import _ensure_tables

    _ensure_tables()

    with _con(old_shape_warehouse) as con:
        con.execute(
            "INSERT INTO analytics.query_history (id, user_id, query, sql, "
            "result_summary, created_at) VALUES ('q1', 'u1', 'select 1', "
            "'SELECT 1', 'one row', CURRENT_TIMESTAMP)"
        )
        rows = con.execute(
            "SELECT id, user_id, query, sql, result_summary, created_at "
            "FROM analytics.query_history"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "q1"


def test_old_schema_then_new_schema_works_in_both_orders(tmp_path, monkeypatch):
    """The two contradictory test schemas must both succeed, in any order.

    One test file creates the *old* ``query_history`` shape, another expects
    the *new* one.  Whichever runs first, the app must be able to read and
    write the table.
    """
    import duckdb

    from config import settings
    import api.state as state
    from api.user_data import _ensure_tables

    def _run(first_ddl, second_ddl):
        path = str(tmp_path / f"order_{abs(hash((first_ddl, second_ddl))) % 100000}.duckdb")
        con = duckdb.connect(path)
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(first_ddl)
        con.execute(second_ddl)
        con.close()

        monkeypatch.setattr(settings.duckdb, "database_path", path)
        state.loader = None
        try:
            _ensure_tables()
            with _con(path) as read_con:
                read_con.execute(
                    "INSERT INTO analytics.query_history (id, user_id, query, sql, "
                    "result_summary, created_at) VALUES ('q1', 'u1', 'q', 's', 'r', "
                    "CURRENT_TIMESTAMP)"
                )
                rows = read_con.execute(
                    "SELECT id, user_id, query, sql, result_summary, created_at "
                    "FROM analytics.query_history"
                ).fetchall()
            assert len(rows) == 1
        finally:
            state.loader = None

    old_ddl = f"CREATE TABLE analytics.query_history ({OLD_QUERY_HISTORY_DDL})"
    new_ddl = (
        "CREATE TABLE analytics.query_history (id VARCHAR, user_id VARCHAR, "
        "query VARCHAR, sql VARCHAR, result_summary VARCHAR, created_at TIMESTAMP)"
    )

    # Old first, then new.
    _run(old_ddl, new_ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
    # New first, then old.
    _run(new_ddl, old_ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))


def test_the_export_covers_every_query_history_column(old_shape_warehouse):
    """The GDPR export must not silently drop columns.

    The export reads the platform tables with ``SELECT *``, so a table left
    in the old shape would produce a document with a different — and
    incomplete — column set.
    """
    from api.user_data import build_user_export

    with _con(old_shape_warehouse) as con:
        con.execute(
            "INSERT INTO analytics.query_history (id, user_id, query) "
            "VALUES ('q1', 'u-export', 'select 1')"
        )
        document = build_user_export("u-export", "default", con=con, platform_con=con)

    rows = document.get("query_history") or []
    assert rows, "the export must contain the caller's query history"
    missing = [c for c in NEW_QUERY_HISTORY_COLUMNS if c not in rows[0]]
    assert not missing, (
        f"the export must carry every query_history column, missing {missing}"
    )


@contextmanager
def _con(path: str):
    import duckdb

    con = duckdb.connect(path)
    try:
        yield con
    finally:
        con.close()
