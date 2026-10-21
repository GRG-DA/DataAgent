"""
SSO Module
OpenID Connect and SAML 2.0 authentication providers
"""

import uuid
import logging
import secrets
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Set
from urllib.parse import urlencode, parse_qs
import json

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2.rfc7523 import JWTBearerTokenValidator
from joserfc import jwt as jose_jwt
from joserfc.jwk import KeySet

from config import settings

logger = logging.getLogger(__name__)


# ── Login state store ─────────────────────────────────────────────────
# The OIDC/SAML `state` parameter binds an authorization response to the
# browser that started the flow. It must be remembered server-side and
# verified on the way back, otherwise an attacker can force a victim's
# browser to complete a callback carrying the attacker's code (login CSRF),
# binding the victim's session to the attacker's identity.
#
# States are single-use and expire. The store is in-process (a desktop app
# is single-process); a multi-worker deployment should move this to the
# shared store used by api/session.py.

_STATE_TTL_SECONDS = 600
_MAX_PENDING_STATES = 64
_pending_states: Dict[str, float] = {}
_pending_states_lock = threading.Lock()


def _prune_states(now: float) -> None:
    expired = [s for s, exp in _pending_states.items() if exp < now]
    for s in expired:
        _pending_states.pop(s, None)
    # Bound memory: drop the oldest entries when over the cap.
    while len(_pending_states) > _MAX_PENDING_STATES:
        oldest = min(_pending_states, key=lambda s: _pending_states[s])
        _pending_states.pop(oldest, None)


def _remember_state(state: str) -> None:
    """Record a freshly issued state value."""
    if not state:
        return
    now = time.time()
    with _pending_states_lock:
        _prune_states(now)
        _pending_states[state] = now + _STATE_TTL_SECONDS


def _consume_state(state: str) -> bool:
    """Verify and consume a state value. Single-use."""
    if not state:
        return False
    now = time.time()
    with _pending_states_lock:
        _prune_states(now)
        expiry = _pending_states.pop(state, None)
    return expiry is not None and expiry >= now


@dataclass
class SSOUserInfo:
    """Normalized user info from SSO provider."""
    subject: str  # Unique identifier from provider
    email: str
    name: str
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    groups: List[str] = field(default_factory=list)
    provider: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenResponse:
    """OAuth2 token response."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    id_token: Optional[str] = None
    scope: str = ""


class SSOProvider(ABC):
    """Abstract SSO provider."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider_name = config.get("provider", "generic")

    @abstractmethod
    async def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Get authorization URL."""
        pass

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str, state: str) -> TokenResponse:
        """Exchange authorization code for tokens."""
        pass

    @abstractmethod
    async def get_user_info(self, token: TokenResponse) -> SSOUserInfo:
        """Get user info from provider."""
        pass

    @abstractmethod
    async def logout(self, token: TokenResponse, redirect_uri: str) -> str:
        """Get logout URL."""
        pass


class OIDCProvider(SSOProvider):
    """OpenID Connect provider (Azure AD, Okta, Keycloak, Generic)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        oidc_config = config.get("oidc", {})
        self.client_id = oidc_config.get("client_id", "")
        self.client_secret = oidc_config.get("client_secret", "")
        self.issuer_url = oidc_config.get("issuer_url", "").format(
            tenant=oidc_config.get("tenant_id", "")
        )
        self.redirect_uri = oidc_config.get("redirect_uri", "")
        self.scopes = oidc_config.get("scopes", ["openid", "profile", "email", "groups"])
        self._metadata: Optional[Dict] = None
        self._jwks: Optional[Any] = None

    async def _get_metadata(self) -> Dict:
        """Fetch OIDC discovery metadata."""
        if self._metadata:
            return self._metadata

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.issuer_url}/.well-known/openid-configuration",
                timeout=10.0
            )
            response.raise_for_status()
            self._metadata = response.json()
            return self._metadata

    async def _get_jwks(self) -> Any:
        """Fetch JWKS for token validation."""
        if self._jwks:
            return self._jwks

        metadata = await self._get_metadata()
        jwks_uri = metadata.get("jwks_uri")

        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_uri, timeout=10.0)
            response.raise_for_status()
            self._jwks = KeySet.import_key_set(response.json())
            return self._jwks

    async def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Build OIDC authorization URL."""
        metadata = await self._get_metadata()
        auth_endpoint = metadata.get("authorization_endpoint")

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            "response_mode": "query",
        }

        return f"{auth_endpoint}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str, state: str) -> TokenResponse:
        """Exchange authorization code for tokens."""
        metadata = await self._get_metadata()
        token_endpoint = metadata.get("token_endpoint")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0
            )
            response.raise_for_status()
            token_data = response.json()

        return TokenResponse(
            access_token=token_data.get("access_token", ""),
            token_type=token_data.get("token_type", "Bearer"),
            expires_in=token_data.get("expires_in"),
            refresh_token=token_data.get("refresh_token"),
            id_token=token_data.get("id_token"),
            scope=token_data.get("scope", ""),
        )

    async def get_user_info(self, token: TokenResponse) -> SSOUserInfo:
        """Get user info from UserInfo endpoint or ID token."""
        # Try UserInfo endpoint first
        metadata = await self._get_metadata()
        userinfo_endpoint = metadata.get("userinfo_endpoint")

        if userinfo_endpoint and token.access_token:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    userinfo_endpoint,
                    headers={"Authorization": f"Bearer {token.access_token}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return self._parse_userinfo(response.json(), token)

        # Fallback to ID token
        if token.id_token:
            return await self._parse_id_token(token.id_token)

        raise ValueError("Unable to get user info")

    async def _parse_id_token(self, id_token: str) -> SSOUserInfo:
        """Parse and validate ID token (joserfc signature + iss/aud/exp checks)."""
        import time as _time

        try:
            jwks = await self._get_jwks()
        except Exception as e:
            logger.warning("OIDC JWKS fetch failed: %s", e)
            raise ValueError("Invalid ID token")
        try:
            token = jose_jwt.decode(id_token, jwks)
        except Exception as e:
            logger.warning("OIDC id_token verification failed: %s", e)
            raise ValueError("Invalid ID token")
        claims = dict(token.claims)
        now = int(_time.time())
        try:
            exp = int(claims.get("exp", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError("Invalid ID token expiry")
        if exp <= now:
            raise ValueError("ID token expired")
        iss = claims.get("iss", "")
        if not iss or iss != self.issuer_url:
            raise ValueError("Invalid ID token issuer")
        aud = claims.get("aud", "")
        if isinstance(aud, list):
            if self.client_id not in aud:
                raise ValueError("Invalid ID token audience")
        elif aud != self.client_id:
            raise ValueError("Invalid ID token audience")
        if not claims.get("sub"):
            raise ValueError("Invalid ID token subject")
        return self._parse_userinfo(claims, TokenResponse(access_token="", id_token=id_token))

    def _parse_userinfo(self, claims: Dict[str, Any], token: TokenResponse) -> SSOUserInfo:
        """Normalize user info from claims."""
        return SSOUserInfo(
            subject=claims.get("sub", ""),
            email=claims.get("email", claims.get("upn", "")),
            name=claims.get("name", claims.get("preferred_username", "")),
            given_name=claims.get("given_name"),
            family_name=claims.get("family_name"),
            groups=claims.get("groups", claims.get("roles", [])),
            provider=self.provider_name,
            raw=claims,
        )

    async def logout(self, token: TokenResponse, redirect_uri: str) -> str:
        """Get OIDC logout URL."""
        metadata = await self._get_metadata()
        logout_endpoint = metadata.get("end_session_endpoint")

        if not logout_endpoint:
            return redirect_uri

        params = {
            "post_logout_redirect_uri": redirect_uri,
            "client_id": self.client_id,
        }

        if token.id_token:
            params["id_token_hint"] = token.id_token

        return f"{logout_endpoint}?{urlencode(params)}"


class SAMLProvider(SSOProvider):
    """SAML 2.0 provider (Azure AD, Okta, Ping, Generic).

    SAML login completes via the Assertion Consumer Service (ACS) endpoint
    only: the IdP POSTs its response there after ``get_auth_url`` redirects
    the browser to the IdP. The OAuth2-style authorization-code exchange used
    by OIDC does not exist in SAML, so ``exchange_code``/``get_user_info``
    are unsupported by design.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        saml_config = config.get("saml", {})
        self.entity_id = saml_config.get("entity_id", "")
        self.sso_url = saml_config.get("sso_url", "")
        self.x509_cert = saml_config.get("x509_cert", "")
        self.private_key = saml_config.get("private_key", "")

    async def get_auth_url(self, state: str, redirect_uri: str) -> str:
        """Generate SAML AuthnRequest and redirect."""
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        req = {"http_host": "localhost:8002", "script_name": "/auth/saml/login", "server_port": 8002, "get_data": {}, "post_data": {}}

        auth = OneLogin_Saml2_Auth(req, self._get_saml_settings())
        auth.login(force_authn=False)
        return auth.get_sso_url()

    def _get_saml_settings(self) -> Dict:
        """Get SAML settings for onelogin."""
        return {
            "strict": True,
            "debug": settings.app.debug,
            "sp": {
                "entityId": self.entity_id,
                "assertionConsumerService": {
                    "url": f"{self.entity_id}/auth/saml/acs",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "singleLogoutService": {
                    "url": f"{self.entity_id}/auth/saml/sls",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": self.x509_cert,
                "privateKey": self.private_key,
            },
            "idp": {
                "entityId": self.sso_url,
                "singleSignOnService": {
                    "url": self.sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "singleLogoutService": {
                    "url": self.sso_url.replace("/saml2", "/saml2/logout"),
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": self.x509_cert,
            },
        }

    async def exchange_code(self, code: str, redirect_uri: str, state: str) -> TokenResponse:
        """Unsupported by design: SAML completes via the ACS endpoint, not a code exchange."""
        raise NotImplementedError("SAML login completes via the ACS endpoint; code exchange is unsupported by design")

    async def get_user_info(self, token: TokenResponse) -> SSOUserInfo:
        """Unsupported by design: SAML user attributes arrive via the ACS response."""
        raise NotImplementedError("SAML user info arrives via the ACS response; token lookup is unsupported by design")

    def _build_acs_request(self, saml_response: str, relay_state: str = "") -> Dict[str, Any]:
        """Build the python3-saml request dict for an ACS POST."""
        return {
            "http_host": "localhost:8002",
            "script_name": "/auth/saml/acs",
            "server_port": 8002,
            "get_data": {},
            "post_data": {
                "SAMLResponse": saml_response,
                "RelayState": relay_state or "",
            },
        }

    @staticmethod
    def _first_attr(attrs: Dict[str, Any], *names: str) -> str:
        """First non-empty attribute value across candidate attribute names."""
        for name in names:
            values = attrs.get(name)
            if isinstance(values, list):
                for value in values:
                    if value:
                        return str(value)
            elif values:
                return str(values)
        return ""

    @staticmethod
    def _all_groups(attrs: Dict[str, Any]) -> List[str]:
        """Collect group/role values across common IdP attribute names."""
        groups: List[str] = []
        for key in (
            "groups",
            "Groups",
            "group",
            "roles",
            "memberOf",
            "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
            "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
        ):
            values = attrs.get(key)
            if isinstance(values, list):
                groups.extend(str(v) for v in values if v)
            elif values:
                groups.append(str(values))
        # De-duplicate while preserving order.
        seen: Set[str] = set()
        unique: List[str] = []
        for group in groups:
            if group not in seen:
                seen.add(group)
                unique.append(group)
        return unique

    async def process_acs(self, saml_response: str, relay_state: str = "") -> SSOUserInfo:
        """Parse and validate the IdP's ACS POST, returning normalized user info.

        Raises:
            ValueError: with the provider's message when the response is
                missing or fails validation (never a raw traceback).
        """
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        if not saml_response or not str(saml_response).strip():
            raise ValueError("SAML authentication failed: missing SAML response")

        try:
            auth = OneLogin_Saml2_Auth(
                self._build_acs_request(str(saml_response), relay_state or ""),
                self._get_saml_settings(),
            )
            auth.process_response()
        except ValueError as e:
            # Re-raise our own validation errors; wrap library ValueErrors
            # (e.g. binascii.Error from base64 decoding) so callers only
            # ever see a provider-level message, never a raw traceback.
            if str(e).startswith("SAML authentication failed"):
                raise
            logger.warning("SAML ACS processing error: %s", e)
            raise ValueError("SAML authentication failed: unable to process response")
        except Exception as e:  # noqa: BLE001 — surfacing raw traces would leak internals
            logger.warning("SAML ACS processing error: %s", e)
            raise ValueError("SAML authentication failed: unable to process response")

        errors = auth.get_errors() or []
        if errors:
            reason = auth.get_last_error_reason() or ", ".join(errors)
            raise ValueError(f"SAML authentication failed: {reason}")

        if not auth.is_authenticated():
            raise ValueError("SAML authentication failed: response not authenticated")

        name_id = auth.get_nameid() or ""
        attrs = auth.get_attributes() or {}

        email = self._first_attr(
            attrs,
            "email",
            "Email",
            "mail",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
            "http://schemas.xmlsoap.org/claims/EmailAddress",
        )
        name = self._first_attr(
            attrs,
            "displayName",
            "display_name",
            "cn",
            "name",
            "http://schemas.microsoft.com/identity/claims/displayname",
            "preferred_username",
            "upn",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
        )
        given_name = self._first_attr(
            attrs,
            "givenName",
            "given_name",
            "firstName",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
        ) or None
        family_name = self._first_attr(
            attrs,
            "sn",
            "surname",
            "family_name",
            "lastName",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
        ) or None
        if not name:
            full = " ".join(part for part in (given_name, family_name) if part).strip()
            name = full or email or name_id

        subject = name_id or email
        if not subject:
            raise ValueError("SAML authentication failed: response carries no subject")
        if not email and "@" in subject:
            email = subject

        return SSOUserInfo(
            subject=subject,
            email=email,
            name=name or subject,
            given_name=given_name,
            family_name=family_name,
            groups=self._all_groups(attrs),
            provider=self.provider_name,
            raw={"name_id": name_id, "attributes": attrs, "relay_state": relay_state or ""},
        )

    async def logout(self, token: TokenResponse, redirect_uri: str) -> str:
        """Initiate SAML single logout; fall back to redirect_uri when unconfigured."""
        try:
            from onelogin.saml2.auth import OneLogin_Saml2_Auth

            req = {
                "http_host": "localhost:8002",
                "script_name": "/auth/saml/sls",
                "server_port": 8002,
                "get_data": {},
                "post_data": {},
            }
            auth = OneLogin_Saml2_Auth(req, self._get_saml_settings())
            slo_url = auth.logout(return_to=redirect_uri or "/")
            if slo_url:
                return slo_url
        except Exception as e:  # noqa: BLE001 — SLO is best-effort
            logger.warning("SAML logout init failed, falling back to redirect: %s", e)
        return redirect_uri


class SSOManager:
    """Manages SSO providers and user provisioning."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or settings.sso.model_dump()
        self.provider: Optional[SSOProvider] = None
        self._init_provider()

    def _init_provider(self):
        """Initialize the configured SSO provider."""
        if not self.config.get("enabled"):
            return

        provider_type = self.config.get("provider", "azure_ad")

        if provider_type in ("azure_ad", "okta", "keycloak", "generic_oidc"):
            self.provider = OIDCProvider(self.config)
        elif provider_type in ("generic_saml", "azure_ad_saml", "okta_saml", "ping_saml"):
            self.provider = SAMLProvider(self.config)
        else:
            logger.warning(f"Unknown SSO provider: {provider_type}")

    def is_enabled(self) -> bool:
        return self.provider is not None

    def get_provider(self) -> Optional[SSOProvider]:
        return self.provider

    async def initiate_login(self, redirect_uri: str) -> tuple[str, str]:
        """Start SSO login flow. Returns (auth_url, state)."""
        if not self.provider:
            raise ValueError("SSO not configured")

        state = secrets.token_urlsafe(32)
        # Remember the state so complete_login can verify it. Without this
        # the state parameter was generated and then discarded, so an
        # attacker could force a victim's browser to complete a callback
        # carrying the attacker's authorization code (login CSRF), binding
        # the victim's session to the attacker's identity.
        _remember_state(state)
        auth_url = await self.provider.get_auth_url(state, redirect_uri)
        return auth_url, state

    async def complete_login(
        self,
        code: str,
        state: str,
        redirect_uri: str
    ) -> tuple[TokenResponse, SSOUserInfo]:
        """Complete SSO login flow."""
        if not self.provider:
            raise ValueError("SSO not configured")

        # Verify the state round-tripped before exchanging the code.
        if not _consume_state(state):
            raise ValueError("Invalid or expired SSO state")

        token = await self.provider.exchange_code(code, redirect_uri, state)
        user_info = await self.provider.get_user_info(token)
        return token, user_info

    def provision_user(self, user_info: SSOUserInfo) -> Dict[str, Any]:
        """Provision or update user from SSO info."""
        from api.auth import user_manager

        email = (user_info.email or "").lower().strip()
        # Reuse existing account by email to avoid duplicates.
        if email:
            existing = user_manager.get_user_by_email(email)
            if existing:
                username = existing["username"]
                user_manager.update_user(
                    username,
                    sso_subject=user_info.subject,
                    sso_provider=user_info.provider,
                    full_name=user_info.name,
                    email=email,
                )
                return user_manager.get_user_info(username)
        # Reuse existing account by SSO subject (email may change).
        if user_info.subject:
            for candidate in user_manager.get_users():
                if candidate.get("sso_subject") == user_info.subject and candidate.get(
                    "sso_provider"
                ) == user_info.provider:
                    user_manager.update_user(
                        candidate["username"],
                        full_name=user_info.name,
                        email=email or candidate.get("email", ""),
                    )
                    return user_manager.get_user_info(candidate["username"])

        # For now, use email as username
        username = email.split("@")[0] if "@" in email else (user_info.subject or "sso_user")

        # Ensure unique username
        base_username = username
        counter = 1
        while user_manager.get_user_info(username):
            username = f"{base_username}{counter}"
            counter += 1

        # Create user with SSO info
        user_manager.add_user(
            username=username,
            password="",  # No password for SSO users
            role=settings.rbac.default_role,
            email=email,
            sso_subject=user_info.subject,
            sso_provider=user_info.provider,
            full_name=user_info.name,
        )

        return user_manager.get_user_info(username)


# Global instance
sso_manager = SSOManager()


def get_sso_manager() -> SSOManager:
    """Dependency injection for SSO manager."""
    return sso_manager