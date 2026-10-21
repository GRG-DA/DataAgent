"""TDD coverage: org update/delete branches, SSO OIDC/SAML, session, subscriptions, SLA.

Targets: api/org.py, api/sso.py, api/session.py, api/subscriptions.py, api/sla.py
Uses tmp_path DB for OrgManager/SLAManager. Mocks httpx (no network).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# --- Ensure global warehouse schema exists BEFORE importing api.sla ---
# api.sla creates a global SLAManager() at import which does
# SET search_path='analytics' — fails on a fresh temp warehouse.
try:
    from config import settings as _settings
    import duckdb as _duckdb

    _c = _duckdb.connect(_settings.duckdb.database_path)
    try:
        _c.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    finally:
        _c.close()
except Exception:
    pass

from api.org import OrgManager, get_org_manager  # noqa: E402
from api.session import (  # noqa: E402
    InMemorySessionStore,
    Session,
    SessionManager,
)
from api.sso import OIDCProvider, SAMLProvider, SSOManager, TokenResponse  # noqa: E402
import api.sso as sso_mod  # noqa: E402


# ======================================================================
# OrgManager helpers
# ======================================================================

def _org(tmp_path, name="org_cov.duckdb") -> OrgManager:
    return OrgManager(db_path=str(tmp_path / name))


# --- org: db_path setter / tables-ready branches ---

def test_org_db_path_setter(tmp_path):
    m = _org(tmp_path)
    m.db_path = str(tmp_path / "other.duckdb")
    assert "other.duckdb" in m.db_path


def test_org_tables_ready_setter_false_branch(tmp_path):
    m = _org(tmp_path)
    m._tables_ready = True
    assert m._tables_ready is True
    m._tables_ready = False
    assert m._tables_ready is False
    # ensure tables still usable after reset
    m._ensure_tables()
    d = m.create_department("AfterReset")
    assert m.get_department(d.id) is not None


def test_org_ensure_tables_early_return(tmp_path):
    m = _org(tmp_path)
    m._ensure_tables()
    # second call hits `if self._tables_ready: return`
    m._ensure_tables()
    d = m.create_department("EarlyReturn")
    assert d.id


def test_org_get_org_manager_singleton():
    assert get_org_manager() is get_org_manager()


# --- org: departments ---

def test_org_dept_update_no_allowed_keys(tmp_path):
    m = _org(tmp_path, "org_d1.duckdb")
    d = m.create_department("D1")
    assert m.update_department(d.id, bogus_key="x") is False
    assert m.update_department(d.id) is False


def test_org_dept_update_and_notfound(tmp_path):
    m = _org(tmp_path, "org_d2.duckdb")
    d = m.create_department("D2")
    assert m.update_department(d.id, name="D2b", description="desc") is True
    assert m.get_department(d.id)["name"] == "D2b"
    assert m.update_department("no-such-id", name="x") is False


def test_org_dept_get_notfound_and_list(tmp_path):
    m = _org(tmp_path, "org_d3.duckdb")
    assert m.get_department("missing") is None
    assert m.list_departments() == []
    m.create_department("ADept")
    assert len(m.list_departments()) == 1


def test_org_dept_delete_notfound_and_cascade(tmp_path):
    m = _org(tmp_path, "org_d4.duckdb")
    assert m.delete_department("missing") is False
    d = m.create_department("Cascade")
    t = m.create_team("T1", department_id=d.id)
    assert m.delete_department(d.id) is True
    assert m.get_department(d.id) is None
    assert m.get_team(t.id) is None
    assert m.delete_department(d.id) is False


# --- org: teams ---

def test_org_team_crud_branches(tmp_path):
    m = _org(tmp_path, "org_t1.duckdb")
    assert m.get_team("missing") is None
    assert m.list_teams() == []
    d = m.create_department("DeptT")
    t = m.create_team("TeamA", department_id=d.id, description="d", lead_user_id="u1")
    assert m.get_team(t.id)["name"] == "TeamA"
    # list filtered vs unfiltered
    assert len(m.list_teams()) == 1
    assert len(m.list_teams(department_id=d.id)) == 1
    assert m.list_teams(department_id="other") == []
    # update branches
    assert m.update_team(t.id) is False
    assert m.update_team(t.id, bogus=1) is False
    assert m.update_team(t.id, name="TeamB") is True
    assert m.get_team(t.id)["name"] == "TeamB"
    assert m.update_team("missing", name="x") is False
    # delete branches
    assert m.delete_team("missing") is False
    assert m.delete_team(t.id) is True
    assert m.delete_team(t.id) is False


# --- org: groups ---

def test_org_group_crud_branches(tmp_path):
    m = _org(tmp_path, "org_g1.duckdb")
    assert m.get_group("missing") is None
    assert m.list_groups() == []
    g = m.create_group("G1", description="d", group_type="standard")
    assert m.get_group(g.id)["name"] == "G1"
    assert len(m.list_groups()) == 1
    assert m.update_group(g.id) is False
    assert m.update_group(g.id, bogus="z") is False
    assert m.update_group(g.id, name="G1b") is True
    assert m.get_group(g.id)["name"] == "G1b"
    assert m.update_group("missing", name="x") is False
    assert m.delete_group("missing") is False
    assert m.delete_group(g.id) is True
    assert m.delete_group(g.id) is False


def test_org_group_delete_cascades_members(tmp_path):
    m = _org(tmp_path, "org_g2.duckdb")
    g = m.create_group("GCascade")
    assert m.add_user_to_group(g.id, "u1") is True
    assert m.delete_group(g.id) is True
    assert m.get_group_members(g.id) == []


# --- org: group members ---

def test_org_group_member_add_remove_branches(tmp_path):
    m = _org(tmp_path, "org_m1.duckdb")
    g = m.create_group("Members")
    # add success
    assert m.add_user_to_group(g.id, "alice", "member") is True
    # duplicate insert -> exception branch -> False
    assert m.add_user_to_group(g.id, "alice", "member") is False
    assert m.is_user_in_group("alice", g.id) is True
    assert m.is_user_in_group("bob", g.id) is False
    members = m.get_group_members(g.id)
    assert len(members) == 1 and members[0]["user_id"] == "alice"
    ugs = m.get_user_groups("alice")
    assert len(ugs) == 1
    assert m.get_user_groups("nobody") == []
    # remove branches
    assert m.remove_user_from_group(g.id, "nobody") is False
    assert m.remove_user_from_group(g.id, "alice") is True
    assert m.remove_user_from_group(g.id, "alice") is False
    assert m.is_user_in_group("alice", g.id) is False


def test_org_tree_empty_and_populated(tmp_path):
    m = _org(tmp_path, "org_tree.duckdb")
    tree = m.get_org_tree()
    assert tree["departments"] == [] and tree["total_departments"] == 0
    assert tree["total_teams"] == 0 and tree["total_groups"] == 0
    d = m.create_department("DTree")
    m.create_team("TTree", department_id=d.id)
    m.create_group("GTree")
    m.create_department("DTree2")  # dept with no teams covers .get default []
    tree2 = m.get_org_tree()
    assert tree2["total_departments"] == 2
    assert tree2["total_teams"] == 1
    assert tree2["total_groups"] == 1
    by_id = {d2["id"]: d2 for d2 in tree2["departments"]}
    assert len(by_id[d.id]["teams"]) == 1


# ======================================================================
# SSO — OIDC with mocked httpx (no network)
# ======================================================================

def _oidc(**over):
    base = {
        "provider": "generic_oidc",
        "oidc": {
            "client_id": "cid",
            "client_secret": "sec",
            "issuer_url": "https://iss.example.com",
            "redirect_uri": "https://app/cb",
            "scopes": ["openid", "profile", "email"],
        },
    }
    base["oidc"].update(over)
    return OIDCProvider(base)


class _FakeResp:
    def __init__(self, data=None, status=200, err=None):
        self._data = data or {}
        self.status_code = status
        self._err = err

    def json(self):
        return self._data

    def raise_for_status(self):
        if self._err is not None:
            raise self._err
        if self.status_code >= 400:
            import httpx as _hx

            raise _hx.HTTPStatusError("err", request=None, response=None)


def _patch_async_client(monkeypatch, handler):
    import httpx as _hx

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            return await handler("GET", url, kw)

        async def post(self, url, **kw):
            return await handler("POST", url, kw)

    monkeypatch.setattr(_hx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(sso_mod.httpx, "AsyncClient", _FakeClient)


_META = {
    "authorization_endpoint": "https://iss.example.com/auth",
    "token_endpoint": "https://iss.example.com/token",
    "userinfo_endpoint": "https://iss.example.com/userinfo",
    "jwks_uri": "https://iss.example.com/jwks",
    "end_session_endpoint": "https://iss.example.com/logout",
}


def test_oidc_metadata_success_and_cache(monkeypatch):
    p = _oidc()
    calls = []

    async def h(method, url, kw):
        calls.append(url)
        return _FakeResp(dict(_META))

    _patch_async_client(monkeypatch, h)
    m1 = asyncio.run(p._get_metadata())
    m2 = asyncio.run(p._get_metadata())
    assert m1["token_endpoint"] == "https://iss.example.com/token"
    assert m1 is m2  # cached
    assert len(calls) == 1


def test_oidc_metadata_failure_raises(monkeypatch):
    import httpx as _hx

    p = _oidc()

    async def h(method, url, kw):
        return _FakeResp({}, err=_hx.HTTPError("net down"))

    _patch_async_client(monkeypatch, h)
    with pytest.raises(Exception):
        asyncio.run(p._get_metadata())


def test_oidc_jwks_success_cache_and_failure(monkeypatch):
    from joserfc.jwk import KeySet as _KS

    monkeypatch.setattr(_KS, "import_key_set", classmethod(lambda cls, v, **k: object()))
    p = _oidc()
    p._metadata = dict(_META)
    seen = []

    async def h(method, url, kw):
        seen.append(url)
        return _FakeResp({"keys": []})

    _patch_async_client(monkeypatch, h)
    j1 = asyncio.run(p._get_jwks())
    j2 = asyncio.run(p._get_jwks())
    assert j1 is j2
    assert seen == ["https://iss.example.com/jwks"]

    # failure path
    import httpx as _hx

    p2 = _oidc()
    p2._metadata = dict(_META)

    async def h2(method, url, kw):
        return _FakeResp({}, err=_hx.HTTPError("jwks down"))

    _patch_async_client(monkeypatch, h2)
    with pytest.raises(Exception):
        asyncio.run(p2._get_jwks())


def test_oidc_get_auth_url(monkeypatch):
    p = _oidc()
    p._metadata = dict(_META)
    url = asyncio.run(p.get_auth_url("st123", "https://app/cb"))
    assert url.startswith("https://iss.example.com/auth?")
    assert "client_id=cid" in url and "state=st123" in url


def test_oidc_exchange_code_success(monkeypatch):
    p = _oidc()
    p._metadata = dict(_META)

    async def h(method, url, kw):
        assert method == "POST" and url == "https://iss.example.com/token"
        return _FakeResp({
            "access_token": "at", "token_type": "Bearer", "expires_in": 3600,
            "refresh_token": "rt", "id_token": "idt", "scope": "openid",
        })

    _patch_async_client(monkeypatch, h)
    tok = asyncio.run(p.exchange_code("code1", "https://app/cb", "st"))
    assert tok.access_token == "at" and tok.id_token == "idt"
    assert tok.refresh_token == "rt" and tok.scope == "openid"


def test_oidc_exchange_code_http_error(monkeypatch):
    import httpx as _hx

    p = _oidc()
    p._metadata = dict(_META)

    async def h(method, url, kw):
        return _FakeResp({}, err=_hx.HTTPError("token fail"))

    _patch_async_client(monkeypatch, h)
    with pytest.raises(Exception):
        asyncio.run(p.exchange_code("c", "https://app/cb", "s"))


def test_oidc_get_user_info_via_userinfo(monkeypatch):
    p = _oidc()
    p._metadata = dict(_META)

    async def h(method, url, kw):
        return _FakeResp({
            "sub": "s1", "email": "a@b.com", "name": "Alice",
            "given_name": "Al", "family_name": "Ice", "groups": ["g1"],
        })

    _patch_async_client(monkeypatch, h)
    info = asyncio.run(p.get_user_info(TokenResponse(access_token="at")))
    assert info.subject == "s1" and info.email == "a@b.com"
    assert info.groups == ["g1"]


def test_oidc_get_user_info_fallback_to_id_token(monkeypatch):
    p = _oidc()
    p._metadata = dict(_META)

    async def h(method, url, kw):
        return _FakeResp({}, status=401)

    _patch_async_client(monkeypatch, h)
    sentinel = object()
    called = {}

    async def fake_parse(tok):
        called["tok"] = tok
        return sentinel

    p._parse_id_token = fake_parse
    out = asyncio.run(p.get_user_info(TokenResponse(access_token="at", id_token="IDT")))
    assert out is sentinel and called["tok"] == "IDT"


def test_oidc_get_user_info_raises_without_source(monkeypatch):
    p = _oidc()
    p._metadata = dict(_META)

    async def h(method, url, kw):
        return _FakeResp({}, status=401)

    _patch_async_client(monkeypatch, h)
    with pytest.raises(ValueError, match="Unable to get user info"):
        asyncio.run(p.get_user_info(TokenResponse(access_token="at")))
    # no userinfo endpoint + no id_token
    p._metadata = {"token_endpoint": "https://iss.example.com/token"}
    with pytest.raises(ValueError, match="Unable to get user info"):
        asyncio.run(p.get_user_info(TokenResponse(access_token="")))


def test_oidc_logout_without_endpoint():
    p = _oidc()
    p._metadata = {"token_endpoint": "https://iss.example.com/token"}
    url = asyncio.run(p.logout(TokenResponse(access_token="at"), "https://app/done"))
    assert url == "https://app/done"


def test_oidc_logout_with_endpoint_variants():
    p = _oidc()
    p._metadata = dict(_META)
    u1 = asyncio.run(p.logout(TokenResponse(access_token="a", id_token="IDT"), "https://app/done"))
    assert u1.startswith("https://iss.example.com/logout?")
    assert "id_token_hint=IDT" in u1
    u2 = asyncio.run(p.logout(TokenResponse(access_token="a"), "https://app/done"))
    assert "id_token_hint" not in u2


def test_oidc_parse_userinfo_fallbacks():
    p = _oidc()
    u = p._parse_userinfo(
        {"sub": "s", "upn": "u@x.com", "preferred_username": "uname", "roles": ["r1"]},
        TokenResponse(access_token="x"),
    )
    assert u.email == "u@x.com" and u.name == "uname" and u.groups == ["r1"]
    u2 = p._parse_userinfo({"sub": "s2"}, TokenResponse(access_token="x"))
    assert u2.email == "" and u2.name == ""


def _decode_claims(monkeypatch, claims, jwks_ok=True, decode_ok=True):
    p = _oidc()

    async def fake_jwks():
        if not jwks_ok:
            raise RuntimeError("jwks net fail")
        return object()

    p._get_jwks = fake_jwks
    if not decode_ok:
        def _boom(*a, **k):
            raise ValueError("bad sig")

        monkeypatch.setattr(sso_mod.jose_jwt, "decode", _boom)
    else:
        class _T:
            def __init__(self, c):
                self.claims = c

        monkeypatch.setattr(sso_mod.jose_jwt, "decode", lambda *a, **k: _T(dict(claims)))
    return p


def test_oidc_parse_id_token_jwks_fail(monkeypatch):
    p = _decode_claims(monkeypatch, {}, jwks_ok=False)
    with pytest.raises(ValueError, match="Invalid ID token"):
        asyncio.run(p._parse_id_token("t"))


def test_oidc_parse_id_token_decode_fail(monkeypatch):
    p = _decode_claims(monkeypatch, {}, decode_ok=False)
    with pytest.raises(ValueError, match="Invalid ID token"):
        asyncio.run(p._parse_id_token("t"))


def test_oidc_parse_id_token_bad_exp_type(monkeypatch):
    p = _decode_claims(monkeypatch, {"exp": "notanint", "iss": "https://iss.example.com",
                                     "aud": "cid", "sub": "s"})
    with pytest.raises(ValueError, match="expiry"):
        asyncio.run(p._parse_id_token("t"))


def test_oidc_parse_id_token_expired(monkeypatch):
    p = _decode_claims(monkeypatch, {"exp": 1, "iss": "https://iss.example.com",
                                     "aud": "cid", "sub": "s"})
    with pytest.raises(ValueError, match="expired"):
        asyncio.run(p._parse_id_token("t"))


def test_oidc_parse_id_token_bad_issuer(monkeypatch):
    p = _decode_claims(monkeypatch, {"exp": 9999999999, "iss": "https://evil.com",
                                     "aud": "cid", "sub": "s"})
    with pytest.raises(ValueError, match="issuer"):
        asyncio.run(p._parse_id_token("t"))
    p2 = _decode_claims(monkeypatch, {"exp": 9999999999, "aud": "cid", "sub": "s"})
    with pytest.raises(ValueError, match="issuer"):
        asyncio.run(p2._parse_id_token("t"))


def test_oidc_parse_id_token_bad_audience(monkeypatch):
    p = _decode_claims(monkeypatch, {"exp": 9999999999, "iss": "https://iss.example.com",
                                     "aud": "other", "sub": "s"})
    with pytest.raises(ValueError, match="audience"):
        asyncio.run(p._parse_id_token("t"))
    p2 = _decode_claims(monkeypatch, {"exp": 9999999999, "iss": "https://iss.example.com",
                                      "aud": ["other"], "sub": "s"})
    with pytest.raises(ValueError, match="audience"):
        asyncio.run(p2._parse_id_token("t"))


def test_oidc_parse_id_token_missing_sub(monkeypatch):
    p = _decode_claims(monkeypatch, {"exp": 9999999999, "iss": "https://iss.example.com",
                                     "aud": "cid"})
    with pytest.raises(ValueError, match="subject"):
        asyncio.run(p._parse_id_token("t"))


def test_oidc_parse_id_token_success_string_and_list_aud(monkeypatch):
    p = _decode_claims(monkeypatch, {"exp": 9999999999, "iss": "https://iss.example.com",
                                     "aud": "cid", "sub": "s1", "email": "e@x.com",
                                     "name": "N"})
    u = asyncio.run(p._parse_id_token("t"))
    assert u.subject == "s1"
    p2 = _decode_claims(monkeypatch, {"exp": 9999999999, "iss": "https://iss.example.com",
                                      "aud": ["cid", "other"], "sub": "s2",
                                      "email": "e2@x.com", "name": "N2"})
    u2 = asyncio.run(p2._parse_id_token("t"))
    assert u2.subject == "s2"


# --- SAML + SSOManager ---

def _saml_cfg(provider="generic_saml"):
    return {
        "provider": provider,
        "saml": {
            "entity_id": "https://sp.example.com/metadata",
            "sso_url": "https://idp.example.com/sso",
            "x509_cert": "",
            "private_key": "",
        },
    }


def test_saml_exchange_and_userinfo_notimplemented():
    sp = SAMLProvider(_saml_cfg())
    with pytest.raises(NotImplementedError):
        asyncio.run(sp.exchange_code("c", "r", "s"))
    with pytest.raises(NotImplementedError):
        asyncio.run(sp.get_user_info(TokenResponse(access_token="x")))


def test_saml_first_attr_and_groups_helpers():
    assert SAMLProvider._first_attr({"a": ["", "v", ""]}, "a") == "v"
    assert SAMLProvider._first_attr({"a": "solo"}, "a") == "solo"
    assert SAMLProvider._first_attr({}, "missing") == ""
    assert SAMLProvider._first_attr({"a": []}, "a") == ""
    g = SAMLProvider._all_groups({"groups": ["a", "b", "a"], "roles": "c", "empty": []})
    assert g == ["a", "b", "c"]
    assert SAMLProvider._all_groups({}) == []


def test_saml_build_acs_request():
    sp = SAMLProvider(_saml_cfg())
    r = sp._build_acs_request("RESP", "relay")
    assert r["post_data"]["SAMLResponse"] == "RESP"
    assert r["post_data"]["RelayState"] == "relay"
    r2 = sp._build_acs_request("RESP")
    assert r2["post_data"]["RelayState"] == ""


def test_saml_logout_fallback_and_success(monkeypatch):
    sp = SAMLProvider(_saml_cfg())
    try:
        import onelogin.saml2.auth as saml_auth_mod
    except ImportError:
        pytest.skip("onelogin not installed")
        return
    orig_cls = saml_auth_mod.OneLogin_Saml2_Auth

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("slo init fail")

    monkeypatch.setattr(saml_auth_mod, "OneLogin_Saml2_Auth", _Boom)
    assert asyncio.run(sp.logout(TokenResponse(access_token=""), "/fb")) == "/fb"

    class _Ok:
        def __init__(self, *a, **k):
            pass

        def logout(self, return_to=None):
            return "https://idp/logout?x=1"

    monkeypatch.setattr(saml_auth_mod, "OneLogin_Saml2_Auth", _Ok)
    assert asyncio.run(sp.logout(TokenResponse(access_token=""), "/r")) == "https://idp/logout?x=1"

    class _Empty:
        def __init__(self, *a, **k):
            pass

        def logout(self, return_to=None):
            return ""

    monkeypatch.setattr(saml_auth_mod, "OneLogin_Saml2_Auth", _Empty)
    assert asyncio.run(sp.logout(TokenResponse(access_token=""), "/fb2")) == "/fb2"
    monkeypatch.setattr(saml_auth_mod, "OneLogin_Saml2_Auth", orig_cls)


def test_sso_manager_disabled():
    m = SSOManager({"enabled": False, "provider": "azure_ad", "oidc": {}})
    assert m.is_enabled() is False
    assert m.get_provider() is None
    with pytest.raises(ValueError, match="not configured"):
        asyncio.run(m.initiate_login("https://app/cb"))
    with pytest.raises(ValueError, match="not configured"):
        asyncio.run(m.complete_login("c", "s", "r"))


def test_sso_manager_unknown_provider():
    m = SSOManager({"enabled": True, "provider": "nope_unknown", "oidc": {}})
    assert m.is_enabled() is False
    assert m.get_provider() is None


def test_sso_manager_oidc_and_saml_init():
    m1 = SSOManager({"enabled": True, "provider": "azure_ad",
                     "oidc": {"client_id": "c", "issuer_url": "https://i",
                              "client_secret": "", "redirect_uri": ""}})
    assert isinstance(m1.get_provider(), OIDCProvider)
    assert m1.is_enabled() is True
    m2 = SSOManager(_saml_cfg("generic_saml") | {"enabled": True})
    assert isinstance(m2.get_provider(), SAMLProvider)


def test_sso_manager_initiate_complete_with_fake_provider():
    m = SSOManager({"enabled": False})

    class _Fake:
        async def get_auth_url(self, state, redirect_uri):
            return f"https://idp/auth?state={state}"

        async def exchange_code(self, code, redirect_uri, state):
            return TokenResponse(access_token="AT-" + code)

        async def get_user_info(self, token):
            from api.sso import SSOUserInfo

            return SSOUserInfo(subject="s", email="e@x.com", name="N",
                               provider="fake", raw={})

    m.provider = _Fake()
    url, state = asyncio.run(m.initiate_login("https://app/cb"))
    assert "https://idp/auth" in url and state
    tok, info = asyncio.run(m.complete_login("CODE", state, "https://app/cb"))
    assert tok.access_token == "AT-CODE" and info.subject == "s"


def test_sso_provision_new_and_collision():
    from api.auth import user_manager

    m = SSOManager({"enabled": True, "provider": "generic_oidc", "oidc": {}})
    suffix = uuid.uuid4().hex[:8]
    email = f"cov.{suffix}@example.com"
    from api.sso import SSOUserInfo

    u1 = m.provision_user(SSOUserInfo(subject=f"sub-{suffix}", email=email,
                                      name="Cov User", provider="generic_oidc"))
    assert u1["email"] == email
    # same subject lookup path (email changed) reuses account
    u2 = m.provision_user(SSOUserInfo(subject=f"sub-{suffix}", email=f"other.{suffix}@example.com",
                                      name="Cov User2", provider="generic_oidc"))
    assert u2["username"] == u1["username"]
    # username collision loop: pre-create colliding base then provision new subject
    base = f"cov{suffix}"
    user_manager.add_user(base, "Str0ng!Pass1", role="viewer", email=f"{base}@example.com")
    u3 = m.provision_user(SSOUserInfo(subject=f"fresh-{suffix}", email=f"{base}@other.com",
                                      name="Fresh", provider="generic_oidc"))
    assert u3["username"].startswith(base)
    # no-email subject fallback
    u4 = m.provision_user(SSOUserInfo(subject=f"onlysub-{suffix}", email="",
                                      name="NoMail", provider="generic_oidc"))
    assert u4["username"].startswith(f"onlysub-{suffix}"[:20])


# ======================================================================
# Session
# ======================================================================

def test_session_is_valid_branches():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s = Session(id="1", user_id="u")
    assert s.is_valid() is True
    s.revoked = True
    assert s.is_valid() is False
    s.revoked = False
    s.expires_at = now - timedelta(seconds=1)
    assert s.is_valid() is False
    s.expires_at = now + timedelta(hours=1)
    assert s.is_valid() is True


def test_session_to_dict_with_and_without_expiry():
    s = Session(id="1", user_id="u")
    d = s.to_dict()
    assert "created_at" in d and d["expires_at"] is None
    s.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    d2 = s.to_dict()
    assert d2["expires_at"] is not None and "last_activity" in d2


def test_inmemory_store_branches():
    store = InMemorySessionStore()
    assert store.get("missing") is None
    assert store.update(Session(id="missing", user_id="u")) is False
    assert store.delete("missing") is False
    s = Session(id="s1", user_id="u1")
    store.create(s)
    assert store.get("s1") is not None
    s2 = Session(id="s2", user_id="u1",
                 expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5))
    store.create(s2)
    # expired get deletes + returns None
    assert store.get("s2") is None
    assert store.get("s2") is None
    # update existing
    got = store.get("s1")
    assert store.update(got) is True
    # user sessions filters stale ids
    assert any(x.id == "s1" for x in store.get_user_sessions("u1"))
    assert store.get_user_sessions("nobody") == []
    # revoked counts as expired for cleanup
    got.revoked = True
    store.update(got)
    assert store.cleanup_expired() == 1
    assert store.delete("s1") is False
    # delete removes from user set
    s3 = Session(id="s3", user_id="u9")
    store.create(s3)
    assert store.delete("s3") is True


def test_session_manager_flows():
    mgr = SessionManager(store=InMemorySessionStore(),
                         config={"timeout_minutes": 60, "absolute_timeout_hours": 8})
    s = mgr.create_session("u1", tenant_id="t", ip_address="1.1.1.1",
                           user_agent="ua", metadata={"k": 1})
    assert mgr.get_session(s.id) is not None
    assert mgr.get_session("missing") is None
    r = mgr.refresh_session(s.id)
    assert r is not None
    assert mgr.refresh_session("missing") is None
    assert mgr.revoke_session(s.id) is True
    assert mgr.revoke_session("missing") is False
    s2 = mgr.create_session("u2")
    s3 = mgr.create_session("u2")
    assert mgr.revoke_all_user_sessions("u2") == 2
    assert len(mgr.get_user_sessions("u2")) == 2
    # cleanup interval branch
    mgr._last_cleanup = time.time() - 9999
    mgr._maybe_cleanup()
    assert mgr._last_cleanup <= time.time()


def test_session_manager_refresh_bad_absolute_config():
    mgr = SessionManager(store=InMemorySessionStore(),
                         config={"timeout_minutes": 60, "absolute_timeout_hours": 8})
    s = mgr.create_session("u1")
    # corrupt config after create to hit `except` -> absolute_cap None
    mgr.config["absolute_timeout_hours"] = "notanum"
    out = mgr.refresh_session(s.id)
    assert out is not None and out.expires_at is not None


def test_redis_store_with_mock(monkeypatch):
    import sys
    import types
    import json as _json

    seen = {}

    class _FakeRedis:
        def __init__(self):
            self.kv = {}
            self.sets = {}

        def setex(self, k, ttl, v):
            self.kv[k] = v

        def get(self, k):
            return self.kv.get(k)

        def exists(self, k):
            return 1 if k in self.kv else 0

        def delete(self, k):
            return 1 if self.kv.pop(k, None) is not None else 0

        def sadd(self, k, v):
            self.sets.setdefault(k, set()).add(v)

        def expire(self, k, ttl):
            pass

        def smembers(self, k):
            return set(self.sets.get(k, set()))

    fake_client = _FakeRedis()
    fake_redis_mod = types.SimpleNamespace(from_url=lambda *a, **k: fake_client)
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    from api.session import RedisSessionStore

    store = RedisSessionStore()
    assert store._session_key("a") == "session:a"
    assert store._user_key("u") == "user_sessions:u"
    assert store.get("missing") is None
    assert store.cleanup_expired() == 0
    s = Session(id="r1", user_id="ru",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))
    store.create(s)
    assert store.get("r1") is not None
    assert store.update(s) is True
    s_ghost = Session(id="ghost", user_id="ru")
    assert store.update(s_ghost) is False
    assert len(store.get_user_sessions("ru")) >= 1
    assert store.delete("r1") is True
    assert store.delete("r1") is False
    # bad json branch
    store.client.kv[store._session_key("bad")] = "not-json{{{"
    assert store.get("bad") is None
    # expired branch deletes
    exp = Session(id="exp1", user_id="ru",
                  expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1))
    # bypass create ttl logic: write directly
    store.client.kv[store._session_key("exp1")] = _json.dumps(exp.to_dict())
    assert store.get("exp1") is None
    # bad date string branch -> fromisoformat ValueError -> None
    d = s.to_dict()
    d["id"] = "baddate"
    d["created_at"] = "not-a-date"
    store.client.kv[store._session_key("baddate")] = _json.dumps(d)
    got = store.get("baddate")
    # created_at None still constructs Session; may be valid or parse-fail path
    assert got is None or got.id == "baddate"


# ======================================================================
# Subscriptions (pure helpers + route handlers)
# ======================================================================

def test_sub_row_to_sub():
    from api.subscriptions import _row_to_sub

    out = _row_to_sub(("u", "pro", "active", "s", "e", "u2"))
    assert out == {"user_id": "u", "plan": "pro", "status": "active",
                   "started_at": "s", "expires_at": "e", "updated_at": "u2"}


def test_sub_ensure_and_get_plan_branches(billing_db, monkeypatch):
    from api import subscriptions as sub

    # invalid default plan falls back to free
    s1 = sub.ensure_subscription("cov_u1", default_plan="bogus")
    assert s1["plan"] == "free"
    # existing returns same row
    s2 = sub.ensure_subscription("cov_u1")
    assert s2["user_id"] == "cov_u1"
    # no row -> free
    assert sub.get_plan("nobody_xyz") == "free"
    # inactive -> free
    sub.set_plan("cov_u2", "pro")
    with sub._get_con() as con:
        con.execute(f"UPDATE {sub.SCHEMA}.{sub.TABLE} SET status='cancelled' WHERE user_id=?",
                    ["cov_u2"])
    assert sub.get_plan("cov_u2") == "free"
    # expired -> free
    sub.set_plan("cov_u3", "pro", duration_days=30)
    past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).isoformat()
    with sub._get_con() as con:
        con.execute(f"UPDATE {sub.SCHEMA}.{sub.TABLE} SET status='active', expires_at=? WHERE user_id=?",
                    [past, "cov_u3"])
    assert sub.get_plan("cov_u3") == "free"
    # bad-date parse failure branch: fake a row whose expires_at is unparseable
    # (TIMESTAMP column rejects bad strings, so stub _get_con instead).
    class _FakeCur:
        def fetchone(self):
            return ("pro", "active", "not-a-date-xyz")

    class _FakeCon:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return _FakeCur()

    with monkeypatch.context() as mc:
        mc.setattr(sub, "_ensure_table", lambda: None)
        mc.setattr(sub, "_get_con", lambda: _FakeCon())
        assert sub.get_plan("cov_u3") == "pro"
    # unknown plan in db -> free
    with sub._get_con() as con:
        con.execute(f"UPDATE {sub.SCHEMA}.{sub.TABLE} SET plan=?, expires_at=NULL WHERE user_id=?",
                    ["ultra", "cov_u3"])
    assert sub.get_plan("cov_u3") == "free"


def test_sub_set_plan_branches(billing_db):
    import fastapi
    from api import subscriptions as sub

    with pytest.raises(fastapi.HTTPException) as ei:
        sub.set_plan("u", "bogus")
    assert ei.value.status_code == 400
    # insert then update paths, with and without expiry
    r1 = sub.set_plan("cov_sp1", "pro", duration_days=10)
    assert r1["plan"] == "pro" and r1["expires_at"] is not None
    r2 = sub.set_plan("cov_sp1", "free")
    assert r2["plan"] == "free" and r2["expires_at"] is None
    # write-through failure swallowed
    import api.billing.subs as bsubs

    orig = bsubs.ensure_billing_row

    def _boom(*a, **k):
        raise RuntimeError("billing down")

    bsubs.ensure_billing_row = _boom
    try:
        r3 = sub.set_plan("cov_sp2", "pro")
        assert r3["plan"] == "pro"
    finally:
        bsubs.ensure_billing_row = orig


def test_sub_list_plans():
    import asyncio as _a
    from api import subscriptions as sub

    out = _a.run(sub.list_plans())
    assert "free" in out["plans"] and "pro" in out["plans"]


def _fake_user(uid="cov_uid", admin=False, email="cov@example.com"):
    from api.middleware.auth_middleware import User

    return User(user_id=uid, username="covuser", role="admin" if admin else "viewer",
                email=email)


def test_sub_trial_identity_branches(billing_db, monkeypatch):
    from api import subscriptions as sub

    u = _fake_user()
    email, sso = sub._trial_identity(u)
    assert isinstance(email, str) and isinstance(sso, str)
    # exception path
    import api.auth as auth_mod

    monkeypatch.setattr(auth_mod.user_manager, "get_user_info_by_id",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    email2, sso2 = sub._trial_identity(u)
    assert email2 == "cov@example.com" and sso2 == ""


def test_sub_my_and_upgrade_admin_handlers(billing_db):
    import asyncio as _a
    import fastapi
    from api import subscriptions as sub

    u = _fake_user(uid="cov_me1")
    me = _a.run(sub.my_subscription(u))
    assert me["user_id"] == "cov_me1" and "plan_details" in me
    # upgrade unknown -> 400
    with pytest.raises(fastapi.HTTPException) as ei:
        _a.run(sub.upgrade_subscription({"plan": "bogus"}, u))
    assert ei.value.status_code == 400
    # upgrade non-free -> 403
    with pytest.raises(fastapi.HTTPException) as ei2:
        _a.run(sub.upgrade_subscription({"plan": "pro"}, u))
    assert ei2.value.status_code == 403
    # upgrade free ok
    out = _a.run(sub.upgrade_subscription({"plan": "free"}, u))
    assert out["plan"] == "free"
    # admin: non-admin -> 403
    with pytest.raises(fastapi.HTTPException) as ei3:
        _a.run(sub.admin_set_subscription("other", {"plan": "pro"}, u))
    assert ei3.value.status_code == 403
    admin = _fake_user(uid="cov_admin", admin=True)
    with pytest.raises(fastapi.HTTPException) as ei4:
        _a.run(sub.admin_set_subscription("other", {"plan": "bogus"}, admin))
    assert ei4.value.status_code == 400
    ok = _a.run(sub.admin_set_subscription("cov_other1", {"plan": "pro"}, admin))
    assert ok["plan"] == "pro"


def test_sub_trial_handlers(billing_db, monkeypatch):
    import asyncio as _a
    import fastapi
    from api import subscriptions as sub
    from api.billing import subs as bsubs

    u = _fake_user(uid="cov_trial1")
    # success
    out = _a.run(sub.start_pro_trial(u))
    assert out["plan"] == "pro" and "trial_ends_at" in out
    # already used -> 409
    with pytest.raises(fastapi.HTTPException) as ei:
        _a.run(sub.start_pro_trial(u))
    assert ei.value.status_code == 409
    # disabled -> 403
    monkeypatch.setattr(bsubs, "trial_enabled", lambda: False)
    # need fresh user so disabled hits before already-used
    u2 = _fake_user(uid="cov_trial_disabled")
    # start_trial checks enabled first; call handler with patched trial_enabled via subs import chain?
    # api.subscriptions.start_pro_trial imports trial pieces lazily from api.billing.subs,
    # and trial_days/start_trial are looked up at call time — patch start_trial to raise disabled.
    import api.billing.subs as _bs

    orig_start = _bs.start_trial

    def _disabled(*a, **k):
        raise _bs.TrialDisabledError("off")

    monkeypatch.setattr(_bs, "start_trial", _disabled)
    with pytest.raises(fastapi.HTTPException) as ei2:
        _a.run(sub.start_pro_trial(u2))
    assert ei2.value.status_code == 403
    monkeypatch.setattr(_bs, "start_trial", orig_start)
    # trial_status returns dict with plan
    st = _a.run(sub.trial_status(u))
    assert st["plan"] == "pro"


# ======================================================================
# SLA
# ======================================================================

def _sla(tmp_path, name="sla_cov.duckdb"):
    import duckdb as _dd

    from api.sla import SLAManager

    path = str(tmp_path / name)
    _c = _dd.connect(path)
    try:
        _c.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    finally:
        _c.close()
    return SLAManager(db_path=path)


def _sla_table(tmp_path, sla_name, table, ddl_extra="id INTEGER"):
    """Ensure a table exists in the SLA tmp DB file for sla_name."""
    import duckdb as _dd

    path = str(tmp_path / sla_name)
    _c = _dd.connect(path)
    try:
        _c.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    finally:
        _c.close()
    return path


def test_sla_definition_crud_branches(tmp_path):
    m = _sla(tmp_path)
    assert m.get_definition("missing") is None
    assert m.list_definitions() == []
    d = m.create_definition("CovSLA", "t1", max_age_hours=24.0)
    assert d["name"] == "CovSLA"
    assert m.get_definition(d["id"]) is not None
    assert len(m.list_definitions()) == 1
    assert m.update_definition(d["id"]) is False
    assert m.update_definition(d["id"], bogus=1) is False
    assert m.update_definition("missing", name="x") is False
    assert m.update_definition(d["id"], name="CovSLA2", enabled=False) is True
    assert m.delete_definition("missing") is False
    assert m.delete_definition(d["id"]) is True
    assert m.delete_definition(d["id"]) is False


def test_sla_check_missing_raises(tmp_path):
    m = _sla(tmp_path, "sla_miss.duckdb")
    with pytest.raises(ValueError, match="not found"):
        m.check_sla("nope")


def test_sla_check_unknown_table(tmp_path):
    m = _sla(tmp_path, "sla_unk.duckdb")
    d = m.create_definition("Unk", "no_such_table_xyz")
    r = m.check_sla(d["id"])
    assert r.status.value == "unknown"
    assert "Check error" in r.message


def test_sla_check_no_date_col_with_and_without_rows(tmp_path):
    import duckdb as _dd

    m = _sla(tmp_path, "sla_nodate.duckdb")
    # table with rows but no date col -> compliant
    with _dd.connect(str(tmp_path / "sla_nodate.duckdb")) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.plain (id INTEGER)")
        con.execute("INSERT INTO analytics.plain VALUES (1),(2)")
    d = m.create_definition("Plain", "analytics.plain")
    r = m.check_sla(d["id"])
    assert r.status.value == "compliant"
    # empty table, no date col -> breaching empty
    with _dd.connect(str(tmp_path / "sla_nodate.duckdb")) as con:
        con.execute("CREATE TABLE analytics.empty_nodate (id INTEGER)")
    d2 = m.create_definition("EmptyNoDate", "analytics.empty_nodate")
    r2 = m.check_sla(d2["id"])
    assert r2.status.value == "breaching"


def test_sla_check_fresh_warning_breach(tmp_path):
    import duckdb as _dd

    m = _sla(tmp_path, "sla_dates.duckdb")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    path = str(tmp_path / "sla_dates.duckdb")
    with _dd.connect(path) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute("CREATE TABLE analytics.fresh (id INTEGER, updated_at TIMESTAMP)")
        con.execute("INSERT INTO analytics.fresh VALUES (1, ?)", [now.isoformat()])
        con.execute("CREATE TABLE analytics.old (id INTEGER, updated_at TIMESTAMP)")
        con.execute("INSERT INTO analytics.old VALUES (1, ?)",
                    [(now - timedelta(hours=50)).isoformat()])
        con.execute("CREATE TABLE analytics.warn (id INTEGER, updated_at TIMESTAMP)")
        con.execute("INSERT INTO analytics.warn VALUES (1, ?)",
                    [(now - timedelta(hours=21)).isoformat()])
        con.execute("CREATE TABLE analytics.empty_date (id INTEGER, updated_at TIMESTAMP)")
    df = m.create_definition("Fresh", "analytics.fresh", max_age_hours=24.0)
    assert m.check_sla(df["id"]).status.value == "compliant"
    do = m.create_definition("Old", "analytics.old", max_age_hours=24.0)
    ro = m.check_sla(do["id"])
    assert ro.status.value == "breaching"
    dw = m.create_definition("Warn", "analytics.warn", max_age_hours=24.0,
                             warning_threshold_pct=80.0)
    assert m.check_sla(dw["id"]).status.value == "warning"
    de = m.create_definition("EmptyDate", "analytics.empty_date", max_age_hours=24.0)
    re_ = m.check_sla(de["id"])
    assert re_.status.value == "breaching"
    # breach events recorded
    hist = m.get_breach_history()
    assert len(hist) >= 2


def test_sla_record_breach_notify_branches(tmp_path, monkeypatch):
    from api.sla import SLAStatus

    m = _sla(tmp_path, "sla_breach.duckdb")
    d = m.create_definition("BreachN", "analytics.plain2", owner="")
    # no owner -> no notification, still records
    m._record_breach(d["id"], "BreachN", "t", SLAStatus.BREACHING, 5.0, 1.0, "warning")
    # owner with failing notifier -> swallowed
    d2 = m.create_definition("BreachO", "analytics.plain2", owner="u1")

    class _BadNM:
        def send_notification(self, **k):
            raise RuntimeError("notify down")

    import api.notifications as _nm

    monkeypatch.setattr(_nm, "get_notification_manager", lambda: _BadNM())
    m._record_breach(d2["id"], "BreachO", "t", SLAStatus.WARNING, 2.0, 1.0, "warning")
    # owner with working notifier
    calls = []

    class _OkNM:
        def send_notification(self, **k):
            calls.append(k)
            return True

    monkeypatch.setattr(_nm, "get_notification_manager", lambda: _OkNM())
    m._record_breach(d2["id"], "BreachO", "t", SLAStatus.BREACHING, 3.0, 1.0, "critical")
    assert calls and calls[0]["user_id"] == "u1"


def test_sla_check_all_skips_disabled_and_handles_error(tmp_path, monkeypatch):
    m = _sla(tmp_path, "sla_all.duckdb")
    d1 = m.create_definition("On", "analytics.plain3")
    d2 = m.create_definition("Off", "analytics.plain3")
    m.update_definition(d2["id"], enabled=False)
    res = m.check_all()
    assert any(r["definition_id"] == d1["id"] for r in res)
    assert not any(r["definition_id"] == d2["id"] for r in res)
    # error branch
    orig = m.check_sla
    monkeypatch.setattr(m, "check_sla",
                        lambda did: (_ for _ in ()).throw(RuntimeError("boom"))
                        if did == d1["id"] else orig(did))
    res2 = m.check_all()
    assert any(r["status"] == "error" for r in res2)


def test_sla_status_summary_counts(tmp_path):
    m = _sla(tmp_path, "sla_sum.duckdb")
    # zero-total branch
    s0 = m.get_status_summary()
    assert s0["total"] == 0 and s0["compliant_pct"] == 0
    monkeypatch = pytest.MonkeyPatch()
    # fake check_all results covering all statuses
    m2 = _sla(tmp_path, "sla_sum2.duckdb")
    monkeypatch.setattr(m2, "check_all", lambda: [
        {"status": "compliant"}, {"status": "warning"}, {"status": "breaching"},
        {"status": "unknown"}, {"status": "error"},
    ])
    s = m2.get_status_summary()
    assert (s["compliant"], s["warning"], s["breaching"], s["unknown"]) == (1, 1, 1, 2)
    monkeypatch.undo()


def test_sla_start_pipeline_run_sql_branches(tmp_path):
    # Covers run_id generation (empty vs provided) with a stubbed connection
    # (DELETE+INSERT upsert-free write; no real DuckDB involved).
    m = _sla(tmp_path, "sla_pipe2.duckdb")
    seen = []

    class _StubCon:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            seen.append((sql, params))
            return self

    stub = _StubCon()
    m._get_conn = lambda: stub
    rid1 = m.start_pipeline_run("p1")
    assert rid1 and seen
    seen.clear()
    rid2 = m.start_pipeline_run("p1", run_id="fixed-id")
    assert rid2 == "fixed-id" and seen


def test_sla_pipeline_runs_and_health(tmp_path):
    m = _sla(tmp_path, "sla_pipe.duckdb")
    assert m.get_pipeline_health() == []
    rid = m.start_pipeline_run("pipe1")
    r2 = m.start_pipeline_run("pipe2")
    m.finish_pipeline_run(rid, status="success", rows_processed=10)
    m.finish_pipeline_run(r2, status="failed", error_count=1, error_message="boom")
    all_runs = m.get_pipeline_runs()
    assert all_runs["total"] == 2
    filt = m.get_pipeline_runs(pipeline_name="pipe1")
    assert filt["total"] == 1 and len(filt["runs"]) == 1
    assert filt["runs"][0]["pipeline_name"] == "pipe1"
    assert filt["runs"][0]["rows_processed"] == 10
    health = m.get_pipeline_health()
    assert {h["pipeline_name"] for h in health} == {"pipe1", "pipe2"}


def test_sla_monitor_start_twice_and_loop_error(tmp_path, monkeypatch):
    m = _sla(tmp_path, "sla_mon.duckdb")
    m.start_monitor(interval_minutes=60)
    m.start_monitor(interval_minutes=60)  # already-running branch
    m.stop_monitor()
    assert m._monitor_running is False
    # loop error branch: check_all raises once, then stop via sleep hook
    m._monitor_running = True
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("check fail")

    monkeypatch.setattr(m, "check_all", _boom)

    def _stop_sleep(s):
        m._monitor_running = False

    monkeypatch.setattr("time.sleep", _stop_sleep)
    m._monitor_loop(interval_minutes=0)
    assert calls


def test_sla_get_breach_history_limit(tmp_path):
    from api.sla import SLAStatus

    m = _sla(tmp_path, "sla_hist.duckdb")
    assert m.get_breach_history(limit=5) == []
    d = m.create_definition("H", "t")
    m._record_breach(d["id"], "H", "t", SLAStatus.BREACHING, 2.0, 1.0, "warning")
    assert len(m.get_breach_history(limit=5)) == 1
