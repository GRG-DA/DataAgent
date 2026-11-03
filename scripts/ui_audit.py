"""
UI audit for the DataAgent web frontend.

Static checks that do not need a running server:
  1. Utility classes used in markup but never declared in CSS.
  2. Font Awesome classes used in markup but absent from the vendored 6.5.1 CSS.
  3. Raw hex colours in the app's own stylesheets (should be tokenised).
  4. Gradient usage inventory.
  5. Design-token contract conformance (required tokens present in :root and
     in the dark-theme block).

Usage:  python scripts/ui_audit.py
Exit code 0 when no errors, 1 when errors are found.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# The audit prints em-dashes; force UTF-8 so a cp1252 console does not
# render them as mojibake.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - older interpreters
    pass

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

APP_CSS = [
    WEB / "css" / "styles.css",
    WEB / "css" / "utilities.css",
    WEB / "css" / "propsPanel.css",
    WEB / "css" / "kpiStyles.css",
    WEB / "css" / "chat-pro.css",
]
FA_CSS = WEB / "vendor" / "fontawesome" / "css" / "all.min.css"

# Tokens the design system contract requires.
REQUIRED_TOKENS = [
    "--color-primary", "--color-primary-hover", "--color-primary-light",
    "--color-primary-rgb", "--color-surface", "--color-surface-2",
    "--color-border", "--color-text", "--color-text-secondary",
    "--color-text-muted", "--color-success", "--color-warning",
    "--color-danger", "--color-info", "--space-1", "--space-4",
    "--radius-sm", "--radius-md", "--radius-lg", "--shadow-xs",
    "--shadow-sm", "--shadow-md", "--ring", "--chart-1", "--chart-8",
    # Solid fills for filled surfaces behind fixed light ink.
    "--color-success-solid", "--color-warning-solid", "--color-danger-solid",
    "--color-info-solid", "--color-accent-purple-solid",
    "--color-accent-teal-solid", "--color-on-solid",
    # Accent purple needs a TINT token as well as a foreground token —
    # without the tint, `bg-purple-50` + `text-purple-600` render 1:1.
    "--color-accent-purple", "--color-accent-purple-bg",
]

# Legacy names other code still depends on — removing them breaks features.
LEGACY_TOKENS = [
    "--color-primary-hover", "--color-primary-light", "--color-primary-dark",
    "--color-primary-rgb", "--color-card-bg", "--color-card-border",
    "--color-content-bg", "--color-content-text",
    "--color-content-text-secondary", "--color-content-muted",
    "--color-surface-hover", "--color-accent-blue", "--color-accent-green",
    "--color-accent-red", "--color-accent-amber", "--color-accent-purple",
    "--color-accent-teal", "--color-sidebar-bg", "--color-sidebar-hover",
    "--color-sidebar-active", "--color-sidebar-text",
    "--color-sidebar-text-muted", "--color-sidebar-border",
    "--color-sidebar-width", "--color-topnav-bg", "--color-topnav-border",
    "--font-sans", "--font-mono", "--radius-sm", "--radius-md", "--radius-lg",
    "--radius-xl", "--shadow-xs", "--shadow-sm", "--shadow-md", "--shadow-lg",
    "--shadow-focus", "--transition-fast", "--transition-normal",
    "--layout-max-width", "--chart-1", "--chart-8",
]

# Prefixes that mark a class as a component class, not a design-system
# utility. Kept as plain prefixes (no regex) to keep the pattern readable.
NOT_UTILITY_PREFIXES = (
    "fa-", "fas", "far", "fab", "hljs", "data-", "dark", "light",
    "bd-", "ds-", "ci-", "dt-", "xl-", "bp-", "pbi-", "cpx-", "pro-",
    "ff-", "chart-", "kpi-", "app-", "nav-", "user-", "status-", "mobile-",
    "notification-", "toast-", "empty-", "error-", "skeleton-", "page-",
    "home-", "hero-", "section-", "card-", "btn-", "form-", "table-",
    "data-", "modal-", "badge-", "plan-", "ud-", "setting-", "upload-",
    "chat-", "msg-", "step-", "acct-", "tenant-", "org-", "preview-",
    "compare-", "export-", "anomaly-", "quality-", "stat-", "forecast-",
    "connect-", "lineage-", "notebook-", "automation-", "admin-", "audit-",
    "workflow-", "catalog-", "subscription-", "onboarding-", "base-chip",
    "hide-scrollbar", "snap-target", "normal-case", "resize-y",
    "line-through", "premium-", "studio-", "template-", "widget-",
    "grid-", "canvas-", "panel-", "dock-", "ribbon-", "collab-",
    "comment-", "approval-", "insight-", "smart-", "metric-", "measure-",
    "segment-", "slicer-", "axis-", "legend-", "tooltip-", "filter-",
    "search-", "dropdown-", "select-", "option-", "menu-", "tab-",
    "group-", "icon-", "avatar-", "chip-", "tag-", "pill-", "divider-",
    "rule-", "loader-", "spinner-", "progress-", "meter-", "accent-",
    "enterprise-", "business-", "team-", "field-", "value-",
    "series-", "col-", "row-", "cell-", "collab-",
    # Per-feature component namespaces owned by the BI / Excel / chat modules.
    "wed-", "wf-", "wh-", "cqg-", "ctb-", "ci-", "mp-", "cat-", "d3-",
    "ai-gen-", "query-item", "notif-filter", "story-add-dash",
    "near-target", "valid-target", "very-near-target", "account-status-",
    "checkbox-label", "comments-widget", "dashboard-grid",
    "dash-widget-content", "tabs-container",
)
NOT_UTILITY_EXACT = {
    "active", "selected", "open", "closed", "visible", "danger", "hidden",
    "feature-llm", "pro", "free", "muted", "error", "success", "warning",
    "info", "primary", "secondary", "ghost", "outline", "sm", "md", "lg",
    "xl", "xs", "2xl", "block", "flex", "grid", "hidden", "inline",
}


def is_component_class(name: str) -> bool:
    if name in NOT_UTILITY_EXACT:
        return True
    return any(name.startswith(p) for p in NOT_UTILITY_PREFIXES)

CLASS_ATTR = re.compile(r'class="([^"]*)"')
CLASS_JSX = re.compile(r"className\s*=\s*[`'\"]([^`'\"]*)[`'\"]")
CLASS_ADD = re.compile(r"class(?:List)?\.add\(['\"]([^'\"]+)['\"]\)")


def collect_used_classes() -> set[str]:
    used: set[str] = set()
    for path in list(WEB.rglob("*.html")) + list(WEB.rglob("*.js")) + list(WEB.rglob("*.ts")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in (CLASS_ATTR, CLASS_JSX, CLASS_ADD):
            for match in pattern.finditer(text):
                for token in match.group(1).split():
                    if token:
                        used.add(token)
    # Drop template-literal fragments (e.g. "fa-${action}") and punctuation
    # that a naive split picks up out of JS expression context.
    noise = {"-", ":", "-1", "1", "0", "+", "/", "\\"}
    return {c for c in used if c not in noise and "${" not in c and "}" not in c}


def collect_declared_classes(css_text: str) -> set[str]:
    declared = set(re.findall(r"\.((?:\\.|[A-Za-z0-9_-])+)", css_text))
    return {d.replace("\\", "") for d in declared}


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    css_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in APP_CSS if p.exists())
    declared = collect_declared_classes(css_text)

    # ── 1. Missing utility classes ────────────────────────────────────────
    used = collect_used_classes()
    missing = sorted(
        c for c in used
        if c and c not in declared
        and not is_component_class(c)
        and re.fullmatch(r"[a-z0-9:\-/\[\]\.%\(\),]+", c)
        and ("-" in c or ":" in c or "/" in c)
    )
    if missing:
        errors.append(f"{len(missing)} utility class(es) used in markup but not declared in CSS")
        for m in missing:
            errors.append(f"    missing: {m}")

    # ── 2. Font Awesome icons that do not exist ──────────────────────────
    if FA_CSS.exists():
        fa_text = FA_CSS.read_text(encoding="utf-8", errors="ignore")
        fa_defined = set(re.findall(r"\.((?:fa|fas|far|fab)-[a-z0-9-]+)", fa_text))
        fa_used = {c for c in used if c.startswith("fa-")}
        # Modifier classes (fa-solid/fa-fw/fa-lg/...) are not icon names.
        modifiers = {
            "fa-solid", "fa-regular", "fa-brands", "fa-light", "fa-thin",
            "fa-duotone", "fa-fw", "fa-ul", "fa-li", "fa-border", "fa-pull-left",
            "fa-pull-right", "fa-spin", "fa-pulse", "fa-spin-pulse", "fa-spin-reverse",
            "fa-rotate-90", "fa-rotate-180", "fa-flip-horizontal", "fa-flip-vertical",
            "fa-stack", "fa-inverse", "fa-swap-opacity", "fa-xs", "fa-sm", "fa-lg",
            "fa-xl", "fa-2xl", "fa-1x", "fa-2x", "fa-3x", "fa-4x", "fa-5x",
            "fa-6x", "fa-7x", "fa-8x", "fa-9x", "fa-10x",
        }
        fa_missing = sorted(
            c for c in fa_used
            if c not in fa_defined and c not in modifiers
            # A class ending in a quote or dash is a fragment of a
            # template literal (e.g. "fa-${icon}", "fa-chevron-").
            and not c.endswith(("'", '"', "-", "\\"))
            and "'" not in c and '"' not in c and "$" not in c
        )
        if fa_missing:
            errors.append(f"{len(fa_missing)} Font Awesome icon(s) not present in vendored 6.5.1")
            for m in fa_missing:
                errors.append(f"    missing icon: {m}")

    # ── 3. Raw hex colours in app stylesheets ─────────────────────────────
    for path in APP_CSS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Ignore hex inside comments and inside var() fallbacks for tokens.
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", code)
        if hexes:
            warnings.append(f"{path.name}: {len(hexes)} raw hex colour(s) still present")
            counts: dict[str, int] = {}
            for h in hexes:
                counts[h.lower()] = counts.get(h.lower(), 0) + 1
            for h, n in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
                warnings.append(f"    {h} x{n}")

    # ── 4. Gradient inventory ─────────────────────────────────────────────
    total_grad = 0
    for path in APP_CSS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        n = len(re.findall(r"linear-gradient|radial-gradient", text))
        total_grad += n
        if n:
            warnings.append(f"{path.name}: {n} gradient(s)")
    if total_grad > 12:
        warnings.append(f"{total_grad} gradients in app chrome — gradient overuse")

    # ── 5. Token contract ─────────────────────────────────────────────────
    # Match the :root / dark-theme blocks by brace depth, not by a regex —
    # a non-greedy match stops at the first nested `}` and misses tokens.
    def _block(text: str, selector_prefix: str) -> str:
        idx = text.find(selector_prefix)
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

    root_block = _block(css_text, ":root")
    dark_block = _block(css_text, '[data-theme="dark"]')
    for token in REQUIRED_TOKENS:
        if not root_block or token not in root_block:
            errors.append(f"required token missing from :root — {token}")
    for token in LEGACY_TOKENS:
        if token not in css_text:
            errors.append(f"legacy token removed but still referenced elsewhere — {token}")
    if dark_block:
        for token in [
            "--color-primary", "--color-surface", "--color-bg", "--color-text",
            "--color-text-secondary", "--color-border", "--color-success",
            "--color-warning", "--color-danger", "--chart-1", "--chart-8",
        ]:
            if token not in dark_block:
                errors.append(f"token missing from dark theme — {token}")
    else:
        errors.append('dark theme block not found (expected [data-theme="dark"])')

    # ── 6. Every referenced custom property must be declared ─────────────
    # A `var(--x)` with no fallback, where `--x` is declared nowhere, makes
    # the WHOLE declaration invalid at computed-value time — the property is
    # silently dropped. This is the failure mode the token contract exists
    # to prevent, so it is an error, not a warning.
    declared_tokens: set[str] = set()
    referenced: dict[str, list[str]] = {}
    for path in APP_CSS:
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        for name in re.findall(r"(--[\w-]+)\s*:", body):
            declared_tokens.add(name)
        for m in re.finditer(r"var\(\s*(--[\w-]+)\s*([^)]*)\)", body):
            name, rest = m.group(1), m.group(2)
            # A fallback means the declaration still resolves if the token
            # is missing, so it is not an error.
            if "," in rest:
                continue
            referenced.setdefault(name, []).append(path.name)

    for name, files in sorted(referenced.items()):
        if name not in declared_tokens:
            errors.append(
                f"custom property {name} is referenced but never declared "
                f"(in {', '.join(sorted(set(files)))}) — the declaration it "
                "appears in is dropped at computed-value time"
            )

    # ── 7. No class declared in two stylesheets with different bodies ────
    # utilities.css is linked after styles.css, so at equal specificity the
    # later file silently wins. A class declared in two files with different
    # bodies means the reviewed rule is not the one that renders.
    #
    # Only EXACTLY equal selector lists count. A scoped rule
    # (`#bidash .active`) is a deliberate, higher-specificity override, not
    # a conflict — comparing only the last class name would flag those.
    def _selector_bodies(path: Path) -> dict[str, str]:
        body = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        out: dict[str, str] = {}
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", body):
            selectors = m.group(1).strip()
            if not selectors:
                continue
            # Skip at-rules and keyframe stops (`0%`, `50%`, `to`).
            if "@" in selectors:
                continue
            if not re.search(r"[.#:\[]", selectors):
                continue
            key = re.sub(r"\s+", " ", selectors)
            out.setdefault(key, re.sub(r"\s+", " ", m.group(2)).strip())
        return out

    sheets = [p for p in APP_CSS if p.exists()]
    for i, a in enumerate(sheets):
        for b in sheets[i + 1:]:
            sa, sb = _selector_bodies(a), _selector_bodies(b)
            shared = set(sa) & set(sb)
            differing = [c for c in shared if sa[c] != sb[c]]
            if differing:
                errors.append(
                    f"{a.name} and {b.name} both declare {len(differing)} "
                    f"selector(s) with different bodies: "
                    f"{', '.join(sorted(differing)[:8])}"
                    f"{' …' if len(differing) > 8 else ''}"
                )

    # ── Report ────────────────────────────────────────────────────────────
    print("=" * 72)
    print("DataAgent UI audit")
    print("=" * 72)
    if warnings:
        print(f"\nWARNINGS ({len(warnings)})")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print(f"\nERRORS ({len(errors)})")
        for e in errors:
            print(f"  - {e}")
        print(f"\nRESULT: FAIL ({len(errors)} error(s))")
        return 1
    print("\nRESULT: PASS — no errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
