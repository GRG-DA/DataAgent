# ADR-022 — A1 vertical promo: crop, layout and proof rules

**Date:** 2026-10-27
**Status:** Accepted for the A1 launch cut
**Relates to:** ADR-010 (v1 is local-first), `docs/marketing/video-assets/script-a1.md`,
`docs/marketing/video-assets/shot-list-a1.md`, `export/pbix_export.py`,
`tests/test_cov_export.py`, `web/js/pages/chat.js`

## Context

The A1 short is a 21.8 s, 1080×1920, 30 fps screen-only promo. It has to prove,
in one vertical cut, that a messy CSV becomes a question, a chart, a dashboard
and a **real** Power BI `.pbix` — then show the trial and the download CTA.

Three constraints shaped the edit:

1. The source screens were captured at desktop size (1920×1200 app/Power BI,
   1500×820 File Explorer). A naive 16:9 card in a 9:16 frame either shrinks
   the proof text or crops the report's right side.
2. The Power BI export had a real layout bug: a 36 px slicer band clipped the
   dropdown and rendered a blank card. The report also had to stay 1280×720
   (16:9) for the export.
3. The demo subscription page exposed an internal note ("Billing site URL is
   not configured for this environment"). It is environment state, not a
   customer claim, and must not ship in the promo.

The old script also described a live recording, a SQL text panel, and a refresh
action. The delivered proof is sharper as a sequence of captured stills with
slow push-ins, and the chart + result table carry the SQL beat without showing
internal query text.

## Decision

### 1. Frame and safe area

- Output is **1080×1920, 30 fps, H.264/AAC**, 21.8 s.
- Screen cards are **1000×625 at `x=40, y=270`**. Brand sits top-left;
  kicker and insight sit directly below the card.
- Burned captions sit at **`y≈1248`**; the **bottom 432 px** stays clear of
  platform UI. The end-card button ends at **1488 px**.
- The lower-half right **120 px** is reserved for platform action buttons. The
  widest caption ("Opened it in Power BI Desktop,") ends at ~965 px.

### 2. Captures and crops

- Use **real captured stills**, not a live cursor recording. Slow push-ins
  (1.03–1.06) and hard cuts between beats; one crossfade only into the end card
  (18.862–19.312 s).
- Crop convention in the renderer is `(x, y, width, height)`; convert to PIL
  `(left, top, right, bottom)` exactly once.
- Power BI full-window: `(0, 0, 1920, 1060)` with `fit`. This keeps the complete
  report width and excludes the Windows taskbar.
- Power BI close-up: `(55, 220, 1295, 790)` with `fit`, so the Category slicer
  and the Records KPI are not clipped.
- Explorer: `(240, 150, 480, 300)` around the selected `Sales Overview.pbix`
  file, keeping the 18 KB size legible.
- Upload: file chip `(505, 240, 865, 280)`; schema preview
  `(505, 570, 865, 605)`. The schema proves mixed date formats, 10 region
  spellings and 5 nulls.
- Prompt: `(430, 410, 1060, 710)`, including the input and suggestion row.
- Chart: `(565, 140, 840, 487)` with `fit`; the crop removes the clipped
  conversation header and the sidebar toggle, leaving the chart and result line
  as the proof.
- Dashboard: full 1920×1200 present-mode capture.
- Export: `(480, 50, 960, 510)` with the success message visible.
- Subscription: `(400, 40, 1000, 625)`. Redact only the demo environment's
  billing-config note with a background-color fill; leave the plan and trial
  line untouched.
- End card: full-bleed `end-card-vertical.png`; "Link in bio." pill is
  360×72 at `(360, 1416)`, appearing at 20.487 s.

### 3. Power BI export fix

- Slicers use **Dropdown** mode and the **`Values`** role.
- Slicer containers are **72 px high** so the header and the full dropdown
  render; the old 36 px band was the cause of the blank white card.
- Header band layout and the **1280×720** 16:9 page are preserved.
- Regression tests cover slicer mode, role, height, header placement and the
  16:9 page (`tests/test_cov_export.py`).

### 4. Voice, music and claims

- Voice: **`en-US-AndrewNeural`, +0%** (21.53 s). Alternate:
  `en-IN-PrabhatNeural`, +45% (22.0 s). The script is 56 words.
- Audio: synthesized, license-free pad (Am7 → Fmaj7 → Cmaj9) plus short UI
  whooshes/ticks. Final mix **−14.1 LUFS integrated, −1.4 dBFS true peak**,
  48 kHz stereo AAC.
- The final cut uses the chart + result table as the SQL proof, the opened
  report + close-up as the `.pbix` proof, and the subscription page as the
  trial proof. It does not claim a live refresh or show SQL text.
- Claims are limited to what the capture proves: 18 KB file, 120 records,
  4 regions, Pro trial active until 2026-11-09, 14-day trial / no card, about
  440 MB (installer 463,680,019 bytes ≈ 442 MiB), Windows 10/11 x64.
- No absolute privacy claims ("No upload", "never leaves your machine",
  "fully offline"). The bed is local-first by design; cloud AI remains optional.
- The AI-generated voice is disclosed in the social copy: "AI-generated voice;
  product demo is real screen capture."

## Consequences

- The delivered file is
  `docs/marketing/video-assets/dataagent-a1-vertical.mp4`
  (4,280,253 bytes; 1080×1920; 30 fps; 21.8 s; H.264/AAC). The encoded first
  frame is the Power BI report; there is no black pre-roll.
- The Power BI proof is readable at phone size without shrinking the full
  report into an unreadable 16:9 card.
- The end card starts 2.94 s before the end, so the CTA is not rushed; the
  bottom 432 px stays free of platform chrome.
- The subscription redaction is visible only as a clean background; the plan
  and trial line remain real. A future re-shoot must repeat the redaction.
- The renderer/audio scripts live outside the repo in
  `%TEMP%\opencode\dataagent-video`; the asset pack contains the final MP4 and
  the docs. Rebuilding requires those scripts or a fresh edit.
- The A1 MP4 and marketing assets remain uncommitted until the release owner
  decides to publish; no commit or push was performed as part of the cut.

## Verification

- `python -m pytest tests/test_cov_export.py tests/test_chat_visuals.py -q`
  → **61 passed**.
- `ffprobe`: 1080×1920, 30/1 fps, 21.800 s, H.264 + AAC 48 kHz stereo,
  4,280,253 bytes.
- `ebur128`: −14.1 LUFS integrated; true peak −1.4 dBFS.
- Frames inspected at 0.0, 0.3, 2.8, 4.2, 4.8, 6.2, 7.4, 8.6, 9.8, 10.8,
  12.5, 14.0, 16.4, 18.5, 19.4, 20.9 and 21.7 s: the first frame is the PBI
  proof, captions are legible, the PBI slicer and Records card are visible, the
  end-card CTA is clear, and there is no clipped title or prompt nub.
- The python-reviewer also ran `tests/test_pbix_fallback.py` and
  `tests/test_cov_export2.py` → **74 passed**.

## Review follow-ups (not blocking the delivered cut)

- **Python exporter (HIGH risk):** the `Values` slicer role is not emitted as
  an active `dataTransforms` item. The captured v8 report shows the slicer
  rendering, so the delivered PBI proof is intact; before the next generated
  `.pbix`, add the active-role predicate (and update `verify_export.py`) plus a
  regression assertion on `dataTransforms`. The reviewer also flagged
  header-band title/z-order overlap, blankdash multi-page flattening,
  slicer-only dashboards, half-pixel rounding, and the 11+ slicer fallback.
- **chat.js chart fit:** the visible-path fix used for the A1 chart is correct.
  Follow-ups are the hidden/late-render fallback, Plotly `automargin` for long
  axis labels, container-resize handling, and routing `v.html` through the
  HTML sanitizer. The delivered chart frame was captured on the visible path.
- No commit or push was made; these follow-ups should be addressed before the
  exporter/UI changes are committed.
