"""The opencode self-update must install the PINNED release under its own name.

ADR-012 pins the opencode CLI to ONE concrete release (``v1.19.3``). The
download URL, the file written into the runtime bin dir and the version
reported to the user must all come from that single pin.

The defect this file pins down: the download URL was built from the
pinned version while the destination file was named after the version
GitHub reported as "latest" - so a file that holds v1.19.3 was written as
``opencode-1.20.0.exe``. Every consumer that reads the NAME (the runtime
copy ranking, the "already up to date" short-circuit, and the prune that
keeps the newest copies) then believed the lie: the app reported a
version it did not have, and the prune deleted the correctly named copy.

The GitHub "latest" lookup stays, but it is INFORMATIONAL only: it says
"a newer upstream release exists". It never decides what is downloaded
or how the file is named - adopting a newer release is a deliberate pin
bump (a reviewable commit), never a silent install.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

import agent.opencode_bootstrap as ob


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pinned() -> str:
    """The pinned release as a bare X.Y.Z string (the app's one pin)."""
    return ob.OPENCODE_VERSION.lstrip("v")


def _expected_name(version: str) -> str:
    """The on-disk name of an installed runtime copy."""
    return f"opencode-{version}.exe" if os.name == "nt" else f"opencode-{version}"


def _fake_archive(path: Path, body: str) -> None:
    """Write a release archive that contains a fake opencode binary."""
    exe = "opencode.exe" if os.name == "nt" else "opencode"
    data = body.encode("utf-8")
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(exe, data)
        return
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(exe)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))


def _patch_download(monkeypatch, body: str) -> list:
    """Replace the network download with a local archive; record the URLs."""
    urls: list = []

    async def _download(url, dest, progress):
        urls.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        _fake_archive(Path(dest), body)
        return True

    monkeypatch.setattr(ob, "_download", _download)
    return urls


def _patch_probe(monkeypatch) -> None:
    """Read the version from the BYTES of the fake binary, never its name."""
    def _version(path):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        return ob._parse_version(text)

    monkeypatch.setattr(ob, "get_binary_version", _version)


def _latest(value: str):
    """A stub for the (informational) upstream latest-version lookup."""
    async def _fn():
        return value
    return _fn


def _write_latest_cache(runtime_dir: Path, version: str) -> None:
    """Seed the 24h latest-version cache the way the lookup would."""
    (runtime_dir / ".opencode-latest.json").write_text(
        json.dumps({"version": version, "checked_at": time.time()}),
        encoding="utf-8",
    )


@pytest.fixture()
def runtime_dir(tmp_path, monkeypatch):
    """An isolated runtime bin dir with no other opencode binary in sight."""
    monkeypatch.setattr(ob, "_runtime_bin_dir", lambda: tmp_path)
    monkeypatch.setattr(ob, "_bundled_opencode_path", lambda: None)
    monkeypatch.setattr(ob, "_existing_opencode_path", lambda: None)
    monkeypatch.setattr(ob.sys, "executable", str(tmp_path / "app" / "DataAgent"))
    ob._version_cache.clear()
    yield tmp_path
    ob._version_cache.clear()


# ---------------------------------------------------------------------------
# the installed file is named after the version it actually holds
# ---------------------------------------------------------------------------

class TestPinnedInstallName:
    def test_the_installed_copy_is_named_after_the_pinned_version(
        self, runtime_dir, monkeypatch
    ):
        """The file on disk must carry the version it actually holds."""
        _patch_download(monkeypatch, _pinned())
        monkeypatch.setattr(ob, "get_latest_opencode_version", _latest("9.9.9"))

        assert asyncio.run(ob.ensure_opencode_updated()) is True

        names = sorted(p.name for p in runtime_dir.iterdir() if p.is_file())
        assert names == [_expected_name(_pinned())], (
            f"the update installed {names}; the file must be named after the "
            f"PINNED release ({_pinned()}), never after the upstream 'latest'"
        )

    def test_the_pin_is_the_only_source_of_the_installed_name(
        self, runtime_dir, monkeypatch
    ):
        """Move the pin and the installed name moves with it."""
        monkeypatch.setattr(ob, "OPENCODE_VERSION", "v1.20.0")
        _patch_download(monkeypatch, "1.20.0")
        monkeypatch.setattr(ob, "get_latest_opencode_version", _latest("9.9.9"))

        assert asyncio.run(ob.ensure_opencode_updated()) is True

        names = sorted(p.name for p in runtime_dir.iterdir() if p.is_file())
        assert names == [_expected_name("1.20.0")], (
            f"the installed name {names} does not follow the pin - the pin is "
            "the single source of truth for the URL AND for the file name"
        )

    def test_the_download_url_is_built_from_the_pin(self, runtime_dir, monkeypatch):
        """The URL is the pinned release, never the moving 'latest'."""
        urls = _patch_download(monkeypatch, _pinned())
        monkeypatch.setattr(ob, "get_latest_opencode_version", _latest(_pinned()))

        assert asyncio.run(ob.ensure_opencode_updated()) is True

        assert urls, "no download happened"
        assert _pinned() in urls[0], f"the URL {urls[0]} is not the pinned release"
        assert "latest" not in urls[0], f"the URL {urls[0]} is not pinned"


# ---------------------------------------------------------------------------
# the reported version matches the bytes on disk
# ---------------------------------------------------------------------------

class TestReportedVersion:
    def test_the_reported_current_version_matches_the_bytes_on_disk(
        self, runtime_dir, monkeypatch
    ):
        _patch_download(monkeypatch, _pinned())
        _patch_probe(monkeypatch)
        monkeypatch.setattr(ob, "get_latest_opencode_version", _latest("9.9.9"))

        assert asyncio.run(ob.ensure_opencode_updated()) is True

        installed = runtime_dir / _expected_name(_pinned())
        assert installed.is_file(), (
            "the pinned release was not installed: "
            f"{sorted(p.name for p in runtime_dir.iterdir())}"
        )
        # What the bytes say.
        assert installed.read_text(encoding="utf-8").strip() == _pinned()
        # What the app reports.
        snap = ob.check_opencode_update()
        assert snap["current"] == _pinned(), (
            f"the reported current version {snap['current']!r} does not match "
            f"the bytes on disk ({_pinned()})"
        )
        # The name and the reported version agree.
        assert snap["current"] in installed.name, (
            f"the file {installed.name!r} and the reported version "
            f"{snap['current']!r} disagree"
        )


# ---------------------------------------------------------------------------
# the upstream 'latest' lookup is informational only
# ---------------------------------------------------------------------------

class TestLatestIsInformational:
    def test_a_newer_upstream_release_does_not_change_the_installed_name(
        self, runtime_dir, monkeypatch
    ):
        _patch_download(monkeypatch, _pinned())
        _patch_probe(monkeypatch)
        monkeypatch.setattr(ob, "get_latest_opencode_version", _latest("9.9.9"))
        _write_latest_cache(runtime_dir, "9.9.9")

        assert asyncio.run(ob.ensure_opencode_updated()) is True

        names = sorted(p.name for p in runtime_dir.iterdir()
                       if p.is_file() and not p.name.startswith("."))
        assert names == [_expected_name(_pinned())], (
            f"the upstream release changed the installed file: {names}"
        )
        snap = ob.check_opencode_update()
        assert snap["latest"] == "9.9.9", (
            "the newer upstream release must still be REPORTED - it is the "
            "signal that the pin needs a deliberate bump"
        )
        assert snap["pinned"] == _pinned(), (
            "the snapshot must name the release this build ships"
        )
        assert snap["current"] == _pinned()


# ---------------------------------------------------------------------------
# the prune must not evict the copy that carries the pinned release
# ---------------------------------------------------------------------------

class TestPrune:
    def test_the_prune_never_deletes_the_pinned_copy(self, runtime_dir):
        """A copy that claims a newer version must not evict the pin.

        The old updater wrote the PINNED bytes under the upstream latest
        name, so the prune (which ranks copies by their NAME) kept the
        lying copies and deleted the correctly named one.
        """
        pinned_copy = runtime_dir / _expected_name(_pinned())
        pinned_copy.write_text(_pinned(), encoding="utf-8")
        lying = runtime_dir / _expected_name("9.9.9")
        lying.write_text(_pinned(), encoding="utf-8")
        old = runtime_dir / _expected_name("1.0.0")
        old.write_text("1.0.0", encoding="utf-8")

        ob._prune_old_runtime_copies(keep=1)

        assert pinned_copy.is_file(), (
            "the copy that carries the PINNED release must never be pruned - "
            "its name is the only one guaranteed to match the bytes it holds"
        )
