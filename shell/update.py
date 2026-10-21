"""Non-blocking update check for the desktop exe.

Compares the bundled app.version against the latest GitHub release tag.
Never blocks startup, never raises: returns None when offline/unknown.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RELEASES_URL = "https://api.github.com/repos/DataAgent/DataAgent/releases/latest"
TIMEOUT_S = 5


def current_version() -> str:
    """Read app.version from config/settings.yaml (frozen-safe)."""
    try:
        from shell.paths import resource_path

        cfg = Path(resource_path("config/settings.yaml"))
        if not cfg.exists():
            cfg = ROOT / "config" / "settings.yaml"
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        return str((data.get("app") or {}).get("version", "0.0.0"))
    except Exception:  # noqa: BLE001
        return "0.0.0"


def _parse_tag(tag: str) -> tuple[int, ...]:
    parts = []
    for chunk in tag.lstrip("v").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def latest_version(timeout: float = TIMEOUT_S) -> str | None:
    """Fetch latest release tag. None on any failure (offline-safe)."""
    try:
        req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": "DataAgent"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        tag = str(payload.get("tag_name", "")).strip()
        return tag.lstrip("v") or None
    except Exception:  # noqa: BLE001
        return None


def update_available(timeout: float = TIMEOUT_S) -> tuple[bool, str, str]:
    """Return (available, current, latest). Never raises."""
    cur = current_version()
    lat = latest_version(timeout=timeout)
    if not lat:
        return False, cur, cur
    try:
        available = _parse_tag(lat) > _parse_tag(cur)
    except Exception:  # noqa: BLE001
        available = False
    return available, cur, lat
