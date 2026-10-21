"""SAML 2.0 login flow — ACS-only (no authorization-code exchange).

Covers:
(a) exchange_code/get_user_info raise NotImplementedError (ACS-only design),
(b) process_acs with garbage raises ValueError without network access,
(c) SSOManager.provision_user links an existing email account instead of
    duplicating it (user store is isolated per-test by the autouse
    ``isolated_user_store`` fixture in tests/conftest.py).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from api.sso import SAMLProvider, SSOManager, SSOUserInfo, TokenResponse


def _test_provider() -> SAMLProvider:
    return SAMLProvider(
        {
            "provider": "generic_saml",
            "saml": {
                "entity_id": "https://sp.example.com/metadata",
                "sso_url": "https://idp.example.com/sso",
                "x509_cert": "",
                "private_key": "",
            },
        }
    )


def _test_manager() -> SSOManager:
    return SSOManager(
        {
            "enabled": True,
            "provider": "generic_saml",
            "oidc": {},
            "saml": {
                "entity_id": "https://sp.example.com/metadata",
                "sso_url": "https://idp.example.com/sso",
                "x509_cert": "",
                "private_key": "",
            },
        }
    )


# (a) OAuth2-style methods are unsupported by design ──────────────────────


def test_exchange_code_raises_not_implemented():
    provider = _test_provider()
    with pytest.raises(NotImplementedError, match="ACS"):
        asyncio.run(provider.exchange_code("code", "http://localhost:8002/auth/saml/acs", "state"))


def test_get_user_info_raises_not_implemented():
    provider = _test_provider()
    with pytest.raises(NotImplementedError, match="ACS"):
        asyncio.run(provider.get_user_info(TokenResponse(access_token="dummy")))


# (b) ACS validation without network ─────────────────────────────────────


def test_process_acs_garbage_raises_value_error():
    provider = _test_provider()
    with pytest.raises(ValueError, match="[Ss][Aa][Mm][Ll]"):
        asyncio.run(provider.process_acs("not-a-valid-saml-response", "relay-state"))


def test_process_acs_empty_raises_value_error():
    provider = _test_provider()
    with pytest.raises(ValueError):
        asyncio.run(provider.process_acs("", ""))


def test_logout_returns_url_string():
    """Real SLO URL when configured, else the redirect fallback — never empty."""
    provider = _test_provider()
    url = asyncio.run(provider.logout(TokenResponse(access_token=""), "/done"))
    assert isinstance(url, str) and url


# (c) provision_user links by email ───────────────────────────────────────


def test_provision_user_links_existing_email_user():
    from api.auth import user_manager

    suffix = uuid.uuid4().hex[:8]
    username = f"saml_link_user_{suffix}"
    email = f"saml.link.{suffix}@example.com"

    user_manager.add_user(username, f"Str0ng!Pass-{suffix}", role="viewer", email=email)
    before = len(user_manager.get_users())

    mgr = _test_manager()
    user_info = SSOUserInfo(
        subject=f"saml-subject-{suffix}",
        email=email,
        name="SAML Linked User",
        provider="generic_saml",
    )
    provisioned = mgr.provision_user(user_info)

    assert provisioned["username"] == username
    assert provisioned["sso_subject"] == f"saml-subject-{suffix}"
    assert provisioned["sso_provider"] == "generic_saml"
    # No duplicate account created.
    assert len(user_manager.get_users()) == before
