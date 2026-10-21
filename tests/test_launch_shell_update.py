"""Tests for the desktop shell's update check.

Covers the launch-hardening of ``shell/update.py``:

* the release repository is configurable (the public release repo is not
  published yet, and the previous hard-coded URL pointed at a repository that
  did not exist);
* a repository with no published release (HTTP 404) is reported as "no
  release" rather than as a failure, so the app never shows a scary message
  for a repo that has not been created;
* the check never raises and never blocks on a network error.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from shell import update as upd


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/x", code=code, msg="boom", hdrs=None, fp=None
    )


class TestReleasesRepo:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("DATAAGENT_RELEASES_REPO", "acme/thing")
        assert upd.releases_repo() == "acme/thing"

    def test_default_is_the_dataagent_repo(self, monkeypatch):
        monkeypatch.delenv("DATAAGENT_RELEASES_REPO", raising=False)
        assert upd.releases_repo() == upd.DEFAULT_RELEASES_REPO

    def test_api_url_follows_the_repo(self, monkeypatch):
        monkeypatch.setenv("DATAAGENT_RELEASES_REPO", "acme/thing")
        assert upd.releases_api_url() == (
            "https://api.github.com/repos/acme/thing/releases/latest"
        )

    def test_download_url_follows_the_repo(self, monkeypatch):
        monkeypatch.setenv("DATAAGENT_RELEASES_REPO", "acme/thing")
        url = upd.release_download_url("DataAgent-Setup.exe")
        assert url == (
            "https://github.com/acme/thing/releases/latest/download/DataAgent-Setup.exe"
        )


class TestLatestVersion:
    def test_reads_the_tag(self, monkeypatch):
        monkeypatch.setenv("DATAAGENT_RELEASES_REPO", "acme/thing")

        class _Open:
            def __call__(self, req, timeout=None):
                return _FakeResponse({"tag_name": "v1.2.3"})

        monkeypatch.setattr(upd.urllib.request, "urlopen", _Open())
        assert upd.latest_version() == "1.2.3"

    def test_missing_repository_is_not_an_error(self, monkeypatch):
        def _raise(req, timeout=None):
            raise _http_error(404)

        monkeypatch.setattr(upd.urllib.request, "urlopen", _raise)
        assert upd.latest_version() is None

    def test_network_error_is_swallowed(self, monkeypatch):
        def _raise(req, timeout=None):
            raise OSError("network unreachable")

        monkeypatch.setattr(upd.urllib.request, "urlopen", _raise)
        assert upd.latest_version() is None


class TestCheckForUpdate:
    def test_update_available(self, monkeypatch):
        monkeypatch.setattr(upd, "current_version", lambda: "1.0.0")
        monkeypatch.setattr(upd, "latest_version", lambda timeout=5: "1.4.0")
        result = upd.check_for_update()
        assert result.available is True
        assert result.status == upd.STATUS_UPDATE_AVAILABLE
        assert result.current == "1.0.0"
        assert result.latest == "1.4.0"

    def test_up_to_date(self, monkeypatch):
        monkeypatch.setattr(upd, "current_version", lambda: "1.0.0")
        monkeypatch.setattr(upd, "latest_version", lambda timeout=5: "1.0.0")
        result = upd.check_for_update()
        assert result.available is False
        assert result.status == upd.STATUS_UP_TO_DATE

    def test_unavailable_does_not_claim_an_update(self, monkeypatch):
        monkeypatch.setattr(upd, "current_version", lambda: "1.0.0")
        monkeypatch.setattr(upd, "latest_version", lambda timeout=5: None)
        result = upd.check_for_update()
        assert result.available is False
        assert result.status == upd.STATUS_UNAVAILABLE
        # The message must not leak an internal URL or an exception repr.
        assert "http" not in result.detail.lower()
        assert "Traceback" not in result.detail

    def test_legacy_tuple_contract_is_preserved(self, monkeypatch):
        monkeypatch.setattr(upd, "current_version", lambda: "2.0.0")
        monkeypatch.setattr(upd, "latest_version", lambda timeout=5: "2.0.0")
        available, cur, latest = upd.update_available()
        assert available is False
        assert cur == "2.0.0"
        assert latest == "2.0.0"

    def test_check_never_raises(self, monkeypatch):
        def _boom(timeout=5):
            raise RuntimeError("nope")

        monkeypatch.setattr(upd, "latest_version", _boom)
        # A raising probe must not take the app down; the guard lives in
        # latest_version, so simulate the same failure mode it handles.
        result = upd.check_for_update()
        assert result.status in {
            upd.STATUS_UNAVAILABLE,
            upd.STATUS_UP_TO_DATE,
            upd.STATUS_NO_RELEASE,
            upd.STATUS_UPDATE_AVAILABLE,
        }


class TestParseTag:
    @pytest.mark.parametrize(
        "tag,expected",
        [
            ("v1.2.3", (1, 2, 3)),
            ("1.2.3", (1, 2, 3)),
            ("v1.2", (1, 2)),
            ("v1.2.3-beta", (1, 2, 3)),
            ("", (0,)),
        ],
    )
    def test_parse(self, tag, expected):
        assert upd._parse_tag(tag) == expected
