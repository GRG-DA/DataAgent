# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the DataAgent desktop shell (onedir, windowed).
#
# Build:  python -m PyInstaller --noconfirm scripts/DataAgentShell.spec
#
# Produces dist/DataAgentShell/DataAgentShell.exe — a native window that
# embeds the FastAPI-served SPA. The legacy onefile `DataAgent.exe web`
# build (scripts/build_windows.ps1) is unchanged.

import os

from PyInstaller.building.utils import format_binaries_and_datas
from PyInstaller.utils.hooks import collect_all

root = os.path.abspath(os.path.join(SPECPATH, ".."))

def _norm_datas(entries, drop_py=True):
    """Normalise collect_* entries to (dest, src, "DATA") triples.

    PyInstaller 6.x wants ``(dest, src, kind)``; ``format_binaries_and_datas``
    already returns plain (dest, src) pairs from hook-style 2-tuples, so we
    just attach the "DATA" kind.

    When ``drop_py`` is True (the default) any collected entry that is a
    plaintext Python source file (``*.py``/``*.pyc``/``*.pyi``) is discarded
    from the bundle. PyInstaller compiles every module into the PYZ archive
    (``a.pure``/``pyc``), so imports at runtime never read ``.py`` from
    ``_internal`` — the copies end up being shipped only to leak readable
    source code next to the installed exe. Dropping them keeps the app fully
    functional while stopping the source disclosure (the "shows source code
    in Program Files" complaint).
    """
    out = []
    for dest, src in format_binaries_and_datas(entries):
        if drop_py and _is_python_source(src):
            continue
        out.append((dest, src, "DATA"))
    return out


def _is_python_source(src) -> bool:
    """True when a collected data file is an importable Python source file.

    Only the module source itself is stripped — genuine data payloads (yaml,
    json, html, css, js, sql, BaseThemes, webview libs, duckwasm) are kept,
    and the compiled bytecode stays inside the PYZ. Hidden data branches like
    ``<data>``/``<egg>``/portable egg dirs that can hold .py are also pruned.
    """
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
    # Heavy ML/game packages: never imported by the app (verified by
    # dbg_excl.py exe-parity check), but modulegraph follows them via
    # vendored compat shims — GBs of torch CUDA DLLs otherwise land in dist/.
    # unittest is intentionally NOT excluded: pyparsing imports
    # pyparsing.testing (which needs unittest) at package init, so the
    # frozen app crashes on startup without it (ModuleNotFoundError).
    excludes=["tkinter", "pytest", "pdb", "doctest",
              "torch", "transformers", "pygame", "laya",
              "sklearn", "tensorboard", "triton"],
    noarchive=False,
    optimize=2,
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

# pywebview renderers + pythonnet (Windows EdgeChromium backend).
for _pkg in ("webview", "clr"):
    try:
        _datas, _binaries, _hidden = collect_all(_pkg)
        a.datas += _norm_datas(_datas)
        a.binaries += _norm_binaries(_binaries)
        a.hiddenimports += _hidden
    except Exception:  # noqa: BLE001
        pass

# Never bundle private key material: frozen builds verify against the baked
# public-key constant only (keygen_app.config.load_public_key). .pem files
# under keys/ would let an attacker extract or swap signing material.
# NOTE: certifi's public CA bundle (cacert.pem) must ALWAYS be kept — frozen
# httpx builds its SSL context eagerly for every client, and without the
# bundle every HTTP call dies with FileNotFoundError (total AI outage).
def _is_private_key_material(src) -> bool:
    name = str(src).lower().replace("\\", "/")
    base = name.rsplit("/", 1)[-1]
    if base == "cacert.pem":
        return False
    return "ed25519_private" in name or (base.endswith(".pem") and "private" in base)


a.datas = [
    (d, s, k) for (d, s, k) in a.datas
    if not _is_private_key_material(s)
]

# Never bundle runtime credential state. `collect_all("config")` sweeps up
# whatever happens to be in config/ on the build machine — including
# config/users.json (23 accounts, 5 admins, plaintext API keys) and any
# .env sitting in the tree. The app regenerates these on first run.
_CREDENTIAL_BASENAMES = {
    "users.json", "db_connections.json", "google_login_pending.json",
    "llm_runtime.json", "revoked_jtis.json", ".jwt_secret", ".grant_key",
    ".grant_migrated", ".env",
}


def _is_credential_state(src) -> bool:
    name = str(src).replace("\\", "/")
    base = name.rsplit("/", 1)[-1]
    if base in _CREDENTIAL_BASENAMES:
        return True
    if base == ".env" or base.startswith(".env.") or base.endswith(".env"):
        return True
    return False


a.datas = [(d, s, k) for (d, s, k) in a.datas if not _is_credential_state(s)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DataAgentShell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    # macOS only; Windows signing is done post-build via signtool in
    # scripts/build_windows.ps1 -Sign (DATAAENT_CODESIGN_* env). Env hook
    # keeps local unsigned builds working while CI can inject an identity.
    codesign_identity=os.environ.get("DATAAENT_CODESIGN_IDENTITY") or None,
    entitlements_file=os.environ.get("DATAAENT_CODESIGN_ENTITLEMENTS") or None,
    version=os.path.join(root, "packaging", "windows", "version_info.txt"),
    icon=os.path.join(root, "packaging", "windows", "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name="DataAgentShell",
)