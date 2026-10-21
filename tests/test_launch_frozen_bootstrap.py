"""The frozen bootstrap must put the WHOLE app in one directory.

``run.py::_frozen_bootstrap`` chdirs into the writable runtime dir,
because every app path (``data/``, ``config/``, ``logs/``) is
cwd-relative.  It also exports ``$DATAAGENT_DATA_DIR`` so the modules
that resolve their paths independently (the JWT secret, the grant key)
agree with it.

When ``$DATAAGENT_DATA_DIR`` was already set in the environment, only
the resolution honoured it: the app chdir'd into
``%LOCALAPPDATA%\\DataAgent`` (so the warehouse, the config and the logs
lived there) while the key files were written to the override.  Two
halves of one install in two places, and a re-minted JWT secret on every
launch — which logs the user out and invalidates every paid grant.

These tests pin that the override decides BOTH the chdir and the
exported variable, and that the default is still the per-user dir.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    """A frozen-like environment with no inherited runtime dir."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "stdout", None, raising=False)
    monkeypatch.setattr(sys, "stderr", None, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("DATAAGENT_DATA_DIR", raising=False)
    return tmp_path


def _run_bootstrap(cwd):
    import run

    original = Path.cwd()
    os.chdir(cwd)
    try:
        run._frozen_bootstrap()
        return Path.cwd(), os.environ.get("DATAAGENT_DATA_DIR")
    finally:
        os.chdir(original)


class TestDefaultRuntimeDir:
    def test_the_default_is_the_per_user_dir(self, clean_env, monkeypatch):
        cwd, exported = _run_bootstrap(clean_env)
        try:
            expected = clean_env / "localappdata" / "DataAgent"
            assert Path(exported) == expected
            assert Path(cwd).resolve() == expected.resolve()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_the_runtime_subdirs_are_created(self, clean_env):
        _run_bootstrap(clean_env)
        try:
            base = Path(os.environ["DATAAGENT_DATA_DIR"])
            for sub in ("data/raw", "data/staging", "data/curated",
                        "logs", "outputs/charts"):
                assert (base / sub).is_dir(), f"{sub} was not created"
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)


class TestOverrideWins:
    def test_the_override_decides_the_chdir_and_the_export(self, clean_env, monkeypatch):
        override = clean_env / "portable"
        monkeypatch.setenv("DATAAGENT_DATA_DIR", str(override))
        cwd, exported = _run_bootstrap(clean_env)
        try:
            assert Path(exported) == override
            assert Path(cwd).resolve() == override.resolve()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_the_key_files_land_in_the_same_place_as_the_warehouse(self, clean_env, monkeypatch):
        """One install, one directory."""
        override = clean_env / "portable"
        monkeypatch.setenv("DATAAGENT_DATA_DIR", str(override))
        _run_bootstrap(clean_env)
        try:
            from api.runtime_paths import runtime_data_dir, runtime_file

            assert runtime_data_dir().resolve() == override.resolve()
            secret = runtime_file("data", ".jwt_secret")
            assert secret.parent.is_relative_to(override.resolve())
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)
