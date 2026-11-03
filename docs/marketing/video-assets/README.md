# DataAgent — A1 Video Asset Pack

Everything needed to publish, reproduce or re-shoot **A1: "ChatGPT gives you a
chart. This gives you a real `.pbix`."**

**Folder:** `docs/marketing/video-assets/`

---

## 1. What's in the pack

| Asset | File | Use |
|---|---|---|
| Final vertical cut | `dataagent-a1-vertical.mp4` | 1080×1920, 30 fps, 21.8 s, H.264/AAC, 4.3 MB — delivered |
| Full video script | `script-a1.md` | Delivered timeline, voiceover, caption, hooks |
| Voiceover script + timing | `voiceover-a1.txt` | 56 words, 21.53 s read; `en-US-AndrewNeural` +0% |
| Burned-in captions | `captions-a1.srt` | 13 blocks, 0.000–21.800; final Edge-TTS boundaries |
| Shot list | `shot-list-a1.md` | Final 10-beat cut, crops, capture + QA checklist |
| Vertical end card | `end-card-vertical.html` + `.png` | 1080×1920; full-bleed from 18.862 s |
| YouTube end card | `end-card-youtube.html` + `.png` | 1920×1080, long-form outro |
| YouTube thumbnail | `thumbnail-youtube.html` + `.png` | 1280×720 |
| Messy demo data | `demo-data/messy_supermarket_sales.csv` | Synthetic; safe to show |
| Clean demo data | `demo-data/supermarket_sales_clean.csv` | For comparison/testing |
| Music + SFX guide | `music-sfx.md` | Final mix levels, SFX map, free sources |
| Free B-roll | `stock-broll.md` | Sources, search terms, 10-second recipe |
| AI presenter prompt (legacy) | `prompt-heygen.txt` | Not used for the screen-only A1 cut |

The final cut is screen-only: every frame is a real screen capture or the
designed end card. No presenter, avatar or stock footage is required.

---

## 2. Delivered cut

**File:** `dataagent-a1-vertical.mp4` · 1080×1920 · 30 fps · 21.8 s ·
4,280,253 bytes · H.264 video + 48 kHz stereo AAC audio. The encoded first
frame is the real Power BI report (no black pre-roll).

**Proof order:** real `.pbix` in Power BI Desktop → `Sales Overview.pbix`
(18 KB) in File Explorer → messy CSV chip and schema preview → one English
prompt → four-region profit chart + result table → dashboard → Export Data
success → the file opened in Power BI Desktop, with a close-up of the measures,
slicers and 120 rows → Pro trial active → end card.

**Production path:**

1. Captured real stills at 1920×1200 (app/Power BI) and 1500×820 (Explorer).
   The app ran at `http://127.0.0.1:8002`; the dashboard is
   `8eda1186-2778-4e15-a49e-d8167e99ef78`.
2. Reframed the stills in a Python/Pillow renderer: 1000×625 cards at
   `x=40, y=270`, slow push-ins, hard cuts, one crossfade into the end card.
   The PBI full-window crops use `(0, 0, 1920, 1060)` with `fit`; the
   close-up uses `(55, 220, 1295, 790)`.
3. Generated the voice with Edge TTS (`en-US-AndrewNeural`, +0%, 21.53 s) and
   mixed it with a synthesized, license-free pad and subtle UI SFX.
4. Loudness-normalized to −14.1 LUFS integrated, true peak −1.4 dBFS.
5. Rendered with ffmpeg H.264/AAC and QA'd the encoded file (duration,
   resolution, fps, caption starts, safe area, PBI proof, end-card CTA).

The renderer and audio build scripts live outside the repo in
`%TEMP%\opencode\dataagent-video` (`build_video.py`, `build_audio.py`);
the source captures are under `captures/`. The marketing pack contains the
final MP4 and every document needed to publish it.

**Final timing:** the end card starts at 18.862 s and holds for 2.94 s. The
`Link in bio.` button appears at 20.487 s. The bottom 432 px stays clear.

---

## 3. Re-shoot or rebuild

1. **Capture the same proof order** — follow `shot-list-a1.md`. Use a clean
   desktop, notifications off, dark app treatment and a light Power BI window.
   Capture at 1920×1200; the vertical renderer crops to 1080×1920.
2. **Use the public demo data** — `demo-data/messy_supermarket_sales.csv` is
   synthetic. Do not substitute client data.
3. **Record the voice or regenerate it** — the delivered voice is
   `en-US-AndrewNeural` +0%; the Indian-English alternate is
   `en-IN-PrabhatNeural` +45% (22.0 s). Keep the 56-word script unchanged.
4. **Burn captions from `captions-a1.srt`** — the final starts are the Edge-TTS
   boundaries; do not retime to the old 2.3 s blocks.
5. **End card** — use `end-card-vertical.png`; the renderer crossfades in at
   18.862 s. Do not hold the old website CTA; the end card is the designed one.
6. **Run the QA list** in `launch-execution-checklist.md` and the final QA in
   `shot-list-a1.md` before scheduling.

---

## 4. Exporting the HTML assets

The HTML files are exact-size canvases. To export:

1. Open the file in Edge or Chrome.
2. Press `F11` for full screen (optional).
3. Use a screenshot tool at the exact viewport:
   - Vertical end card: **1080 × 1920**
   - YouTube end card: **1920 × 1080**
   - Thumbnail: **1280 × 720**
4. Save as PNG.

Ready-made PNGs are included; the HTML is for edits (text, colours, wording).

**Brand tokens used:** background `#070707`, surface `#0d0d0d`, text `#f4f4f4`,
muted `#868686`, line `#2a2a2a`, accent `#c1c1c1`.

**Thumbnail copy:** the third chip is `Local-first by design`. Do not use
absolute privacy claims, including `never leaves your machine` or `fully
offline` — cloud AI is optional and the app checks GitHub for updates.

---

## 5. Demo data

`demo-data/messy_supermarket_sales.csv` is **synthetic** — no client data. It is
deliberately messy:

- a duplicate header row in the middle
- mixed date formats (`2026-07-01`, `01/07/2026`, `Jul 3 2026`)
- inconsistent region casing and trailing spaces
- currency symbols and commas inside numbers
- blank rows and missing values
- one negative profit row

Use it for the import shot; the clean file is only for testing. The final cut
uses the messy file and the schema preview (mixed dates, 10 region spellings,
5 nulls).

---

## 6. Claims and disclosure

- Say: "Windows 10/11 x64", "about 440 MB", "`.pbix` export is Pro", "14-day
  trial, no card", "25 AI requests/day free", "local-first".
- Verified in the delivered cut: the `.pbix` is 17,619 bytes (Explorer shows
  18 KB); the report has 120 records and 4 regions; the free trial is active
  until 2026-11-09; `dist/DataAgent-Setup.exe` is 463,680,019 bytes (≈442 MiB,
  "about 440 MB").
- Never say: free `.pbix`, unlimited, 100% private, zero telemetry, open source,
  Mac/Linux, replaces Power BI, no internet needed, absolute privacy claims
  ("never leaves your machine", "fully offline").
- The delivered cut uses an AI-generated voice. Disclose it in the caption and
  description:
  `AI-generated voice; product demo is real screen capture.`
  Use the platform's AI-generated-media label where available.

---

## 7. Related docs

- `../ai-video-prompts.md` — screen-first shot lists and prompts
- `../insta-youtube-captions.md` — captions, titles, pinned comments
- `../claim-audit.md` — approved wording
- `../launch-execution-checklist.md` — publishing schedule and QA
