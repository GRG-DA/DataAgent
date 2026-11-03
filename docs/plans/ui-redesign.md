# DataAgent — Professional Software UI Redesign

> **Status: shipped.** All five phases are complete. 3033 tests pass, the UI
> audit reports zero errors, and the app renders with zero console errors
> across 23 pages in both themes. The regression suite is
> `tests/test_ui_design_system.py` (20 tests) and the static audit is
> `python scripts/ui_audit.py`.

## Goal
Turn the current "SaaS landing page" look into a **professional data-application**
UI: calm, dense, hierarchical, accessible, with a real design-token system.

## Findings from live audit (browser-verified, not guessed)

### Critical bugs (must fix — these are broken, not stylistic)
1. **Brand text "DataAgent" is invisible on desktop.** `web/index.html:70` uses
   `class="... hidden sm:block"`. `styles.css` declares `.hidden{display:none!important}`
   twice — the second copy sits at line 3331, *after* the `sm:` media query at
   line 2447. Same specificity + `!important` → last one wins → the brand wordmark
   never appears at ≥640px. The logo tile renders as an empty purple square.
2. **Dashboard Studio hero icon is blank** — `fa-chart-network` does not exist in
   the vendored FontAwesome 6.5.1. (Also `fa-rows`, `fa-clock-rotate` are missing.)
3. **`.hidden` duplicate also breaks other `sm:`/`md:`/`lg:` utilities** used
   across the app (464 utility classes are declared; the ordering bug silently
   disables responsive visibility helpers).
4. **Theme tokens are overridden by inline styles on `<html>`.**
   `web/js/components/widgetPresets.js:_applyTheme()` writes
   `--color-primary`, `--color-content-bg`, `--color-card-bg`,
   `--color-content-text`… onto `document.documentElement` as inline styles.
   Inline style beats any stylesheet rule, so the light/dark theme blocks in
   `styles.css` are **dead** whenever a widget preset has been saved. Observed:
   toggling dark mode changed `data-theme` but the body kept the light
   `--color-content-bg: #f8fafc` until the app re-applied the preset.
5. **`fa-rows` (total-rows KPI) and `fa-clock-rotate` render as blank tiles.**

### Design-system gaps
6. **No real token system.** Two competing `:root` palettes exist — `styles.css`
   and `web/css/enterprise.css` — and `enterprise.css` is **never linked** by any
   page, so its palette is dead code that still confuses maintenance.
7. **Purple/indigo gradient everywhere.** Brand `#4f46e5` (indigo) vs preset
   `#7B61FF` (violet) — the two "brand" colours clash, and the nav, logo, hero,
   buttons and text gradients all use different purple families.
8. **Too many accent colours** — 8 `card-accent-*` gradients on the home page,
   9 chart colours, plus 7 `--color-accent-*` tokens.
9. **Gradient overuse** on logo, avatar, hero, buttons, badges, page title
   gradient text, and the Dashboard Studio hero banner.
10. **Status colours are not a distinct system.** `--color-accent-red/green/amber`
    are used both for status *and* as decorative card accents, so success/error
    do not read as a dedicated language.
11. **No consistent focus ring** on many interactive elements; `.btn` has no
    `:focus-visible` rule.
12. **Chart palette is Wong-based** (correct for accessibility) but is duplicated
    in two places (CSS vars + `web/ts/charts/theme.ts`) and re-sorted in dark mode,
    which breaks series identity between themes.

### Layout / hierarchy gaps
13. **Home page is a landing page, not an app home.** Big centred hero with a
    64px gradient icon, then a "Quick Actions" card grid — repeats the same
    card pattern 8+ times with rainbow accents. No density, no hierarchy.
14. **The "Tools" dropdown holds ~17 items** with no grouping or search — the
    "too many menu items" problem.
15. **Page headers are inconsistent** — some pages use `.page-header` with an
    icon tile, others (Analysis) use a bare tab strip with no title/subtitle.
16. **Tables have no responsive strategy** — `.data-table` is a plain wide table.
17. **Inconsistent control sizes** — `.btn-sm` is 6px/12px at 12px font, but
    toolbar buttons and ribbon buttons use their own paddings.
18. **Dark-mode contrast** — `.home-card-desc` uses `#94a3b8` on `#1e293b`
    (≈2.5:1 in dark) — fails WCAG AA for body text.

## Plan

### Phase 1 — Fix the broken things (foundation)
- Remove the duplicate `.hidden` at `styles.css:3331`; keep one canonical
  `.hidden` **before** the responsive breakpoint block so `sm:`/`md:`/`lg:`
  overrides win.
- Replace the two non-existent FA icons with valid 6.5.1 equivalents
  (`fa-chart-network` → `fa-network-wired`, `fa-rows` → `fa-table-list`,
  `fa-clock-rotate` → `fa-clock-rotate-left`).
- Stop `widgetPresets.js` from stamping global theme tokens inline on `<html>`.
  Scope the preset theme to the widget/canvas subtree it belongs to.

### Phase 2 — Build the real design system
- Consolidate to **one** `:root` token set in `styles.css`; delete the dead
  `enterprise.css` palette (or fold it in) so there is a single source of truth.
- Introduce a **professional enterprise palette**: a single restrained brand hue
  (deep blue), a proper neutral ramp, and a **dedicated semantic status set**
  (`success` / `warning` / `danger` / `info`) that is never used decoratively.
- Add spacing/radius/elevation/typography scales as tokens.
- Replace decorative `card-accent-*` rainbow gradients with a single restrained
  accent applied uniformly.
- Unify the chart palette into one token set, shared by CSS and `ts/charts/theme.ts`.

### Phase 3 — Rebuild the shell (top nav, sidebar, page frame)
- Flatten the gradient top nav to a solid, calm surface with a 1px hairline
  border; unified icon buttons; a proper app-title lockup (visible wordmark).
- Group the Tools menu into labelled sections; add a nav search.
- Standardise the page frame: consistent page header (icon, title, subtitle,
  actions), consistent content max-width and gutters.

### Phase 4 — Components
- One button scale (`sm` / `md` / `lg`) with explicit `primary` / `secondary` /
  `ghost` / `danger` roles, visible focus rings, and a clearly disabled state.
- Cards: one card style, no nested cards, subtle 1px border + minimal shadow.
- Tables: sticky header, zebra, hover row, responsive card-mode under 640px.
- Forms: consistent label/control heights, help text, error text, focus rings.
- Empty / loading / error states unified.
- Dark theme: re-derive every token and verify WCAG AA contrast.

### Phase 5 — Verify
- Screenshot every page in light + dark at desktop / tablet / mobile.
- Re-run the utility-class audit → 0 missing.
- Check contrast ratios programmatically.
- Run the existing test suite.

## What shipped

### Bugs fixed (all browser-verified, all covered by a regression test)
| # | Bug | Fix |
|---|-----|-----|
| 1 | The "DataAgent" wordmark never rendered on desktop — a duplicate `.hidden { display:none !important }` sat *after* the `sm:` breakpoint block and beat it | Removed the duplicate; the responsive display helpers now carry `!important` so they always win (`styles.css`, `.hidden` declared exactly once) |
| 2 | The nav bell, dark-mode toggle and mobile-menu button were white-on-white after the nav became a light surface | Rebuilt the header markup onto `topnav-icon-btn` / `brand-tile` / `brand-wordmark` classes |
| 3 | Light/dark theme switching was dead — `widgetPresets.js` stamped theme tokens onto `<html>` as inline styles, which beat every stylesheet rule | The preset theme is now scoped to the widget/canvas element it belongs to |
| 4 | `fa-chart-network`, `fa-rows`, `fa-clock-rotate` and 6 more icons do not exist in the vendored Font Awesome 6.5.1 — they rendered as blank tiles | Replaced with verified 6.5.1 equivalents; a test now fails the build if any icon is missing |
| 5 | The mobile nav drawer could never be opened — `#mobile-menu-toggle` had no click handler | Wired the toggle, the overlay and link-click close in `app.js` |
| 6 | 244 utility classes used in markup were declared nowhere and silently did nothing | New token-driven `web/css/utilities.css` (510 rules) — every colour resolves to a design token, never a raw hex |
| 7 | Two competing `:root` palettes existed; `web/css/enterprise.css` was dead code holding a second, conflicting palette | Deleted `enterprise.css`; `styles.css` is now the single source of truth |
| 8 | Buttons in dark mode kept the UA default black text on a dark background | `button, input, select, textarea { color: inherit; background-color: var(--color-surface) }` |
| 9 | Chart series changed colour when the theme flipped (the dark palette was re-sorted) | Light and dark palettes now keep the same hue ORDER; `charts.js` reads the palette from the CSS tokens |
| 10 | `bg-purple-50` + `text-purple-600` rendered 1:1 — the text was invisible on ~12 surfaces | A dedicated `--color-accent-purple-bg` tint token; the foreground and the background are now different colours |
| 11 | The top-nav connection dot was a transparent 7×7 box — the health check targeted three element ids that do not exist | `_setConnectionState()` targets `#status-dot` / `#status-text` / `#connection-status`; the dot has a visible resting colour |
| 12 | The sign-in card paired `var(--color-text)` with a hardcoded `#fff` — unreadable in dark mode | The auth and account screens use `var(--color-surface)` and token colours |
| 13 | The focus ring was a translucent halo (1.5:1, below WCAG 2.2 SC 2.4.11) and the redesign deleted a previously compliant outline | `--ring` is a solid brand ring and the outline is restored on buttons and icon buttons |
| 14 | The mobile nav drawer could not be closed and had no ARIA state | A real toggle with `aria-expanded` / `aria-controls`, Escape-to-close, and a real scrim |
| 15 | The export page offered nine format buttons in nine different colours | One button role for all of them — a format is a choice, not a status |

### Design system
- **One brand hue** (`--color-primary: #2563eb`) replacing the indigo/violet clash. The old preset purple `#7B61FF` and brand indigo `#4f46e5` are gone.
- **A neutral ramp** (`--color-bg`, `--color-surface`, `--color-surface-2/3`, `--color-border`, `--color-text*`).
- **A reserved semantic status set** (`--color-success/warning/danger/info` + `-bg` variants) that is never used decoratively. Status KPIs now pair the colour with a `.kpi-status-dot` so colour is never the only signal.
- **Scales** for spacing, radius, elevation, and type.
- **Dark theme** re-derived from the same tokens — a deep navy (`#0b1220`), not pure black, with desaturated accents, not neon.
- **A dedicated chart palette** shared by the CSS and `web/ts/charts/theme.ts`.

### Component work
- **Buttons**: 4 roles (`primary` / `secondary` / `ghost` / `danger`) × 4 sizes (`xs`/`sm`/`md`/`lg`), no hover lift, no coloured glow, a visible focus ring, and an unambiguous disabled state. Destructive actions are always red. A separate set of `*-solid` tokens keeps a filled button legible in both themes.
- **Cards**: one style app-wide — surface bg, 1px border, minimal shadow, no nested cards, no gradient top-bars. The 9-way rainbow `card-accent-*` system is now a set of muted icon tints.
- **Tables**: sticky header, 1px row borders, hover row, and a **stacked card mode under 640px** so they stay readable on a phone.
- **Forms**: 34px controls, 12px labels, `--ring` focus, plus new `.form-help` / `.form-error`. Controls inherit the app text colour and surface, so nothing keeps the UA's black-on-grey.
- **States**: unified `.empty-state`, `.skeleton`, `.error-state`, `.badge*`, `.modal*`.
- **Icon buttons**: a new `.icon-btn` family for row actions — neutral by default, red only when the action destroys.
- The **Dashboard Studio hero** went from a full-bleed purple gradient slab to a calm surface with a brand accent rule.
- The **Tools menu** is grouped into Analysis / Data / Build / Organisation instead of a flat list of 17.
- The **sign-in screen** is a calm app canvas with a neutral card, not a purple gradient wall.

### Verification
- `tests/test_ui_design_system.py` — 38 regression tests locking in every bug above.
- `scripts/ui_audit.py` — a static audit: missing utilities, missing icons, undefined custom properties, cross-file duplicate selectors, raw hex, gradient inventory, token-contract conformance. Currently **PASS, 0 errors**.
- `scripts/audit_css_overlap.py` — cross-file selector overlap report.
- `scripts/gen_utilities.py` — regenerates `web/css/utilities.css` byte-for-byte.
- `python -m pytest tests/ -q` — **3053 passed, 3 skipped**.
- 15 pages rendered in both themes with **0 console errors**; **0 WCAG AA contrast failures** (measured in a real browser with alpha-composited backgrounds, 4.5:1 for body text, 3:1 for large text).
- Two code-review passes: the first found 4 blockers + 11 should-fix items, all fixed; the second confirmed every one and found 3 more narrow ones (an undefined token, a duplicated component rule, and the surviving old-violet hexes), all fixed.

### Numbers
| Metric | Before | After |
|---|---|---|
| Raw hex colours in `styles.css` | 1,076 | 470 |
| Gradients in `styles.css` | 89 | 14 (all data-visualisation) |
| Utility classes used but not declared | 244 | 0 |
| Selectors declared in two stylesheets with different bodies | 41 | 0 |
| Font Awesome icons that don't exist in 6.5.1 | 9 | 0 |
| Undefined custom properties (silent declaration drop) | 1 | 0 |
| WCAG AA contrast failures (15 pages × 2 themes) | 146 | **0** |
| Competing `:root` palettes | 2 | **1** |
| Regression tests for the redesign | 0 | **38** |
| Full test suite | 3,033 passed | **3,053 passed, 3 skipped** |

## Constraints
- No build step. Plain CSS + vanilla JS. The app is served statically.
- One writer on the DuckDB warehouse — do not run ETL concurrently with the dev server.
- Do not regress: auth, RBAC, tenant gating, or the security invariants in `AGENTS.md`.
