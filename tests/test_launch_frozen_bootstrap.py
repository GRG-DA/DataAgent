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


def _expected_per_user_dir(root: Path) -> Path:
    """The per-user dir for THIS platform (the test suite runs on Linux too)."""
    if sys.platform == "win32":
        return root / "localappdata" / "DataAgent"
    return root / "xdg" / "DataAgent"


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
            expected = _expected_per_user_dir(clean_env)
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


class TestBundleOverrideIsRefused:
    """The runtime dir may NEVER live inside the PyInstaller bundle.

    ``api/runtime_paths.py`` refuses an override that resolves inside
    ``sys._MEIPASS`` — the bundle is inside ``C:\\Program Files`` and a
    normal user cannot write there, so a key file written into it is
    lost and silently re-minted on every launch.

    ``run.py::_frozen_bootstrap`` re-derived the base directory on its
    own and skipped that rule, so it chdir'd INTO the bundle and
    exported it as the runtime dir: the warehouse, the config and the
    logs lived in the bundle while the JWT secret and the grant key
    lived in ``%LOCALAPPDATA%\\DataAgent`` — two halves of one install.

    There must be exactly ONE rule about where the runtime dir may be,
    and it is the one ``api.runtime_paths`` owns.
    """

    def test_an_override_pointing_into_the_bundle_is_refused(self, clean_env, monkeypatch):
        bundle = clean_env / "_internal"
        (bundle / "data").mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        monkeypatch.setenv("DATAAGENT_DATA_DIR", str(bundle / "data"))

        cwd, exported = _run_bootstrap(clean_env)
        try:
            resolved = Path(exported).resolve()
            assert not resolved.is_relative_to(bundle.resolve()), (
                "the runtime dir was placed inside the PyInstaller bundle: "
                f"{resolved}"
            )
            assert Path(cwd).resolve() == resolved
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_the_refused_override_falls_back_to_the_per_user_dir(self, clean_env, monkeypatch):
        bundle = clean_env / "_internal"
        (bundle / "data").mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        monkeypatch.setenv("DATAAGENT_DATA_DIR", str(bundle / "data"))

        cwd, exported = _run_bootstrap(clean_env)
        try:
            expected = _expected_per_user_dir(clean_env)
            assert Path(exported).resolve() == expected.resolve()
            assert Path(cwd).resolve() == expected.resolve()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_the_bootstrap_agrees_with_the_runtime_path_resolver(self, clean_env, monkeypatch):
        """One rule: the bootstrap and the resolver must not disagree."""
        bundle = clean_env / "_internal"
        (bundle / "data").mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        monkeypatch.setenv("DATAAGENT_DATA_DIR", str(bundle / "data"))

        _run_bootstrap(clean_env)
        try:
            from api.runtime_paths import runtime_data_dir

            assert runtime_data_dir().resolve() == Path(
                os.environ["DATAAGENT_DATA_DIR"]
            ).resolve()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)


def _bundle_with_settings(root: Path, settings_text: str) -> Path:
    bundle = root / "_internal"
    cfg = bundle / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.yaml").write_text(settings_text, encoding="utf-8")
    return bundle


def _user_settings_path(root: Path) -> Path:
    return _expected_per_user_dir(root) / "config" / "settings.yaml"


def _seed_user_settings(root: Path, settings_text: str) -> Path:
    path = _user_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings_text, encoding="utf-8")
    return path


class TestBillingSyncBackfill:
    def test_empty_billing_values_are_filled_and_comments_survive(self, clean_env, monkeypatch):
        bundle = _bundle_with_settings(clean_env, """
billing:
  external_url: "https://data-agent.me/billing.html"
  sync_url: "https://pay.data-agent.me"
  sync_secret: "s3cr3t:with#chars"
""")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        user_path = _seed_user_settings(clean_env, """# keep this comment
billing:
  # checkout lives on the website
  external_url: ""
  sync_url: ""
  sync_secret: ""
  tenant_ai_daily_cap: 100
""")

        _run_bootstrap(clean_env)
        try:
            import yaml

            text = user_path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            assert "# keep this comment" in text
            assert "# checkout lives on the website" in text
            assert data["billing"]["external_url"] == "https://data-agent.me/billing.html"
            assert data["billing"]["sync_url"] == "https://pay.data-agent.me"
            assert data["billing"]["sync_secret"] == "s3cr3t:with#chars"
            assert data["billing"]["tenant_ai_daily_cap"] == 100
            assert (user_path.parent / ".billing_sync_defaults_applied").is_file()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_custom_values_are_never_overwritten(self, clean_env, monkeypatch):
        bundle = _bundle_with_settings(clean_env, """
billing:
  external_url: "https://data-agent.me/billing.html"
  sync_url: "https://pay.data-agent.me"
  sync_secret: "baked-secret"
""")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        user_path = _seed_user_settings(clean_env, """
billing:
  external_url: "https://custom.example/billing.html"
  sync_url: ""
  sync_secret: "customer-secret"
""")

        _run_bootstrap(clean_env)
        try:
            import yaml

            data = yaml.safe_load(user_path.read_text(encoding="utf-8"))
            assert data["billing"]["external_url"] == "https://custom.example/billing.html"
            assert data["billing"]["sync_url"] == "https://pay.data-agent.me"
            assert data["billing"]["sync_secret"] == "customer-secret"
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_backfill_runs_only_once(self, clean_env, monkeypatch):
        bundle = _bundle_with_settings(clean_env, """
billing:
  sync_url: "https://pay.data-agent.me"
  sync_secret: "baked-secret"
""")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        user_path = _seed_user_settings(clean_env, """
billing:
  sync_url: ""
  sync_secret: ""
""")
        _run_bootstrap(clean_env)
        try:
            import yaml

            user_path.write_text(
                'billing:\n  sync_url: ""\n  sync_secret: ""\n',
                encoding="utf-8",
            )
            _run_bootstrap(clean_env)
            data = yaml.safe_load(user_path.read_text(encoding="utf-8"))
            assert data["billing"]["sync_url"] == ""
            assert data["billing"]["sync_secret"] == ""
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_block_style_without_a_trailing_newline_is_patched_safely(
        self, clean_env, monkeypatch
    ):
        bundle = _bundle_with_settings(clean_env, """
billing:
  sync_url: "https://pay.data-agent.me"
  sync_secret: "baked-secret"
""")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        user_path = _seed_user_settings(clean_env, 'billing:\n  sync_url: ""\n  sync_secret: ""')

        _run_bootstrap(clean_env)
        try:
            import yaml

            text = user_path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            assert data["billing"]["sync_url"] == "https://pay.data-agent.me"
            assert data["billing"]["sync_secret"] == "baked-secret"
            assert text.endswith("\n")
            assert text.count("billing:") == 1
            assert (user_path.parent / ".billing_sync_defaults_applied").is_file()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_quoted_billing_key_is_left_untouched(self, clean_env, monkeypatch):
        bundle = _bundle_with_settings(clean_env, """
billing:
  sync_url: "https://pay.data-agent.me"
  sync_secret: "baked-secret"
""")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        quoted = '"billing":\n  sync_url: ""\n  sync_secret: ""\n'
        user_path = _seed_user_settings(clean_env, quoted)

        _run_bootstrap(clean_env)
        try:
            assert user_path.read_text(encoding="utf-8") == quoted
            assert not (user_path.parent / ".billing_sync_defaults_applied").exists()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_flow_style_billing_is_left_untouched(self, clean_env, monkeypatch):
        bundle = _bundle_with_settings(clean_env, """
billing:
  sync_url: "https://pay.data-agent.me"
  sync_secret: "baked-secret"
""")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        flow = 'billing: {external_url: "", sync_url: "", sync_secret: "customer"}\n'
        user_path = _seed_user_settings(clean_env, flow)

        _run_bootstrap(clean_env)
        try:
            assert user_path.read_text(encoding="utf-8") == flow
            assert not (user_path.parent / ".billing_sync_defaults_applied").exists()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)

    def test_invalid_user_yaml_is_left_untouched(self, clean_env, monkeypatch):
        bundle = _bundle_with_settings(clean_env, """
billing:
  sync_url: "https://pay.data-agent.me"
  sync_secret: "baked-secret"
""")
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        broken = "billing: [unclosed\n"
        user_path = _seed_user_settings(clean_env, broken)

        _run_bootstrap(clean_env)
        try:
            assert user_path.read_text(encoding="utf-8") == broken
            assert not (user_path.parent / ".billing_sync_defaults_applied").exists()
        finally:
            os.environ.pop("DATAAGENT_DATA_DIR", None)
