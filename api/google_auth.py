"""
Google OAuth 2.0 (PKCE desktop flow) for the Gemini provider.

Lets the user "Sign in with Google" from the Settings page and use their
Google account directly with the Gemini API (no pasted API key required).

Setup (one-time):
  1. Create an OAuth Client ID of type "Desktop app" at
     https://console.cloud.google.com/apis/credentials  (or Google AI Studio
     -> Get API key -> Configure OAuth client).
  2. Add the client ID (and secret, if issued) to config/settings.yaml under
     `google:` or export GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.
  3. The redirect URI must be registered for the client:
     http://127.0.0.1:8002/auth/google/callback

Tokens are stored locally in config/google_oauth_token.json and silently
refreshed. The Gemini provider (agent/llm_client.GeminiProvider) uses the
access token as the Authorization: Bearer credential.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

TOKEN_PATH = Path("config/google_oauth_token.json")
PENDING_PATH = Path("config/google_oauth_pending.json")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "openid email"


@dataclass
class GoogleOAuthConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://127.0.0.1:8002/auth/google/callback"


def _load_google_config() -> GoogleOAuthConfig:
    """Read google OAuth config from settings.yaml or env vars."""
    cfg = GoogleOAuthConfig()
    try:
        import yaml  # noqa: PLC0415
        from pathlib import Path as _P

        p = _P("config/settings.yaml")
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            g = data.get("google", {}) or {}
            cfg.client_id = str(g.get("client_id", "") or "")
            cfg.client_secret = str(g.get("client_secret", "") or "")
            cfg.redirect_uri = str(g.get("redirect_uri", "") or cfg.redirect_uri)
    except Exception:  # noqa: BLE001
        pass
    cfg.client_id = os.getenv("GOOGLE_CLIENT_ID", cfg.client_id)
    cfg.client_secret = os.getenv("GOOGLE_CLIENT_SECRET", cfg.client_secret)
    cfg.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", cfg.redirect_uri)
    return cfg


class GoogleAuthStore:
    """Persists and refreshes the Google OAuth tokens for Gemini."""

    def __init__(self):
        self._token: Optional[Dict] = None
        self._token_mtime: float = 0.0
        self._config: Optional[GoogleOAuthConfig] = None

    @property
    def config(self) -> GoogleOAuthConfig:
        if self._config is None:
            self._config = _load_google_config()
        return self._config

    # ── token cache ──────────────────────────────────────
    def _load_token(self) -> Optional[Dict]:
        try:
            if not TOKEN_PATH.exists():
                return None
            mtime = TOKEN_PATH.stat().st_mtime
            if self._token is None or mtime != self._token_mtime:
                self._token = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
                self._token_mtime = mtime
        except Exception:  # noqa: BLE001
            self._token = None
        return self._token

    def _save_token(self, data: Dict):
        TOKEN_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._token = data
        self._token_mtime = TOKEN_PATH.stat().st_mtime

    def is_active(self) -> bool:
        tok = self._load_token()
        if not tok:
            return False
        if not tok.get("access_token"):
            return False
        if tok.get("refresh_token"):
            return True
        return float(tok.get("expires_at", 0)) > time.time()

    def account_email(self) -> str:
        tok = self._load_token()
        if not tok:
            return ""
        id_token = tok.get("id_token", "")
        if id_token:
            payload = id_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            try:
                claims = json.loads(base64.urlsafe_b64decode(payload))
                return str(claims.get("email", ""))
            except Exception:  # noqa: BLE001
                pass
        return str(tok.get("email", ""))

    def is_configured(self) -> bool:
        return bool(self.config.client_id)

    def get_access_token(self) -> str:
        """Return a (possibly freshly refreshed) access token, or empty string."""
        tok = self._load_token()
        if not tok or not tok.get("access_token"):
            return ""
        expires_at = float(tok.get("expires_at", 0)) or 0
        if expires_at - time.time() > 60:
            return str(tok["access_token"])
        if not tok.get("refresh_token"):
            return ""
        new_tok = self._refresh(tok)
        return str(new_tok.get("access_token", "")) if new_tok else ""

    def _refresh(self, tok: Dict) -> Optional[Dict]:
        cfg = self.config
        body = {
            "grant_type": "refresh_token",
            "client_id": cfg.client_id,
            "refresh_token": tok.get("refresh_token", ""),
        }
        if cfg.client_secret:
            body["client_secret"] = cfg.client_secret
        try:
            resp = httpx.post(TOKEN_URL, data=body, timeout=20.0)
            resp.raise_for_status()
            data = resp.json()
            data = {
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token") or tok.get("refresh_token", ""),
                "id_token": data.get("id_token") or tok.get("id_token", ""),
                "expires_at": time.time() + float(data.get("expires_in", 3600)),
                "email": tok.get("email", ""),
            }
            self._save_token(data)
            return data
        except Exception as e:  # noqa: BLE001
            logger.warning("Google token refresh failed: %s", e)
            return None

    # ── sign-in flow ─────────────────────────────────────
    def build_signin_url(self) -> tuple[str, str]:
        """Create the PKCE authorization URL. Returns (url, error or '')."""
        cfg = self.config
        if not cfg.client_id:
            return "", "Google sign-in not configured. Add `google.client_id` to config/settings.yaml (Desktop-app OAuth client)."
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(24)
        PENDING_PATH.write_text(json.dumps({
            "verifier": verifier,
            "state": state,
            "created": time.time(),
        }), encoding="utf-8")
        params = {
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        from urllib.parse import urlencode
        return f"{AUTH_URL}?{urlencode(params)}", ""

    def handle_callback(self, code: str, state: str) -> tuple[bool, str]:
        """Exchange the authorization code for tokens. Returns (ok, message)."""
        cfg = self.config
        try:
            pending = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pending = {}
        if state and pending.get("state") and state != pending.get("state"):
            return False, "State mismatch — restart the sign-in"
        body = {
            "grant_type": "authorization_code",
            "client_id": cfg.client_id,
            "code": code,
            "redirect_uri": cfg.redirect_uri,
            "code_verifier": pending.get("verifier", ""),
        }
        if cfg.client_secret:
            body["client_secret"] = cfg.client_secret
        try:
            resp = httpx.post(TOKEN_URL, data=body, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            self._save_token({
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token", ""),
                "id_token": data.get("id_token", ""),
                "expires_at": time.time() + float(data.get("expires_in", 3600)),
                "email": self._email_from_id(data.get("id_token", "")) or "",
            })
        except Exception as e:  # noqa: BLE001
            detail = ""
            try:
                detail = str(resp.json().get("error_description") or resp.json().get("error") or "")  # noqa: F821
            except Exception:  # noqa: BLE001
                pass
            logger.warning("Google token exchange failed: %s", e)
            return False, f"Sign-in failed: {e} {detail}".strip()
        try:
            PENDING_PATH.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return True, f"Signed in as {self.account_email() or 'Google user'}"

    def _email_from_id(self, id_token: str) -> str:
        try:
            payload = id_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return str(json.loads(base64.urlsafe_b64decode(payload)).get("email", ""))
        except Exception:  # noqa: BLE001
            return ""

    def sign_out(self) -> bool:
        try:
            TOKEN_PATH.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        self._token = None
        return True

    def status(self) -> Dict:
        return {
            "active": self.is_active(),
            "email": self.account_email(),
            "configured": self.is_configured(),
        }


_store: Optional[GoogleAuthStore] = None


def get_google_auth_store() -> GoogleAuthStore:
    global _store
    if _store is None:
        _store = GoogleAuthStore()
    return _store