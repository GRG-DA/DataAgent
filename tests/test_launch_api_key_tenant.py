"""The API key must carry its tenant.

An API key is the credential a customer is told to use for programmatic
access.  Before this change the key carried no tenant at all, so
``resolve_tenant_id()`` fell back to ``default`` — the platform ``analytics``
schema — and every tenant's key read and wrote the operator's own data.

These tests pin:

* the key STORES its tenant (``analytics.api_keys.tenant_id``);
* ``validate_key`` returns it in the context;
* the auth middleware binds it, preferring the key's own tenant over the
  account record;
* the store resolves the CURRENT warehouse path, not a hard-coded one
  (a hard-coded path points at the developer's real warehouse, so the key
  store is shared between concurrent test processes).
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """An API-key store on a throwaway warehouse."""
    from api.api_keys import DuckDBAPIKeyStore, api_key_manager

    st = DuckDBAPIKeyStore(str(tmp_path / "keys.duckdb"))
    monkeypatch.setattr(api_key_manager, "store", st)
    return st


class TestTenantColumn:
    def test_the_table_has_a_tenant_column(self, store):
        with store._get_conn() as conn:
            cols = [r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'api_keys'"
            ).fetchall()]
        assert "tenant_id" in cols, (
            "api_keys has no tenant_id column - an API key cannot be bound "
            "to a tenant"
        )

    def test_the_tenant_is_stored_and_read_back(self, store):
        from api.api_keys import APIKeyManager

        mgr = APIKeyManager()
        mgr.store = store
        key, raw = mgr.create_key(
            user_id="u-1",
            name="acme-bot",
            scopes=["read"],
            tenant_id="t-acme",
        )
        assert key.tenant_id == "t-acme"

        stored = store.get(key.id)
        assert stored is not None
        assert stored.tenant_id == "t-acme"

    def test_validate_key_returns_the_tenant(self, store):
        from api.api_keys import APIKeyManager

        mgr = APIKeyManager()
        mgr.store = store
        key, raw = mgr.create_key(
            user_id="u-1",
            name="acme-bot",
            scopes=["read"],
            tenant_id="t-acme",
        )
        context = mgr.validate_key(raw)
        assert context is not None
        assert context.tenant_id == "t-acme"

    def test_a_key_without_a_tenant_is_still_usable(self, store):
        from api.api_keys import APIKeyManager

        mgr = APIKeyManager()
        mgr.store = store
        key, raw = mgr.create_key(
            user_id="u-1", name="legacy", scopes=["read"]
        )
        context = mgr.validate_key(raw)
        assert context is not None
        assert context.tenant_id is None


class TestStorePath:
    def test_the_store_resolves_the_current_warehouse(self, tmp_path, monkeypatch):
        """A hard-coded path points at the developer's real warehouse."""
        from api.api_keys import DuckDBAPIKeyStore, _default_db_path
        from config import settings

        assert _default_db_path() == str(settings.duckdb.database_path)
        st = DuckDBAPIKeyStore()
        assert st.db_path == str(settings.duckdb.database_path)

    def test_an_explicit_path_still_wins(self, tmp_path):
        from api.api_keys import DuckDBAPIKeyStore

        target = str(tmp_path / "explicit.duckdb")
        st = DuckDBAPIKeyStore(target)
        assert st.db_path == target


class TestMiddlewareBinding:
    def test_the_middleware_prefers_the_keys_own_tenant(self):
        """The key's tenant wins over the account record."""
        import inspect

        from api.middleware.auth_middleware import AuthMiddleware

        src = inspect.getsource(AuthMiddleware)
        assert 'getattr(context, "tenant_id", None) or user_info.get("tenant_id")' in src, (
            "the API-key branch must prefer the key's own tenant"
        )
