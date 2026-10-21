"""opencode serve robustness — no duplicate spawns, stale pid cleanup, slow start.

Regression tests for the installed-.exe failure where the AI stayed dead:
concurrent first-AI requests each spawned a duplicate ``opencode serve`` on
the same port (losers crash with ServeError), a wedged port-holder caused
every later ensure to spawn doomed duplicates, and a slow cold start
(180MB binary + Defender scan) outlived the 20s ready wait.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

import agent.opencode_bootstrap as ob


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Instant sleeps + short holder waits so loops run hermetically."""
    async def _nosleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    # Holder-wait would otherwise spin on the wall clock with no-op sleeps.
    monkeypatch.setattr(ob, "_HOLDER_WAIT_S", 0.0)
    yield


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    ob._version_cache.clear()
    ob._spawn_in_progress = False
    ob._last_probe_error = ""
    monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
    yield
    ob._version_cache.clear()
    ob._spawn_in_progress = False


def _hold_port():
    """Bind a real listener to simulate a port held by another process."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


class _TimeoutClient:
    """httpx stand-in: TCP would connect (port held) but HTTP never answers."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        import httpx

        raise httpx.ConnectTimeout("wedged holder")


def test_spawn_lock_exists():
    """Concurrent ensures must serialize spawns through a module-level lock."""
    assert hasattr(ob, "_spawn_lock"), "opencode_bootstrap needs a _spawn_lock"
    assert isinstance(ob._spawn_lock, asyncio.Lock)


def test_no_duplicate_spawn_when_port_busy(monkeypatch):
    """A held-but-silent port must NOT trigger a duplicate server spawn."""
    import subprocess as _sp

    sock, port = _hold_port()
    real_popen = _sp.Popen
    try:
        import httpx as _httpx

        monkeypatch.setattr(_httpx, "AsyncClient", _TimeoutClient)
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path(r"C:\fake\opencode.exe"))
        spawns = []

        def _fake_popen(*a, **k):
            # Count only real `serve` spawns — subprocess.check_output (used
            # by the port-holder diagnostics) builds on Popen internally and
            # must keep working.
            cmd = a[0] if a else k.get("args", [])
            parts = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
            if any(part == "serve" for part in parts):
                spawns.append(1)
                raise AssertionError("no spawn")
            return real_popen(*a, **k)

        monkeypatch.setattr(_sp, "Popen", _fake_popen)

        ok = asyncio.run(ob.ensure_opencode_server(f"http://127.0.0.1:{port}", auto_install=False))
        assert ok is False
        assert spawns == [], "must not spawn a duplicate server onto a held port"
    finally:
        sock.close()


def test_stale_pidfile_cleaned(monkeypatch, tmp_path):
    """A pidfile pointing at a dead process must be removed, not reused."""
    import httpx as _httpx

    port = 39999
    (tmp_path / f"opencode-serve-{port}.pid").write_text("999999")
    monkeypatch.setattr(_httpx, "AsyncClient", _TimeoutClient)
    monkeypatch.setattr(ob, "opencode_bin_path", lambda: None)

    ok = asyncio.run(ob.ensure_opencode_server(f"http://127.0.0.1:{port}", auto_install=False))
    assert ok is False
    assert not (tmp_path / f"opencode-serve-{port}.pid").exists(), "stale pidfile must be cleaned"


def test_ready_wait_allows_slow_cold_start(monkeypatch):
    """A server answering after ~25s (cold start) must still count as ready."""
    import subprocess as _sp

    import httpx as _httpx

    calls = {"n": 0}

    class _SlowClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            calls["n"] += 1
            if calls["n"] < 55:  # ~27s of 0.5s polls with real sleeps
                raise _httpx.ConnectError("starting")

            class _R:
                status_code = 200

            return _R()

    class _Proc:
        pid = 4242

    monkeypatch.setattr(_httpx, "AsyncClient", _SlowClient)
    monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path(r"C:\fake\opencode.exe"))
    monkeypatch.setattr(ob, "get_binary_version", lambda p: "9.9.9")
    monkeypatch.setattr(_sp, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(ob, "schedule_background_opencode_update", lambda: None)

    ok = asyncio.run(ob.ensure_opencode_server("http://127.0.0.1:32123", auto_install=False))
    assert ok is True, "ready wait must survive a slow cold start"


def test_concurrent_ensures_spawn_once(monkeypatch):
    """Two concurrent ensures with a down server must spawn exactly once."""
    import subprocess as _sp

    import httpx as _httpx

    state = {"ready": False}

    class _FlapClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            if state["ready"]:
                class _R:
                    status_code = 200

                return _R()
            raise _httpx.ConnectError("down")

    class _Proc:
        pid = 7777

    def _fake_popen(*a, **k):
        state["ready"] = True
        return _Proc()

    spawns = []
    orig_fake = _fake_popen

    def _counting_popen(*a, **k):
        spawns.append(1)
        return orig_fake(*a, **k)

    monkeypatch.setattr(_httpx, "AsyncClient", _FlapClient)
    monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path(r"C:\fake\opencode.exe"))
    monkeypatch.setattr(ob, "get_binary_version", lambda p: "9.9.9")
    monkeypatch.setattr(_sp, "Popen", _counting_popen)
    monkeypatch.setattr(ob, "schedule_background_opencode_update", lambda: None)

    async def _both():
        return await asyncio.gather(
            ob.ensure_opencode_server("http://127.0.0.1:32124", auto_install=False),
            ob.ensure_opencode_server("http://127.0.0.1:32124", auto_install=False),
        )

    results = asyncio.run(_both())
    assert results == [True, True]
    assert len(spawns) == 1, f"expected exactly one spawn, got {len(spawns)}"


def test_no_autospawn_for_remote_server_url(monkeypatch):
    """Auto-spawn serves loopback only: a remote URL that is down must not
    trigger a local `serve` bind, and must fail with a clear False."""
    import subprocess as _sp

    import httpx as _httpx

    class _DownClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise _httpx.ConnectError("no route")

    monkeypatch.setattr(_httpx, "AsyncClient", _DownClient)
    monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path(r"C:\fake\opencode.exe"))
    spawns = []
    monkeypatch.setattr(_sp, "Popen", lambda *a, **k: spawns.append(1))

    ok = asyncio.run(ob.ensure_opencode_server("http://192.0.2.1:3000", auto_install=False))
    assert ok is False
    assert spawns == [], "must never auto-spawn for a non-loopback server_url"


def test_port_holder_info_parsed(monkeypatch):
    """The port holder must be identified by pid + image from netstat/tasklist."""
    import subprocess as _sp

    netstat = (
        "Active Connections\r\n"
        "  Proto  Local Address          Foreign Address        State           PID\r\n"
        "  TCP    127.0.0.1:3000         0.0.0.0:0              LISTENING       17456\r\n"
        "  TCP    127.0.0.1:8002         0.0.0.0:0              LISTENING       6792\r\n"
    )
    tasklist = '"opencode.exe","17456","Console","1","180,599,176 K"\r\n'

    def _fake_check_output(cmd, **kwargs):
        if cmd[0] == "netstat":
            return netstat
        if cmd[0] == "tasklist":
            assert "17456" in cmd[2]
            return tasklist
        raise AssertionError(cmd)

    monkeypatch.setattr(_sp, "check_output", _fake_check_output)
    assert ob._port_holder_info(3000) == {"pid": 17456, "image": "opencode.exe"}
    assert ob._port_holder_info(9999) == {}


def test_port_holder_info_never_raises(monkeypatch):
    """netstat failures must degrade to {} instead of raising."""
    import subprocess as _sp

    def _boom(*a, **k):
        raise OSError("no netstat")

    monkeypatch.setattr(_sp, "check_output", _boom)
    assert ob._port_holder_info(3000) == {}


def test_probe_remembers_last_error(monkeypatch):
    """Probe failures must be queryable (no log spam, no silent mystery)."""
    import httpx as _httpx

    class _DownClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise _httpx.ConnectError("refused")

    class _UpClient(_DownClient):
        async def get(self, url):
            class _R:
                status_code = 200

            return _R()

    monkeypatch.setattr(_httpx, "AsyncClient", _DownClient)
    assert asyncio.run(ob._probe_config("http://127.0.0.1:39998")) is False
    assert "ConnectError" in ob._last_probe_error
    monkeypatch.setattr(_httpx, "AsyncClient", _UpClient)
    assert asyncio.run(ob._probe_config("http://127.0.0.1:39998")) is True
    assert ob._last_probe_error == ""


def test_runtime_keys_include_server_fields():
    """Settings UI persistence must cover the opencode server-mode fields."""
    from config import _RUNTIME_ALLOWED_KEYS

    for key in (
        "opencode_server_url",
        "opencode_server_auto_start",
        "opencode_server_hostname",
        "opencode_server_port",
    ):
        assert key in _RUNTIME_ALLOWED_KEYS, f"{key} must be persistable"
