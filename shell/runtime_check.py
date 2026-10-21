"""Preflight checks for the frozen desktop exe.

Runs before pywebview starts so a missing WebView2 runtime or an
unwritable data dir fails with a clear dialog instead of a silent exit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def runtime_dir() -> Path:
    """Writable per-user dir used by frozen builds (mirrors run.py)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "DataAgent"


def check_writable() -> tuple[bool, str]:
    """Ensure the runtime dir is creatable/writable."""
    try:
        target = runtime_dir()
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, str(target)
    except OSError as exc:
        return False, f"Data dir not writable: {exc}"


def check_webview2() -> tuple[bool, str]:
    """Check Edge WebView2 runtime on Windows (pywebview backend)."""
    if sys.platform != "win32":
        return True, "non-windows: system webkit used"
    try:
        import winreg

        for hive, sub in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    ver, _ = winreg.QueryValueEx(key, "pv")
                    if ver:
                        return True, f"WebView2 {ver}"
            except OSError:
                continue
    except ImportError:
        pass
    # Fallback: evergreen loader DLL present means runtime is installed.
    for candidate in (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft/EdgeWebView/Application",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft/EdgeWebView/Application",
    ):
        try:
            if candidate.exists() and any(candidate.iterdir()):
                return True, f"WebView2 found at {candidate}"
        except OSError:
            continue
    return False, (
        "Microsoft Edge WebView2 runtime not found. "
        "Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 "
        "then relaunch DataAgent."
    )


def preflight() -> tuple[bool, list[str]]:
    """Run all checks. Returns (ok, messages)."""
    messages: list[str] = []
    ok_all = True
    ok, msg = check_writable()
    messages.append(msg)
    ok_all = ok_all and ok
    ok, msg = check_webview2()
    messages.append(msg)
    ok_all = ok_all and ok
    return ok_all, messages
