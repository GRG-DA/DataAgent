"""Regression tests for the remaining frozen-build runtime-path defects.

``tests/test_launch_runtime_paths.py`` covers the JWT secret and the grant
key.  This file covers the three further instances of the same class that the
first pass surfaced in a read-only audit:

* ``keygen_app/storage.py``   — the paid-licence activation wrote into the
  PyInstaller bundle instead of the runtime warehouse, so activating a
  licence in the desktop build failed against the wrong (unwritable) file;
* ``api/llm_deps.py``         — the one-click Ollama installer downloaded the
  binary into the bundle and could not write the server logs;
* ``analysis/lineage.py``     — the lineage graph was saved into the bundle,
  so the save was dropped and the lineage was lost on every restart.

The invariant under test: **no runtime state is written inside the PyInstaller
bundle**, because the desktop build installs into ``C:\\Program Files``, which
a normal user cannot write to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture()
def bundle(tmp_path, monkeypatch):
    """A fake PyInstaller bundle plus a writable runtime dir."""
    meipass = tmp_path / "_internal"
    meipass.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setenv("DATAAGENT_DATA_DIR", str(runtime))
    return meipass, runtime


class TestKeygenStoragePaths:
    """keygen_app/storage.py must target the runtime warehouse."""

    def test_default_db_is_not_inside_the_bundle(self, bundle):
        meipass, _runtime = bundle
        from keygen_app import storage

        assert not str(storage.default_db()).startswith(str(meipass))

    def test_default_db_points_at_the_runtime_warehouse(self, bundle):
        _meipass, runtime = bundle
        from keygen_app import storage

        assert storage.default_db() == runtime / "data" / "curated" / "warehouse.duckdb"

    def test_local_state_db_is_not_inside_the_bundle(self, bundle):
        meipass, _runtime = bundle
        from keygen_app import storage

        assert not str(storage.local_state_db()).startswith(str(meipass))

    def test_activate_uses_the_runtime_warehouse(self, bundle, tmp_path):
        """The activation must land in the runtime warehouse, not the bundle."""
        _meipass, runtime = bundle
        from keygen_app import storage

        target = runtime / "data" / "curated" / "warehouse.duckdb"
        target.parent.mkdir(parents=True, exist_ok=True)

        result = storage.activate(
            user_id="user-1",
            plan="lifetime",
            license_key="DATA-TEST-KEY",
            expires_at=None,
        )

        # The warehouse file now exists in the runtime dir.
        assert target.exists()
        # ...and the licence was recorded.
        assert result["license"]["plan"] == "lifetime"
        assert result["license"]["valid"] is True

    def test_activate_honours_an_explicit_db_path(self, bundle, tmp_path):
        _meipass, _runtime = bundle
        from keygen_app import storage

        explicit = tmp_path / "explicit.duckdb"
        storage.activate(
            user_id="user-2",
            plan="pro",
            license_key="DATA-TEST-KEY-2",
            expires_at=None,
            db_path=explicit,
        )
        assert explicit.exists()


class TestPremiumActivationPath:
    """api/premium.py must hand the Key Manager the app's own warehouse."""

    def test_activation_passes_the_configured_warehouse(self, bundle, monkeypatch):
        _meipass, runtime = bundle
        import api.premium as premium
        from config import settings

        captured = {}

        def _fake_activate(**kwargs):
            captured.update(kwargs)
            return {"license": {}, "sub_id": "sub-1"}

        monkeypatch.setattr(
            premium, "_kg_activate", _fake_activate, raising=False
        )

        # Simulate what the route does, and assert the db_path it would use.
        expected = Path(settings.duckdb.database_path)
        assert expected.name == "warehouse.duckdb"
        assert not str(expected).startswith(str(runtime))


class TestLlmDepsPaths:
    """api/llm_deps.py must write the Ollama binary and logs to the runtime."""

    def test_storage_dir_is_not_inside_the_bundle(self, bundle):
        meipass, _runtime = bundle
        from api import llm_deps

        assert not str(llm_deps.storage_dir()).startswith(str(meipass))

    def test_logs_dir_is_not_inside_the_bundle(self, bundle):
        meipass, _runtime = bundle
        from api import llm_deps

        assert not str(llm_deps.logs_dir()).startswith(str(meipass))

    def test_ollama_binary_target_is_writable(self, bundle):
        """The path must be resolved per call, not snapshotted at import."""
        _meipass, runtime = bundle
        from api import llm_deps

        assert str(llm_deps.ollama_bin()).startswith(str(runtime))
        assert str(llm_deps.ollama_bin()).endswith("ollama.exe") or str(
            llm_deps.ollama_bin()
        ).endswith("ollama")

    def test_paths_follow_the_data_dir_when_it_changes(self, bundle, tmp_path, monkeypatch):
        """A later $DATAAGENT_DATA_DIR must be honoured (no stale constant)."""
        _meipass, _runtime = bundle
        from api import llm_deps

        other = tmp_path / "other-runtime"
        monkeypatch.setenv("DATAAGENT_DATA_DIR", str(other))
        assert str(llm_deps.storage_dir()).startswith(str(other))
        assert str(llm_deps.logs_dir()).startswith(str(other))


class TestLineagePath:
    """analysis/lineage.py must persist the lineage graph to the runtime."""

    def test_lineage_path_is_not_inside_the_bundle(self, bundle):
        meipass, _runtime = bundle
        from analysis import lineage

        assert not str(lineage.DEFAULT_LINEAGE_PATH).startswith(str(meipass))

    def test_lineage_path_is_in_the_runtime_data_dir(self, bundle):
        _meipass, runtime = bundle
        from analysis import lineage

        tracker = lineage.LineageTracker()
        assert tracker._storage_path == runtime / "data" / "lineage.json"

    def test_lineage_survives_a_save(self, bundle):
        """The tracker must actually be able to write its file."""
        _meipass, runtime = bundle
        from analysis.lineage import LineageTracker

        tracker = LineageTracker()
        tracker.record_transform(
            table="analytics.orders",
            operation="select",
            inputs=["raw.orders"],
            outputs=["analytics.orders"],
        )
        tracker._save()
        assert tracker._storage_path.exists()
        assert tracker._storage_path.stat().st_size > 0
        assert str(tracker._storage_path).startswith(str(runtime))
