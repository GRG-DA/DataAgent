"""
Regression tests for the UI redesign.

These lock in the bugs that were found and fixed during the DataAgent
frontend redesign. Each test names the specific defect it prevents.

Run with:  python -m pytest tests/test_ui_design_system.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CSS_DIR = WEB / "css"
STYLES = CSS_DIR / "styles.css"
INDEX = WEB / "index.html"
FA_CSS = WEB / "vendor" / "fontawesome" / "css" / "all.min.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _block(text: str, selector: str) -> str:
    """Return the body of the first CSS block whose selector matches."""
    idx = text.find(selector)
    if idx == -1:
        return ""
    start = text.find("{", idx)
    if start == -1:
        return ""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return text[start + 1:]


@pytest.fixture(scope="module")
def styles() -> str:
    return _read(STYLES)


@pytest.fixture(scope="module")
def index_html() -> str:
    return _read(INDEX)


# ── 1. The `.hidden` utility ordering bug ───────────────────────────────
# A duplicate `.hidden { display:none !important }` sat AFTER the responsive
# breakpoint block, so it beat `.sm\:block { display:block !important }` and
# the "DataAgent" wordmark never rendered on desktop.


def test_hidden_utility_is_declared_exactly_once(styles: str) -> None:
    declarations = re.findall(r"^\.hidden\s*\{[^}]*display:\s*none\s*!important", styles, re.M)
    assert len(declarations) == 1, (
        f"`.hidden` must be declared exactly once — found {len(declarations)}. "
        "A duplicate after the breakpoint block silently hides the wordmark."
    )


def test_responsive_display_helpers_carry_important(styles: str) -> None:
    """`sm:`/`md:`/`lg:` display helpers must override the base `.hidden`."""
    display_helpers = {"block", "flex", "inline", "inline-block", "grid", "table"}
    checked = 0
    for match in re.finditer(r"@media \(min-width: (?:640|769|1024)px\)\s*\{", styles):
        body = _block(styles, match.group(0).rstrip(" {"))
        for helper in re.finditer(r"\.(sm|md|lg)\\:([\w-]+)\s*\{([^}]*)\}", body):
            prefix, name, decl = helper.groups()
            if name in display_helpers:
                assert "important" in decl, (
                    f".{prefix}\\:{name} must carry !important to beat "
                    "`.hidden { display: none !important }`"
                )
                checked += 1
    assert checked >= 6, (
        f"expected the responsive display helpers to be found — checked {checked}. "
        "The breakpoint blocks may have moved or been renamed."
    )


def test_wordmark_is_not_hidden_by_utility(styles: str, index_html: str) -> None:
    """The brand wordmark must always be visible — never `hidden`."""
    match = re.search(r'id="topnav-logo".*?</a>', index_html, re.S)
    assert match, "#topnav-logo anchor not found"
    wordmark = re.search(r"<span[^>]*>(?:DataAgent)</span>", match.group(0))
    assert wordmark, "brand wordmark span not found"
    classes = wordmark.group(0)
    assert "hidden" not in classes, (
        "the brand wordmark must not use the `hidden` utility — it is the "
        "app identity and must render at every width"
    )


# ── 2. The theme-token override bug ────────────────────────────────────
# widgetPresets wrote theme tokens onto <html> as INLINE styles. Inline
# styles beat every stylesheet rule, so the light/dark theme blocks in
# styles.css went dead and the app could not switch themes.


def test_widget_presets_do_not_write_to_document_element() -> None:
    src = _read(WEB / "js" / "components" / "widgetPresets.js")
    offenders = re.findall(r"documentElement\.style\.setProperty\([^)]*\)", src)
    assert not offenders, (
        "widgetPresets must not stamp theme tokens onto <html>: "
        f"{offenders} — inline styles there kill the light/dark theme."
    )


def test_widget_presets_scopes_theme_to_an_element() -> None:
    src = _read(WEB / "js" / "components" / "widgetPresets.js")
    assert "_themeScope" in src or "_applyThemeTo" in src, (
        "widgetPresets must apply a preset theme to a scoped element"
    )


# ── 3. Font Awesome icons must exist in the vendored 6.5.1 CSS ───────────


def _fa_defined() -> set[str]:
    """Icon names declared by the vendored Font Awesome CSS.

    FA 6 ships icons in combined rules such as
    `.fa-house:before, .fa-home:before { ... }`, so a naive
    `\\.fa-name` scan only finds the first name in each rule. Every
    selector in every rule is collected instead.
    """
    text = _read(FA_CSS)
    return set(re.findall(r"\.((?:fa|fas|far|fab)-[a-z0-9-]+)", text))


def _used_fa_classes() -> set[str]:
    used: set[str] = set()
    for path in list(WEB.rglob("*.html")) + list(WEB.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        for match in re.finditer(r"class=\"([^\"]*)\"", _read(path)):
            for token in match.group(1).split():
                if token.startswith("fa-"):
                    used.add(token)
    return used


def test_no_missing_font_awesome_icons() -> None:
    defined = _fa_defined()
    modifiers = {
        "fa-solid", "fa-regular", "fa-brands", "fa-fw", "fa-ul", "fa-li",
        "fa-border", "fa-pull-left", "fa-pull-right", "fa-spin", "fa-pulse",
        "fa-rotate-90", "fa-rotate-180", "fa-flip-horizontal", "fa-flip-vertical",
        "fa-stack", "fa-inverse", "fa-swap-opacity", "fa-xs", "fa-sm", "fa-lg",
        "fa-xl", "fa-2xl", "fa-1x", "fa-2x", "fa-3x", "fa-4x", "fa-5x",
        "fa-6x", "fa-7x", "fa-8x", "fa-9x", "fa-10x",
    }
    missing = sorted(
        c for c in _used_fa_classes()
        if c not in defined and c not in modifiers
        and not c.endswith(("'", '"', "-"))
        and "'" not in c and '"' not in c and "$" not in c
    )
    assert not missing, f"Font Awesome icon(s) missing from vendored 6.5.1: {missing}"


# ── 4. One design-token system ──────────────────────────────────────────


def test_single_token_palette(styles: str) -> None:
    """Exactly one `:root` block defines the palette."""
    roots = re.findall(r"^:root\s*\{", styles, re.M)
    assert len(roots) == 1, f"expected one :root token block, found {len(roots)}"


def test_no_duplicate_competing_palette() -> None:
    """`enterprise.css` held a second, conflicting palette and was dead code."""
    assert not (CSS_DIR / "enterprise.css").exists(), (
        "enterprise.css defines a second, conflicting :root palette and is "
        "not linked by any page — remove it rather than letting it drift"
    )


def test_required_tokens_present(styles: str) -> None:
    root = _block(styles, ":root")
    assert root, ":root block not found"
    for token in [
        "--color-primary", "--color-primary-hover", "--color-primary-light",
        "--color-primary-rgb", "--color-surface", "--color-surface-2",
        "--color-border", "--color-text", "--color-text-secondary",
        "--color-text-muted", "--color-success", "--color-warning",
        "--color-danger", "--color-info", "--space-1", "--space-4",
        "--radius-sm", "--radius-md", "--radius-lg", "--shadow-xs",
        "--shadow-sm", "--shadow-md", "--ring", "--chart-1", "--chart-8",
    ]:
        assert token in root, f"required design token missing from :root — {token}"


def test_dark_theme_overrides_every_surface_token(styles: str) -> None:
    dark = _block(styles, '[data-theme="dark"]')
    assert dark, "dark theme block not found"
    for token in [
        "--color-primary", "--color-bg", "--color-surface", "--color-text",
        "--color-text-secondary", "--color-border", "--color-success",
        "--color-warning", "--color-danger", "--chart-1", "--chart-8",
    ]:
        assert token in dark, f"token missing from the dark theme — {token}"


def test_legacy_tokens_preserved(styles: str) -> None:
    """JS and TS files still reference these names — removing them breaks features."""
    for token in [
        "--color-card-bg", "--color-card-border", "--color-content-bg",
        "--color-content-text", "--color-content-text-secondary",
        "--color-surface-hover", "--color-accent-blue", "--color-accent-green",
        "--color-accent-red", "--color-accent-amber", "--color-accent-purple",
        "--color-accent-teal", "--color-sidebar-bg", "--color-topnav-bg",
        "--font-sans", "--font-mono", "--layout-max-width",
    ]:
        assert token in styles, f"legacy token removed but still referenced — {token}"


def test_no_self_referencing_custom_property(styles: str) -> None:
    """A custom property that references itself is invalid and resolves to unset."""
    for match in re.finditer(r"(--[\w-]+)\s*:\s*var\(\s*\1\s*[,)]", styles):
        raise AssertionError(f"self-referencing custom property: {match.group(1)}")


# ── 5. No gradient / glow in app chrome ────────────────────────────────


def test_no_gradient_on_topnav(styles: str) -> None:
    block = _block(styles, "#topnav {")
    assert block, "#topnav block not found"
    assert "gradient" not in block, "the top nav must be a flat surface, not a gradient"


def test_no_gradient_text_in_title(styles: str) -> None:
    for selector in (".hero-title", ".stud-hero-title"):
        block = _block(styles, selector + " ")
        if not block:
            continue
        assert "background-clip" not in block, (
            f"{selector} must not use gradient text — solid colour only"
        )


def test_buttons_have_no_glow_shadow(styles: str) -> None:
    block = _block(styles, ".btn-primary {")
    assert block, ".btn-primary block not found"
    assert "box-shadow" not in block or "0 1px 2px rgba(var(--color-primary-rgb)" not in block, (
        "primary buttons must not carry a coloured glow"
    )


def test_danger_button_is_red(styles: str) -> None:
    block = _block(styles, ".btn-danger {")
    assert block, ".btn-danger block not found"
    assert "danger" in block, "the destructive button must use the danger token, not the brand colour"


# ── 6. Status colours are reserved ─────────────────────────────────────


def test_status_kpi_uses_a_dot_not_colour_alone() -> None:
    """A status KPI must pair its colour with a status dot — colour alone is
    not an accessible signal.

    The dot has a single owner (kpiStyles.css). It used to be declared in
    both styles.css and kpiStyles.css, so the later file silently won and
    the unmodelled state defaulted to GREEN — a dot that said "healthy"
    before the health check had answered.
    """
    panel = _read(WEB / "css" / "kpiStyles.css")
    assert ".kpi-status-dot" in panel, "the status dot must be declared in kpiStyles.css"

    styles = _read(STYLES)
    assert ".kpi-status-dot" not in styles, (
        ".kpi-status-dot is declared in two stylesheets — the later one "
        "silently wins. It must have exactly one owner."
    )

    # The resting state must be neutral, not "healthy".
    block = _block(panel, ".kpi-status-dot {")
    assert block, ".kpi-status-dot rule not found in kpiStyles.css"
    assert "--color-success" not in block, (
        "the unmodelled status dot must be neutral, not green — it should "
        "not claim a health state before the health check has answered"
    )
    for tone in ("is-success", "is-warning", "is-danger", "is-info"):
        assert f".kpi-status-dot.{tone}" in panel, f"the {tone} variant is missing"


# ── 7. Utility layer completeness ──────────────────────────────────────


def test_utility_classes_are_declared() -> None:
    """Every utility class used in markup must exist in the CSS."""
    declared: set[str] = set()
    for path in CSS_DIR.glob("*.css"):
        for name in re.findall(r"\.((?:\\.|[A-Za-z0-9_-])+)", _read(path)):
            declared.add(name.replace("\\", ""))

    used: set[str] = set()
    for path in list(WEB.rglob("*.html")) + list(WEB.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = _read(path)
        for pattern in (r'class="([^"]*)"', r"className\s*=\s*[`'\"]([^`'\"]*)[`'\"]"):
            for match in re.finditer(pattern, text):
                used.update(match.group(1).split())

    # Component classes (per-feature namespaces) are not design utilities.
    component_prefixes = (
        "fa-", "bd-", "ds-", "ci-", "dt-", "xl-", "bp-", "pbi-", "cpx-",
        "pro-", "ff-", "chart-", "kpi-", "app-", "nav-", "user-", "status-",
        "mobile-", "notification-", "toast-", "empty-", "error-", "skeleton-",
        "page-", "home-", "hero-", "section-", "card-", "btn-", "form-",
        "table-", "data-", "modal-", "badge-", "plan-", "ud-", "setting-",
        "upload-", "chat-", "msg-", "step-", "acct-", "tenant-", "org-",
        "preview-", "compare-", "export-", "anomaly-", "quality-", "stat-",
        "forecast-", "connect-", "lineage-", "notebook-", "automation-",
        "admin-", "audit-", "workflow-", "catalog-", "subscription-",
        "onboarding-", "wed-", "wf-", "wh-", "cqg-", "ctb-", "mp-", "cat-",
        "d3-", "hidden", "dark", "light", "active", "selected", "open",
        "closed", "feature-llm", "base-chip", "hide-scrollbar", "snap-target",
        "normal-case", "resize-y", "line-through", "premium-", "studio-",
        "template-", "widget-", "grid-", "canvas-", "panel-", "dock-",
        "ribbon-", "collab-", "comment-", "approval-", "insight-", "smart-",
        "metric-", "measure-", "segment-", "slicer-", "axis-", "legend-",
        "tooltip-", "filter-", "search-", "dropdown-", "select-", "option-",
        "menu-", "tab-", "group-", "icon-", "avatar-", "chip-", "tag-",
        "pill-", "divider-", "rule-", "loader-", "spinner-", "progress-",
        "meter-", "accent-", "enterprise-", "business-", "team-", "field-",
        "value-", "series-", "col-", "row-", "cell-", "query-item",
        "notif-filter", "story-add-dash", "near-target", "valid-target",
        "very-near-target", "account-status-", "checkbox-label",
        "comments-widget", "dashboard-grid", "dash-widget-content",
        "tabs-container", "ai-gen-",
    )
    noise = {"-", ":", "-1", "1", "0", "+", "/", "\\"}

    missing = sorted(
        c for c in used
        if c and c not in declared and c not in noise
        and not any(c.startswith(p) for p in component_prefixes)
        and "${" not in c and "}" not in c
        and re.fullmatch(r"[a-z0-9:\-/\[\]\.%\(\),]+", c)
        and ("-" in c or ":" in c or "/" in c)
    )
    assert not missing, f"utility class(es) used in markup but not declared: {missing}"


# ── 8. Chart palette identity across themes ────────────────────────────


def test_chart_series_order_is_stable_across_themes() -> None:
    """A series must keep its colour slot when the theme flips."""
    theme = _read(WEB / "ts" / "charts" / "theme.ts")
    light = re.search(r"const LIGHT_THEME[\s\S]*?palette:\s*\[(.*?)\]", theme, re.S)
    dark = re.search(r"const DARK_THEME[\s\S]*?palette:\s*\[(.*?)\]", theme, re.S)
    assert light and dark, "LIGHT_THEME / DARK_THEME palette not found"

    def _hues(block: str) -> list[str]:
        hues = []
        for hexval in re.findall(r"#[0-9A-Fa-f]{6}", block):
            r, g, b = (int(hexval[i:i + 2], 16) for i in (1, 3, 5))
            if max(r, g, b) - min(r, g, b) < 20:
                hues.append("grey")
            elif r >= g and r >= b:
                hues.append("red")
            elif g >= r and g >= b:
                hues.append("green")
            else:
                hues.append("blue")
        return hues

    assert _hues(light.group(1)) == _hues(dark.group(1)), (
        "light and dark chart palettes must keep the same hue ORDER so a "
        "series keeps its identity across a theme switch"
    )


def test_chart_js_reads_tokens_from_css() -> None:
    src = _read(WEB / "js" / "charts.js")
    assert "--chart-" in src, (
        "charts.js must read the chart palette from the CSS tokens so the "
        "charts and the stylesheet cannot drift apart"
    )


# ── 9. Contrast-critical token rules ───────────────────────────────────


def test_solid_fill_tokens_exist(styles: str) -> None:
    """A filled surface behind white text needs a deep fill in BOTH themes.

    The semantic tokens are lightened in dark mode; using them as a button
    fill would put white text on a light green/red and fail WCAG AA.
    """
    root = _block(styles, ":root")
    dark = _block(styles, '[data-theme="dark"]')
    for token in [
        "--color-success-solid", "--color-warning-solid", "--color-danger-solid",
        "--color-info-solid", "--color-accent-purple-solid", "--color-accent-teal-solid",
    ]:
        assert token in root, f"solid fill token missing from :root — {token}"
        assert token in dark, f"solid fill token missing from the dark theme — {token}"


def test_solid_tokens_are_identical_in_both_themes(styles: str) -> None:
    """The solid fills must NOT be lightened in dark mode."""
    root = _block(styles, ":root")
    dark = _block(styles, '[data-theme="dark"]')

    def value(block: str, token: str) -> str:
        m = re.search(re.escape(token) + r"\s*:\s*([^;]+);", block)
        return m.group(1).strip().lower() if m else ""

    for token in [
        "--color-success-solid", "--color-warning-solid", "--color-danger-solid",
        "--color-info-solid", "--color-accent-purple-solid", "--color-accent-teal-solid",
    ]:
        assert value(root, token) == value(dark, token), (
            f"{token} differs between themes — a filled surface with white "
            "text must keep the same deep fill in both"
        )


def test_danger_and_success_buttons_use_solid_fills(styles: str) -> None:
    """The destructive button must be red, and the success button green — in
    both themes — and never the brand colour."""
    for selector, token in ((".btn-danger", "--color-danger-solid"),
                            (".btn-success", "--color-success-solid")):
        block = _block(styles, selector + " {")
        assert block, f"{selector} block not found"
        assert token in block, (
            f"{selector} must use {token} so white text stays legible"
        )


def test_form_controls_inherit_colour(styles: str) -> None:
    """Buttons and inputs must inherit the app text colour and surface.

    Without this a button with a dark-mode background keeps the UA default
    black text and is unreadable.
    """
    block = _block(styles, "button, input, select, textarea {")
    assert block, "the form-control base rule not found"
    assert "color: inherit" in block, (
        "form controls must inherit the app text colour"
    )
    assert "background-color" in block, (
        "form controls must use the app surface, not the UA buttonface"
    )


def test_text_muted_meets_contrast_on_the_canvas(styles: str) -> None:
    """`--color-text-muted` is used for body copy; it must clear 4.5:1 on the
    app canvas in light mode."""

    def rgb(value: str) -> tuple[int, int, int]:
        h = value.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def luminance(rgbv: tuple[int, int, int]) -> float:
        out = []
        for v in rgbv:
            v /= 255
            out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    def ratio(fg: str, bg: str) -> float:
        l1, l2 = luminance(rgb(fg)), luminance(rgb(bg))
        hi, lo = max(l1, l2), min(l1, l2)
        return (hi + 0.05) / (lo + 0.05)

    root = _block(styles, ":root")

    def value(token: str) -> str:
        m = re.search(re.escape(token) + r"\s*:\s*([^;]+);", root)
        return m.group(1).strip() if m else ""

    muted = value("--color-text-muted")
    canvas = value("--color-bg")
    assert muted and canvas, "token values not found"
    assert ratio(muted, canvas) >= 4.5, (
        f"--color-text-muted ({muted}) on --color-bg ({canvas}) is "
        f"{ratio(muted, canvas):.2f}:1 — body copy must clear 4.5:1"
    )


# ── 10. Regressions caught by the code review ──────────────────────────


def test_tint_utilities_are_not_the_foreground_colour(styles: str) -> None:
    """A `bg-*-50` tint must not be the same colour as the `text-*-600` that
    sits on it — that renders the text invisible.

    This is the bug that made every `bg-purple-50` + `text-purple-600` chip
    in Analysis, Notebook, SQL, Warehouse, Upload and the widget editor
    render 1:1.
    """
    utils = _read(WEB / "css" / "utilities.css")
    pairs = [
        ("bg-purple-50", "text-purple-600"),
        ("bg-purple-100", "text-purple-700"),
        ("bg-green-50", "text-green-600"),
        ("bg-blue-50", "text-blue-600"),
        ("bg-red-50", "text-red-600"),
    ]
    for bg_class, text_class in pairs:
        bg = re.search(r"^\." + re.escape(bg_class) + r"\s*\{([^}]*)\}", utils, re.M)
        fg = re.search(r"^\." + re.escape(text_class) + r"\s*\{([^}]*)\}", utils, re.M)
        assert bg and fg, f"{bg_class} / {text_class} not found in utilities.css"
        bg_value = re.search(r"background(?:-color)?:\s*([^;]+);", bg.group(1))
        fg_value = re.search(r"color:\s*([^;]+);", fg.group(1))
        assert bg_value and fg_value, "could not read the declaration values"
        if bg_value.group(1).strip() == fg_value.group(1).strip():
            raise AssertionError(
                f".{bg_class} and .{text_class} resolve to the same colour "
                f"({bg_value.group(1).strip()}) — the text would be invisible"
            )


def test_focus_indicator_is_not_translucent(styles: str) -> None:
    """WCAG 2.2 SC 2.4.11 wants the focus indicator to clear 3:1 against the
    adjacent colour. A translucent halo does not."""
    root = _block(styles, ":root")
    m = re.search(r"--ring\s*:\s*([^;]+);", root)
    assert m, "--ring not found"
    value = m.group(1).strip()
    assert "rgba(" not in value and "color-mix" not in value, (
        f"--ring ({value}) is translucent — the focus indicator must be opaque"
    )


def test_focus_rule_does_not_change_the_control_radius(styles: str) -> None:
    """Forcing a border-radius on :focus-visible visibly reshapes a control."""
    block = _block(styles, "a:focus-visible, button:focus-visible")
    assert block, "the focus-visible rule not found"
    assert "border-radius" not in block, (
        "the focus-visible rule must not set border-radius — it would change "
        "a control's shape when it receives focus"
    )


def test_status_dot_is_never_invisible(styles: str) -> None:
    """The connection dot must have a visible resting colour."""
    block = _block(styles, ".status-dot {")
    assert block, ".status-dot rule not found"
    assert "background" in block, (
        ".status-dot must declare a background — otherwise the state colours "
        "are the only thing making it visible"
    )


def test_health_check_targets_the_real_status_elements() -> None:
    """The health check must update the elements that are actually in the DOM."""
    app = _read(WEB / "js" / "app.js")
    assert 'getElementById("status-dot")' in app, (
        "the health check must target #status-dot"
    )
    assert 'getElementById("status-text")' in app, (
        "the health check must target #status-text"
    )
    for stale in ("health-dot", "health-text", "health-status-wrapper"):
        assert f'getElementById("{stale}")' not in app, (
            f"#{stale} does not exist in the DOM — the health check would "
            "silently do nothing"
        )


def test_auth_screen_uses_tokens_not_a_hardcoded_white_card() -> None:
    """A card with a hardcoded white background plus tokenised text is
    unreadable in dark mode."""
    auth = _read(WEB / "js" / "auth.js")
    assert "linear-gradient(135deg,#1e1b4b" not in auth, (
        "the sign-in screen must not use the old purple gradient background"
    )
    assert "linear-gradient(135deg,#6366f1,#8b5cf6)" not in auth, (
        "the brand tile must not use the old violet gradient"
    )
    assert "background:#fff;" not in auth, (
        "the sign-in card must use var(--color-surface), not a hardcoded white"
    )


def test_widget_theme_cannot_clobber_app_wide_tokens() -> None:
    """A dashboard theme may only own widget-scoped tokens.

    `--radius-md` is read by ~80 rules and `--color-warning` is a RESERVED
    status colour — neither may be rewritten by a widget theme.
    """
    src = _read(WEB / "js" / "components" / "widgetPresets.js")
    m = re.search(r"var _THEME_TOKENS = \[(.*?)\];", src, re.S)
    assert m, "_THEME_TOKENS not found"
    tokens = set(re.findall(r'"(--[\w-]+)"', m.group(1)))
    for reserved in ("--radius-md", "--color-warning", "--color-success",
                     "--color-danger", "--color-info", "--color-text"):
        assert reserved not in tokens, (
            f"{reserved} is app-wide (or a reserved status colour) and must "
            "not be writable by a widget theme"
        )
    assert "--bd-radius" in tokens, (
        "the widget theme should use the widget-scoped --bd-radius token"
    )


# ── 11. Invariants the audit alone cannot guard ─────────────────────────


def test_purple_tint_resolves_to_a_different_colour_in_both_themes(styles: str) -> None:
    """The tint and the foreground must RESOLVE to different colours.

    Comparing the `var()` expressions is not enough — both could point at
    the same token and still pass. This is the bug that made every
    `bg-purple-50` + `text-purple-600` chip render 1:1.
    """
    root = _block(styles, ":root")
    dark = _block(styles, '[data-theme="dark"]')

    def value(block: str, token: str) -> str:
        m = re.search(re.escape(token) + r"\s*:\s*([^;]+);", block)
        return m.group(1).strip().lower() if m else ""

    for scope_name, scope in (("light", root), ("dark", dark)):
        tint = value(scope, "--color-accent-purple-bg")
        foreground = value(scope, "--color-accent-purple")
        assert tint, f"--color-accent-purple-bg missing in the {scope_name} theme"
        assert foreground, f"--color-accent-purple missing in the {scope_name} theme"
        assert tint != foreground, (
            f"in the {scope_name} theme the purple tint ({tint}) and the "
            f"purple foreground ({foreground}) are the same colour — the text "
            "would be invisible"
        )


def test_accent_purple_bg_token_exists_in_both_themes(styles: str) -> None:
    root = _block(styles, ":root")
    dark = _block(styles, '[data-theme="dark"]')
    assert "--color-accent-purple-bg" in root, "--color-accent-purple-bg missing from :root"
    assert "--color-accent-purple-bg" in dark, "--color-accent-purple-bg missing from the dark theme"


def test_no_class_is_declared_in_two_stylesheets_with_different_bodies() -> None:
    """The single-utility-layer invariant.

    `utilities.css` is linked after `styles.css`, so a class declared in both
    with different bodies means the reviewed rule is not the one that renders.
    """
    css_files = sorted((WEB / "css").glob("*.css"))

    def bodies(path: Path) -> dict[str, str]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        out: dict[str, str] = {}
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", text):
            selectors = m.group(1).strip()
            if not selectors or "@" in selectors:
                continue
            if not re.search(r"[.#:\[]", selectors):
                continue
            out.setdefault(re.sub(r"\s+", " ", selectors),
                           re.sub(r"\s+", " ", m.group(2)).strip())
        return out

    for i, a in enumerate(css_files):
        for b in css_files[i + 1:]:
            sa, sb = bodies(a), bodies(b)
            shared = set(sa) & set(sb)
            differing = sorted(c for c in shared if sa[c] != sb[c])
            assert not differing, (
                f"{a.name} and {b.name} both declare {len(differing)} selector(s) "
                f"with different bodies: {', '.join(differing[:6])} — the later "
                "file silently wins"
            )


def test_mobile_nav_has_a_real_toggle() -> None:
    """The drawer must open AND close, and announce its state."""
    index = _read(INDEX)
    app = _read(WEB / "js" / "app.js")

    m = re.search(r'<button[^>]*id="mobile-menu-toggle"[^>]*>', index)
    assert m, "#mobile-menu-toggle not found"
    button = m.group(0)
    assert 'aria-expanded="false"' in button, (
        "the mobile-nav toggle must carry aria-expanded"
    )
    assert 'aria-controls="mobile-nav"' in button, (
        "the mobile-nav toggle must point at the drawer with aria-controls"
    )

    assert "aria-expanded" in app, "the toggle handler must keep aria-expanded in sync"
    assert "Escape" in app, "the drawer must close on Escape"
    # The scrim must actually exist.
    assert "bg-black\\/50" in _read(WEB / "css" / "utilities.css") or \
        "#mobile-nav-overlay" in _read(STYLES), (
        "the mobile-nav scrim must be a real, visible backdrop"
    )


def test_health_check_targets_the_real_status_elements() -> None:
    """The health check must update the elements that are actually in the DOM."""
    app = _read(WEB / "js" / "app.js")
    assert 'getElementById("status-dot")' in app, (
        "the health check must target #status-dot"
    )
    assert 'getElementById("status-text")' in app, (
        "the health check must target #status-text"
    )
    for stale in ("health-dot", "health-text", "health-status-wrapper"):
        assert f'getElementById("{stale}")' not in app, (
            f"#{stale} does not exist in the DOM — the health check would "
            "silently do nothing"
        )


def test_every_referenced_token_is_declared() -> None:
    """A `var(--x)` with no fallback, where `--x` is declared nowhere, makes
    the whole declaration invalid at computed-value time."""
    css_files = sorted((WEB / "css").glob("*.css"))
    declared: set[str] = set()
    referenced: dict[str, str] = {}

    for path in css_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        for name in re.findall(r"(--[\w-]+)\s*:", text):
            declared.add(name)
        for m in re.finditer(r"var\(\s*(--[\w-]+)\s*([^)]*)\)", text):
            name, rest = m.group(1), m.group(2)
            if "," in rest:
                continue  # has a fallback
            referenced.setdefault(name, path.name)

    missing = {n: f for n, f in referenced.items() if n not in declared}
    assert not missing, (
        "custom propert(y/ies) referenced but never declared: "
        + ", ".join(f"{n} (in {f})" for n, f in sorted(missing.items()))
        + " — the declaration they appear in is dropped at computed-value time"
    )


def test_text_white_is_white_in_both_themes(styles: str) -> None:
    """`.text-white` is used on saturated fills; it must not invert in dark
    mode (a class named text-white that renders near-black is a trap)."""
    root = _block(styles, ":root")
    dark = _block(styles, '[data-theme="dark"]')

    def value(block: str, token: str) -> str:
        m = re.search(re.escape(token) + r"\s*:\s*([^;]+);", block)
        return m.group(1).strip().lower() if m else ""

    for scope_name, scope in (("light", root), ("dark", dark)):
        on_solid = value(scope, "--color-on-solid")
        assert on_solid, f"--color-on-solid missing in the {scope_name} theme"
        assert on_solid in ("#fff", "#ffffff"), (
            f"--color-on-solid is {on_solid} in the {scope_name} theme — it "
            "must stay white so white-on-colour never inverts"
        )

    utils = _read(WEB / "css" / "utilities.css")
    assert ".text-white { color: var(--color-on-solid); }" in utils, (
        ".text-white must use --color-on-solid, not --color-text-inverse"
    )
