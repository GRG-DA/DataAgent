"""Packaging consistency — version and exe branding must not drift.

Single sources of truth for the release version:
  config/settings.yaml (app.version) · installers/DataAgent-Setup.iss
  (MyAppVersion) · packaging/windows/version_info.txt (FileVersion).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _app_version() -> str:
    import yaml

    data = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    version = (data.get("app") or {}).get("version", "")
    assert version, "app.version missing in config/settings.yaml"
    return str(version)


def test_version_consistent_across_release_files():
    version = _app_version()

    iss = (ROOT / "installers" / "DataAgent-Setup.iss").read_text(encoding="utf-8")
    m = re.search(r'#define MyAppVersion "([^"]+)"', iss)
    assert m and m.group(1) == version, "ISS MyAppVersion drifted"

    vi = (ROOT / "packaging" / "windows" / "version_info.txt").read_text(encoding="utf-8").replace(" ", "")
    nums = tuple(int(x) for x in version.split("."))
    nums4 = "(" + ",".join(str(n) for n in (nums + (0,) * (4 - len(nums)))) + "),"
    assert f"filevers={nums4}" in vi, "version_info FileVersion drifted"
    assert f"prodvers={nums4}" in vi, "version_info ProductVersion drifted"
    assert f"StringStruct(u'FileVersion',u'{version}')" in vi
    assert f"StringStruct(u'ProductVersion',u'{version}')" in vi


def test_windows_icon_is_valid_ico():
    from PIL import Image

    ico = ROOT / "packaging" / "windows" / "app.ico"
    assert ico.exists(), "packaging/windows/app.ico missing"
    with Image.open(ico) as im:
        assert im.size[0] >= 48, f"icon too small: {im.size}"
        # Multi-size payload (16/32/48/256): a single-size PNG renamed to
        # .ico must not pass.
        sizes = {s for s in (im.info.get("sizes") or set())}
        assert {(16, 16), (256, 256)} <= sizes, f"icon missing sizes: {sizes}"


def test_shell_spec_wires_version_and_icon():
    spec = (ROOT / "scripts" / "DataAgentShell.spec").read_text(encoding="utf-8")
    assert "version=os.path.join(root" in spec and "version_info.txt" in spec
    assert "icon=os.path.join(root" in spec and "app.ico" in spec


def test_shell_spec_anti_crack_flags():
    """Frozen builds must stay hardened: optimized, stripped, no private keys."""
    spec = (ROOT / "scripts" / "DataAgentShell.spec").read_text(encoding="utf-8")
    assert "optimize=2" in spec, "spec must use optimize=2 (strip asserts/docstrings)"
    assert "strip=True" in spec, "spec must strip symbols"
    assert "disable_windowed_traceback=True" in spec, "no traceback popup leaking internals"
    assert "ed25519_private" in spec or '.pem' in spec, "spec must filter private key material"
    assert "excludes=" in spec and "pytest" in spec, "spec must exclude dev/test modules"


def test_shell_spec_keeps_public_ca_bundle():
    """certifi's cacert.pem must survive the private-key filter.

    Regression: a blanket `.pem` exclusion dropped the public CA bundle, so
    frozen httpx died at SSL-context creation with FileNotFoundError on
    EVERY request (total AI outage while raw sockets worked).
    """
    spec = (ROOT / "scripts" / "DataAgentShell.spec").read_text(encoding="utf-8")
    assert "cacert.pem" in spec, "spec must exempt the public CA bundle"


def test_onefile_spec_anti_crack_flags():
    spec = (ROOT / "DataAgent.spec").read_text(encoding="utf-8")
    assert "optimize=2" in spec
    assert "strip=True" in spec
    assert "disable_windowed_traceback=True" in spec


def test_onefile_spec_wires_version_and_icon():
    spec = (ROOT / "DataAgent.spec").read_text(encoding="utf-8")
    assert "version_info.txt" in spec, "DataAgent.spec must wire version_info.txt"
    assert "app.ico" in spec, "DataAgent.spec must wire app.ico"


def test_cython_surface_covers_enforcement():
    """P4: every license-enforcement module must be in the Cython list."""
    src = (ROOT / "scripts" / "cythonize_license.py").read_text(encoding="utf-8")
    for mod in (
        "keygen_app/keygen.py",
        "keygen_app/config.py",
        "keygen_app/storage.py",
        "api/premium.py",
        "api/billing/license_guard.py",
        "api/billing/entitlement_token.py",
        "api/billing/feature_ticket.py",
        "api/billing/entitlements.py",
        "api/billing/cloud_sync.py",
        "api/billing/redemption.py",
        "api/billing/gate.py",
    ):
        assert mod in src, f"P4 build omits enforcement module {mod}"
        assert (ROOT / mod).exists(), f"listed module missing: {mod}"


def test_cython_build_wiring():
    """Release build offers opt-in Cythonize and cleans up afterwards."""
    ps1 = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "Cythonize" in ps1
    assert "cythonize_license.py build" in ps1
    assert "cythonize_license.py clean" in ps1


def test_frozen_pubkey_is_baked_only():
    """Frozen load_public_key must ignore env/file overrides (anti-crack)."""
    src = (ROOT / "keygen_app" / "config.py").read_text(encoding="utf-8")
    assert "def is_frozen" in src
    assert "if is_frozen():" in src
    assert "_HARDCODED_FINGERPRINT" in src


def test_installer_branding_wired():
    iss = (ROOT / "installers" / "DataAgent-Setup.iss").read_text(encoding="utf-8")
    assert "SetupIconFile=" in iss
    assert "VersionInfoVersion=" in iss
    assert "CloseApplications=yes" in iss
