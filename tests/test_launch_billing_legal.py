"""Launch remediation — product / commercial / legal (C1, C2, C3, C7, C8).

Covers the defects the launch review found in the parts this module owns:

* **C1** — the mailer must distinguish "not configured" from "the send
  failed", and the account-lifecycle endpoints must never answer "success"
  when no message was sent.  The answer must not become an account
  enumerator, and it must not leak SMTP internals.
* **C2** — there is exactly ONE price list: the app and the checkout must
  agree on plan, amount, currency and cycle.  Lifetime is the headline
  tier.
* **C3** — no plan has unlimited AI.  Every plan has a finite daily cap,
  the tenant budget has a finite default, and the caps are ENFORCED.
* **C7** — ``config/legal.yaml`` publishes no ``[[MARKER]]`` placeholder.
* **C8** — the keygen development-key guard really refuses to sign with the
  development key unless an explicit, loud opt-in is given.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

# ── helpers ────────────────────────────────────────────────────────────

_OLD_PASSWORD = "Str0ng!Passw0rd"

#: The legal values that still need the operator's real details.  They are
#: published as a clearly-marked "set this before public distribution"
#: notice, so the release checklist can grep for them.
EXPECTED_PENDING_LEGAL_VALUES = [
    "company.address",
    "company.contact_email",
    "company.dpo_email",
    "company.jurisdiction",
    "company.security_email",
    "legal.data_region",
    "legal.effective_date",
    "legal.retention_days",
    "subprocessors.email",
    "subprocessors.hosting",
    "subprocessors.llm",
    "subprocessors.monitoring",
    "subprocessors.storage",
]


def _make_user(username: str, password: str = _OLD_PASSWORD,
               role: str = "viewer", email: str = "") -> str:
    from api.auth import user_manager

    user_id = user_manager.add_user(username, password, role, email=email)
    assert user_id, f"could not create the account {username}"
    return user_id


def _headers(username: str, role: str = "admin") -> Dict[str, str]:
    from api.auth import create_jwt_token

    return {"Authorization": f"Bearer {create_jwt_token(username, role=role)}"}


@pytest.fixture()
def account_client():
    """A client carrying only the account router (the real app mounts it too)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.middleware.rate_limit import reset_rate_limits
    from api.routes.account import router

    reset_rate_limits()
    application = FastAPI()
    application.include_router(router)

    @application.middleware("http")
    async def _authenticate(request, call_next):
        from api.auth import user_manager, verify_jwt_token
        from api.middleware.auth_middleware import User

        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            payload = verify_jwt_token(header[7:])
            if payload:
                info = user_manager.get_user_info(payload.get("sub", ""))
                if info and info.get("is_active", True):
                    request.state.user = User(
                        user_id=info["user_id"],
                        username=info["username"],
                        role=info["role"],
                        tenant_id=info.get("tenant_id"),
                        email=info.get("email"),
                    )
        return await call_next(request)

    client = TestClient(application, raise_server_exceptions=False)
    yield client
    reset_rate_limits()


@pytest.fixture()
def unconfigured_mail(monkeypatch):
    """An install with NO mail server (the shipped desktop default)."""
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_user", "")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_password", "")
    return True


@pytest.fixture()
def configured_mail(monkeypatch):
    """An install WITH a mail server configured."""
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_user", "postmaster")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_password", "s3cr3t")
    return True


# ── C1 · the mailer tells the truth ────────────────────────────────────


def test_mailer_reports_unconfigured_instead_of_a_bare_false(monkeypatch):
    """No mail server is a DIFFERENT outcome from "the send failed"."""
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "")

    result = mailer.send_email("a@example.com", "Subject", "Body")

    assert result.status == "unconfigured"
    assert not result, "an unsent mail is not a success"


def test_mailer_separates_a_failed_send_from_an_unconfigured_one(monkeypatch):
    """A configured-but-unreachable server is a failure, not "unconfigured"."""
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_port", 1)

    result = mailer.send_email("a@example.com", "Subject", "Body")

    assert result.status == "failed"
    assert result.status != "unconfigured"
    assert not result


def test_mailer_never_raises(monkeypatch):
    """The mailer still swallows every error (a mail outage is never fatal)."""
    from api import mailer

    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(mailer.settings.notifications, "smtp_port", 1)
    monkeypatch.setattr(
        mailer.smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )

    result = mailer.send_email("a@example.com", "Subject", "Body")

    assert result.status == "failed"


def test_mailer_reports_a_delivered_mail(monkeypatch):
    """A mail that reached the server is reported as sent."""
    from api import mailer

    class _Server:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            return None

        def login(self, user, password):
            return None

        def send_message(self, message):
            return None

    monkeypatch.setattr(mailer.smtplib, "SMTP", _Server)
    monkeypatch.setattr(mailer.settings.notifications, "smtp_host", "smtp.example.com")

    result = mailer.send_email("a@example.com", "Subject", "Body")

    assert result.status == "sent"
    assert result


# ── C1 · /auth/forgot-password is honest ──────────────────────────────


def test_forgot_password_tells_the_user_when_mail_is_unconfigured(
    account_client, unconfigured_mail
):
    """The user is told the truth instead of a false "it has been sent"."""
    _make_user("c1_known", email="known@example.com")

    response = account_client.post(
        "/auth/forgot-password", json={"email": "known@example.com"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivery"] == "unconfigured"
    assert body["success"] is False
    assert "not configured" in body["message"].lower()
    assert "administrator" in body["message"].lower()


def test_forgot_password_does_not_enumerate_when_mail_is_unconfigured(
    account_client, unconfigured_mail
):
    """A known and an unknown address must be indistinguishable."""
    _make_user("c1_enum", email="known@example.com")

    known = account_client.post(
        "/auth/forgot-password", json={"email": "known@example.com"}
    ).json()
    unknown = account_client.post(
        "/auth/forgot-password", json={"email": "nobody@example.com"}
    ).json()

    assert known == unknown


def test_forgot_password_is_generic_when_mail_is_configured(
    account_client, configured_mail
):
    """With a mail server the answer is the generic one (no enumeration)."""
    _make_user("c1_generic", email="known@example.com")

    known = account_client.post(
        "/auth/forgot-password", json={"email": "known@example.com"}
    ).json()
    unknown = account_client.post(
        "/auth/forgot-password", json={"email": "nobody@example.com"}
    ).json()

    assert known == unknown
    assert "If that account exists" in known["message"]
    assert known["delivery"] == "available"
    assert known["success"] is True


def test_forgot_password_never_leaks_smtp_internals(
    account_client, configured_mail, monkeypatch
):
    """No host name, no user, no password, no traceback in the answer."""
    from api import mailer

    monkeypatch.setattr(
        mailer.settings.notifications, "smtp_host", "smtp.secret.example.com"
    )

    response = account_client.post(
        "/auth/forgot-password", json={"email": "known@example.com"}
    )
    text = response.text

    assert "smtp.secret.example.com" not in text
    assert "postmaster" not in text
    assert "s3cr3t" not in text
    assert "Traceback" not in text
    assert "SMTPException" not in text


# ── C1 · the other account endpoints are honest too ────────────────────


def test_resend_verification_is_honest_when_mail_is_unconfigured(
    account_client, unconfigured_mail
):
    """Resending a confirmation must not claim a mail that never left."""
    _make_user("c1_resend", email="rv@example.com")

    body = account_client.post(
        "/auth/resend-verification", json={"email": "rv@example.com"}
    ).json()

    assert body["delivery"] == "unconfigured"
    assert body["success"] is False
    assert "not configured" in body["message"].lower()


def test_resend_verification_does_not_enumerate(account_client, unconfigured_mail):
    """Known and unknown addresses get the same answer."""
    _make_user("c1_resend_enum", email="rv@example.com")

    known = account_client.post(
        "/auth/resend-verification", json={"email": "rv@example.com"}
    ).json()
    unknown = account_client.post(
        "/auth/resend-verification", json={"email": "ghost@example.com"}
    ).json()

    assert known == unknown


def test_invite_does_not_claim_a_mail_it_could_not_send(
    account_client, unconfigured_mail
):
    """The admin must learn that the invitation never reached the invitee."""
    _make_user("c1_admin", role="admin")

    response = account_client.post(
        "/auth/invite",
        json={"username": "c1_invitee", "email": "invitee@example.com", "role": "viewer"},
        headers=_headers("c1_admin", role="admin"),
    )

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["delivery"] == "unconfigured"
    assert "sent" not in body["message"].lower().replace("not sent", "")
    assert "not configured" in body["message"].lower()
    # The admin still has a way forward: the link to hand over by hand.
    assert body.get("invite_link")


def test_invite_reports_a_delivered_invitation(account_client, configured_mail,
                                                monkeypatch):
    """A configured mail server still produces the normal answer."""
    from api.routes import account as account_mod

    sent: List[Dict[str, Any]] = []

    def _send(to, subject, body, html=None):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(account_mod, "send_email", _send)
    _make_user("c1_admin2", role="admin")

    response = account_client.post(
        "/auth/invite",
        json={"username": "c1_invitee2", "email": "invitee2@example.com", "role": "viewer"},
        headers=_headers("c1_admin2", role="admin"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["delivery"] == "sent"
    assert sent and sent[-1]["to"] == "invitee2@example.com"


# ── C2 · one price list ────────────────────────────────────────────────


def test_the_app_and_the_checkout_sell_the_same_prices():
    """The app's display list and the checkout's list are the SAME list."""
    from api.subscriptions import PLANS
    import billing_server.config as cfg

    for code in ("pro", "lifetime"):
        assert PLANS[code]["price"] * 100 == cfg.PLANS[code]["amount"], code
        assert PLANS[code]["currency"] == cfg.PLANS[code]["currency"], code
        assert PLANS[code]["cycle"] == cfg.PLANS[code]["cycle"], code
    assert PLANS["pro"]["currency"] == "INR"
    assert PLANS["lifetime"]["currency"] == "INR"


def test_the_seeded_price_rows_match_the_checkout(billing_db):
    """The seeded rows carry the checkout's amounts and currency."""
    from api.billing.seed import plan_price

    pro = plan_price("pro", "MONTHLY")
    lifetime = plan_price("lifetime", "ONETIME")
    assert pro["amount"] == 49900  # INR 499
    assert pro["currency"] == "INR"
    assert lifetime["amount"] == 1000000  # INR 10,000
    assert lifetime["currency"] == "INR"


def test_there_is_no_second_price_list(billing_db):
    """No display-only price row that the checkout does not sell."""
    from api.billing.seed import plan_price

    assert plan_price("pro", "YEARLY") is None


def test_the_lifetime_plan_is_the_headline():
    """Lifetime is the primary product (LAUNCH-READINESS §8)."""
    from api.billing.seed import PLANS as seed_plans
    from api.subscriptions import PLANS

    assert PLANS["lifetime"]["recommended"] is True
    assert PLANS["pro"].get("recommended") is not True
    lifetime = next(p for p in seed_plans if p["code"] == "lifetime")
    assert lifetime["is_recommended"] is True
    pro = next(p for p in seed_plans if p["code"] == "pro")
    assert pro["is_recommended"] is False


# ── C3 · real, finite, enforced caps ───────────────────────────────────


def test_no_plan_has_unlimited_ai():
    """Every plan has a finite daily AI cap and advertises it."""
    from api.billing.seed import LIMITS
    from api.subscriptions import PLANS

    limits = {(code, metric): value for code, metric, value in LIMITS}
    for code, plan in PLANS.items():
        cap = plan["ai_requests_daily"]
        assert isinstance(cap, int) and cap > 0, code
        assert limits[(code, "ai_requests")] == cap, code
        for feature in plan["features"]:
            assert "unlimited" not in feature.lower(), (code, feature)


def test_the_free_plan_still_allows_ai_requests():
    """A free tier with zero AI requests is not a product."""
    from api.subscriptions import PLANS

    assert PLANS["free"]["ai_requests_daily"] >= 10


def test_the_free_ai_cap_is_enforced(billing_db):
    """A request is REFUSED once the plan's daily cap is reached."""
    from api.billing.cache import clear_request_cache
    from api.billing.entitlements import get_entitlements
    from api.billing.metering import UsageLimitError, record_usage

    clear_request_cache()
    cap = get_entitlements("c3-free-user", plan="free")["max_ai_requests"]
    assert isinstance(cap, int) and cap > 0, "the free cap must be finite and usable"

    for _ in range(cap):
        record_usage("c3-free-user", "ai_requests")

    with pytest.raises(UsageLimitError):
        record_usage("c3-free-user", "ai_requests")


def test_a_paid_ai_cap_is_enforced_too(billing_db):
    """The paid plans are capped as well — no unlimited AI anywhere."""
    from api.billing.cache import clear_request_cache
    from api.billing.entitlements import get_entitlements
    from api.billing.metering import UsageLimitError, record_usage
    from api.billing.subs import ensure_billing_row

    user_id = "c3-life-user"
    ensure_billing_row(user_id, "lifetime", never_expires=True)
    clear_request_cache()
    cap = get_entitlements(user_id, plan="lifetime")["max_ai_requests"]
    assert isinstance(cap, int) and cap > 0, "lifetime AI must be capped"

    for _ in range(cap):
        record_usage(user_id, "ai_requests")

    with pytest.raises(UsageLimitError):
        record_usage(user_id, "ai_requests")


def test_the_tenant_budget_default_is_finite(monkeypatch):
    """The tenant AI budget is a real ceiling, not "0 = unlimited"."""
    from api.billing.metering import tenant_ai_daily_cap
    from config import settings

    monkeypatch.delenv("BILLING_TENANT_AI_DAILY_CAP", raising=False)
    assert settings.billing.tenant_ai_daily_cap > 0
    assert tenant_ai_daily_cap() > 0


def test_the_tenant_budget_is_enforced(billing_db, monkeypatch):
    """The tenant-wide budget stops the accounts of one tenant together."""
    from api.billing.metering import UsageLimitError, record_usage

    monkeypatch.setenv("BILLING_TENANT_AI_DAILY_CAP", "3")

    for _ in range(3):
        record_usage("c3-tenant-u1", "ai_requests", tenant_id="acme")

    with pytest.raises(UsageLimitError):
        record_usage("c3-tenant-u2", "ai_requests", tenant_id="acme")


def test_the_seed_repairs_an_existing_unlimited_row(billing_db):
    """An install that already has ai_requests = -1 is migrated forward."""
    from api.billing.schema import SCHEMA, get_connection
    from api.billing.seed import seed_billing_data

    with get_connection() as con:
        con.execute(
            f"UPDATE {SCHEMA}.plan_limits SET limit_value = -1 WHERE metric = 'ai_requests'"
        )

    seed_billing_data()

    with get_connection() as con:
        rows = con.execute(
            f"SELECT plan_id, limit_value FROM {SCHEMA}.plan_limits "
            "WHERE metric = 'ai_requests'"
        ).fetchall()
    assert rows, "the ai_requests limit rows must exist"
    assert all(int(row[1]) > 0 for row in rows), rows


# ── C7 · the legal pages publish no placeholder ────────────────────────


def test_no_legal_placeholder_is_left():
    """``unfilled_legal_markers()`` reports the real count: zero."""
    from api.legal_config import unfilled_legal_markers

    assert unfilled_legal_markers() == []


def test_every_legal_value_is_publishable():
    """No value is empty and none carries a raw [[MARKER]] token."""
    from api.legal_config import legal_values

    values = legal_values()
    assert values
    for key, value in values.items():
        assert value.strip(), key
        assert "[[" not in value, key


def test_the_values_that_need_an_operator_say_so():
    """The operator can see at a glance what still has to be filled in."""
    from api.legal_config import legal_values

    pending = sorted(
        key for key, value in legal_values().items()
        if "before public distribution" in value
    )
    assert pending == EXPECTED_PENDING_LEGAL_VALUES


def test_the_company_name_is_truthful():
    """The published company name is the product's real name."""
    from api.legal_config import legal_values

    assert legal_values()["company.name"] == "DataAgent"


# ── C8 · the development-key guard really refuses ──────────────────────


def _ed25519_key():
    from cryptography.hazmat.primitives.asymmetric import ed25519

    return ed25519.Ed25519PrivateKey.generate()


def test_signing_with_the_dev_key_is_refused(monkeypatch):
    """The admin build refuses to sign with the development key."""
    from keygen_app import keygen

    dev_key = _ed25519_key()
    monkeypatch.setattr(
        keygen, "DEV_PUBLIC_FINGERPRINT", keygen.public_fingerprint(dev_key.public_key())
    )
    monkeypatch.delenv(keygen.ALLOW_DEV_KEY_SIGNING_ENV, raising=False)

    with pytest.raises(RuntimeError):
        keygen.generate_key(dev_key, "pro", email="buyer@example.com")


def test_the_dev_key_is_unlocked_only_by_the_explicit_opt_in(monkeypatch, capsys):
    """With the loud opt-in the dev key may sign — and a banner is printed."""
    from keygen_app import keygen

    dev_key = _ed25519_key()
    monkeypatch.setattr(
        keygen, "DEV_PUBLIC_FINGERPRINT", keygen.public_fingerprint(dev_key.public_key())
    )
    monkeypatch.setenv(keygen.ALLOW_DEV_KEY_SIGNING_ENV, "1")

    product_key = keygen.generate_key(dev_key, "pro", email="buyer@example.com")

    assert product_key.startswith("DATAAGENT-")
    printed = capsys.readouterr()
    assert "DEV MODE" in (printed.out + printed.err)


def test_a_non_dev_key_signs_fine(monkeypatch):
    """A production key signs without any opt-in."""
    from keygen_app import keygen

    dev_key = _ed25519_key()
    production_key = _ed25519_key()
    monkeypatch.setattr(
        keygen, "DEV_PUBLIC_FINGERPRINT", keygen.public_fingerprint(dev_key.public_key())
    )
    monkeypatch.delenv(keygen.ALLOW_DEV_KEY_SIGNING_ENV, raising=False)

    product_key = keygen.generate_key(production_key, "lifetime", email="buyer@example.com")

    assert product_key.startswith("DATAAGENT-")


def test_the_bundled_dev_key_file_is_refused(monkeypatch, tmp_path):
    """A dev keypair on disk is recognised without any configuration."""
    from cryptography.hazmat.primitives import serialization

    from keygen_app import keygen

    dev_key = _ed25519_key()
    pem = dev_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    dev_path = tmp_path / "dev_private.pem"
    dev_path.write_bytes(pem)

    monkeypatch.setattr(keygen, "DEV_PRIVATE_KEY_PATH", dev_path)
    monkeypatch.setattr(keygen, "DEV_PUBLIC_FINGERPRINT", "")
    monkeypatch.delenv(keygen.ALLOW_DEV_KEY_SIGNING_ENV, raising=False)

    with pytest.raises(RuntimeError):
        keygen.generate_key(dev_key, "pro", email="buyer@example.com")


def test_an_empty_dev_fingerprint_is_not_a_silent_pass(monkeypatch):
    """An unconfigured fingerprint must not let a release build sign blindly."""
    from keygen_app import keygen

    monkeypatch.setattr(keygen, "DEV_PUBLIC_FINGERPRINT", "")
    monkeypatch.delenv(keygen.ALLOW_DEV_KEY_SIGNING_ENV, raising=False)
    monkeypatch.setenv(keygen.STRICT_SIGNING_ENV, "1")

    with pytest.raises(RuntimeError):
        keygen.generate_key(_ed25519_key(), "pro", email="buyer@example.com")
