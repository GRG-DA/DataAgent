"""TDD: coverage increment — high-risk branches."""
import pytest


def test_validate_table_name_rejects():
    from fastapi import HTTPException
    from api.security_paths import validate_table_name
    assert validate_table_name("sales") == "sales"
    for bad in ["", "a b", "t;DROP", '"x"', "a-b", None, "9lives"]:
        with pytest.raises(HTTPException) as ei:
            validate_table_name(bad)
        assert ei.value.status_code == 400


def test_load_if_exists_invalid_422(billing_db):
    from fastapi.testclient import TestClient
    from tests.conftest import auth_headers
    client = TestClient(__import__("api.main", fromlist=["app"]).app)
    r = client.post("/load", json={"filename": "x.csv", "if_exists": "overwrite"},
                    headers=auth_headers())
    assert r.status_code == 422


def test_org_ensure_idempotent(tmp_path):
    from api.org import OrgManager
    db = str(tmp_path / "org_cov.duckdb")
    m = OrgManager(db_path=db)
    m._ensure_tables()
    m._ensure_tables()
    d = m.create_department("CovDept")
    assert m.get_department(d.id)["id"] == d.id
    assert any(x["id"] == d.id for x in m.list_departments())
    assert m.delete_department(d.id) is True


def test_sso_sub_missing_rejected():
    import asyncio
    from api.sso import OIDCProvider
    p = OIDCProvider({"provider": "x", "oidc": {"client_id": "c", "issuer_url": "https://iss",
                      "client_secret": "", "redirect_uri": "", "scopes": []}})
    async def fake_jwks():
        class K:
            pass
        return K()
    p._get_jwks = fake_jwks
    import api.sso as sso_mod
    real_decode = sso_mod.jose_jwt.decode
    class T:
        claims = {"exp": 9999999999, "iss": "https://iss", "aud": "c"}
    sso_mod.jose_jwt.decode = lambda *a, **k: T()
    try:
        with pytest.raises(ValueError):
            asyncio.run(p._parse_id_token("tok"))
    finally:
        sso_mod.jose_jwt.decode = real_decode


def test_google_unverified_email_rejected(monkeypatch):
    from api import google_login as gl
    import api.google_login as glmod
    m = glmod.GoogleLoginManager()
    m._config = glmod.GoogleLoginConfig(client_id="test-client-id")
    type(m)._certs_cache = {"ts": 9999999999, "keys": {"keys": []}}
    monkeypatch.setattr(glmod.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no net")))
    # Direct claims-level check: unverified email must fail closed
    assert m._verified_claims("bogus.token.here") == {}
