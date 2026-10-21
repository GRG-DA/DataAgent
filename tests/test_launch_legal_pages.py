"""Tests for the legal pages.

The pages under ``web/legal/`` carry ``[[MARKER]]`` placeholders that are
filled from ``config/legal.yaml`` when the page is served.

The bug this pins: the pages use screaming-snake markers
(``[[COMPANY_NAME]]``) and the config uses dotted paths (``company.name``).
The lookup used to be derived from the config key's last segment, which
matched NONE of the markers, so every legal page published
"[to be completed by the operator]" in all 38 places even with a fully
filled config.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api.routes import legal as legal_route
from api.legal_config import legal_values, unfilled_legal_markers

ROOT = Path(__file__).resolve().parent.parent
LEGAL_DIR = ROOT / "web" / "legal"
PAGES = ("privacy", "terms", "dpa", "security")

MARKER_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")


def _markers_in(page: str) -> set:
    markup = (LEGAL_DIR / f"{page}.html").read_text(encoding="utf-8")
    return {m.group(1) for m in MARKER_RE.finditer(markup)}


class TestMarkerTable:
    @pytest.mark.parametrize("page", PAGES)
    def test_every_marker_used_by_a_page_has_a_config_key(self, page):
        """A marker with no key would silently render the placeholder."""
        missing = sorted(_markers_in(page) - set(legal_route.MARKER_KEYS))
        assert not missing, (
            f"web/legal/{page}.html uses markers with no key in "
            f"api/routes/legal.py::MARKER_KEYS: {missing}"
        )

    @pytest.mark.parametrize("page", PAGES)
    def test_every_config_key_exists_in_the_yaml(self, page):
        values = legal_values()
        for marker in sorted(_markers_in(page)):
            key = legal_route.MARKER_KEYS[marker]
            assert key in values, f"{marker} -> {key} is not in config/legal.yaml"


class TestFilling:
    def test_a_marker_is_replaced_with_its_value(self):
        out = legal_route._fill("<p>[[COMPANY_NAME]]</p>")
        assert "[[COMPANY_NAME]]" not in out
        assert "DataAgent" in out

    @pytest.mark.parametrize("page", PAGES)
    def test_no_marker_survives_the_fill(self, page):
        markup = (LEGAL_DIR / f"{page}.html").read_text(encoding="utf-8")
        out = legal_route._fill(markup)
        assert "[[" not in out, f"web/legal/{page}.html still has a raw marker"

    @pytest.mark.parametrize("page", PAGES)
    def test_an_unknown_marker_becomes_the_notice_not_a_raw_marker(self, page):
        markup = (LEGAL_DIR / f"{page}.html").read_text(encoding="utf-8")
        out = legal_route._fill(markup)
        assert "[[" not in out
        assert "to be completed by the operator" not in out, (
            f"web/legal/{page}.html still shows the placeholder notice"
        )

    def test_the_config_is_the_single_source_of_truth(self):
        """Changing the yaml changes the rendered page."""
        values = legal_values()
        out = legal_route._fill(f"<p>{values['company.name']}</p>")
        assert values["company.name"] in out


class TestConfig:
    def test_no_placeholder_marker_remains_in_the_yaml(self):
        assert unfilled_legal_markers() == []

    def test_every_value_the_pages_need_is_present(self):
        values = legal_values()
        for marker, key in legal_route.MARKER_KEYS.items():
            assert key in values
            assert values[key], f"{marker} -> {key} resolved to an empty value"
