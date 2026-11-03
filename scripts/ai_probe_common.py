"""
Shared helpers for the AI feature probe and benchmark scripts.

Resolves the probe account created by `ai_probe_setup.py` so every script
uses the same credentials and the same seeded table.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

BASE = "http://127.0.0.1:8002"
TABLE = "ai_probe_sales"
STATE = Path(__file__).resolve().parent / ".ai_probe_user.json"

DEFAULT_USERNAME = "aiprobe"
DEFAULT_PASSWORD = "UiTest!2345x"


def credentials() -> tuple[str, str]:
    """The probe account to use. Falls back to a documented default."""
    if STATE.exists():
        try:
            data = json.loads(STATE.read_text(encoding="utf-8"))
            return data.get("username", DEFAULT_USERNAME), \
                data.get("password", DEFAULT_PASSWORD)
        except Exception:  # noqa: BLE001
            pass
    return DEFAULT_USERNAME, DEFAULT_PASSWORD


def call(method: str, path: str, body=None, token: str = "", timeout: int = 300):
    """Return (status, elapsed_ms, parsed_body)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ms = (time.perf_counter() - start) * 1000
            try:
                return resp.status, ms, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, ms, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - start) * 1000
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, ms, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, ms, raw
    except Exception as e:  # noqa: BLE001
        ms = (time.perf_counter() - start) * 1000
        return 0, ms, str(e)[:300]


def login() -> str:
    """Log in as the probe account; returns the bearer token."""
    username, password = credentials()
    status, _, body = call("POST", "/auth/login",
                           {"username": username, "password": password})
    if status == 200 and isinstance(body, dict) and body.get("token"):
        return body["token"]
    raise SystemExit(f"login failed for {username}: {status} {body}")


def used_llm(body, status: int) -> bool:
    """True only when the endpoint answered 200 AND did not fall back."""
    if status != 200:
        return False
    blob = json.dumps(body, default=str).lower()
    if isinstance(body, str):
        blob = body.lower()
    return not any(k in blob for k in
                   ("fallback", "rule-based", "no llm", "not configured"))
