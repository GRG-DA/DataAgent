"""Billing-server email verification for website accounts.

Registration creates an account in an *unverified* state. Until the email
ownership link is consumed, ``/v1/accounts/summary`` must not reveal a plan,
subscription, or purchase history. A buyer can claim a squatted, still
unverified email by registering again; a verified email can never be replaced.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

STRONG = "Corr3ct!HorseBattery9"
STRONGER = "N3w!HorseBattery2026"


@pytest.fixture()
def client(billing_env, monkeypatch):
    import billing_server.accounts as accounts_mod
    import billing_server.config as cfg
    import billing_server.main as main_mod

    monkeypatch.setattr(cfg, "SESSION_COOKIE_DOMAIN", "", raising=False)
    monkeypatch.setattr(cfg, "SESSION_COOKIE_SECURE", False, raising=False)
    monkeypatch.setattr(cfg, "BILLING_SITE_URL", "https://data-agent.me", raising=False)
    accounts_mod.reset_rate_limits()
    return TestClient(main_mod.app, raise_server_exceptions=False)


@pytest.fixture()
def mail_outbox(monkeypatch):
    import billing_server.emailer as emailer

    sent: list[dict] = []

    def fake_send(email: str, link: str) -> bool:
        sent.append({"email": email, "link": link})
        return True

    monkeypatch.setattr(emailer, "send_verification_email", fake_send, raising=False)
    return sent


def _register(client, email="User@Example.com", password=STRONG):
    return client.post("/v1/accounts/register",
                       json={"email": email, "password": password})


def _login(client, email="user@example.com", password=STRONG):
    return client.post("/v1/accounts/login",
                       json={"email": email, "password": password})


def _token_from(link: str) -> str:
    return parse_qs(urlsplit(link).fragment)["verify"][0]


def _verify(client, link: str):
    return client.post("/v1/accounts/verify-email",
                       json={"token": _token_from(link)})


def _register_and_verify(client, mail_outbox, email="user@example.com"):
    r = _register(client, email=email)
    assert r.status_code == 200
    assert _verify(client, mail_outbox[-1]["link"]).status_code == 200
    return r


# ── registration sends a link, never returns it ──────────────────────


def test_register_sends_verification_link_and_reports_unverified(client, mail_outbox):
    r = _register(client)

    assert r.status_code == 200
    body = r.json()
    assert body["email_verified"] is False
    assert body["verification_sent"] is True

    assert len(mail_outbox) == 1
    assert mail_outbox[0]["email"] == "user@example.com"
    link = mail_outbox[0]["link"]
    assert link.startswith("https://data-agent.me/account.html#verify=")
    token = _token_from(link)
    assert token not in r.text  # never hand the token back to the browser

    me = client.get("/v1/accounts/me").json()
    assert me["email_verified"] is False


def test_verification_token_is_stored_only_as_a_hash(client, mail_outbox):
    import billing_server.accounts as accounts_mod
    from billing_server import store

    _register(client)
    token = _token_from(mail_outbox[0]["link"])

    assert store.get_email_verification(token) is None
    assert store.get_email_verification(
        accounts_mod.hash_email_verification_token(token)
    ) is not None


# ── unverified summary is closed ─────────────────────────────────────


def test_unverified_summary_hides_plan_and_purchases(client, mail_outbox):
    from billing_server import store

    _register(client)
    store.record_order("ord_hidden", "user@example.com", "lifetime", 1000000,
                       "INR", provider="famgateway")
    store.mark_paid("ord_hidden", "pay_hidden_secret", grant_days=None)

    r = client.get("/v1/accounts/summary")

    assert r.status_code == 200
    body = r.json()
    assert body["email_verified"] is False
    assert body["plan"] is None
    assert body["status"] == "unverified"
    assert body["expires_at"] is None
    assert body["purchases"] == []
    assert body["subscription"] is None
    for secret in ("ord_hidden", "pay_hidden_secret", "lifetime"):
        assert secret not in r.text


def test_verify_email_unlocks_plan_and_purchase_history(client, mail_outbox):
    from billing_server import store

    r = _register(client)
    user_id = r.json()["user_id"]
    store.record_order("ord_visible", "user@example.com", "lifetime", 1000000,
                       "INR", provider="famgateway")
    store.mark_paid("ord_visible", "pay_visible", grant_days=None)

    verify = _verify(client, mail_outbox[0]["link"])

    assert verify.status_code == 200
    assert verify.json() == {"ok": True, "email_verified": True}
    assert store.get_account_by_id(user_id)["email_verified_at"]

    body = client.get("/v1/accounts/summary").json()
    assert body["email_verified"] is True
    assert body["plan"] == "lifetime"
    assert body["status"] == "active"
    assert [p["order_id"] for p in body["purchases"]] == ["ord_visible"]


def test_verified_free_account_reports_free_not_unverified(client, mail_outbox):
    _register_and_verify(client, mail_outbox)

    body = client.get("/v1/accounts/summary").json()

    assert body["email_verified"] is True
    assert body["plan"] == "free"
    assert body["status"] == "free"
    assert body["purchases"] == []


# ── token failures ───────────────────────────────────────────────────


def test_verification_token_is_single_use(client, mail_outbox):
    _register(client)
    link = mail_outbox[0]["link"]

    assert _verify(client, link).status_code == 200
    second = _verify(client, link)
    assert second.status_code == 400
    assert "invalid or expired" in second.json()["detail"].lower()


def test_expired_verification_token_is_rejected(client, mail_outbox):
    import billing_server.accounts as accounts_mod
    from billing_server import store

    r = _register(client)
    token = _token_from(mail_outbox[0]["link"])
    store.create_email_verification(
        r.json()["user_id"], "user@example.com",
        accounts_mod.hash_email_verification_token(token),
        "2000-01-01T00:00:00+00:00",
    )

    verify = client.post("/v1/accounts/verify-email", json={"token": token})

    assert verify.status_code == 400
    assert "invalid or expired" in verify.json()["detail"].lower()


# ── resend ───────────────────────────────────────────────────────────


def test_resend_requires_a_session(client):
    r = client.post("/v1/accounts/resend-verification")
    assert r.status_code == 401


def test_resend_issues_a_fresh_link_and_invalidates_the_old_one(client, mail_outbox):
    _register(client)
    old_link = mail_outbox[0]["link"]

    r = client.post("/v1/accounts/resend-verification")

    assert r.status_code == 200
    assert r.json()["verification_sent"] is True
    assert len(mail_outbox) == 2
    new_link = mail_outbox[1]["link"]
    assert new_link != old_link

    assert _verify(client, old_link).status_code == 400
    assert _verify(client, new_link).status_code == 200


def test_resend_after_verification_is_a_noop(client, mail_outbox):
    _register_and_verify(client, mail_outbox)
    before = len(mail_outbox)

    r = client.post("/v1/accounts/resend-verification")

    assert r.status_code == 200
    assert r.json()["already_verified"] is True
    assert len(mail_outbox) == before


def test_email_send_failure_does_not_claim_it_was_sent(client, monkeypatch):
    import billing_server.emailer as emailer

    monkeypatch.setattr(emailer, "send_verification_email",
                        lambda email, link: False, raising=False)

    r = _register(client)

    assert r.status_code == 200
    assert r.json()["verification_sent"] is False
    assert r.json()["email_verified"] is False


# ── account squatting ────────────────────────────────────────────────


def test_buyer_can_claim_an_unverified_squatted_email(client, mail_outbox):
    first = _register(client, password=STRONG)
    assert first.status_code == 200

    second = _register(client, password=STRONGER)

    assert second.status_code == 200
    assert second.json()["user_id"] == first.json()["user_id"]
    assert _login(client, password=STRONG).status_code == 401
    assert _login(client, password=STRONGER).status_code == 200

    assert _verify(client, mail_outbox[-1]["link"]).status_code == 200


def test_verified_email_cannot_be_re_registered(client, mail_outbox):
    _register_and_verify(client, mail_outbox)

    r = _register(client, password=STRONGER)

    assert r.status_code == 409
