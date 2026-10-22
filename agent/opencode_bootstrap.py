"""
OpenCode Server Bootstrap
=========================
Silent, cross-platform resolution + start of the opencode CLI server so the
Data Agent AI's ``server_url`` session-API path works out of the box — even in
packaged .exe builds where opencode is bundled inside the PyInstaller archive.

Binary resolution order:
  1. Bundled copy extracted from the PyInstaller bundle (``sys._MEIPASS``).
  2. An existing opencode install on the host (``~/.opencode/bin/opencode``,
     ``%USERPROFILE%\\.opencode\\bin\\opencode.exe``, or on PATH).
  3. A copy placed next to the running app (installer-installed, e.g. the
     ``{app}`` directory of the setup package).
  4. A copy previously extracted to the app's user-writable runtime bin dir.
  5. Download from the official opencode release (GitHub) into that dir.

Server start:
  ``ensure_opencode_server`` health-probes ``GET /config`` on the configured
  ``server_url`` and, when nothing answers, spawns ``opencode serve`` on the
  loopback host/port (detached, hidden on Windows) with a pidfile guard so only
  one instance runs.

The ``progress`` callback has the signature ``(stage, pct, line)`` and is
awaited, matching the ``_install_jobs`` polling shape used by the API.
"""

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Progress callback: (stage, pct, log_line). ``line`` may be empty.
Progress = Callable[[str, int, str], Awaitable[None]]

# The opencode release this app installs — PINNED (ADR-012).
#
# It used to be "releases/latest", which made the install unreproducible and
# unverified: a different binary arrived on every run, with no way to check
# it. This constant is the SINGLE source of truth: the download URL, the
# name of the installed file and the version reported to the user all
# derive from it. Keep it in sync with $OpenCodeVersion in
# scripts/build_windows.ps1, with OPENCODE_VERSION in
# scripts/build_linux.sh and with packaging/linux/opencode-install.sh.
OPENCODE_VERSION = "v1.19.3"

# Official release base — always the PINNED release, never "latest".
_RELEASE_URL_BASE = "https://github.com/anomalyco/opencode/releases/download"

# GitHub API for the latest release. Used ONLY to report "a newer version
# exists" — it never decides WHAT is downloaded.
_OPENCODE_LATEST_API = "https://api.github.com/repos/anomalyco/opencode/releases/latest"

# How long a fetched "latest version" answer is trusted (GitHub allows
# 60 unauthenticated API calls/hour/IP — cache aggressively, fail silent).
_LATEST_CACHE_TTL_S = 24 * 3600

# In-memory version cache: binary path -> (mtime, version-or-None).
_version_cache: Dict[str, tuple] = {}

# Where the opencode installer puts the CLI (kept in sync with the install script).
_OPENCODE_INSTALL_DIRS = {
    "windows": [r"%USERPROFILE%\.opencode\bin\opencode.exe"],
    "linux": ["~/.opencode/bin/opencode"],
    "darwin": ["~/.opencode/bin/opencode"],
}


# ── platform helpers ─────────────────────────────────────────────

def _pinned_version() -> str:
    """The pinned release as a bare X.Y.Z string ('v1.19.3' -> '1.19.3')."""
    return OPENCODE_VERSION.lstrip("v")


def _release_url(target: str, ext: str) -> str:
    """The pinned release URL for a target (never the moving 'latest')."""
    return f"{_RELEASE_URL_BASE}/{OPENCODE_VERSION}/opencode-{target}{ext}"


def _runtime_filename(version: str) -> str:
    """The on-disk name of a versioned runtime copy.

    The name carries the version that was DOWNLOADED and verified, so a
    runtime copy's name always matches the bytes it holds (ADR-012). It is
    never named after the upstream "latest" release.
    """
    return f"opencode-{version}.exe" if os.name == "nt" else f"opencode-{version}"


def _os_platform() -> str:
    """Normalized platform name: windows / linux / darwin."""
    if os.name == "nt":
        return "windows"
    return sys.platform


def _machine_arch() -> str:
    """Normalized machine arch: x64 / arm64."""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine


def _runtime_bin_dir() -> Path:
    """User-writable directory for extracted/downloaded binaries."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return Path(base) / "DataAgentKeyManager" / "bin"
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "DataAgentKeyManager" / "bin"


def _bundled_opencode_path() -> Optional[Path]:
    """Path to the opencode binary bundled inside the PyInstaller archive."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    exe = "opencode.exe" if os.name == "nt" else "opencode"
    for candidate in (Path(meipass) / "opencode" / exe, Path(meipass) / exe):
        if candidate.is_file():
            return candidate
    return None


def _existing_opencode_path() -> Optional[Path]:
    """Path to an opencode CLI already installed on the host."""
    for pattern in _OPENCODE_INSTALL_DIRS.get(_os_platform(), []):
        path = Path(os.path.expandvars(os.path.expanduser(pattern)))
        if path.is_file():
            return path
    # Probe for the native binary name: on Windows this skips any
    # opencode.cmd / opencode.bat shims so the server is never launched
    # through cmd.exe (which is what surfaces a console window).
    found = shutil.which("opencode.exe" if os.name == "nt" else "opencode")
    if found:
        return Path(found)
    return None


def _runtime_candidates() -> list:
    """Versioned runtime copies: [(path, version-or-None)], newest first.

    Self-updates land as ``opencode-<version>.exe`` so installing never
    touches a running binary (Windows locks those). The legacy plain
    ``opencode.exe`` (first-install path) is included via --version probe.
    """
    exe = "opencode.exe" if os.name == "nt" else "opencode"
    stem = "opencode"
    try:
        bin_dir = _runtime_bin_dir()
    except Exception:  # noqa: BLE001
        return []
    found: list = []
    try:
        entries = list(bin_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if not entry.is_file():
            continue
        if name == exe:
            found.append((entry, get_binary_version(entry)))
        elif name.startswith(stem + "-"):
            # Parse the filename ONLY — parent dirs may contain versions
            # (e.g. Python3.13) that a full-path regex would match first.
            found.append((entry, _parse_version(Path(name).stem)))
    def _key(item: tuple) -> tuple:
        v = item[1]
        if not v:
            return (-1,)
        try:
            return (1, tuple(int(p) for p in v.split(".")))
        except ValueError:
            return (0,)
    found.sort(key=_key, reverse=True)
    return found


def _best_runtime_copy() -> Optional[Path]:
    """Newest readable runtime copy, or None."""
    for path, version in _runtime_candidates():
        if version:
            return path
    # Fall back to an unreadable-but-present copy (old priority behavior).
    for path, _version in _runtime_candidates():
        return path
    return None


def opencode_bin_path() -> Optional[Path]:
    """Resolve the opencode CLI binary.

    Priority is bundled → app dir → existing → runtime, EXCEPT that a
    runtime copy (the background self-update target) wins when it is
    strictly newer than the otherwise-winning candidate — so a shipped
    .exe keeps working on day one and transparently upgrades afterwards.
    Version probes are cached; unreadable versions keep the old priority.
    """
    bundled = _bundled_opencode_path()
    if bundled:
        chosen: Optional[Path] = bundled
    else:
        exe_dir = Path(sys.executable).resolve().parent
        exe_name = "opencode.exe" if os.name == "nt" else "opencode"
        app_dir = exe_dir / exe_name
        if app_dir.is_file():
            chosen = app_dir
        else:
            chosen = _existing_opencode_path()
            if chosen is None:
                runtime = _runtime_bin_dir() / exe_name
                chosen = runtime if runtime.is_file() else None
    if chosen is None:
        chosen = _best_runtime_copy()
    if chosen is None:
        return None
    try:
        runtime = _best_runtime_copy()
        if runtime is not None and runtime.resolve() != chosen.resolve():
            runtime_v = get_binary_version(runtime)
            chosen_v = get_binary_version(chosen)
            if runtime_v and (not chosen_v or _compare_versions(runtime_v, chosen_v) > 0):
                logger.info("opencode: preferring newer runtime copy %s over %s",
                            runtime_v, chosen_v)
                return runtime
    except Exception:  # noqa: BLE001
        pass
    return chosen


# ── install ──────────────────────────────────────────────────────

def _safe_extract_zip(archive: Path, dest: Path) -> None:
    """Extract a zip guarding against absolute paths and ../ escapes."""
    import zipfile

    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if target != dest.resolve() and dest.resolve() not in target.parents:
                raise ValueError(f"Unsafe archive member: {member!r}")
        zf.extractall(dest)


def _extract_opencode(archive: Path, dest: Path, ext: str) -> tuple:
    """Extract a release archive; return (binary path, error message).

    The binary path is None when the archive could not be extracted or
    does not hold an opencode binary; the message is then safe to show
    to the user.
    """
    try:
        if ext == ".tar.gz":
            import tarfile

            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(dest, filter="data")
        else:
            _safe_extract_zip(archive, dest)
    except Exception as e:  # noqa: BLE001
        return None, f"Extract failed: {e}"
    exe = "opencode.exe" if _os_platform() == "windows" else "opencode"
    src = dest / exe
    if not src.is_file():
        return None, "opencode binary not found in archive."
    return src, ""


def _install_runtime_copy(src: Path, dest: Path) -> bool:
    """Copy an extracted binary into the runtime bin dir. Never raises."""
    try:
        shutil.copy2(src, dest)
        os.chmod(dest, 0o755)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _download(url: str, dest: Path, progress: Optional[Progress]) -> bool:
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
            await progress("error", 0, f"OpenCode download failed: {e}\n")
        return False


def _download_target() -> str:
    """opencode release target name, e.g. linux-x64 / windows-x64."""
    os_name = _os_platform()
    arch = _machine_arch()
    target = f"{os_name}-{arch}"
    if os_name == "linux" and arch == "x64":
        try:
            if not Path("/proc/cpuinfo").read_text().lower().count("avx2"):
                target += "-baseline"
        except Exception:  # noqa: BLE001
            pass
    return target


async def ensure_opencode_installed(progress: Optional[Progress] = None) -> bool:
    """Make sure an opencode CLI binary is available for the server.

    Returns True when a usable binary exists (bundled, already installed, or
    freshly downloaded). Never elevates — everything lands in the user's own
    runtime bin dir.
    """
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    if opencode_bin_path():
        return True

    bundled = _bundled_opencode_path()
    if bundled:
        await _p("installing", 40, "Extracting bundled opencode…\n")
        dest_dir = _runtime_bin_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / bundled.name
        try:
            shutil.copy2(bundled, dest)
            os.chmod(dest, 0o755)
        except Exception as e:  # noqa: BLE001
            await _p("error", 0, f"Extract failed: {e}\n")
            return False
        await _p("installed", 100, "opencode ready.\n")
        return True

    await _p("downloading", 10, "Downloading opencode CLI…\n")
    dest_dir = _runtime_bin_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    os_name = _os_platform()
    ext = ".tar.gz" if os_name == "linux" else ".zip"
    url = _release_url(_download_target(), ext)
    with tempfile.TemporaryDirectory(prefix="opencode_install_") as tmp:
        archive = Path(tmp) / f"opencode{ext}"
        if not await _download(url, archive, progress):
            return False
        await _p("installing", 60, "Extracting opencode…\n")
        src, error = _extract_opencode(archive, Path(tmp), ext)
        if src is None:
            await _p("error", 0, f"{error}\n")
            return False
        dest = dest_dir / ("opencode.exe" if os_name == "windows" else "opencode")
        if not _install_runtime_copy(src, dest):
            await _p("error", 0, "Copy failed.\n")
            return False
    await _p("installed", 100, "opencode installed.\n")
    return True


# ── version check + background self-update ─────────────────────

def _parse_version(text: str) -> Optional[str]:
    """First X.Y.Z in free-form `--version` / release-tag output."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else None


def _compare_versions(a: str, b: str) -> int:
    """Compare dotted versions: >0 when a is newer, <0 when older, 0 when equal."""
    def _parts(v: str) -> list:
        core = re.split(r"[-+]", v.lstrip("v"), 1)[0]
        out = []
        for p in core.split("."):
            try:
                out.append(int(p))
            except ValueError:
                out.append(0)
        return out

    pa, pb = _parts(a), _parts(b)
    width = max(len(pa), len(pb))
    pa += [0] * (width - len(pa))
    pb += [0] * (width - len(pb))
    return (pa > pb) - (pa < pb)


def get_binary_version(path: Path) -> Optional[str]:
    """Version of an opencode binary (`--version`), cached by path+mtime."""

    try:
        key = str(path)
        mtime = path.stat().st_mtime
        cached = _version_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        kwargs: Dict[str, object] = {"timeout": 15}
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            kwargs["startupinfo"] = si
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run([str(path), "--version"], capture_output=True,
                              text=True, **kwargs)  # noqa: S603 — local binary, fixed flag
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        version = _parse_version(out)
        _version_cache[key] = (mtime, version)
        return version
    except Exception:  # noqa: BLE001
        return None


def _latest_cache_path() -> Path:
    return _runtime_bin_dir() / ".opencode-latest.json"


async def get_latest_opencode_version() -> Optional[str]:
    """Latest release tag from GitHub (24h file cache, silent on failure)."""
    import json as _json
    import time as _time

    cache = _latest_cache_path()
    try:
        cached = _json.loads(cache.read_text())
        if _time.time() - float(cached.get("checked_at", 0)) < _LATEST_CACHE_TTL_S:
            return cached.get("version")
    except Exception:  # noqa: BLE001
        pass
    version: Optional[str] = None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "dataagent-opencode-bootstrap"},
        ) as client:
            resp = await client.get(_OPENCODE_LATEST_API)
            resp.raise_for_status()
            version = _parse_version(str(resp.json().get("tag_name", "")))
    except Exception as e:  # noqa: BLE001
        logger.debug("opencode latest-version check failed: %s", e)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(_json.dumps({"version": version, "checked_at": _time.time()}))
    except Exception:  # noqa: BLE001
        pass
    return version


def check_opencode_update() -> Dict[str, object]:
    """Synchronous status snapshot.

    ``{current, latest, pinned, update_available, source}``:

    * ``current`` — the version of the CLI that is actually on disk. It is
      read from the binary itself, never guessed from a file name, so it
      always matches the bytes that run.
    * ``latest`` — the newest upstream release (informational only).
    * ``pinned`` — the release this build ships (ADR-012).
    * ``update_available`` — an upstream release newer than the installed
      CLI exists. It is INFORMATIONAL: the app installs the pinned release
      only, so adopting a newer one is a deliberate pin bump (a reviewable
      commit), never a silent install.
    """
    current: Optional[str] = None
    source = ""
    binary = opencode_bin_path()
    if binary:
        current = get_binary_version(binary)
        try:
            meipass = getattr(sys, "_MEIPASS", None)
            s = str(binary)
            if meipass and s.startswith(str(meipass)):
                source = "bundled"
            elif s.startswith(str(_runtime_bin_dir())):
                source = "runtime"
            else:
                source = "host"
        except Exception:  # noqa: BLE001
            source = "host"
    latest: Optional[str] = None
    try:
        import json as _json
        import time as _time

        cached = _json.loads(_latest_cache_path().read_text())
        if _time.time() - float(cached.get("checked_at", 0)) < _LATEST_CACHE_TTL_S:
            latest = cached.get("version")
    except Exception:  # noqa: BLE001
        pass
    update_available = bool(
        current and latest and _compare_versions(latest, current) > 0
    )
    return {"current": current, "latest": latest, "pinned": _pinned_version(),
            "update_available": update_available, "source": source}


async def _report_newer_upstream_release(pinned: str) -> None:
    """Log that a newer upstream release exists — information, nothing more.

    The app installs the pinned release only (ADR-012). Adopting a newer
    upstream release is a deliberate pin bump: a reviewed commit that
    moves OPENCODE_VERSION and the committed hashes together.
    """
    latest = await get_latest_opencode_version()
    if latest and _compare_versions(latest, pinned) > 0:
        logger.info(
            "opencode: upstream release %s is newer than the pinned %s - "
            "adopting it is a deliberate pin bump (ADR-012), not an "
            "automatic install", latest, pinned)


async def ensure_opencode_updated(progress: Optional[Progress] = None) -> bool:
    """Install the PINNED opencode CLI into the runtime bin dir.

    ADR-012: the download is pinned to ``OPENCODE_VERSION`` and the
    installed file is named after that same version, so the name on disk
    always matches the bytes on disk. The upstream "latest" lookup stays
    but is INFORMATIONAL only — it never decides what is downloaded or
    how the file is named.

    Never elevates and never touches Program Files / the app dir — the
    runtime copy wins resolution whenever it is newer (see opencode_bin_path).
    Returns True when the pinned release is already in place or was
    installed; False (with a progress line, never an exception) when the
    download fails — the existing binary keeps working.
    """
    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    pinned = _pinned_version()
    dest_dir = _runtime_bin_dir()

    # Informational only: a newer upstream release exists. Refreshed first
    # so the "latest" cache stays current even when there is nothing to
    # install (the snapshot the Settings UI reads comes from that cache).
    await _report_newer_upstream_release(pinned)

    # The pinned release is already installed as a versioned runtime copy.
    if (dest_dir / _runtime_filename(pinned)).is_file():
        return True

    # The CLI we would use is already at (or above) the pinned release:
    # never install a downgrade over it.
    binary = opencode_bin_path()
    current = get_binary_version(binary) if binary else None
    if current and _compare_versions(current, pinned) >= 0:
        return True

    await _p("downloading", 10, f"Installing opencode {pinned}…\n")
    dest_dir.mkdir(parents=True, exist_ok=True)
    os_name = _os_platform()
    ext = ".tar.gz" if os_name == "linux" else ".zip"
    url = _release_url(_download_target(), ext)

    with tempfile.TemporaryDirectory(prefix="opencode_update_") as tmp:
        archive = Path(tmp) / f"opencode{ext}"
        if not await _download(url, archive, progress):
            return False
        await _p("installing", 60, "Installing opencode…\n")
        src, error = _extract_opencode(archive, Path(tmp), ext)
        if src is None:
            await _p("error", 0, f"{error}\n")
            return False
        # Versioned filename: installing must never overwrite a running
        # binary (Windows locks those — WinError 32), so each release gets
        # its own file and resolution picks the newest (see opencode_bin_path).
        # The name carries the version that was DOWNLOADED — never the
        # upstream "latest" (ADR-012).
        dest = dest_dir / _runtime_filename(pinned)
        if not _install_runtime_copy(src, dest):
            await _p("error", 0, "Update install failed.\n")
            return False
        _prune_old_runtime_copies(keep=2)
    _version_cache.pop(str(dest), None)
    await _p("installed", 100, f"opencode {pinned} installed.\n")
    return True


def _prune_old_runtime_copies(keep: int = 2) -> None:
    """Delete older versioned runtime copies, keeping the newest ``keep``.

    The copy that carries the PINNED release is never deleted: its name is
    the only one guaranteed to match the bytes it holds, and the ranking
    below trusts names (ADR-012).

    Best-effort: a running binary cannot be deleted on Windows, so failures
    are ignored — the next update prunes again.
    """
    try:
        bin_dir = _runtime_bin_dir()
        pinned_name = _runtime_filename(_pinned_version())
        ranked = [(p, v) for p, v in _runtime_candidates()
                  if v and p.name != ("opencode.exe" if os.name == "nt" else "opencode")]
        for path, _version in ranked[keep:]:
            try:
                if path.parent != bin_dir or path.name == pinned_name:
                    continue
                path.unlink()
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def schedule_background_opencode_update() -> None:
    """Fire-and-forget latest-version refresh (once per process).

    Called whenever the server path is used so a shipped .exe refreshes its
    CLI in the background without ever blocking an AI request.
    """
    if getattr(schedule_background_opencode_update, "_scheduled", False):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    schedule_background_opencode_update._scheduled = True  # type: ignore[attr-defined]

    async def _run() -> None:
        try:
            await ensure_opencode_updated()
        except Exception as e:  # noqa: BLE001
            logger.debug("background opencode update failed: %s", e)

    loop.create_task(_run())


# ── server start ─────────────────────────────────────────────────

def _parse_server_url(server_url: str) -> tuple:
    """Return (hostname, port) from a server_url like http://127.0.0.1:3000."""
    parsed = urllib.parse.urlsplit(server_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 3000
    return host, port


async def server_status(server_url: str) -> Dict[str, object]:
    """Probe the opencode server.

    Returns {installed, running, port, pid, binary, version, port_busy}:
    ``port_busy`` is True when something holds the port without answering
    the health probe (cold-starting server or foreign process).
    """
    host, port = _parse_server_url(server_url)
    binary = opencode_bin_path()
    installed = binary is not None
    running = await _probe_config(base=server_url.rstrip("/"), timeout_s=3.0)
    pid: Optional[int] = None
    if running:
        pid = _read_pidfile(port)
        if pid and not _pid_alive(pid):
            pid = None
    version = None
    if binary:
        # Never block the event loop on the 180MB binary's --version probe.
        version = await asyncio.to_thread(get_binary_version, binary)
    port_busy = (not running) and await asyncio.to_thread(_port_in_use, host, port)
    holder: Dict[str, object] = {}
    if port_busy:
        holder = await asyncio.to_thread(_port_holder_info, port)
    return {"installed": installed, "running": running, "port": port,
            "pid": pid, "binary": str(binary) if binary else "",
            "version": version, "port_busy": port_busy, "holder": holder,
            "last_error": _last_probe_error}


def _pidfile_path(port: int) -> Path:
    return _runtime_bin_dir() / f"opencode-serve-{port}.pid"


def _read_pidfile(port: int) -> Optional[int]:
    try:
        return int(_pidfile_path(port).read_text().strip())
    except Exception:  # noqa: BLE001
        return None


def _clear_pidfile(port: int) -> None:
    """Best-effort removal of a stale pidfile (dead process, never raises)."""
    try:
        _pidfile_path(port).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == 259  # STILL_ACTIVE
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# Serializes server-spawn DECISIONS inside one backend process: N concurrent
# first-AI requests must not each spawn their own ``opencode serve`` on the
# same port (the losers crash with ServeError and fill the server log). The
# lock is held only for the fast probe + bind check + Popen — the slow
# ready-wait happens outside, with latecomers waiting on _spawn_in_progress.
_spawn_lock = asyncio.Lock()
_spawn_in_progress = False

# How long to wait for a foreign port-holder (already-listening process that
# is not answering yet — typically our own earlier server still cold-starting
# the 180MB binary) before giving up instead of spawning a duplicate.
_HOLDER_WAIT_S = 30.0
# How long to wait for a freshly spawned server to answer. Cold starts
# (Defender scan + instance bootstrap) routinely exceed the old 20s.
_READY_WAIT_S = 60.0
_POLL_INTERVAL_S = 0.5


# Last /config probe failure (set by _probe_config, read by server_status).
_last_probe_error = ""


def _port_in_use(host: str, port: int, timeout_s: float = 0.5) -> bool:
    """True when something accepts TCP on host:port. Never raises."""
    import socket as _socket

    try:
        with _socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _port_holder_info(port: int) -> Dict[str, object]:
    """Best-effort ``{pid, image}`` of the process listening on
    127.0.0.1:port (Windows ``netstat``/``tasklist``, stdlib only).

    Exists so a "server not answering" report names the actual holder
    instead of leaving a port-conflict mystery. Never raises; ``{}`` when
    unknown or on non-Windows platforms.
    """
    if os.name != "nt":
        return {}
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, timeout=10,
            stderr=subprocess.DEVNULL, creationflags=creationflags,
        )
    except Exception:  # noqa: BLE001
        return {}
    pid: Optional[int] = None
    for line in out.splitlines():
        parts = line.split()
        # TCP    127.0.0.1:3000    0.0.0.0:0    LISTENING    17456
        if len(parts) != 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        local_ip, _, local_port = parts[1].rpartition(":")
        if local_port != str(port) or local_ip not in (
            "127.0.0.1", "0.0.0.0", "[::1]", "::1", "::",
        ):
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        break
    if not pid:
        return {}
    info: Dict[str, object] = {"pid": pid, "image": ""}
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        rows = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True, timeout=10,
            stderr=subprocess.DEVNULL, creationflags=creationflags,
        ).strip()
        if rows:
            info["image"] = rows.split('","')[0].strip('"')
    except Exception:  # noqa: BLE001
        pass
    return info


async def _probe_config(base: str, timeout_s: float = 2.0) -> bool:
    """True when GET <base>/config answers HTTP 200. Never raises.

    The last failure is remembered in ``_last_probe_error`` (surfaced via
    ``server_status``) so a silent outage stays diagnosable without spamming
    the logs on every 0.5s poll.
    """
    global _last_probe_error
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 2.0))
        ) as client:
            status = (await client.get(f"{base}/config")).status_code
            if status == 200:
                _last_probe_error = ""
                return True
            _last_probe_error = f"HTTP {status}"
            return False
    except Exception as e:  # noqa: BLE001
        _last_probe_error = f"{type(e).__name__}: {e}"[:300]
        return False


async def _wait_for_config(base: str, wait_s: float) -> bool:
    """Poll GET <base>/config for up to wait_s seconds. Never raises."""
    import time as _time

    deadline = _time.monotonic() + max(0.0, wait_s)
    while True:
        if await _probe_config(base):
            return True
        if _time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_POLL_INTERVAL_S)


async def ensure_opencode_server(
    server_url: str,
    auto_install: bool = True,
    progress: Optional[Progress] = None,
) -> bool:
    """Make sure an opencode server answers on ``server_url``.

    Health-probes ``GET /config``; when nothing answers, resolves the CLI
    binary (installing it first if needed and ``auto_install``) and spawns
    ``opencode serve`` detached on the loopback host/port. Returns True when
    the server is reachable.

    Spawn safety: concurrent callers serialize the spawn DECISION on a
    process-wide lock with a double-checked probe (the slow ready-wait runs
    outside the lock). A port already held by another process is waited on
    (cold start) instead of being double-bound — spawning onto a held port
    only produces a ``ServeError`` crasher. Auto-spawn serves loopback
    addresses only; a remote ``server_url`` is assumed externally managed.
    """
    global _spawn_in_progress

    async def _p(stage: str, pct: int, line: str = "") -> None:
        if progress:
            await progress(stage, pct, line)

    host, port = _parse_server_url(server_url)
    base = server_url.rstrip("/")
    # Host comes from admin config — strip control chars before it reaches
    # progress lines / logs.
    safe_host = re.sub(r"[\r\n\t ]+", "", host)
    loopback = safe_host.lower() in ("127.0.0.1", "localhost", "::1")

    # Already running? Fast path — no version probes, no pidfile, no spawn.
    if await _probe_config(base):
        # Server is up — refresh the CLI in the background so a
        # shipped .exe never rots on an old version. Never blocks.
        schedule_background_opencode_update()
        return True

    pid = _read_pidfile(port)
    if pid and _pid_alive(pid):
        # A process claims the pidfile but isn't answering — it may still be
        # cold-starting, so give it a moment before doing anything drastic.
        if await _wait_for_config(base, 5.0):
            schedule_background_opencode_update()
            return True
    else:
        # Stale pidfile (dead process) or garbage content — drop it so later
        # probes and status responses stop pointing at a phantom server.
        _clear_pidfile(port)

    # Serialize the spawn decision; the slow waits happen outside the lock.
    spawned_here = False
    holder_wait = False
    async with _spawn_lock:
        # Double-checked probe: another coroutine may have started the
        # server while we waited for the lock.
        if await _probe_config(base):
            schedule_background_opencode_update()
            return True

        if _spawn_in_progress or await asyncio.to_thread(_port_in_use, host, port):
            # A sibling spawn is underway, or someone holds the port but
            # isn't answering yet (cold-starting server, or a foreign
            # process). Spawning here would only crash with ServeError —
            # wait for the holder instead.
            holder_wait = True
        else:
            if not loopback:
                await _p("error", 0,
                         f"opencode server at {safe_host}:{port} is not answering; "
                         f"automatic start only serves loopback addresses.\n")
                return False
            if not opencode_bin_path():
                if not auto_install:
                    await _p("error", 0, "opencode CLI not found and auto-install is off.\n")
                    return False
                if not await ensure_opencode_installed(progress):
                    return False

            binary = opencode_bin_path()
            if not binary:
                await _p("error", 0, "opencode CLI unavailable.\n")
                return False

            await _p("starting", 70, f"Starting opencode server on {safe_host}:{port}…\n")
            try:
                log_dir = Path("logs")
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / "opencode-server.log"
            except OSError as e:  # noqa: BLE001 — frozen exe with read-only cwd
                logger.warning("opencode server log dir unavailable: %s", e)
                await _p("error", 0, f"Cannot write server log directory: {e}\n")
                return False
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                # Keep the server 100% invisible: CREATE_NO_WINDOW plus an explicit
                # SW_HIDE STARTUPINFO means no console window can appear even when a
                # crashed server is re-spawned on the next AI request.
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
                startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            try:
                with open(log_path, "ab") as log_fh:
                    proc = subprocess.Popen(
                        [str(binary), "serve", "--hostname", host, "--port", str(port)],
                        stdout=log_fh,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True,
                        startupinfo=startupinfo,
                        creationflags=creationflags,
                    )
            except Exception as e:  # noqa: BLE001
                await _p("error", 0, f"Failed to start opencode server: {e}\n")
                return False

            try:
                _pidfile_path(port).parent.mkdir(parents=True, exist_ok=True)
                _pidfile_path(port).write_text(str(proc.pid))
            except OSError as e:  # noqa: BLE001 — pid tracking is best-effort
                logger.warning("opencode pidfile write failed: %s", e)
            _spawn_in_progress = True
            spawned_here = True

    if holder_wait:
        await _p("starting", 50,
                 f"Port {port} is busy — waiting for the running server…\n")
        if await _wait_for_config(base, _HOLDER_WAIT_S):
            schedule_background_opencode_update()
            return True
        holder = await asyncio.to_thread(_port_holder_info, port)
        detail = ""
        if holder:
            detail = (f" It is held by {holder.get('image', '?')} "
                      f"(pid {holder.get('pid', '?')}).")
        await _p("error", 0,
                 f"Port {port} is held by another process that is not "
                 f"answering.{detail} Stop it (or change "
                 f"opencode_server_port) and retry.\n")
        return False

    if spawned_here:
        # Wait for the server to answer (cold starts are slow: Defender scan
        # of the 180MB binary + instance bootstrap can take ~30s).
        try:
            if await _wait_for_config(base, _READY_WAIT_S):
                await _p("started", 100, f"opencode server ready on {safe_host}:{port}.\n")
                schedule_background_opencode_update()
                return True
        finally:
            async with _spawn_lock:
                _spawn_in_progress = False
        binary = opencode_bin_path()
        version = await asyncio.to_thread(get_binary_version, binary) if binary else "unknown"
        await _p("error", 0,
                 f"opencode server did not become ready on {safe_host}:{port} "
                 f"(binary {binary} v{version}). See logs/opencode-server.log.\n")
        return False
    return False