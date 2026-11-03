# DataAgent — Instagram poster series

Poster art mirroring the "Enterprise Software / Without Enterprise Complexity" style
reference the user supplied on 2026-10-27: brand top-left, big navy headline,
subline, CTA pill, trust chips, screenshot panels with floating stat cards, blue
ribbon artwork.

## Final deliverables (6-frame carousel)

| File | Frame | Focus |
| --- | --- | --- |
| `dataagent-insta-post.png` | 01 — Hero | "Enterprise analytics. Without the enterprise stack." — laptop with dashboard |
| `dataagent-insta-02-how-it-works.png` | 02 — How it works | 3 numbered steps: Connect a CSV → Ask in plain English → Export |
| `dataagent-insta-03-ask-anything.png` | 03 — AI chat | Real chat reply (chart + stats + table) with "SQL you can inspect" |
| `dataagent-insta-04-dashboards.png` | 04 — Dashboards | Real dashboard window (Region/Category slicers, 240K, Records 120, table) |
| `dataagent-insta-05-pbix-export.png` | 05 — Power BI export | Real Power BI Desktop window + flow `DataAgent → sales-overview.pbix → Power BI Desktop` |
| `dataagent-insta-06-get-started.png` | 06 — Get started | 2×2 capability grid + CTA (+ Windows 10/11, ~440 MB installer chips) |

All 1080×1080 (grid-safe). Frames 02–06 share `assets/poster-base.css`; "NN / 06"
pagination top-right (bottom-right on 02), consistent CTA pill, and the vanilla-HTML
pure HTML/CSS source (Segoe UI system font, no external requests).

Rendering QA: every frame visually inspected at full size; fixed during iteration —
chip/CTA overlaps (02, 06), subline collisions under float cards (04), missing CTA
(03), broken grid stacking on 06 (absolute `.card` inside CSS grid → set
`.grid .cell { position: static; }`), and the `.pbix` badge size claim (see below).

## Source & asset files

- `dataagent-insta-post.html` — hero (self-contained; predates the shared CSS).
- `dataagent-insta-0N-*.html` — frames 02–06 (link `assets/poster-base.css`).
- `assets/poster-base.css` — shared design tokens (brand, eyebrow, headline, sub,
  CTA, chips, cards, dark screenshot panel).
- `assets/dashboard-crop.png` — `captures/dashboard-dark-1920.png` crop
  `(233, 175, 1680, 1035)` → 1200×713 (hero laptop + frame 04 panel).
- `assets/chat-reply.png` — `captures/chat-state2.png` crop `(563, 48, 1377, 910)`
  (bar chart + result + stats + table; the reply to "Show total profit by region…").
- `assets/pbi-desk.png` — Power BI Desktop window from the A1 video build
  (`pbi-v8-refreshed.png` crop `(0, 0, 1920, 1128)` → 1440×846; excludes taskbar,
  keeps the real `sales-overview-v8 • Last saved` title bar and Visualizations pane).

## Re-rendering

Chrome's `--headless` screenshot mode currently hangs on this machine
(sandbox/network-service errors, no PNG written — verified even on
`about:blank`), so the repeatable path is the Chrome DevTools MCP:

1. `emulate` → viewport `1080x1080x2` (fresh override per render if glitches)
2. `navigate_page` → `file:///…/docs/marketing/posters/dataagent-insta-0N-*.html`
3. `evaluate_script` → `document.readyState` round-trip
4. `take_screenshot` → temp PNG (2160²); retry up to 3× on protocol timeout
5. `python -c "from PIL import Image; Image.open(src).resize((1080,1080), Image.LANCZOS).save(dst)"`
6. `emulate` with no viewport + navigate back to `about:blank` (restore tab)

Chrome MCP quirks observed on this machine:

- A screenshot requested immediately after emulate/navigate can race the
  compositor. Two failure shapes: protocol timeout (retry works), or a stale
  **2×2 tiled composition** of the page's top-left region in a 2160² PNG.
  Workaround that fixed it: navigate + evaluate, wait ≈2.5 s (idle shell sleep),
  take one screenshot, and verify the frame visually before downscaling.
- CLI quirks kept from earlier sessions: needs `--no-sandbox`; paths with spaces
  must stay inside double-quoted arguments; dashboard screenshot card columns end
  at asset-x 288 / 592 / 896 / 1200 → align float-card edges to page-x ≈
  348 / 535 / 736 to avoid cutting card text.

## Claims audit (kept verifiable)

- Headlines ("Enterprise analytics, without the enterprise stack", "Three steps.
  No BI team", "Your BI stack. Minus the stack.") are positioning copy — no
  absolute privacy/offline/"never leaves your machine" claims anywhere (house rule).
- "Ask in plain English", "SQL you can inspect", "charts on reply",
  "quick-task prompts" — all visible capabilities in the app (frame 03 shows the
  real reply with the generated result, stats, and table).
- Frame 04 laptop/panel is the real dashboard capture; "TOTAL REVENUE 240K" float
  matches the visible dashboard value; "Everything editable" matches in-app
  dashboard editing. Decorative mini bars carry no numbers.
- Frame 05 "17 KB .pbix in this demo" — measured: the exported
  `sales-overview-v8.pbix` is 17,619 bytes (17.2 KB). Corrected from an earlier
  "180 KB" draft (typo vs. the 18 KB claim seen in launch copy — the poster shows
  the measured number of the exact artifact displayed).
- "Free Windows download", "Windows 10 / 11", "~440 MB installer" — consistent
  with approved launch-site copy; no URLs printed on any poster.
- Sub wording deliberately avoids "upload" phrasing in shared frames (02/04/06)
  to keep local-first positioning; frame 06 says "CSV in, Excel in and out,
  .pbix out" (all supported).

## Status

- All six frames rendered and QA'd visually. **Not committed** (marketing/docs
  stay uncommitted until the pending exporter/chat.js review follow-ups are
  resolved).
