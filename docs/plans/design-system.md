# DataAgent Design System — Shared Contract

Every agent working on this redesign MUST use these tokens. Do not invent new
colours, do not hardcode hex values in markup, and do not add a second palette.

## 1. Palette — one brand hue, one neutral ramp, four semantic status colours

### Brand (deep professional blue — replaces the indigo/violet clash)
```
--color-primary:        #2563eb   /* the single brand hue */
--color-primary-hover:  #1d4ed8
--color-primary-active: #1e40af
--color-primary-light:  #eff6ff   /* tinted background */
--color-primary-border: #bfdbfe
--color-primary-rgb:    37, 99, 235
--color-on-primary:     #ffffff
```
Rule: the brand hue is used for **primary actions, active nav state, links, and
focus rings only**. Never for decoration, never for status.

### Neutral ramp (slate) — the entire UI is built from this
```
--color-bg:            #f1f5f9   /* app canvas */
--color-surface:       #ffffff   /* cards, panels, menus */
--color-surface-2:     #f8fafc   /* subtle fills: table head, hover, wells */
--color-surface-3:     #f1f5f9   /* deeper fill: code blocks, empty states */
--color-border:        #e2e8f0
--color-border-strong: #cbd5e1
--color-text:          #0f172a   /* primary text — 17.4:1 on surface */
--color-text-secondary:#475569   /* secondary text — 7.5:1 on surface */
--color-text-muted:    #5a6678   /* tertiary/labels — 5.3:1 on the canvas */
--color-text-disabled: #94a3b8
--color-text-inverse:  #ffffff
--color-on-solid:      #ffffff   /* ink on a saturated fill — NEVER inverts */
```

`--color-on-solid` exists because `--color-text-inverse` flips to near-black
in dark mode. A class named `.text-white` that renders near-black is a trap,
so white-on-colour uses `--color-on-solid`, which is white in both themes.

### Accent purple
```
--color-accent-purple:    #7c3aed   /* foreground */
--color-accent-purple-bg: #f3e8ff   /* TINT for a `-50` background */
```
A `bg-*-50` tint and its `text-*-600` foreground must resolve to **different**
colours. The tint token is what keeps that true — pointing both at
`--color-accent-purple` renders the text invisible at 1:1.

### Semantic status — a dedicated, colour-blind-safe status language
These are **never** used decoratively. Status is always icon + label + colour.
```
--color-success:      #15803d   --color-success-bg:   #dcfce7
--color-warning:      #b45309   --color-warning-bg:   #fef3c7
--color-danger:       #b91c1c   --color-danger-bg:    #fee2e2
--color-info:         #1d4ed8   --color-info-bg:      #dbeafe
```
Dark equivalents (see section 6).

### Solid fills — for FILLED surfaces behind white text
The semantic colours above are tuned for **text on a light surface**. A filled
button with white text needs a darker shade, so the semantic colours are
lightened in dark mode and white-on-them would fail WCAG AA. Filled surfaces
use the `*-solid` tokens, which stay deep in both themes.
```
--color-success-solid:      #15803d
--color-warning-solid:      #b45309
--color-danger-solid:       #b91c1c
--color-info-solid:         #1d4ed8
--color-accent-purple-solid: #7c3aed
--color-accent-teal-solid:  #0d9488
```
Rule: `.btn-primary` / `.btn-danger` / `.btn-success` and any `bg-*-500`
utility that sits behind white text use a `-solid` token. The plain semantic
token is for text, icons and tinted backgrounds.

### Chart palette — one shared set, read from CSS by the TS layer
```
--chart-1: #2563eb   --chart-2: #0d9488   --chart-3: #b45309   --chart-4: #7c3aed
--chart-5: #be123c   --chart-6: #0369a1   --chart-7: #4d7c0f   --chart-8: #64748b
```
Series order must NOT change between light and dark — only the lightness is
adjusted in dark mode, so a series keeps its identity when the theme flips.

## 2. Typography
```
--font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
--font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
--text-xs: 11px   --text-sm: 12px   --text-base: 13px
--text-md:  14px   --text-lg:  16px   --text-xl:  20px   --text-2xl: 24px
```
Base body copy is 13–14px. **Never below 11px.** No more than two font weights
per surface (600 for headings/labels, 500/400 for body). No gradient text.

## 3. Spacing, radius, elevation
```
--space-1: 4px   --space-2: 8px   --space-3: 12px  --space-4: 16px
--space-5: 20px  --space-6: 24px  --space-8: 32px  --space-10: 40px

--radius-sm: 4px   --radius-md: 6px   --radius-lg: 8px   --radius-xl: 12px
--radius-pill: 999px

--shadow-xs: 0 1px 2px rgba(15,23,42,.05)
--shadow-sm: 0 1px 3px rgba(15,23,42,.08)
--shadow-md: 0 4px 12px rgba(15,23,42,.10)
--shadow-lg: 0 12px 32px rgba(15,23,42,.14)
--ring: 0 0 0 3px var(--color-primary)   /* focus ring - SOLID */
```
Shadows are for **elevation only** (popovers, modals, sticky bars). Cards get a
1px border + `--shadow-xs`, never a large glow.

## 4. Component rules

### Buttons — 4 roles, 3 sizes, one shape
Roles: `primary` (brand fill), `secondary` (surface + border),
`ghost` (transparent, hover fill), `danger` (destructive — **always red**).
Sizes: `sm` (28px h), `md` (34px h), `lg` (40px h). All share `--radius-md`.
- Primary and secondary must be clearly distinct (fill vs. outline).
- Danger buttons are always `--color-danger` fill or a danger-tinted outline —
  never the brand colour.
- Disabled: `opacity:.5` + `cursor:not-allowed` + `pointer-events:none`.
- Every button has a `:focus-visible` ring using `--ring`.

### Cards
- One card style app-wide: `--color-surface` bg, 1px `--color-border`,
  `--radius-lg`, `--shadow-xs`.
- **No cards inside cards.** Nested content uses `--color-surface-2` wells.
- No decorative gradient top-bars, no rainbow per-card accents.

### Tables
- Sticky header (`--color-surface-2`), 1px row borders, 13px text,
  zebra optional, hover row `--color-surface-2`.
- Under 640px, `.data-table` switches to a stacked card layout.

### Forms
- 34px control height (`.form-control`), `--radius-md`, 1px border.
- Label above control, 12px `--color-text-secondary`, 4px gap.
- Focus: border `--color-primary` + `--ring`.
- Error: border `--color-danger` + 12px message in `--color-danger`.

### Status
Never rely on colour alone — pair with an icon and a text label.

### Icons
- Font Awesome Free 6.5.1 only. Verify every icon exists in
  `web/vendor/fontawesome/css/all.min.css` before using it.
- One visual weight: `fas` (solid) for all UI icons. No mixing `far`/`fas`.

## 5. Layout
- Top nav: solid surface, 1px bottom border, 52px tall. **No gradient.**
- Page header: icon tile (32px, `--color-primary-light` bg) + title (18px,
  700) + subtitle (13px, `--color-text-secondary`) + actions on the right.
- Content max-width 1600px, 24px gutters (12px under 768px).
- Page padding and card gaps must be identical across pages.

## 6. Dark theme
Every token above gets a dark override. Dark bg is `#0f172a`-family, **not**
pure black, and accents are desaturated — not neon.
```
--color-bg: #0b1220   --color-surface: #131c2e   --color-surface-2: #1a2436
--color-border: #24304a --color-text: #e8eef7    --color-text-secondary: #a3b1c6
--color-text-muted: #8494ab
--color-primary: #60a5fa  --color-primary-light: #16243f
--color-success: #4ade80  --color-warning: #fbbf24  --color-danger: #f87171
```
The `*-solid` tokens keep their LIGHT-theme values in dark mode — a filled
surface with white text must stay dark in both themes.

## 7. Hard rules
1. **One palette.** `web/css/styles.css` `:root` is the single source of the
   design tokens. (`web/css/enterprise.css` held a second, conflicting
   palette and was never linked — it has been removed.) `web/css/utilities.css`
   is the single utility layer; it must not be duplicated into `styles.css`.
2. No gradient on buttons, cards, nav, avatars, or text.
3. No new accent colours for decoration.
4. Status colours are reserved.
5. All colours come from tokens; no raw hex in HTML/JS.
6. Every interactive element is keyboard reachable with a visible focus ring.
7. `prefers-reduced-motion` respected.
