"""
WARP Bootstrap
==============
Silent, cross-platform install + connect of the Cloudflare WARP client so the
Data Agent AI's egress-IP rotation works out of the box.

Platforms:
  * Windows — silent MSI (``msiexec /qn /norestart``), arch-aware (x64/arm64).
  * Linux   — distro-aware repo install of the ``cloudflare-warp`` package,
              then ``systemctl enable --now warp-svc``.
  * macOS   — silent ``.pkg`` via ``installer`` (kept for future use).

Everything is idempotent: if ``warp-cli`` is already present we return early.
Elevation is requested only when actually needed — on Windows that is the
single unavoidable UAC consent; on Linux it uses ``pkexec`` (GUI) or ``sudo``.

The ``progress`` callback has the signature ``(stage, pct, line)`` and is
awaited, matching the ``_install_jobs`` polling shape used by the API.
"""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Awaitable, Callable, Dict, Optional

import httpx

from agent.warp import _warp_cli_path, get_rotator

logger = logging.getLogger(__name__)

# Official installer URLs (single source of truth for all installers).
_WARP_INSTALL_URLS = {
    "windows": "https://1111-releases.cloudflareclient.com/windows/Cloudflare_WARP_Release-x64.msi",
    "windows_arm64": "https://1111-releases.cloudflareclient.com/windows/Cloudflare_WARP_Release-arm64.msi",
    "darwin": "https://1111-releases.cloudflareclient.com/mac/Cloudflare_WARP.pkg",
    "linux": "https://pkg.cloudflareclient.com/install",
}

# Progress callback: (stage, pct, log_line). ``line`` may be empty.
Progress = Callable[[str, int, str], Awaitable[None]]


# ── platform helpers ─────────────────────────────────────────────

def _os_platform() -> str:
    """Normalized platform name: windows / linux / darwin."""
    if os.name == "nt":
        return "windows"
    return sys.platform


def _machine_arch() -> str:
    """Normalized machine arch: amd64 / arm64."""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "amd64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine


def is_elevated() -> bool:
    """True when the current process already has admin/root rights."""
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False
    if sys.platform.startswith("linux"):
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False
    return False


def _run_sync(command: list, cwd: Optional[str] = None, timeout: float = 600.0) -> int:
    """Run a command synchronously, hidden on Windows. Returns exit code."""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            command, cwd=cwd, timeout=timeout,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
        )
        return proc.returncode
    except Exception as e:  # noqa: BLE001
        logger.error("command failed: %s %s", command, e)
        return -1


# ── elevation ────────────────────────────────────────────────────

def _windows_elevate_msiexec(msi: str) -> bool:
    """Launch msiexec elevated via ShellExecuteW (the one UAC consent)."""
    try:
        import ctypes
        from ctypes import wintypes
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
        ]
        shell32.ShellExecuteW.restype = ctypes.c_intptr_t
        res = shell32.ShellExecuteW(
            None, "runas", "msiexec.exe", f'/i "{msi}" /qn /norestart', None, 0
        )
        return res > 32
    except Exception as e:  # noqa: BLE001
        logger.error("UAC elevation failed: %s", e)
        return False


def _elevate_script(script_path: str) -> bool:
    """Run a bash script elevated (Linux). Returns True on exit 0."""
    if is_elevated():
        return _run_sync(["bash", script_path]) == 0
    if shutil.which("pkexec"):
        return _run_sync(["pkexec", "bash", script_path]) == 0
    if shutil.which("sudo"):
        return _run_sync(["sudo", "bash", script_path]) == 0
    return False


# ── Linux install scripts ────────────────────────────────────────

_APT_SCRIPT = r"""#!/usr/bin/env bash
set -e
export DEBIAN_FRONTEND=noninteractive
CODENAME="$(lsb_release -cs 2>/dev/null || . /etc/os-release && echo "$VERSION_CODENAME")"
[ -n "$CODENAME" ] || CODENAME="stable"
install -d /usr/share/keyrings
if command -v curl >/dev/null 2>&1; then
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
else
  wget -qO- https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
fi
echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $CODENAME main" > /etc/apt/sources.list.d/cloudflare-client.list
apt-get update
apt-get install -y cloudflare-warp
"""

_DNF_SCRIPT = r"""#!/usr/bin/env bash
set -e
rpm --import https://pkg.cloudflareclient.com/pubkey.gpg
cat > /etc/yum.repos.d/cloudflare-warp.repo <<'EOF'
[cloudflare-warp]
name=Cloudflare WARP
baseurl=https://pkg.cloudflareclient.com/rpm/
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://pkg.cloudflareclient.com/pubkey.gpg
EOF
dnf install -y cloudflare-warp
"""

_ZYPPER_SCRIPT = r"""#!/usr/bin/env bash
set -e
rpm --import https://pkg.cloudflareclient.com/pubkey.gpg
zypper --non-interactive addrepo https://pkg.cloudflareclient.com/rpm/ cloudflare-warp
zypper --non-interactive install cloudflare-warp
"""

_PACMAN_SCRIPT = r"""#!/usr/bin/env bash
set -e
if command -v yay >/dev/null 2>&1; then
  yay -S --noconfirm cloudflare-warp-bin
elif command -v paru >/dev/null 2>&1; then
  paru -S --noconfirm cloudflare-warp-bin
else
  echo "Arch: install cloudflare-warp-bin from the AUR (yay/paru) first." >&2
  exit 1
fi
"""


def _detect_package_manager() -> Optional[str]:
    for pm in ("apt-get", "dnf", "zypper", "pacman"):
        if shutil.which(pm):
            return pm
    return None


def _linux_install_script() -> Optional[str]:
    pm = _detect_package_manager()
    if pm == "apt-get":
        return _APT_SCRIPT
    if pm == "dnf":
        return _DNF_SCRIPT
    if pm == "zypper":
        return _ZYPPER_SCRIPT
    if pm == "pacman":
        return _PACMAN_SCRIPT
    return None


# ── install ──────────────────────────────────────────────────────

async def _download(url: str, dest: str, progress: Optional[Progress]) -> bool:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=15.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes(262144):
                        fh.write(chunk)
        return True
    except Exception as e:  # noqa: BLE001
        if progress:
            await progress("error", 0, f"Download failed: {e}\n")
        return False


async def _install_windows(progress: Optional[Progress], msi_path: Optional[str]) -> bool:
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    msi = msi_path
    tmp = None
    if not msi:
        arch = _machine_arch()
        url = _WARP_INSTALL_URLS.get(
            "windows_arm64" if arch == "arm64" else "windows"
        )
        tmp = tempfile.TemporaryDirectory(prefix="warp_install_")
        msi = os.path.join(tmp.name, "Cloudflare_WARP_Release.msi")
        await _p("downloading", 10, f"Downloading Cloudflare WARP MSI ({arch})…\n")
        if not await _download(url, msi, progress):
            return False

    await _p("installing", 60, "Installing Cloudflare WARP (silent)…\n")
    if is_elevated():
        proc = await asyncio.create_subprocess_exec(
            "msiexec", "/i", msi, "/qn", "/norestart",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=600.0)
        except asyncio.TimeoutError:
            proc.kill()
    else:
        # Single UAC consent; msiexec then runs elevated in the background.
        if not _windows_elevate_msiexec(msi):
            await _p("error", 0, "Elevation was declined — WARP not installed.\n")
            return False

    # Wait for the CLI to appear (the MSI installs the service + binaries).
    for _ in range(150):
        if _warp_cli_path():
            break
        await asyncio.sleep(2.0)
    if tmp:
        tmp.cleanup()
    if not _warp_cli_path():
        await _p("error", 0, "WARP install did not complete (warp-cli not found).\n")
        return False
    await _p("installed", 100, "Cloudflare WARP installed.\n")
    return True


async def _install_linux(progress: Optional[Progress]) -> bool:
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    await _p("installing", 10, "Detecting Linux distribution…\n")
    script = _linux_install_script()
    if not script:
        await _p("error", 0, "Unsupported Linux distribution (no apt/dnf/zypper/pacman).\n")
        return False

    with tempfile.TemporaryDirectory(prefix="warp_install_") as tmp:
        path = os.path.join(tmp, "warp_install.sh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        os.chmod(path, 0o755)
        await _p("installing", 30, "Installing cloudflare-warp (elevated)…\n")
        if not _elevate_script(path):
            await _p("error", 0, "WARP install failed (elevation declined or error).\n")
            return False

    await _ensure_linux_service(progress)
    if not _warp_cli_path():
        await _p("error", 0, "warp-cli not found after install.\n")
        return False
    await _p("installed", 100, "Cloudflare WARP installed.\n")
    return True


async def _install_macos(progress: Optional[Progress]) -> bool:
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    await _p("downloading", 10, "Downloading Cloudflare WARP.pkg…\n")
    with tempfile.TemporaryDirectory(prefix="warp_install_") as tmp:
        dest = os.path.join(tmp, "Cloudflare_WARP.pkg")
        if not await _download(_WARP_INSTALL_URLS["darwin"], dest, progress):
            return False
        await _p("installing", 60, "Installing Cloudflare WARP (admin)…\n")
        cmd = f'/usr/sbin/installer -pkg "{dest}" -target /'
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", f'do shell script "{cmd}" with administrator privileges',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=600.0)
        except asyncio.TimeoutError:
            proc.kill()
            await _p("error", 0, "WARP install timed out.\n")
            return False
        if proc.returncode != 0:
            await _p("error", 0, "WARP install failed (admin auth declined?).\n")
            return False
    if not _warp_cli_path():
        await _p("error", 0, "warp-cli not found after install.\n")
        return False
    await _p("installed", 100, "Cloudflare WARP installed.\n")
    return True


async def ensure_warp_installed(
    progress: Optional[Progress] = None, msi_path: Optional[str] = None
) -> bool:
    """Install Cloudflare WARP silently for the host OS. Idempotent."""
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    if _warp_cli_path():
        await _p("installed", 100, "Cloudflare WARP already installed.\n")
        return True
    plat = _os_platform()
    if plat == "windows":
        return await _install_windows(progress, msi_path)
    if plat == "linux":
        return await _install_linux(progress)
    if plat == "darwin":
        return await _install_macos(progress)
    await _p("error", 0, f"No WARP installer known for platform: {plat}\n")
    return False


# ── connect ──────────────────────────────────────────────────────

async def _ensure_linux_service(progress: Optional[Progress]) -> None:
    """Enable + start the warp-svc daemon (Linux only)."""
    if not sys.platform.startswith("linux"):
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "warp-svc",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if (out or b"").decode("utf-8", errors="replace").strip() == "active":
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "enable", "--now", "warp-svc",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        if progress:
            await progress("service", 0, (out or b"").decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        logger.warning("could not start warp-svc: %s", e)


async def connect_warp(progress: Optional[Progress] = None) -> bool:
    """Register the device (if needed) and connect the WARP tunnel."""
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    cli = _warp_cli_path()
    if not cli:
        await _p("error", 0, "warp-cli not found.\n")
        return False
    if sys.platform.startswith("linux"):
        await _ensure_linux_service(progress)

    rotator = get_rotator()
    await _p("connecting", 40, "Registering WARP device…\n")
    try:
        out = (await rotator._run(cli, "status") or "").lower()
        if any(k in out for k in ("registration required", "not registered", "unregistered")):
            await rotator._run(cli, "registration", "new")
    except Exception as e:  # noqa: BLE001
        logger.debug("warp status/register: %s", e)

    await _p("connecting", 70, "Connecting WARP tunnel…\n")
    ok = await rotator.connect()
    if ok:
        ip = await rotator.get_ip()
        await _p("connected", 100, f"WARP connected{(' — ' + ip) if ip else ''}.\n")
    else:
        await _p("error", 0, "WARP connect failed.\n")
    return ok


# ── orchestration ────────────────────────────────────────────────

async def bootstrap(
    progress: Optional[Progress] = None, connect: bool = True
) -> Dict[str, object]:
    """Ensure WARP is installed (and optionally connected). Returns a status dict.

    ``progress`` is awaited with ``(stage, pct, line)`` tuples so callers can
    stream install progress to a UI (the API's ``_install_jobs`` shape).
    """
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    if _warp_cli_path():
        await _p("installed", 100, "Cloudflare WARP already installed.\n")
    else:
        ok = await ensure_warp_installed(progress=progress)
        if not ok:
            return {
                "success": False, "installed": False, "connected": False,
                "error": "WARP install failed — see logs.",
            }

    if not connect:
        return {"success": True, "installed": True, "connected": False}

    ok = await connect_warp(progress=progress)
    st = await get_rotator().status()
    return {
        "success": ok,
        "installed": True,
        "connected": st.get("connected", False),
        "healthy": st.get("healthy", False),
        "ip": st.get("ip", ""),
        "error": "" if ok else "WARP installed but could not connect — check warp-svc and try again.",
    }