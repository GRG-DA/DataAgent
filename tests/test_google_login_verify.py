"""Google login: verified id_tokens + per-state pending isolation.

_rs256 verification (aud/iss/exp) replaces the old base64-decode trust;
concurrent logins must not share one pending file.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")
authlib_jose = pytest.importorskip("authlib.jose")


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    import api.google_login as gl

    monkeypatch.setattr(gl, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(gl, "RESULT_PATH", tmp_path / "results.json")
    m = gl.GoogleLoginManager()
    m._config = gl.GoogleLoginConfig(client_id="test-client-id")
    type(m)._certs_cache = {}
    return m


def _b64u_int(n: int) -> str:
    import base64

    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _rsa_keypair():
    """Fresh RSA pair: (private_pem_bytes, public_jwk_dict)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = priv.public_key().public_numbers()
    return pem, {"kty": "RSA", "kid": "testkid",
                 "n": _b64u_int(pub.n), "e": _b64u_int(pub.e)}


def _jwks_of(jwk: dict) -> dict:
    return {"keys": [jwk]}


def _id_token(priv_pem: bytes, payload: dict) -> str:
    from authlib.jose import jwt as auth_jwt

    tok = auth_jwt.encode({"alg": "RS256", "kid": "testkid"}, payload, priv_pem)
    return tok.decode("ascii") if isinstance(tok, bytes) else tok


def _certs_ok(monkeypatch, jwks: dict):
    import api.google_login as gl

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return jwks

    monkeypatch.setattr(gl.httpx, "get", lambda *a, **k: R())


def _claims(mgr, email="user@example.com", sub="123",Expired=False):
    import time

    now = int(time.time())
    return {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id",
        "sub": sub,
        "email": email,
        "exp": now - 10 if Expired else now + 600,
        "iat": now - 60,
    }


# ── signature verification ────────────────────────────────────────


def test_valid_token_verifies(mgr, monkeypatch):
    priv, jwk = _rsa_keypair()
    _certs_ok(monkeypatch, _jwks_of(jwk))
    claims = mgr._verified_claims(_id_token(priv, _claims(mgr)))
    assert claims["email"] == "user@example.com"
    assert claims["sub"] == "123"


def test_forged_token_rejected(mgr, monkeypatch):
    priv, jwk = _rsa_keypair()
    rogue_priv, _ = _rsa_keypair()
    _certs_ok(monkeypatch, _jwks_of(jwk))
    assert mgr._verified_claims(_id_token(rogue_priv, _claims(mgr))) == {}


def test_wrong_audience_rejected(mgr, monkeypatch):
    priv, jwk = _rsa_keypair()
    _certs_ok(monkeypatch, _jwks_of(jwk))
    payload = _claims(mgr)
    payload["aud"] = "someone-elses-client"
    assert mgr._verified_claims(_id_token(priv, payload)) == {}


def test_expired_token_rejected(mgr, monkeypatch):
    priv, jwk = _rsa_keypair()
    _certs_ok(monkeypatch, _jwks_of(jwk))
    assert mgr._verified_claims(_id_token(priv, _claims(mgr, Expired=True))) == {}


def test_unverifiable_token_fails_closed(mgr, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("offline")

    import api.google_login as gl
    monkeypatch.setattr(gl.httpx, "get", boom)
    priv, _jwk = _rsa_keypair()
    assert mgr._verified_claims(_id_token(priv, _claims(mgr))) == {}


# ── per-state pending isolation ───────────────────────────────────


def test_concurrent_logins_do_not_clobber(mgr):
    url1, err1 = mgr.build_login_url()
    url2, err2 = mgr.build_login_url()
    assert err1 == "" and err2 == ""
    assert url1 != url2
    from urllib.parse import parse_qs, urlparse

    s1 = parse_qs(urlparse(url1).query)["state"][0]
    s2 = parse_qs(urlparse(url2).query)["state"][0]
    assert mgr._read_pending(s1).get("state") == s1
    assert mgr._read_pending(s2).get("state") == s2


def test_callback_with_unknown_state_fails_closed(mgr):
    ok, msg, claims = mgr.handle_callback("code-abc", "no-such-state")
    assert ok is False and claims == {}
    assert "expired" in msg.lower() or "mismatch" in msg.lower() or "sign-in" in msg.lower()


def test_claim_result_enforces_ttl(mgr):
    import time as _time

    mgr.store_result("stale-state", "tok", {"user_id": "u"})
    assert mgr.claim_result("stale-state") is not None
    # Second claim is single-use.
    assert mgr.claim_result("stale-state") is None

    mgr.store_result("old-state", "tok2", {"user_id": "u2"})
    import api.google_login as gl

    results = gl.json.loads(gl.RESULT_PATH.read_text(encoding="utf-8"))
    results["old-state"]["created"] = _time.time() - gl.RESULT_TTL_SECONDS - 1
    gl.RESULT_PATH.write_text(gl.json.dumps(results), encoding="utf-8")
    assert mgr.claim_result("old-state") is None
