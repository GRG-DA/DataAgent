"""Non-blocking update check for the desktop exe.

Compares the bundled ``app.version`` against the latest published release tag.
Never blocks startup, never raises: returns a structured result that says what
actually happened, so the UI can distinguish "you are up to date" from "the
check did not work".

The release repository is configurable because the public release repo is not
published yet.  Resolution order:

1. ``$DATAAGENT_RELEASES_REPO``  -> ``<owner>/<repo>`` (used by CI and by
   anyone who forks the project)
2. ``config/settings.yaml`` -> ``app.releases_repo``
3. the built-in default ``DataAgent/DataAgent``

A repository that does not exist yet answers HTTP 404, which is NOT an error —
it simply means "no published release to compare against".  That case is
reported as :data:`STATUS_NO_RELEASE` rather than as a failure, so the app
never shows the user a scary message for a repo that has not been created.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent

#: Fallback release repository (``<owner>/<repo>`` on GitHub).
DEFAULT_RELEASES_REPO = "DataAgent/DataAgent"

#: Where the download links on the marketing site point.  Kept in sync with the
#: single configurable constant in ``website/download.html``.
DEFAULT_RELEASE_DOWNLOAD_BASE = (
    "https://github.com/{repo}/releases/latest/download"
)

TIMEOUT_S = 5

#: The update check succeeded and the published release is newer.
STATUS_UPDATE_AVAILABLE = "update_available"
#: The update check succeeded and the build is current.
STATUS_UP_TO_DATE = "up_to_date"
#: The release repository exists but has no published release yet.
STATUS_NO_RELEASE = "no_release"
#: The check could not complete (offline, DNS failure, rate limit, timeout...).
STATUS_UNAVAILABLE = "unavailable"


class UpdateResult(NamedTuple):
    """Outcome of an update check.

    ``available`` is True only when a strictly newer release was published.
    ``status`` is one of the ``STATUS_*`` constants above.  ``detail`` is a
    short, user-facing explanation — never a raw exception or a URL that
    leaks internal state.
    """

    available: bool
    current: str
    latest: str
    status: str
    detail: str


def releases_repo() -> str:
    """Return the ``owner/repo`` slug the update check queries."""
    env = (os.environ.get("DATAAGENT_RELEASES_REPO") or "").strip()
    if env:
        return env
    try:
        cfg = _settings_yaml()
        configured = str(((cfg.get("app") or {}).get("releases_repo")) or "").strip()
        if configured:
            return configured
    except Exception:  # noqa: BLE001 — the config is optional
        pass
    return DEFAULT_RELEASES_REPO


def releases_api_url() -> str:
    """The GitHub API URL for the latest release of the configured repo."""
    return f"https://api.github.com/repos/{releases_repo()}/releases/latest"


def release_download_url(asset: str = "DataAgent-Setup.exe") -> str:
    """The public download URL for a release asset."""
    return DEFAULT_RELEASE_DOWNLOAD_BASE.format(repo=releases_repo()) + f"/{asset}"


def _settings_yaml() -> dict:
    """Read config/settings.yaml (frozen-safe). Returns {} on any failure."""
    try:
        from shell.paths import resource_path

        cfg = Path(resource_path("config/settings.yaml"))
        if not cfg.exists():
            cfg = ROOT / "config" / "settings.yaml"
        return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def current_version() -> str:
    """Read app.version from config/settings.yaml (frozen-safe)."""
    try:
        data = _settings_yaml()
        return str((data.get("app") or {}).get("version", "0.0.0"))
    except Exception:  # noqa: BLE001
        return "0.0.0"


def _parse_tag(tag: str) -> tuple[int, ...]:
    parts = []
    for chunk in tag.lstrip("v").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def latest_version(timeout: float = TIMEOUT_S) -> Optional[str]:
    """Fetch the latest release tag.

    Returns ``None`` when the check could not be completed (offline, DNS
    failure, rate limit, timeout) and ``""`` when the repository exists but
    has **no published release** — the normal state of a project whose
    release repo has not been published yet.  The two are different
    situations and must not be reported the same way.
    """
    try:
        req = urllib.request.Request(
            releases_api_url(), headers={"User-Agent": "DataAgent"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        tag = str(payload.get("tag_name", "")).strip()
        return tag.lstrip("v") or None
    except urllib.error.HTTPError as exc:
        # 404 == the repository has no published release (or does not exist yet).
        # That is a normal state, not a failure.
        if exc.code == 404:
            return ""
        return None
    except Exception:  # noqa: BLE001
        return None


def update_available(timeout: float = TIMEOUT_S) -> tuple[bool, str, str]:
    """Return (available, current, latest). Never raises.

    Kept for backwards compatibility with the original 3-tuple contract.
    """
    result = check_for_update(timeout=timeout)
    return result.available, result.current, result.latest


def check_for_update(timeout: float = TIMEOUT_S) -> UpdateResult:
    """Run the update check and describe the outcome.

    Never raises and never performs a blocking network call on the startup
    path — call it from a background thread.
    """
    cur = current_version()
    try:
        lat = latest_version(timeout=timeout)
    except Exception:  # noqa: BLE001 — the check must never take the app down
        lat = None
    if lat == "":
        # The repository exists but has no published release yet. That is the
        # normal state before the first release - it is NOT an outage.
        return UpdateResult(
            available=False,
            current=cur,
            latest=cur,
            status=STATUS_NO_RELEASE,
            detail=(
                f"You are running the latest build (v{cur}). No newer release "
                "has been published yet."
            ),
        )
    if not lat:
        return UpdateResult(
            available=False,
            current=cur,
            latest=cur,
            status=STATUS_UNAVAILABLE,
            detail=(
                "Could not reach the release server. You are running the "
                f"latest build available to you (v{cur})."
            ),
        )
    try:
        newer = _parse_tag(lat) > _parse_tag(cur)
    except Exception:  # noqa: BLE001
        newer = False
    if newer:
        return UpdateResult(
            available=True,
            current=cur,
            latest=lat,
            status=STATUS_UPDATE_AVAILABLE,
            detail=(
                f"Version {lat} is available (you have {cur}). "
                "Download it from the website to update."
            ),
        )
    return UpdateResult(
        available=False,
        current=cur,
        latest=lat,
        status=STATUS_UP_TO_DATE,
        detail=f"You are on the latest version ({cur}).",
    )
