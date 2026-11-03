# A1 — Shot List

**Video:** "ChatGPT gives you a chart. This gives you a real `.pbix`."
**Length:** 21.8 s · **Format:** 1080×1920, 30 fps, H.264/AAC
**Source captures:** 1920×1200 (app and Power BI), 1500×820 (File Explorer).
**Delivery:** screen-only real captures; no avatar, no live recording.
**Final file:** `dataagent-a1-vertical.mp4` (this folder).

**Rule:** the first frame is proof, not a logo. The cut opens on the real
`.pbix` report; the brand sits small in the top-left.

---

## Timeline

| Time | Visual | Audio / caption | Notes |
|---:|---|---|---|
| 0:00.000–0:02.275 | Power BI Desktop: the exported report, KPIs 239.57K / 2.00K / 10.36K / 120, table and charts | "ChatGPT gives you a chart." | Full-window fit crop `(0,0,1920,1060)`; taskbar excluded. |
| 0:02.275–0:03.937 | File Explorer: `Sales Overview.pbix` selected, 18 KB | "This gives you a `.pbix`." | Close crop `(240,150,480,300)`; filename and size visible. |
| 0:03.937–0:05.612 | DataAgent upload: messy CSV chip → schema preview | "I imported a messy CSV" | File crop `(505,240,865,280)`; schema `(505,570,865,605)`; mixed dates, 10 region spellings, 5 nulls. |
| 0:05.612–0:07.037 | Chat prompt: `Show total profit by region as a bar chart` | "and asked one question." | Crop `(430,410,1060,710)`; heading + input fully visible. |
| 0:07.037–0:09.375 | Result chart: WEST 17.5K, SOUTH 16.9K, NORTH 14.8K, EAST 12.8K + result table | "It wrote the SQL and drew the chart." | Crop `(565,140,840,487)`; no clipped title or sidebar toggle. |
| 0:09.375–0:10.400 | Dashboard present mode: 240K / 1,996 / 10K / 120, charts, table | "Then I exported a `.pbix`." | Full 1920×1200 dashboard; dark treatment. |
| 0:10.400–0:11.375 | Export Data: Power BI Report (`.pbix`) + success message | — (visual continues the export line) | Crop `(480,50,960,510)`; "Sales Overview.pbix downloaded" visible. |
| 0:11.375–0:14.587 | Power BI Desktop opens the exported file; full report, then close-up of measures/slicers/120 rows | "Opened it in Power BI Desktop, real file." | Full `(0,0,1920,1060)` fit; close-up `(55,220,1295,790)` so the Category slicer and Records card are not cut. |
| 0:14.587–0:18.862 | Subscription: Current plan Pro; "Pro trial active — valid until 2026-11-09"; no card | "Export is Pro. The 14-day trial unlocks it, no card." | Crop `(400,40,1000,625)`; demo-only billing-config note redacted. |
| 0:18.862–0:21.800 | End card: Free Windows download; URL; Windows 10/11 x64 · About 440 MB · No card; Link in bio | "Free Windows download. Link in bio." | Crossfade 18.862–19.312; pill at 20.487; end-card button ends at 1488. |

---

## Overlays

- **Brand (all screen beats):** small `DA` tile + `DataAgent` top-left; `LOCAL-FIRST BY DESIGN` chip top-right.
- **Card kickers/insights:** `REAL .PBIX`, `THE OUTPUT`, `MESSY INPUT / SCHEMA PREVIEW`, `ONE QUESTION`, `SQL → CHART`, `DASHBOARD`, `THE EXPORT`, `POWER BI DESKTOP / REAL FILE`, `PRO · 14-DAY TRIAL`.
- **Burned captions:** final `captions-a1.srt`; starts 0.000, 2.275, 3.937, 5.612, 7.037, 8.187, 9.375, 11.375, 13.537, 14.587, 15.775, 18.862, 20.487; caption 11 breaks after "it,"; caption 13 becomes the pill.
- **End card:** `Free Windows download`; `data-agent.me/download`; `Windows 10/11 x64 · About 440 MB · No card`; `Link in bio.`
- **Disclosure (social copy):** `AI-generated voice; product demo is real screen capture.`

---

## Capture checklist

- [x] App at `http://127.0.0.1:8002`; dashboard `8eda1186-2778-4e15-a49e-d8167e99ef78`.
- [x] Public synthetic CSV (`messy_supermarket_sales.csv`, 9.1 KB); no client data.
- [x] Dark app captures: upload selected, schema, prompt, chart, dashboard, export, subscription.
- [x] Power BI frames from the verified `.pbix`; KPIs 239.57K / 2.00K / 10.36K / 120; normalized table.
- [x] File Explorer capture with `Sales Overview.pbix` selected; 18 KB shown.
- [x] Trial active; "Pro trial active — valid until 2026-11-09" visible.
- [x] Clean PBI v8 frame chosen; the error-state refresh frame discarded.
- [x] Taskbar excluded from PBI (`(0,0,1920,1060)`).
- [x] Internal demo note "Billing site URL is not configured" redacted from the subscription crop.
- [x] End card is the designed `end-card-vertical.png`; no broken web page at 1080×1920.
- [x] Voice: `en-US-AndrewNeural` +0% (21.53 s); alternate `en-IN-PrabhatNeural` +45% (22.0 s).
- [x] Audio: synthesized license-free pad + subtle whooshes/ticks; −14.1 LUFS, true peak −1.4 dBFS.

---

## Edit checklist / final QA

- [x] 1080×1920, 30 fps, H.264; 21.8 s; 4,280,253 bytes.
- [x] First encoded frame is the real Power BI report; no black pre-roll.
- [x] Audio 48 kHz stereo AAC, 192 kbps; −14.1 LUFS integrated; −1.4 dBFS true peak.
- [x] Captions burned at the final starts; last caption ends at 21.800.
- [x] Safe area: screen cards end at 890; captions at ~1248–1390; end-card button ends at 1488; bottom 432 px clear.
- [x] Right side: the longest caption ends at ~965; lower-half right 115 px clear.
- [x] PBI proof: full report and close-up show the real KPIs, table, slicers and 120 records.
- [x] End-card CTA readable: headline, URL, Windows line, Link in bio.
- [x] Claims: `.pbix` is real; export is Pro; 14-day trial, no card; Windows 10/11 x64; about 440 MB (443 MB installer); 18 KB file; 25 AI requests/day free.
- [x] No "No upload", "never leaves your machine", "fully offline" or other absolute privacy claims.
- [x] Crossfade only into the end card; no live cursor/refresh claim; chart + result table carry the SQL beat.
- [x] Tests: `python -m pytest tests/test_cov_export.py tests/test_chat_visuals.py -q` → 61 passed.
- [x] Final file: `docs/marketing/video-assets/dataagent-a1-vertical.mp4`.

---

## Re-shoot notes

If the cut is ever re-recorded, keep the same proof order: real `.pbix` → file
in Explorer → messy CSV + schema → one prompt → chart → dashboard → export →
Power BI → trial → end card. Capture at 1920×1200 with notifications off, no
bookmarks and a clean desktop; reframe to 1080×1920 in the renderer. The
original live-recording path (OBS/Game Bar at 1920×1080) remains valid, but the
delivered cut uses stills with slow push-ins so every proof frame is sharp.
