"""
Google OAuth 2.0 (PKCE) for user login.

Lets users "Sign in with Google" from the Settings page and get a real app
account (JWT + user record) provisioned automatically from their Google
profile. This is separate from the Gemini-provider Google sign-in in
api/google_auth.py — it authenticates the *user*, not the LLM provider.

Setup (one-time):
  1. Create an OAuth Client ID of type "Desktop app" at
     https://console.cloud.google.com/apis/credentials
  2. Add the client ID (and secret, if issued) to config/settings.yaml under
     `google_login:` (falls back to the `google:` block used by Gemini), or
     export GOOGLE_LOGIN_CLIENT_ID / GOOGLE_LOGIN_CLIENT_SECRET.
  3. Register this redirect URI on that client:
       http://127.0.0.1:8002/auth/google/login/callback
     (change `redirect_uri` below if your host/port differs)

Flow:
  GET /auth/google/login            -> Google consent URL (PKCE, state)
  GET /auth/google/login/callback   -> exchange code, provision user, issue JWT
  GET /auth/google/login/result     -> frontend claims {token, user} by state

The JWT is never placed in a URL: the callback stores it in a short-lived
pending-result store keyed by the OAuth `state`, and the frontend exchanges
that state for the token via /auth/google/login/result.
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

PENDING_DIR = Path("config/google_login_pending")
PENDING_TTL_SECONDS = 600
RESULT_PATH = Path("config/google_login_result.json")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "openid email profile"
RESULT_TTL_SECONDS = 120


@dataclass
class GoogleLoginConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://127.0.0.1:8002/auth/google/login/callback"
    auto_provision: bool = True
    # Self-serve standard, matching settings.rbac.default_role. "viewer" used
    # to auto-provision Google sign-ups as read-only, which locked them out
    # of dashboard creation (the AI builder) with confusing 403 toasts.
    default_role: str = "user"


def _load_google_login_config() -> GoogleLoginConfig:
    """Read google_login config from settings.yaml or env vars."""
    cfg = GoogleLoginConfig()
    try:
        import yaml  # noqa: PLC0415

        p = Path("config/settings.yaml")
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            g = data.get("google_login", {}) or {}
            cfg.client_id = str(g.get("client_id", "") or "")
            cfg.client_secret = str(g.get("client_secret", "") or "")
            cfg.redirect_uri = str(g.get("redirect_uri", "") or cfg.redirect_uri)
            if g.get("auto_provision") is not None:
                cfg.auto_provision = bool(g.get("auto_provision"))
            cfg.default_role = str(g.get("default_role", "") or cfg.default_role)
    except Exception:  # noqa: BLE001
        pass

    # Fall back to the Gemini `google:` block when google_login is empty.
    if not cfg.client_id or not cfg.client_secret:
        try:
            import yaml  # noqa: PLC0415

            p = Path("config/settings.yaml")
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                g = data.get("google", {}) or {}
                if not cfg.client_id:
                    cfg.client_id = str(g.get("client_id", "") or "")
                if not cfg.client_secret:
                    cfg.client_secret = str(g.get("client_secret", "") or "")
        except Exception:  # noqa: BLE001
            pass

    cfg.client_id = os.getenv("GOOGLE_LOGIN_CLIENT_ID", cfg.client_id)
    cfg.client_secret = os.getenv("GOOGLE_LOGIN_CLIENT_SECRET", cfg.client_secret)
    cfg.redirect_uri = os.getenv("GOOGLE_LOGIN_REDIRECT_URI", cfg.redirect_uri)
    return cfg


class GoogleLoginManager:
    """Google OAuth login flow: consent URL, callback, user provisioning."""

    def __init__(self):
        self._config: Optional[GoogleLoginConfig] = None

    @property
    def config(self) -> GoogleLoginConfig:
        if self._config is None:
            self._config = _load_google_login_config()
        return self._config

    def is_configured(self) -> bool:
        return bool(self.config.client_id)

    def status(self) -> Dict:
        return {
            "configured": self.is_configured(),
            "enabled": self.is_configured() and self.config.auto_provision,
            "redirect_uri": self.config.redirect_uri,
        }

    # ── sign-in flow ─────────────────────────────────────
    def _pending_file(self, state: str) -> Path:
        """Per-state pending file — concurrent logins never clobber."""
        safe = re.sub(r"[^A-Za-z0-9_-]", "", state or "")[:64] or "unknown"
        return PENDING_DIR / f"{safe}.json"

    def _read_pending(self, state: str) -> Dict:
        try:
            raw = json.loads(self._pending_file(state).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
        try:
            if time.time() - float(raw.get("created", 0)) > PENDING_TTL_SECONDS:
                try:
                    self._pending_file(state).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                return {}
        except (ValueError, TypeError):
            return {}
        return raw

    def build_login_url(self) -> tuple[str, str]:
        """Create the PKCE authorization URL. Returns (url, error or '')."""
        cfg = self.config
        if not cfg.client_id:
            return "", ("Google login not configured. Add `google_login.client_id` "
                        "to config/settings.yaml (Desktop-app OAuth client).")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(24)
        try:
            PENDING_DIR.mkdir(parents=True, exist_ok=True)
            self._pending_file(state).write_text(json.dumps({
                "verifier": verifier,
                "state": state,
                "created": time.time(),
            }), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return "", f"Could not start sign-in: {exc}"
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

    def handle_callback(self, code: str, state: str) -> tuple[bool, str, Dict]:
        """Exchange the code for tokens and return (ok, message, claims)."""
        cfg = self.config
        pending = self._read_pending(state or "")
        if state and not pending:
            # Unknown/expired state file: possible concurrent-login collision
            # on very old installs, or a replayed callback. Never proceed
            # without the matching PKCE verifier.
            try:
                legacy = json.loads(Path("config/google_login_pending.json")
                                    .read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                legacy = {}
            if legacy.get("state") == state:
                pending = legacy
            else:
                return False, "Sign-in expired — restart the sign-in", {}
        if state and pending.get("state") and state != pending.get("state"):
            return False, "State mismatch — restart the sign-in", {}
        body = {
            "grant_type": "authorization_code",
            "client_id": cfg.client_id,
            "code": code,
            "redirect_uri": cfg.redirect_uri,
            "code_verifier": pending.get("verifier", ""),
        }
        if cfg.client_secret:
            body["client_secret"] = cfg.client_secret
        resp = None
        try:
            resp = httpx.post(TOKEN_URL, data=body, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            try:
                detail = str(resp.json().get("error_description") or resp.json().get("error") or "")  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            logger.warning("Google login token exchange failed: %s %s", e, detail)
            return False, "Sign-in failed — please try again", {}
        try:
            self._pending_file(state or "").unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        claims = self._verified_claims(data.get("id_token", ""))
        if not claims.get("email"):
            return False, "Google did not return an email address", {}
        return True, f"Signed in as {claims['email']}", claims

    _GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
    _GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
    _certs_cache: dict = {}

    def _verified_claims(self, id_token: str) -> Dict:
        """Verify the Google RS256 signature + aud/iss/exp before trusting.

        Fail closed: anything unverifiable yields {} (same failure mode as
        before — "no email address"), never an impersonated account.
        """
        try:
            from joserfc import jwt as _jose_jwt
            from joserfc.jwk import KeySet as _KeySet

            now = time.time()
            cached = type(self)._certs_cache
            if now - float(cached.get("ts", 0)) > 3600 or "keys" not in cached:
                resp = httpx.get(self._GOOGLE_CERTS_URL, timeout=15.0)
                resp.raise_for_status()
                cached.clear()
                cached.update({"ts": now, "keys": resp.json()})
            key_set = _KeySet.import_key_set(cached["keys"])
            token = _jose_jwt.decode(id_token, key_set)
            claims = dict(token.claims)
            try:
                exp = int(claims.get("exp", 0) or 0)
            except (TypeError, ValueError):
                return {}
            if exp <= int(now):
                return {}
            if claims.get("aud") != self.config.client_id:
                return {}
            if claims.get("iss") not in self._GOOGLE_ISSUERS:
                return {}
            if claims.get("email_verified") is False:
                return {}
            return {"email": str(claims.get("email", "") or ""),
                    "sub": str(claims.get("sub", "") or ""),
                    "name": str(claims.get("name", "") or "")}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google id_token verification failed: %s", exc)
            return {}

    # ── user provisioning ────────────────────────────────
    def provision_user(self, claims: Dict) -> Optional[Dict]:
        """Find or create the app user for a Google account. Returns user info."""
        from api.auth import user_manager

        email = str(claims.get("email", "")).lower()
        if not email:
            return None
        name = str(claims.get("name", "") or "")
        google_sub = str(claims.get("sub", "") or "")

        existing = user_manager.get_user_by_email(email)
        if existing:
            username = existing["username"]
            updates = {}
            if not existing.get("sso_subject"):
                updates["sso_subject"] = google_sub
            if not existing.get("sso_provider"):
                updates["sso_provider"] = "google_login"
            if name and not existing.get("full_name"):
                updates["full_name"] = name
            if updates:
                user_manager.update_user(username, **updates)
            return user_manager.get_user_info(username)

        if not self.config.auto_provision:
            return None

        base_username = email.split("@")[0] or "google_user"
        username = base_username
        counter = 1
        while user_manager.get_user_info(username):
            username = f"{base_username}{counter}"
            counter += 1

        user_manager.add_user(
            username=username,
            password="",  # SSO-style user: no password
            role=self.config.default_role,
            email=email,
            sso_subject=google_sub,
            sso_provider="google_login",
            full_name=name,
        )
        return user_manager.get_user_info(username)

    # ── pending result (JWT hand-off) ────────────────────
    _results_lock = None  # lazy threading.Lock (module-level would fork-copy)

    def _locked_results(self):
        import threading as _th

        if type(self)._results_lock is None:
            type(self)._results_lock = _th.Lock()
        return type(self)._results_lock

    def _atomic_write_json(self, path, data: Dict) -> None:
        import os as _os
        import tempfile as _tf

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = _tf.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            _os.replace(tmp, path)
        finally:
            try:
                _os.unlink(tmp)
            except Exception:  # noqa: BLE001
                pass

    def store_result(self, state: str, token: str, user: Dict):
        """Store the issued JWT keyed by state so the frontend can claim it."""
        try:
            with self._locked_results():
                results = self._load_results()
                results[state] = {
                    "token": token,
                    "user": user,
                    "created": time.time(),
                }
                # Drop expired entries.
                results = {k: v for k, v in results.items()
                           if time.time() - float(v.get("created", 0)) < RESULT_TTL_SECONDS}
                self._atomic_write_json(RESULT_PATH, results)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to store google login result: %s", e)

    def claim_result(self, state: str) -> Optional[Dict]:
        """Return and delete the pending login result for a state."""
        try:
            with self._locked_results():
                results = self._load_results()
                entry = results.pop(state, None)
                if not entry:
                    return None
                try:
                    if time.time() - float(entry.get("created", 0)) >= RESULT_TTL_SECONDS:
                        entry = None
                except (ValueError, TypeError):
                    entry = None
                try:
                    self._atomic_write_json(RESULT_PATH, results)
                except Exception:  # noqa: BLE001
                    pass
                return entry
        except Exception:  # noqa: BLE001
            return None

    def _load_results(self) -> Dict:
        try:
            if RESULT_PATH.exists():
                return json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        return {}


_manager: Optional[GoogleLoginManager] = None


def get_google_login_manager() -> GoogleLoginManager:
    global _manager
    if _manager is None:
        _manager = GoogleLoginManager()
    return _manager