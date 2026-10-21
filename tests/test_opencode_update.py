"""opencode CLI background self-update — version check, staleness, resolution.

A shipped .exe must keep working after its bundled CLI goes stale: the
runtime copy (background self-update target) wins resolution only when
strictly newer, and every network touch fails silent with caching.
"""

from __future__ import annotations

import pytest

import agent.opencode_bootstrap as ob


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    ob._version_cache.clear()
    monkeypatch.delattr(ob.schedule_background_opencode_update, "_scheduled", raising=False)
    yield
    ob._version_cache.clear()
    if hasattr(ob.schedule_background_opencode_update, "_scheduled"):
        delattr(ob.schedule_background_opencode_update, "_scheduled")


def test_parse_version():
    assert ob._parse_version("opencode 1.2.3") == "1.2.3"
    assert ob._parse_version("v0.9.12 (abc)") == "0.9.12"
    assert ob._parse_version("no digits here") is None
    assert ob._parse_version("") is None


def test_compare_versions():
    assert ob._compare_versions("1.2.3", "1.2.3") == 0
    assert ob._compare_versions("1.2.4", "1.2.3") > 0
    assert ob._compare_versions("1.2.3", "1.3.0") < 0
    assert ob._compare_versions("v2.0.0", "1.9.9") > 0
    assert ob._compare_versions("1.10.0", "1.9.9") > 0
    assert ob._compare_versions("1.2", "1.2.0") == 0


def test_binary_version_cached_by_mtime(tmp_path, monkeypatch):
    calls = []

    class _Stat:
        st_mtime = 123.0

    import subprocess as _sp

    def _fake_run(*a, **k):
        calls.append(1)

        class _R:
            stdout = "opencode 2.0.1\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)
    fake = tmp_path / "opencode.exe"
    fake.write_bytes(b"x")
    monkeypatch.setattr(type(fake), "stat", lambda self: _Stat(), raising=False)
    assert ob.get_binary_version(fake) == "2.0.1"
    assert ob.get_binary_version(fake) == "2.0.1"
    assert len(calls) == 1


def test_latest_version_uses_cache_without_network(tmp_path, monkeypatch):
    import json as _json
    import time as _time

    monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
    cache = tmp_path / ".opencode-latest.json"
    cache.write_text(_json.dumps({"version": "9.9.9", "checked_at": _time.time()}))

    async def _boom(*a, **k):
        raise AssertionError("network must not be touched with fresh cache")

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    got = __import__("asyncio").run(ob.get_latest_opencode_version())
    assert got == "9.9.9"


def _patch_resolution(monkeypatch, tmp_path, bundled_v, runtime_v):
    from pathlib import Path

    monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
    bundled = tmp_path / "bundled" / "opencode.exe"
    (tmp_path / "bundled").mkdir()
    bundled.write_bytes(b"x")
    versions = {str(bundled): bundled_v}

    runtime = tmp_path / "opencode.exe"
    if runtime_v is not None:
        runtime.write_bytes(b"x")
        versions[str(runtime)] = runtime_v
    monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: bundled)
    monkeypatch.setattr(ob, "get_binary_version", lambda p: versions.get(str(p)))
    return bundled, runtime


def test_resolution_prefers_newer_runtime(tmp_path, monkeypatch):
    bundled, runtime = _patch_resolution(monkeypatch, tmp_path, "1.0.0", "1.1.0")
    assert ob.opencode_bin_path() == runtime


def test_resolution_keeps_newer_bundled(tmp_path, monkeypatch):
    bundled, runtime = _patch_resolution(monkeypatch, tmp_path, "2.0.0", "1.1.0")
    assert ob.opencode_bin_path() == bundled


def test_resolution_old_priority_when_versions_unknown(tmp_path, monkeypatch):
    bundled, runtime = _patch_resolution(monkeypatch, tmp_path, None, None)
    assert ob.opencode_bin_path() == bundled


def test_resolution_prefers_versioned_newer_over_bundled(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
    bundled = tmp_path / "bundled" / "opencode.exe"
    (tmp_path / "bundled").mkdir()
    bundled.write_bytes(b"x")
    new = tmp_path / "opencode-1.1.0.exe"
    new.write_bytes(b"x")
    monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: bundled)
    monkeypatch.setattr(ob, "get_binary_version",
                        lambda p: "1.0.0" if Path(p) == bundled else "1.1.0")
    assert ob.opencode_bin_path() == new


def test_prune_keeps_newest_two(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        (tmp_path / f"opencode-{v}.exe").write_bytes(b"x")
    ob._prune_old_runtime_copies(keep=2)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["opencode-1.1.0.exe", "opencode-1.2.0.exe"], remaining


def test_safe_extract_rejects_traversal(tmp_path):
    import zipfile

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../pwned.txt", "x")
        zf.writestr("ok.txt", "y")
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        ob._safe_extract_zip(evil, dest)
    except ValueError:
        pass
    else:
        raise AssertionError("traversal member must be rejected")
    assert not (tmp_path / "pwned.txt").exists()


def test_ensure_updated_skips_when_runtime_has_latest(tmp_path, monkeypatch):
    import json as _json
    import time as _time

    _patch_resolution(monkeypatch, tmp_path, "1.0.0", None)
    (tmp_path / "opencode-1.1.0.exe").write_bytes(b"x")
    (tmp_path / ".opencode-latest.json").write_text(
        _json.dumps({"version": "1.1.0", "checked_at": _time.time()}))

    async def _no_download(*a, **k):
        raise AssertionError("must not download when runtime already has latest")

    monkeypatch.setattr(ob, "_download", _no_download)
    assert __import__("asyncio").run(ob.ensure_opencode_updated()) is True


def test_check_update_snapshot(tmp_path, monkeypatch):
    import json as _json
    import time as _time

    _patch_resolution(monkeypatch, tmp_path, "1.0.0", "1.1.0")
    (tmp_path / ".opencode-latest.json").write_text(
        _json.dumps({"version": "1.1.0", "checked_at": _time.time()}))
    snap = ob.check_opencode_update()
    assert snap["current"] == "1.1.0"  # runtime copy wins resolution
    assert snap["latest"] == "1.1.0"
    assert snap["update_available"] is False
    assert snap["source"] == "runtime"


def test_ensure_updated_noop_when_current(tmp_path, monkeypatch):
    _patch_resolution(monkeypatch, tmp_path, "1.1.0", "1.1.0")
    import json as _json
    import time as _time

    (tmp_path / ".opencode-latest.json").write_text(
        _json.dumps({"version": "1.1.0", "checked_at": _time.time()}))

    async def _no_download(*a, **k):
        raise AssertionError("must not download when current")

    monkeypatch.setattr(ob, "_download", _no_download)
    assert __import__("asyncio").run(ob.ensure_opencode_updated()) is True


def test_schedule_once_per_process():
    import asyncio as _asyncio

    async def _main():
        ob.schedule_background_opencode_update()
        assert getattr(ob.schedule_background_opencode_update, "_scheduled", False) is True
        ob.schedule_background_opencode_update()  # second call is a no-op
        await _asyncio.sleep(0)  # let the task run/fail silently

    _asyncio.run(_main())


def test_schedule_without_running_loop_is_noop():
    ob.schedule_background_opencode_update()
    assert getattr(ob.schedule_background_opencode_update, "_scheduled", False) is False
