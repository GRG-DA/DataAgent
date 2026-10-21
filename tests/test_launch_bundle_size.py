"""The release bundle must not ship 100 MB of a feature that cannot work.

``playwright`` is NOT in ``requirements.txt``.  It is imported only inside
``export/pdf_export.py::_html_to_pdf_playwright``, which is guarded and
falls back to reportlab.  But PyInstaller's dependency analysis pulled the
package into the bundle anyway — 102 MB (``playwright/driver/node.exe``)
of dead weight in a ~400 MB installer.

Worse, the bundle does NOT contain the Chromium binaries: ``playwright
install chromium`` downloads them to ``%LOCALAPPDATA%\\ms-playwright``,
not into the bundle.  So the bundled path fails at runtime with
"Executable doesn't exist".  The PDF export falls back to reportlab and
says so — which is the honest behaviour we shipped.

``kaleido`` and ``plotly`` are NOT excluded: the agent's chart tool
(``agent/tools_viz.py``) writes PNGs through plotly's ``write_image``,
which needs kaleido.

These tests pin the exclusion so a future dependency change cannot quietly
put the 100 MB back.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _spec_excludes(path: Path) -> set:
    """The module names a PyInstaller spec excludes."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"excludes=\s*\[(.*?)\]", text, re.S)
    assert match, f"{path} has no excludes list"
    return set(re.findall(r"['\"]([A-Za-z0-9_]+)['\"]", match.group(1)))


def test_the_desktop_shell_spec_excludes_playwright():
    excludes = _spec_excludes(ROOT / "scripts" / "DataAgentShell.spec")
    assert "playwright" in excludes, (
        "playwright must be excluded from the desktop bundle: it is not in "
        "requirements.txt, the bundle does not contain its Chromium binaries, "
        "and it costs 102 MB of download"
    )


def test_the_legacy_onefile_spec_excludes_playwright():
    excludes = _spec_excludes(ROOT / "DataAgent.spec")
    assert "playwright" in excludes


def test_the_onefile_cli_build_excludes_playwright():
    ps1 = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "--exclude-module playwright" in ps1


def test_kaleido_and_plotly_are_NOT_excluded():
    """The agent's chart tool writes PNGs through plotly's write_image."""
    assert "from plotly" in (ROOT / "agent" / "tools_viz.py").read_text(encoding="utf-8") \
        or "import plotly" in (ROOT / "agent" / "tools_viz.py").read_text(encoding="utf-8")

    for spec in (ROOT / "scripts" / "DataAgentShell.spec", ROOT / "DataAgent.spec"):
        excludes = _spec_excludes(spec)
        assert "kaleido" not in excludes, (
            f"{spec.name} excludes kaleido, but agent/tools_viz.py needs it "
            "for write_image"
        )
        assert "plotly" not in excludes, (
            f"{spec.name} excludes plotly, but agent/tools_viz.py imports it"
        )


def test_kaleido_stays_in_the_requirements():
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "kaleido" in reqs, (
        "kaleido must stay in requirements.txt - agent/tools_viz.py writes "
        "PNGs through plotly's write_image"
    )


def test_playwright_is_not_in_the_requirements():
    """It is excluded from the bundle, so it must not be a declared dep."""
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert not re.search(r"^\s*playwright", reqs, re.M | re.I)
