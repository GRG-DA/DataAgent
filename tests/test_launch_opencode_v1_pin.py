"""The bundled opencode CLI must stay a **v1** release, and the app must be
able to tell which opencode answered on its port.

Two separate guarantees live here:

1. **The pin.** The opencode CLI this project ships is pinned to one concrete
   v1 release. The pin appears in FOUR files (the bootstrap that downloads and
   installs it, the Windows build, the Linux build and the Linux installer);
   they must agree, and the committed SHA256 files must exist for it. A v2
   release would break the v1 session API this build speaks, so the pin is
   asserted to be a 1.x release - not merely "a" release.

2. **The dialect.** A server may already be running on the port (the app
   auto-starts one only when nothing answers). That server may be an opencode
   v2 - e.g. an npm upgrade replaced the CLI on the host. The provider speaks
   only the v1 API, so a foreign server is a hard error: the app refuses to
   use it and reports why.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

import agent.opencode_bootstrap as ob  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _major(version: str) -> str:
    m = re.match(r"v?(\d+)", version.strip())
    return m.group(1) if m else ""


def _sha256_files() -> dict:
    """The committed pins, keyed by the file name."""
    return {p.name: p.read_text(encoding="utf-8").strip() for p in
            (ROOT / "installers").glob("opencode-*.sha256")}


# ── 1. the pin ───────────────────────────────────────────────────

class TestPinnedReleaseIsV1:
    def test_the_bootstrap_pin_is_a_v1_release(self):
        assert ob.OPENCODE_VERSION.startswith("v1."), (
            f"the bundled opencode must stay a v1 release (the provider's "
            f"session API is the v1 one), got {ob.OPENCODE_VERSION}"
        )

    def test_all_four_pin_sites_agree(self):
        pins = {
            "agent/opencode_bootstrap.py": ob.OPENCODE_VERSION,
            "scripts/build_windows.ps1": re.search(
                r'\$OpenCodeVersion\s*=\s*"([^"]+)"',
                _read("scripts/build_windows.ps1"),
            ).group(1),
            "scripts/build_linux.sh": re.search(
                r'OPENCODE_VERSION="([^"]+)"', _read("scripts/build_linux.sh")
            ).group(1),
            "packaging/linux/opencode-install.sh": re.search(
                r'OPENCODE_VERSION="([^"]+)"',
                _read("packaging/linux/opencode-install.sh"),
            ).group(1),
        }
        assert len(set(pins.values())) == 1, (
            f"the opencode pin must be the same everywhere, got {pins}"
        )
        assert _major(next(iter(pins.values()))) == "1", (
            f"the bundled opencode must be a v1 release, got {pins}"
        )

    def test_every_pinned_target_has_a_committed_hash(self):
        pins = _sha256_files()
        version = ob.OPENCODE_VERSION
        expected = [
            f"opencode-{version}.sha256",              # Windows (extracted exe)
            f"opencode-{version}-linux-x64.sha256",
            f"opencode-{version}-linux-x64-baseline.sha256",
            f"opencode-{version}-linux-arm64.sha256",
        ]
        for name in expected:
            assert name in pins, f"missing committed hash pin: {name}"
            digest = pins[name].split()[0]
            assert re.fullmatch(r"[0-9a-f]{64}", digest), (
                f"{name} must hold a 64-char lowercase hex SHA256, got {digest!r}"
            )

    def test_no_hash_pin_survives_for_another_version(self):
        """A stale pin for an unused release would ship in the RPM (it globs
        opencode-*.sha256) and could silently verify the wrong artefact."""
        current = ob.OPENCODE_VERSION
        for name in _sha256_files():
            version = re.search(r"opencode-(v[\d.]+?)(?:-linux-[a-z0-9-]+)?\.sha256$",
                                name)
            assert version, f"unrecognised pin file name: {name}"
            assert version.group(1) == current, (
                f"{name} pins {version.group(1)} but the build pins {current}"
            )


# ── 2. the dialect diagnostic ────────────────────────────────────

class _Resp:
    """A minimal httpx response stand-in."""

    def __init__(self, body: str, status_code: int = 200, content_type: str = ""):
        self.text = body
        self.status_code = status_code
        self.headers = {"content-type": content_type} if content_type else {}

    def json(self):
        return json.loads(self.text)


class _Client:
    """Serves canned bodies per path."""

    def __init__(self, routes: dict):
        self.routes = routes

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        for path, resp in self.routes.items():
            if url.endswith(path):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected url: {url}")


V1_CONFIG = json.dumps({"$schema": "https://opencode.ai/config.json",
                         "command": {}, "agent": {}})


@pytest.fixture
def v1_server(monkeypatch):
    monkeypatch.setattr(ob.httpx, "AsyncClient", _Client({
        "/config": _Resp(V1_CONFIG, content_type="application/json"),
        "/global/health": _Resp(json.dumps({"healthy": True, "version": "1.18.30"}),
                                content_type="application/json"),
    }))


@pytest.fixture
def v2_server(monkeypatch):
    """An opencode v2 server answers every path with its SPA shell."""
    monkeypatch.setattr(ob.httpx, "AsyncClient", _Client({
        "/config": _Resp("<!doctype html><html><title>OpenCode</title></html>"),
        "/global/health": _Resp("<!doctype html><html><title>OpenCode</title></html>"),
        "/api/info": _Resp(json.dumps({"version": "2.1.2", "pid": 1}),
                           content_type="application/json"),
    }))


class TestServerDialectDetection:
    def test_a_v1_server_is_recognised(self, v1_server):
        assert asyncio.run(ob._probe_config_kind("http://127.0.0.1:3000")) == ob.KIND_V1

    def test_a_v2_server_is_recognised_as_foreign(self, v2_server):
        assert asyncio.run(ob._probe_config_kind("http://127.0.0.1:3000")) == ob.KIND_FOREIGN

    def test_an_unreachable_server_is_unknown_not_foreign(self, monkeypatch):
        import httpx as _httpx

        class _Down:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                raise _httpx.ConnectError("refused")

        monkeypatch.setattr(ob.httpx, "AsyncClient", _Down)
        assert asyncio.run(ob._probe_config_kind("http://127.0.0.1:3000")) == ob.KIND_UNKNOWN

    def test_the_server_version_is_read_from_global_health(self, v1_server):
        assert asyncio.run(ob._probe_server_version("http://127.0.0.1:3000")) == "1.18.30"

    def test_the_version_is_none_when_the_server_does_not_report_one(self, v2_server):
        assert asyncio.run(ob._probe_server_version("http://127.0.0.1:3000")) is None

    def test_the_identity_probe_sends_the_server_credentials(self, monkeypatch):
        """A v1 server behind Basic auth must still be recognised as v1."""
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "s3cret")
        seen = {}

        class _AuthClient:
            def __init__(self, *a, **k):
                seen.update(k)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return _Resp(V1_CONFIG, content_type="application/json")

        monkeypatch.setattr(ob.httpx, "AsyncClient", _AuthClient)
        assert asyncio.run(ob._probe_config_kind("http://127.0.0.1:3000")) == ob.KIND_V1
        auth = seen.get("headers", {}).get("Authorization", "")
        assert auth.startswith("Basic "), "the identity probe must authenticate"


class TestForeignServerIsRejected:
    def test_a_v2_server_is_rejected_with_an_error(self, v2_server, monkeypatch):
        """The provider speaks only v1, so a v2 server must stop the app from
        using it - and say why."""
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path("opencode.exe"))
        monkeypatch.setattr(ob, "schedule_background_opencode_update", lambda: None)
        monkeypatch.setattr(ob, "_read_pidfile", lambda port: None)

        lines = []

        async def _progress(stage, pct, line=""):
            lines.append((stage, line))

        ok = asyncio.run(ob.ensure_opencode_server(
            "http://127.0.0.1:3000", auto_install=False, progress=_progress))

        assert ok is False, "a v2 server must be refused"
        assert any("speaks only the opencode v1" in line for _s, line in lines), (
            f"the refusal must be reported, got {lines}"
        )

    def test_an_unrecognised_server_is_rejected(self, monkeypatch):
        monkeypatch.setattr(ob.httpx, "AsyncClient", _Client({
            "/config": _Resp("{}", content_type="application/json"),
        }))
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path("opencode.exe"))
        monkeypatch.setattr(ob, "schedule_background_opencode_update", lambda: None)
        monkeypatch.setattr(ob, "_read_pidfile", lambda port: None)
        lines = []

        async def _progress(stage, pct, line=""):
            lines.append((stage, line))

        ok = asyncio.run(ob.ensure_opencode_server(
            "http://127.0.0.1:3000", auto_install=False, progress=_progress))
        assert ok is False, "an unrecognised server must be refused (fail closed)"
        assert any("speaks only the opencode v1" in line for _s, line in lines)

    def test_a_v1_server_is_accepted_silently(self, v1_server, monkeypatch):
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path("opencode.exe"))
        monkeypatch.setattr(ob, "schedule_background_opencode_update", lambda: None)
        monkeypatch.setattr(ob, "_read_pidfile", lambda port: None)

        lines = []

        async def _progress(stage, pct, line=""):
            lines.append((stage, line))

        ok = asyncio.run(ob.ensure_opencode_server(
            "http://127.0.0.1:3000", auto_install=False, progress=_progress))

        assert ok is True
        assert not any("speaks only the opencode v1" in line for _s, line in lines), (
            f"the pinned server must not be refused, got {lines}"
        )


class TestServerStatusReportsTheDialect:
    @pytest.mark.asyncio
    async def test_status_names_the_server_kind(self, v1_server, monkeypatch):
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: None)
        st = await ob.server_status("http://127.0.0.1:3000")
        assert st["server_kind"] == ob.KIND_V1
        assert st["server_version"] == "1.18.30"

    @pytest.mark.asyncio
    async def test_status_names_a_foreign_server(self, v2_server, monkeypatch):
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: None)
        st = await ob.server_status("http://127.0.0.1:3000")
        assert st["server_kind"] == ob.KIND_FOREIGN
        assert st["server_version"] == ""


class TestTheServerDoesNotInheritTheProjectTools:
    """The app's LLM must not be able to reach the project's MCP servers.

    opencode loads its MCP servers from the project configuration, which is
    found in its WORKING DIRECTORY. Started in the app's own directory the
    server inherits a DuckDB server (direct access to the warehouse, around
    the app's row-level security) and a filesystem server scoped to
    ``config/`` and ``data/`` - where ``.jwt_secret`` and ``.grant_key``
    live. The server therefore runs in a dedicated, empty directory.
    """

    def test_the_server_working_directory_is_not_the_project(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
        cwd = ob._server_cwd()
        assert cwd.exists() and cwd.is_dir()
        assert cwd.resolve() != Path(ob.__file__).resolve().parent
        assert cwd.resolve() != ROOT.resolve()
        assert not list(cwd.iterdir()), (
            "the server working directory must stay empty - a config file "
            "there would re-introduce the project's MCP servers"
        )

    def test_the_server_is_spawned_in_that_directory(self, monkeypatch, tmp_path):
        """The spawn must set cwd - otherwise the server starts in the app's
        directory and inherits the project's tools."""
        import subprocess as _sp

        monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
        seen = {}

        class _Proc:
            pid = 4242

        def _fake_popen(*a, **k):
            seen["cwd"] = k.get("cwd")
            return _Proc()

        monkeypatch.setattr(_sp, "Popen", _fake_popen)
        monkeypatch.setattr(ob, "opencode_bin_path", lambda: Path("opencode.exe"))
        monkeypatch.setattr(ob, "_probe_config", _async_false)
        monkeypatch.setattr(ob, "_port_in_use", lambda host, port: False)
        monkeypatch.setattr(ob, "_server_env", lambda host, port: {})
        monkeypatch.setattr(ob, "get_binary_version", lambda p: "1.18.30")

        ok = asyncio.run(ob.ensure_opencode_server("http://127.0.0.1:3199"))
        assert ok is False  # the fake server never becomes ready
        assert seen.get("cwd"), "opencode serve must be spawned with a cwd"
        assert Path(seen["cwd"]).resolve() != ROOT.resolve(), (
            "opencode serve must NOT run in the project directory - it would "
            "inherit the project's MCP servers"
        )


async def _async_false(*a, **k):
    return False

