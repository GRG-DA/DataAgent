# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the DataAgent desktop shell (onedir, windowed).
# Wine-build variant: normalizes collect_all TOC entries with
# format_binaries_and_datas (the official hook-datas converter).

import os

from PyInstaller.building.utils import format_binaries_and_datas
from PyInstaller.utils.hooks import collect_all

root = os.path.abspath(os.path.join(SPECPATH, ".."))

def _norm_datas(entries):
    return [(dest, src, "DATA") for dest, src in format_binaries_and_datas(entries)]

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

for _pkg in ("api", "agent", "warehouse", "config", "etl", "export"):
    try:
        _datas, _binaries, _hidden = collect_all(_pkg)
        a.datas += _norm_datas(_datas)
        a.binaries += _norm_binaries(_binaries)
        a.hiddenimports += _hidden
    except Exception:  # noqa: BLE001
        pass

for _pkg in ("webview", "clr"):
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
