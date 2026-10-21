"""EXE 100% gates — signing wiring, checksums, preflight, update, CI."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_specs_support_env_signing():
    for spec in ("scripts/DataAgentShell.spec", "DataAgent.spec"):
        text = (ROOT / spec).read_text(encoding="utf-8")
        assert "DATAAENT_CODESIGN_IDENTITY" in text, f"{spec} missing signing hook"


def test_build_script_sign_and_checksums():
    ps1 = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "-Sign" in ps1 or "$Sign" in ps1
    assert "signtool" in ps1.lower()
    assert "SHA256SUMS" in ps1
    assert "opencode.exe.sha256" in ps1


def test_shell_preflight_and_update_exist():
    assert (ROOT / "shell" / "runtime_check.py").exists()
    assert (ROOT / "shell" / "update.py").exists()
    rc = (ROOT / "shell" / "runtime_check.py").read_text(encoding="utf-8")
    assert "WebView2" in rc
    assert "preflight" in rc
    up = (ROOT / "shell" / "update.py").read_text(encoding="utf-8")
    assert "update_available" in up
    app = (ROOT / "shell" / "app.py").read_text(encoding="utf-8")
    assert "preflight()" in app


def test_ci_builds_windows_exe():
    yml = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    assert "PyInstaller" in yml or "build_windows" in yml
    assert "upload-artifact" in yml
    assert "test_packaging" in yml or "test_exe_release" in yml


def test_runtime_check_importable():
    from shell.runtime_check import check_writable, check_webview2, preflight

    assert callable(check_writable) and callable(check_webview2) and callable(preflight)


def test_update_check_offline_safe():
    from shell.update import current_version, update_available

    assert isinstance(current_version(), str)
    ok, cur, lat = update_available(timeout=0.01)
    assert isinstance(ok, bool) and isinstance(cur, str) and isinstance(lat, str)
