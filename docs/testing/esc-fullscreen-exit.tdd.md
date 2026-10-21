# Esc exits fullscreen in the editor — TDD evidence

Source: UI verification round via chrome-devtools MCP (live app, 2026-10-17).
Issues triaged from the verification report:

1. "PII scan gives no toast feedback" — **false positive** (no code change).
2. "Esc does not exit fullscreen in the dashboard editor" — **fixed**.

## Defect 2: Escape could not leave editor fullscreen

`BlankDash._toggleFullscreen()` requests fullscreen on `#page-content`. With
nothing selected, Chrome's native Esc-exit is UA/automation dependent (it did
not fire under MCP-driven keys); with a widget selected, `_onEditKey` called
`e.preventDefault()` when deselecting, and by fullscreen spec a canceled
`keydown` **suppresses the native exit entirely** — so for real users too,
Esc could never leave fullscreen while a widget was selected.

### Contract (new)

- Edit mode, fullscreen active, Esc pressed → `document.exitFullscreen()` is
  called explicitly; selection (if any) is kept.
- Edit mode, no fullscreen, Esc with a selection → still deselects (unchanged).
- Present mode Esc → exits presentation (unchanged, `_onPresentKey` →
  `_exitPresent`, which also exits fullscreen).

### Change

`web/js/pages/blankdash.js` — `_onEditKey` Escape branch (line 10625):

```js
if (k === "Escape") {
  if (document.fullscreenElement) {
    e.preventDefault();
    try { document.exitFullscreen(); } catch (err) {}
    return;
  }
  if (_selectedIds.length) {
    BlankDash._deselect();
    e.preventDefault();
  }
}
```

## Tests (tests/test_blankdash_esc_fullscreen.py — 2 tests)

| # | Guarantee | Test | Result |
|---|-----------|------|--------|
| 1 | Escape branch checks `document.fullscreenElement` and calls `exitFullscreen` | test_escape_exits_fullscreen_in_edit_mode | PASS |
| 2 | Plain Esc (not fullscreen) keeps deselecting | test_escape_still_deselects_without_fullscreen | PASS |

RED (pre-change): test 1 failed with
`'_onEditKey's Escape handling never checks document.fullscreenElement'`;
test 2 passed. GREEN: `python -m pytest tests/test_blankdash_esc_fullscreen.py -v`
→ 2 passed. Regression bundle:
`python -m pytest tests/test_blankdash_viz.py tests/test_blankdash_esc_fullscreen.py -q`
→ 8 passed.

## Live verification (chrome-devtools MCP, disposable dashboard `b411501b…`)

| # | Scenario | Expected | Observed |
|---|----------|----------|----------|
| 1 | Fullscreen, no selection, Esc | fullscreen exits | `fullscreenElement: null` |
| 2 | Fullscreen, widget selected, Esc | fullscreen exits, selection kept | `null` + widget keeps `ff-selected` |
| 3 | Not fullscreen, widget selected, Esc | deselect only | selection cleared |
| 4 | Present mode, Esc | presentation exits | `.bd-present` gone, no fullscreen |

Pre-fix reproductions: (1) `fullscreenElement` stayed `#page-content` (no
preventDefault at document capture); (2) `preventedAtDocCapture: true`,
selection cleared, fullscreen stayed — the spec-level suppression case.

Console after fix: no errors/warnings. Screenshots:
`outputs/ui_verify/mcp--editor-esc-exit-normal-chrome.png`,
`outputs/ui_verify/mcp--pii-scan-toast.png`.

## Defect 1 correction: PII scan toast works

`SettingsPage._scanPii()` (settings.js:1543) shows an info toast
("Scanning tables for PII...") and a success toast ("Found N PII columns").
Live re-test (Settings → Governance → "Scan tables for PII") rendered both
toasts and refreshed the PII Inventory — screenshot
`mcp--pii-scan-toast.png`. The original "no toast" note was a timing artifact
(screenshot taken after the 4 s toast lifetime). No change made.

## Known gaps

- Native Chrome Esc-exit behavior under CDP-synthesized keys is not a reliable
  signal; the explicit `exitFullscreen()` call makes the behavior deterministic
  across UAs and automation.
- Esc does not close the Arrange/Zoom menus (out of scope; reported behaviour
  unchanged).
