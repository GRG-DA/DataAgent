"""
WARP Rotator
============
Cloudflare WARP quick-reset for rotating the egress IP when the opencode
free tier's per-IP quota is exhausted. Uses `warp-cli` (disconnect →
registration new → connect), which needs no sudo on Linux and works
identically on Windows.

Conservative by design: at most one rotation per ``interval`` seconds, and a
longer backoff window after consecutive failures. Never raises — callers
treat a False return as "give up and fall back to rules".
"""

import asyncio
import logging
import os
import re
import shutil
import sys
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_WARP_CLI_CANDIDATES = ("warp-cli", "warp-cli.exe")

# Known install locations per platform, checked after PATH.
_WARP_CLI_KNOWN_PATHS = {
    "nt": (
        r"%PROGRAMFILES%\Cloudflare\Cloudflare WARP\warp-cli.exe",
        r"%PROGRAMFILES(X86)%\Cloudflare\Cloudflare WARP\warp-cli.exe",
    ),
    "darwin": (
        "/Applications/Cloudflare WARP.app/Contents/MacOS/warp-cli",
        "/Applications/Cloudflare WARP.app/Contents/Resources/warp-cli",
    ),
    "linux": (
        "/usr/sbin/warp-cli",
        "/usr/bin/warp-cli",
    ),
}


def _warp_cli_path() -> Optional[str]:
    """Locate the warp-cli binary (PATH, then known per-OS install dirs)."""
    for probe in _WARP_CLI_CANDIDATES:
        found = shutil.which(probe)
        if found:
            return found
    key = "nt" if os.name == "nt" else sys.platform
    for template in _WARP_CLI_KNOWN_PATHS.get(key, ()):
        candidate = os.path.expandvars(template)
        if os.path.exists(candidate):
            return candidate
    return None


async def _linux_warp_service_active() -> bool:
    """True when the warp-svc daemon is active (Linux only)."""
    if not sys.platform.startswith("linux"):
        return True
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "warp-svc",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        return (out or b"").decode("utf-8", errors="replace").strip() == "active"
    except Exception as e:  # noqa: BLE001
        logger.debug("warp-svc status check failed: %s", e)
        return False


class WarpRotator:
    """Rotate the Cloudflare WARP egress IP to reset per-IP rate limits."""

    def __init__(
        self,
        interval: float = 180.0,
        backoff_window: float = 600.0,
        max_consecutive_failures: int = 2,
    ):
        self._interval = interval
        self._backoff_window = backoff_window
        self._max_consecutive_failures = max_consecutive_failures
        self._lock = asyncio.Lock()
        self._last_rotate_at: float = 0.0
        self._consecutive_failures: int = 0
        self._last_ip: Optional[str] = None

    # ── public API ──────────────────────────────────────────────

    async def rotate(self, force: bool = False) -> bool:
        """Quick-reset (disconnect → registration new → connect).

        Returns True when the tunnel is connected afterwards. Throttled by
        ``interval`` and backed off after repeated failures, unless ``force``
        is set (explicit user-triggered reset always rotates).
        """
        async with self._lock:
            now = time.time()
            if not force and now - self._last_rotate_at < self._interval:
                logger.info(
                    "WARP rotation throttled (%.0fs since last)", now - self._last_rotate_at
                )
                return await self.healthy()
            if (
                not force
                and self._consecutive_failures >= self._max_consecutive_failures
                and now - self._last_rotate_at < self._backoff_window
            ):
                logger.warning(
                    "WARP rotation in backoff window (%.0fs since last)", now - self._last_rotate_at
                )
                return await self.healthy()
            self._last_rotate_at = now
            cli = _warp_cli_path()
            if not cli:
                logger.warning("warp-cli not found — WARP rotation unavailable")
                return False
            try:
                await self._run(cli, "disconnect")
                await self._run(cli, "registration", "new")
                await self._run(cli, "connect")
            except Exception as e:  # noqa: BLE001
                self._consecutive_failures += 1
                logger.error("WARP rotation failed: %s", e)
                return False
            # The tunnel takes a moment to reach "healthy" after connect —
            # poll briefly so we don't report a false failure.
            ok = False
            for _ in range(10):
                if await self.healthy():
                    ok = True
                    break
                await asyncio.sleep(1.0)
            if ok:
                self._consecutive_failures = 0
                new_ip = await self.get_ip()
                if new_ip:
                    self._last_ip = new_ip
                    logger.info("WARP rotation complete — new egress IP %s", new_ip)
                else:
                    logger.info("WARP rotation complete (IP unknown)")
            else:
                self._consecutive_failures += 1
            return ok

    async def healthy(self) -> bool:
        """True when the WARP tunnel is connected and network is healthy."""
        cli = _warp_cli_path()
        if not cli:
            return False
        # Linux: warp-cli reports "connected" only once the warp-svc daemon is
        # up — treat a stopped service as not healthy instead of a false read.
        if not await _linux_warp_service_active():
            return False
        try:
            out = await self._run(cli, "status")
            text = (out or "").lower()
            # "connected" is a substring of "disconnected" — require the
            # explicit "Status update: Connected" form (real warp-cli output).
            return (
                "connected" in text
                and "disconnected" not in text
                and "healthy" in text
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("warp-cli status failed: %s", e)
            return False

    async def get_ip(self) -> Optional[str]:
        """Current egress IP via Cloudflare's trace endpoint (with retry)."""
        for _ in range(3):
            try:
                import httpx

                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get("https://www.cloudflare.com/cdn-cgi/trace")
                    if resp.status_code == 200:
                        m = re.search(r"^ip=(\S+)$", resp.text, re.MULTILINE)
                        if m:
                            return m.group(1)
            except Exception as e:  # noqa: BLE001
                logger.debug("WARP IP lookup failed: %s", e)
            await asyncio.sleep(1.0)
        return None

    async def connect(self) -> bool:
        cli = _warp_cli_path()
        if not cli:
            return False
        try:
            await self._run(cli, "connect")
            return await self.healthy()
        except Exception as e:  # noqa: BLE001
            logger.error("WARP connect failed: %s", e)
            return False

    async def disconnect(self) -> bool:
        cli = _warp_cli_path()
        if not cli:
            return False
        try:
            await self._run(cli, "disconnect")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("WARP disconnect failed: %s", e)
            return False

    async def status(self) -> Dict[str, object]:
        """Structured status for the Settings UI."""
        cli = _warp_cli_path()
        info: Dict[str, object] = {
            "installed": cli is not None,
            "binary": cli or "",
            "connected": False,
            "healthy": False,
            "ip": self._last_ip or "",
            "version": "",
            "raw": "",
        }
        if not cli:
            return info
        try:
            out = await self._run(cli, "status")
            info["raw"] = (out or "").strip()
            text = (out or "").lower()
            info["connected"] = "connected" in text and "disconnected" not in text
            info["healthy"] = (
                "connected" in text
                and "disconnected" not in text
                and "healthy" in text
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("warp-cli status failed: %s", e)
        try:
            ver = await self._run(cli, "--version")
            info["version"] = (ver or "").strip()[:200]
        except Exception:  # noqa: BLE001
            pass
        ip = await self.get_ip()
        if ip:
            info["ip"] = ip
            self._last_ip = ip
        return info

    # ── helpers ─────────────────────────────────────────────────

    async def _run(self, cli: str, *args: str) -> str:
        cmd = [cli]
        if await _accept_tos_supported(cli):
            # Newer warp-cli refuses non-TTY commands until the Terms of
            # Service are accepted; the global --accept-tos flag silences it.
            cmd.append("--accept-tos")
        cmd.extend(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            raise
        return (out or b"").decode("utf-8", errors="replace")


# Module-level singleton so the provider and the API share one throttle.
_rotator: Optional[WarpRotator] = None

# Cached: does this warp-cli build support the global --accept-tos flag?
_ACCEPT_TOS_SUPPORTED: Optional[bool] = None


async def _accept_tos_supported(cli: str) -> bool:
    """True when `warp-cli --help` advertises --accept-tos (cached)."""
    global _ACCEPT_TOS_SUPPORTED
    if _ACCEPT_TOS_SUPPORTED is None:
        try:
            proc = await asyncio.create_subprocess_exec(
                cli, "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            _ACCEPT_TOS_SUPPORTED = "--accept-tos" in (out or b"").decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            _ACCEPT_TOS_SUPPORTED = False
    return _ACCEPT_TOS_SUPPORTED


def get_rotator(interval: float = 180.0, backoff_window: float = 600.0) -> WarpRotator:
    """Return the shared WarpRotator (created on first use)."""
    global _rotator
    if _rotator is None:
        _rotator = WarpRotator(interval=interval, backoff_window=backoff_window)
    return _rotator