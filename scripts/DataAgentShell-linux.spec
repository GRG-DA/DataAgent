# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the DataAgent desktop shell, Linux build (onedir).
#
# Linux differences vs scripts/DataAgentShell.spec (Windows):
#   * pythonnet/clr is Windows-only (guarded in requirements-desktop.txt by
#     sys_platform == "win32"), so it is NOT collected here.
#   * pywebview is collected the same way; on Linux it uses the GTK or Qt
#     WebKit backend installed from the distro repo.
#   * The DataAgentShell binary name is kept, so launch scripts and desktop
#     entries are identical across platforms.
#
# Build on a Linux host:
#   python3 -m PyInstaller --noconfirm scripts/DataAgentShell-linux.spec
#
# Produces dist/DataAgentShell/DataAgentShell — the native GTK/Qt window that
# embeds the FastAPI-served SPA. Bundled readable .py sources are stripped
# (the same policy as the Windows shell build); compiled bytecode lives in the
# PYZ archive.

import os

from PyInstaller.building.utils import format_binaries_and_datas
from PyInstaller.utils.hooks import collect_all

root = os.path.abspath(os.path.join(SPECPATH, ".."))


def _norm_datas(entries, drop_py=True):
    """Normalise collect_* entries to (dest, src, "DATA") triples and drop
    plaintext Python sources so readable code is not shipped next to the app.

    PyInstaller compiles every module into the PYZ archive, so imports at
    runtime never read .py from _internal; the only effect of dropping them is
    stopping source disclosure. Genuine data payloads (yaml, json, html, css,
    js, sql, webview libs, duckwasm) are kept.
    """
    out = []
    for dest, src in format_binaries_and_datas(entries):
        if drop_py and _is_python_source(src):
            continue
        out.append((dest, src, "DATA"))
    return out


def _is_python_source(src) -> bool:
    try:
        name = str(src).lower()
    except Exception:  # noqa: BLE001
        return True
    if name.startswith("<") and name.endswith(">"):
        return True
    return name.endswith((".py", ".pyc", ".pyi", ".pyw")) or "/__pycache__/" in name


def _norm_binaries(entries):
    return [(dest, src, "BINARY") for dest, src in format_binaries_and_datas(entries)]


a = Analysis(
    [os.path.join(root, "run.py")],
    pathex=[root],
    binaries=[],
    datas=[
        (os.path.join(root, "web"), "web"),
        (os.path.join(root, "shell", "splash.html"), "shell"),
    ],
    hiddenimports=["api.main"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

# Mirror the CLI build's --collect-all for the app packages (source stripped).
for _pkg in ("api", "agent", "warehouse", "config", "etl", "export"):
    try:
        _datas, _binaries, _hidden = collect_all(_pkg)
        a.datas += _norm_datas(_datas)
        a.binaries += _norm_binaries(_binaries)
        a.hiddenimports += _hidden
    except Exception:  # noqa: BLE001
        pass

# pywebview renderers only. pythonnet/clr is intentionally NOT collected on
# Linux (Windows-only backend).
for _pkg in ("webview",):
    try:
        _datas, _binaries, _hidden = collect_all(_pkg)
        a.datas += _norm_datas(_datas)
        a.binaries += _norm_binaries(_binaries)
        a.hiddenimports += _hidden
    except Exception:  # noqa: BLE001
        pass

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DataAgentShell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DataAgentShell",
)