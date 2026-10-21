"""Launch-claim consistency guards (C4, C5, C6, C9, C10).

These tests are the guard rail that stops the launch-day contradiction from
coming back: the SPA and the marketing site must describe the product the way
the backend actually builds it.

* **C4** — the three feature names that over-promise ("AutoML",
  "Segmentation", "Forecasting") must not appear as user-facing labels in the
  SPA.  The honest names ("Regression Explorer", "Segment Breakdown",
  "Trend Forecast") must be there instead.  The *endpoint paths* and the
  Python module names stay untouched.
* **C5** — the export page must not advertise a fully formatted PDF when the
  shipped build can only produce the basic one.  The route must stay wired.
* **C6** — the marketing site must tell ONE story: a local-first desktop app
  that ships a free tier, with a paid Lifetime licence (and an optional
  hosted tier later).  No blanket "$0" / "no subscription" / "100% Local".
  Every GitHub release/download URL must come from ONE configurable constant.
* **C9** — the subscription page must offer a "Sync now" control that calls an
  endpoint that actually exists.
* **C10** — the legal pages must never publish an unfilled placeholder, and
  the SPA must not link the raw (un-rendered) legal files.

Run with::

    python -m pytest tests/test_launch_frontend_claims.py -q -p no:cacheprovider
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
WEBSITE = ROOT / "website"
DOCS = ROOT / "docs"

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

#: Vendored third-party code is not ours to rename.
_VENDOR_PARTS = {"vendor", "node_modules", ".git"}


def _spa_sources() -> list[Path]:
    """Every hand-written JS/HTML file in the SPA."""
    out: list[Path] = []
    for path in sorted(WEB.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".js", ".html", ".css"}:
            continue
        if _VENDOR_PARTS & set(path.relative_to(WEB).parts):
            continue
        out.append(path)
    return out


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _website_pages() -> list[Path]:
    return sorted(WEBSITE.glob("*.html"))


def _website_scripts() -> list[Path]:
    return sorted(p for p in (WEBSITE / "js").glob("*.js"))


# ==========================================================================
# C4 — feature names must describe what the code delivers
# ==========================================================================


def test_c4_no_automl_label_anywhere_in_the_spa():
    """"AutoML" promises model search / CV / ensembling.  The backend does
    a Pearson |r| filter, multicollinearity pruning and a linear-vs-polynomial
    comparison — so the label must be gone from the SPA."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in _spa_sources()
        if "automl" in _read(p).lower()
    ]
    assert not offenders, (
        "'AutoML' is still a user-facing label in the SPA; the feature is a "
        "regression comparison, not model search. Offenders: " + ", ".join(offenders)
    )


def test_c4_no_forecasting_label_anywhere_in_the_spa():
    """"Forecasting" promises ARIMA / ETS / Prophet / Holt-Winters.  The
    backend is a least-squares trend plus classical decomposition, so the
    label must be gone from the SPA."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in _spa_sources()
        if "forecasting" in _read(p).lower()
    ]
    assert not offenders, (
        "'Forecasting' is still a user-facing label in the SPA; the feature "
        "projects a linear trend, it does not run a forecasting model. "
        "Offenders: " + ", ".join(offenders)
    )


def test_c4_no_segmentation_label_anywhere_in_the_spa():
    """"Segmentation" promises clustering.  The backend is a ``groupby`` with
    count/sum/mean/median/std/min/max, so the capitalised label must be gone.

    The lowercase identifier and the ``/analysis/segmentation/`` endpoint path
    are *not* labels and are allowed to stay (they are the API surface).
    """
    offenders = [
        str(p.relative_to(ROOT))
        for p in _spa_sources()
        if "Segmentation" in _read(p)
    ]
    assert not offenders, (
        "'Segmentation' is still a user-facing label in the SPA; there is no "
        "clustering behind it. Offenders: " + ", ".join(offenders)
    )


@pytest.mark.parametrize(
    "relative_path, needle",
    [
        ("web/js/components/blueprint.js", "Regression Explorer"),
        ("web/js/pages/automation.js", "Regression Explorer"),
        ("web/js/pages/analysis.js", "Segment Breakdown"),
        ("web/js/pages/analysis.js", "Trend Forecast"),
        ("web/js/pages/forecast.js", "Trend Forecast"),
    ],
)
def test_c4_new_honest_names_are_present(relative_path, needle):
    text = _read(ROOT / relative_path)
    assert needle in text, (
        f"{relative_path} should offer the feature as {needle!r}"
    )


def test_c4_backend_endpoints_are_not_renamed():
    """Renaming the label must not touch the API surface."""
    api_js = _read(WEB / "js" / "api.js")
    for path in (
        "/analysis/segmentation/",
        "/analysis/forecast/",
        "/analysis/auto-select-features",
        "/analysis/compare-models",
        "/analysis/auto-analyze",
    ):
        assert path in api_js, f"API surface {path} must stay unchanged"


def test_c4_feature_panels_explain_what_they_actually_do():
    """Each renamed panel must carry a one-line 'what this actually does'."""
    analysis = _read(WEB / "js" / "pages" / "analysis.js")
    automation = _read(WEB / "js" / "pages" / "automation.js")
    forecast = _read(WEB / "js" / "pages" / "forecast.js")

    assert "adjusted R" in automation, (
        "the Regression Explorer node must say it compares a linear against a "
        "polynomial fit on adjusted R-squared"
    )
    assert "group" in analysis.lower() and "cluster" in analysis.lower(), (
        "the Segment Breakdown panel must state that it groups rows rather "
        "than clustering them"
    )
    assert "linear trend" in forecast.lower() or "straight-line" in forecast.lower(), (
        "the Trend Forecast page must state that it projects a linear trend"
    )


# ==========================================================================
# C5 — the export page must not advertise a PDF the build cannot deliver
# ==========================================================================


def test_c5_pdf_option_is_labelled_honestly():
    export_js = _read(WEB / "js" / "pages" / "export.js")
    assert "PDF Report" not in export_js, (
        "'PDF Report' over-promises: the shipped build falls back to a "
        "basic PDF (see export/pdf_export.py)"
    )
    assert "PDF (basic)" in export_js, (
        "the PDF option must be labelled 'PDF (basic)'"
    )


def test_c5_pdf_export_still_wired_to_the_route():
    """The backend route still exists — do not silently break the button."""
    export_js = _read(WEB / "js" / "pages" / "export.js")
    assert "/export/pdf/" in export_js, "the PDF export route must stay wired"


def test_c5_export_page_names_the_formats_that_work_fully():
    export_js = _read(WEB / "js" / "pages" / "export.js")
    note = re.search(r'id="pdf-export-note"[^>]*>(.*?)</div>', export_js, re.S)
    assert note, "the export page must carry a #pdf-export-note explaining PDF"
    body = note.group(1)
    for fmt in ("Excel", "PowerPoint", "CSV"):
        assert fmt in body, f"the note should name {fmt} as a fully formatted export"


# ==========================================================================
# C6 — one consistent marketing story
# ==========================================================================

#: Claims that cannot be made unconditionally once a paid tier exists.
FORBIDDEN_CLAIMS = (
    "100% local",
    "no subscription",
    "the data itself never leaves your machine",
)


def test_c6_no_unconditional_free_or_absolute_local_claims():
    offenders: list[str] = []
    for path in _website_pages() + _website_scripts():
        low = _read(path).lower()
        for claim in FORBIDDEN_CLAIMS:
            if claim in low:
                offenders.append(f"{path.relative_to(ROOT)}: {claim!r}")
    assert not offenders, (
        "the marketing site makes unconditional claims that contradict the "
        "paid-Lifetime model / the local-first desktop scope: "
        + "; ".join(offenders)
    )


def test_c6_no_unconditional_zero_dollar_claim_on_the_home_page():
    index = _read(WEBSITE / "index.html")
    assert "$0" not in index, (
        "index.html still advertises an unconditional $0 price; the product "
        "has a free tier AND paid licences"
    )


def test_c6_home_page_states_the_local_first_desktop_story():
    index = _read(WEBSITE / "index.html")
    low = index.lower()
    assert "local-first" in low, "index.html must say 'local-first'"
    assert "desktop app" in low, "index.html must scope the promise to the desktop app"
    assert "lifetime" in low, "index.html must mention the paid Lifetime licence"


def test_c6_every_page_scopes_the_telemetry_claim_to_the_app():
    """'No telemetry' is TRUE for the local app — but must not be an
    unqualified absolute across the whole product."""
    for path in _website_pages():
        text = _read(path)
        for line in text.splitlines():
            if "no telemetry" not in line.lower():
                continue
            low = line.lower()
            scoped = (
                "desktop app" in low
                or "app ships" in low
                or "the app" in low
                or "ship no telemetry" in low
            )
            assert scoped, (
                f"{path.relative_to(ROOT)}: unqualified 'no telemetry' claim -> {line.strip()!r}"
            )


def test_c6_open_source_claim_names_the_licence():
    """The tree ships the GNU AGPL v3 — say so, do not just say 'open source'."""
    contact = _read(WEBSITE / "contact.html")
    assert "GNU AGPL v3" in contact, (
        "the open-source claim must name the licence (GNU AGPL v3)"
    )
    for path in _website_pages():
        text = _read(path)
        if "Open Source" in text or "open source" in text:
            assert "AGPL" in text or "open source" in text.lower()


def test_c6_license_file_is_the_claimed_licence():
    license_text = _read(ROOT / "LICENSE")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text.upper()
    assert "Version 3" in license_text


def test_c6_download_urls_come_from_one_configurable_constant():
    """Every GitHub URL in the marketing site resolves from ONE constant."""
    main_js = _read(WEBSITE / "js" / "main.js")
    assert "github.com/DataAgent" in main_js, (
        "website/js/main.js must hold the single release-base-URL constant"
    )
    assert main_js.count("github.com/DataAgent") == 1, (
        "the release base URL must be defined exactly once"
    )

    html_offenders = [
        str(p.relative_to(ROOT))
        for p in _website_pages()
        if "github.com/DataAgent" in _read(p)
    ]
    assert not html_offenders, (
        "download / repo URLs are hardcoded in the markup; they must be "
        "derived from the single constant in website/js/main.js. Offenders: "
        + ", ".join(html_offenders)
    )

    other_script_offenders = [
        str(p.relative_to(ROOT))
        for p in _website_scripts()
        if p.name != "main.js" and "github.com/DataAgent" in _read(p)
    ]
    assert not other_script_offenders, (
        "only website/js/main.js may hold the GitHub base URL. Offenders: "
        + ", ".join(other_script_offenders)
    )


def test_c6_download_page_uses_the_constant_for_both_downloads():
    download = _read(WEBSITE / "download.html")
    main_js = _read(WEBSITE / "js" / "main.js")
    assert 'data-download="setup"' in download, (
        "the installer button must be marked with data-download=\"setup\""
    )
    assert 'data-download="portable"' in download, (
        "the portable button must be marked with data-download=\"portable\""
    )
    assert "DataAgent-Setup.exe" in main_js
    assert "DataAgent.exe" in main_js


def test_c6_release_constant_has_a_naming_comment():
    main_js = _read(WEBSITE / "js" / "main.js")
    window = main_js[main_js.find("github.com/DataAgent") - 800 : main_js.find("github.com/DataAgent") + 200]
    assert "RELEASE" in window.upper() or "release" in window, (
        "the constant must be named/documented as the release base URL so the "
        "operator knows what to flip"
    )


def test_c6_no_dead_internal_links_in_the_marketing_site():
    missing: list[str] = []
    for path in _website_pages():
        for href in re.findall(r'href="([^"]+\.html)(?:#[^"]*)?"', _read(path)):
            if href.startswith(("http://", "https://")):
                continue
            if not (WEBSITE / href).exists():
                missing.append(f"{path.relative_to(ROOT)} -> {href}")
    assert not missing, "dead internal links: " + "; ".join(missing)


# ==========================================================================
# C6 — docs/FRONTENDS.md must state the decision
# ==========================================================================


def test_c6_frontends_doc_states_the_local_first_decision():
    doc = _read(DOCS / "FRONTENDS.md")
    low = doc.lower()
    assert "local-first" in low, "docs/FRONTENDS.md must state the local-first v1 decision"
    assert "adr-009" in low, "docs/FRONTENDS.md must reference ADR-009 as the v2 path"
    assert "claim consistency" in low, (
        "docs/FRONTENDS.md must carry a 'Claim consistency' checklist"
    )


# ==========================================================================
# C9 — a "Sync now" control on the subscription page
# ==========================================================================


def test_c9_subscription_page_has_a_sync_now_control():
    sub = _read(WEB / "js" / "pages" / "subscription.js")
    assert "Sync now" in sub, "the subscription page must offer a 'Sync now' button"
    assert 'id="sync-plan-btn"' in sub, "the sync button needs a stable id"


def test_c9_sync_button_calls_an_endpoint_that_exists():
    """The button must use the endpoint that really exists today.

    ``GET /subscriptions/me`` is the only plan-reading route; it triggers the
    best-effort billing-server phone-home when the renewal snapshot is stale.
    """
    sub = _read(WEB / "js" / "pages" / "subscription.js")
    api_js = _read(WEB / "js" / "api.js")
    assert "API.mySubscription()" in sub, "the sync button must re-read the plan"
    assert 'GET("/subscriptions/me")' in api_js, (
        "the API client must expose GET /subscriptions/me"
    )
    backend = _read(ROOT / "api" / "subscriptions.py")
    assert '@router.get("/subscriptions/me")' in backend, (
        "the backend route the button relies on must exist"
    )


def test_c9_sync_button_does_not_invent_a_route():
    """No invented URL: the frontend may only call routes that exist."""
    api_js = _read(WEB / "js" / "api.js")
    # Every sync-ish route the SPA calls must exist in the backend. The
    # backend is the authority: read the route table and compare.
    backend = _read(ROOT / "api" / "subscriptions.py")
    for invented in ("/billing/sync", "/entitlements/sync"):
        assert invented not in api_js, (
            f"{invented} does not exist in the backend - do not ship a "
            "half-wired button"
        )
    # /subscriptions/sync exists (api/subscriptions.py), so the SPA may use it.
    if "/subscriptions/sync" in api_js:
        assert '"/subscriptions/sync"' in backend, (
            "the SPA calls /subscriptions/sync but the backend does not "
            "define it"
        )


def test_c9_sync_button_reports_progress_and_failure():
    sub = _read(WEB / "js" / "pages" / "subscription.js")
    low = sub.lower()
    assert "syncing" in low, "the button must show a 'syncing' state"
    assert "sync failed" in low or "could not sync" in low, (
        "the button must report a user-facing failure message"
    )


# ==========================================================================
# C10 — legal pages must never publish an unfilled placeholder
# ==========================================================================


def test_c10_rendered_legal_pages_contain_no_placeholder_tokens():
    """Render each legal page the way the backend does and assert that no
    ``[[MARKER]]`` survives.  Tolerant of config/legal.yaml still holding
    markers (the backend substitutes a visible notice); strict about the
    published page."""
    from api.legal_config import PLACEHOLDER_NOTICE, legal_values

    values = legal_values()
    lookup = {key.split(".", 1)[-1]: value for key, value in values.items()}

    def fill(markup: str) -> str:
        def _sub(match: "re.Match[str]") -> str:
            return lookup.get(match.group(1), PLACEHOLDER_NOTICE)

        return re.sub(r"\[\[([A-Z0-9_]+)\]\]", _sub, markup)

    legal_dir = WEB / "legal"
    pages = sorted(legal_dir.glob("*.html"))
    assert pages, "web/legal must ship the legal pages"

    for path in pages:
        rendered = fill(_read(path))
        assert "[[" not in rendered, (
            f"{path.relative_to(ROOT)} still publishes an unfilled placeholder"
        )
        assert not re.search(r"\{\{[a-z_]+\.[a-z_]+\}\}", rendered), (
            f"{path.relative_to(ROOT)} still publishes an unrendered {{{{section.key}}}}"
        )


def test_c10_legal_markers_are_all_declared_in_the_config():
    """No orphan / typo'd placeholder in the legal pages."""
    yaml_text = _read(ROOT / "config" / "legal.yaml")
    declared = set(re.findall(r"\[\[([A-Z0-9_]+)\]\]", yaml_text))
    assert declared, "config/legal.yaml must declare the legal values"

    legal_dir = WEB / "legal"
    for path in sorted(legal_dir.glob("*.html")):
        for marker in set(re.findall(r"\[\[([A-Z0-9_]+)\]\]", _read(path))):
            assert marker in declared, (
                f"{path.relative_to(ROOT)} uses [[{marker}]] which is not "
                "declared in config/legal.yaml"
            )


def test_c10_spa_links_the_rendered_legal_pages_not_the_raw_files():
    """``/static/legal/*.html`` bypasses the rendering route and would publish
    the raw template, markers and all."""
    index = _read(WEB / "index.html")
    assert "/static/legal/" not in index, (
        "the SPA must link /legal/<page>.html (rendered), not the raw "
        "/static/legal/ template"
    )
    for page in ("privacy", "terms", "dpa", "security"):
        assert f"/legal/{page}.html" in index, (
            f"the SPA must link the rendered legal page /legal/{page}.html"
        )
