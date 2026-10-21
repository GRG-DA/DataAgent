# -*- mode: python ; coding: utf-8 -*-
# LEGACY onefile spec: `DataAgent.exe web` (console build via
# scripts/build_windows.ps1). The current desktop build is the onedir
# scripts/DataAgentShell.spec. Keep this file building; do not extend it.
#
# Private-key hygiene: never bundle *.pem / private key material in datas or
# binaries (see tests/test_packaging.py anti-crack flags).
#
# TODO packaging: wired — version from packaging/windows/version_info.txt and
# icon from packaging/windows/app.ico (cf. shell spec).
import os as _os

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules

datas = [('web', 'web')]
binaries = []
hiddenimports = ['api.main']
datas += collect_data_files('api')
datas += collect_data_files('agent')
datas += collect_data_files('warehouse')
datas += collect_data_files('config')
datas += collect_data_files('etl')
datas += collect_data_files('export')
binaries += collect_dynamic_libs('api')
binaries += collect_dynamic_libs('agent')
binaries += collect_dynamic_libs('warehouse')
binaries += collect_dynamic_libs('config')
binaries += collect_dynamic_libs('etl')
binaries += collect_dynamic_libs('export')
hiddenimports += collect_submodules('api')
hiddenimports += collect_submodules('agent')
hiddenimports += collect_submodules('warehouse')
hiddenimports += collect_submodules('config')
hiddenimports += collect_submodules('etl')
hiddenimports += collect_submodules('export')

# Never bundle runtime credential state. `collect_data_files('config')`
# sweeps up whatever happens to be in config/ on the build machine —
# including config/users.json (password hashes, plaintext API keys) and any
# .env in the tree. The app regenerates these on first run.
_CREDENTIAL_BASENAMES = {
    'users.json', 'db_connections.json', 'google_login_pending.json',
    'llm_runtime.json', 'revoked_jtis.json', '.jwt_secret', '.grant_key',
    '.grant_migrated', '.env',
}


def _is_credential_state(src):
    name = str(src).replace('\\', '/')
    base = name.rsplit('/', 1)[-1]
    if base in _CREDENTIAL_BASENAMES:
        return True
    if base == '.env' or base.startswith('.env.') or base.endswith('.env'):
        return True
    return False


datas = [(d, s) for (d, s) in datas if not _is_credential_state(s)]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'pdb', 'doctest',
              'torch', 'transformers', 'pygame', 'laya',
              'sklearn', 'tensorboard', 'triton'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DataAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    # Windows signing is done post-build via signtool in
    # scripts/build_windows.ps1 -Sign (DATAAENT_CODESIGN_* env). Env hook
    # keeps local unsigned builds working while CI can inject an identity.
    codesign_identity=_os.environ.get("DATAAENT_CODESIGN_IDENTITY") or None,
    entitlements_file=_os.environ.get("DATAAENT_CODESIGN_ENTITLEMENTS") or None,
    version=_os.path.join(SPECPATH, 'packaging', 'windows', 'version_info.txt'),
    icon=_os.path.join(SPECPATH, 'packaging', 'windows', 'app.ico'),
)
