# A1 — Full Video Script

**Title:** ChatGPT gives you a chart. This gives you a real `.pbix`.
**Length:** 21.8 seconds (21–22 s)
**Format:** 1080×1920 vertical, 30 fps, H.264/AAC
**Presenter:** None — screen-only real screen capture (no avatar)
**Voice:** `en-US-AndrewNeural`, +0% rate, natural US English, analyst-to-analyst, no hype.
Final read 21.53 s. Alternate: `en-IN-PrabhatNeural`, +45%, 22.0 s.

---

## Delivered cut

The final file is `dataagent-a1-vertical.mp4` in this folder. It was built from
real captured stills (1920×1200 app/Power BI, 1500×820 Explorer), not a live
screen recording. Cards are 1000×625 at `x=40, y=270`; hard cuts between beats;
one crossfade into the end card at 18.86 s. The PBI full-window cards use a
fit crop so the complete report width is preserved and the Windows taskbar is
excluded. The chart, schema and subscription captures are cropped to the proof.

## Script

| Time | Visual | Voiceover | On-screen text / proof |
|---:|---|---|---|
| 0:00–2.275 | Real exported `.pbix` open in Power BI Desktop: KPIs 239.57K / 2.00K / 10.36K / 120, table, charts | “ChatGPT gives you a chart.” | Brand + `REAL .PBIX` · “Sales Overview · 120 rows · 4 regions” |
| 2.275–3.937 | File Explorer: `Sales Overview.pbix` selected, 18 KB | “This gives you a `.pbix`.” | `THE OUTPUT` · “Sales Overview.pbix · 18 KB · opens natively” |
| 3.937–5.612 | DataAgent upload: messy CSV chip, then schema preview | “I imported a messy CSV” | `MESSY INPUT` · `SCHEMA PREVIEW` · “mixed date formats · 10 region spellings · 5 nulls” |
| 5.612–7.037 | Chat prompt: “Show total profit by region as a bar chart” | “and asked one question.” | `ONE QUESTION` · “asked in plain English” |
| 7.037–9.375 | Result chart: WEST 17.5K, SOUTH 16.9K, NORTH 14.8K, EAST 12.8K; result table | “It wrote the SQL and drew the chart.” | `SQL → CHART` · the four values |
| 9.375–10.400 | Dashboard present mode: 240K / 1,996 / 10K / 120, charts, table | “Then I exported a `.pbix`.” | `DASHBOARD` · “120 records · 4 regions · KPI cards” |
| 10.400–11.375 | Export Data panel: Power BI Report (`.pbix`) and success message | — (visual continues the export line) | `THE EXPORT` · “Sales Overview.pbix downloaded” |
| 11.375–14.587 | Power BI Desktop opens the exported file; full report, then close-up of measures, slicers and 120 rows | “Opened it in Power BI Desktop, real file.” | `POWER BI DESKTOP` · `REAL FILE` · “measures · slicers · 120 rows” |
| 14.587–18.862 | Subscription: Current plan Pro; “Pro trial active — valid until 2026-11-09”; no card | “Export is Pro. The 14-day trial unlocks it, no card.” | `PRO · 14-DAY TRIAL` · “Pro trial active · paid features unlocked” |
| 18.862–21.800 | End card: Free Windows download; URL; Windows 10/11 x64 · About 440 MB · No card; Link in bio | “Free Windows download. Link in bio.” | `Free Windows download` · `data-agent.me/download` · `Link in bio.` |

The final cut does **not** show a separate SQL text panel, a refresh action or a
trial banner. The chart + result table are the SQL proof; the opened report and
close-up are the `.pbix` proof; the subscription page is the trial proof.

---

## Full voiceover

```text
ChatGPT gives you a chart.
This gives you a .pbix.

I imported a messy CSV
and asked one question.

It wrote the SQL
and drew the chart.

Then I exported a .pbix.

Opened it in Power BI Desktop,
real file.

Export is Pro.
The 14-day trial unlocks it, no card.

Free Windows download.
Link in bio.
```

**Word count:** 56. The selected Andrew +0% read lands at **21.53 s**; the video
is 21.8 s. If the delivery runs long, slow the CTA rather than rush the proof.
The Indian-English alternate is `en-IN-PrabhatNeural` at +45% (22.0 s).

---

## Screen direction

- No presenter, no avatar; every frame is real screen capture or the end card.
- Captured stills at 1920×1200 (app/PBI) and 1500×820 (Explorer); slow
  1.03–1.06 push-ins, hard cuts between screen beats.
- Cards: 1000×625 at `x=40, y=270`. Brand top-left; kicker and insight directly
  below the card. Captions at `y≈1248`; the bottom 432 px stays clear.
- PBI full-window shots use a fit crop over `(0, 0, 1920, 1060)`; this preserves
  the full report width and excludes the taskbar. The close-up uses
  `(55, 220, 1295, 790)` so the Category slicer and the Records card are not cut.
- Explorer uses a close crop around the selected `Sales Overview.pbix` file so
  the filename and the 18 KB size are legible.
- The subscription capture redacts the demo environment's internal
  “Billing site URL is not configured” note; the trial line is untouched.
- Crossfade only into the end card (18.862–19.312); no crossfades between
  screen beats.

---

## Edit notes

- First frame is the real `.pbix`; never open on a logo or a black pre-roll.
- Burn captions from `captions-a1.srt` at the Edge-TTS boundaries. Caption 11
  breaks after “it,”; caption 13 becomes the “Link in bio.” button.
- Keep the bottom 420 px clear. The screen cards end at `y=890`; the last
  caption pixel is ~1390; the end-card button ends at 1488.
- Deliver the CTA slightly slower than the body.
- The end card starts at 18.862 s and holds for 2.94 s; the “Link in bio.”
  button appears at 20.487 s.
- Final mix: −14.1 LUFS integrated, true peak −1.4 dBFS, 48 kHz stereo AAC.
  The bed is a synthesized, license-free pad with subtle UI whooshes/ticks; the
  pad is ducked ~4.5 dB under the voice, including the SQL line.
- Keep the right 120 px clear in the lower half for platform action buttons.
  The long caption “Opened it in Power BI Desktop,” is the widest line; it ends
  at ~965 px, leaving 115 px on the right.

---

## Caption (Instagram)

```text
ChatGPT gives you a chart. This gives you a real .pbix.

I imported a messy CSV, asked one question, and exported a real Power BI file
— then opened it in Power BI Desktop.

The export is a Pro feature. The 14-day trial unlocks it, no card.

Free Windows download + 14-day Pro trial, no card — link in bio.

AI-generated voice; product demo is real screen capture.

#PowerBI #DAX #DataAnalytics #LocalAI #BusinessIntelligence #DataModeling
```

## Title + description (YouTube Shorts)

**Title:** `ChatGPT gives charts. This exports a real Power BI file`

```text
Ask a CSV a question and export a real Power BI .pbix — local-first on Windows.
The .pbix export is a Pro feature; the 14-day trial unlocks it, no card.

Free download: https://data-agent.me/download
Windows 10/11 x64.

AI-generated voice; product demo is real screen capture.

#Shorts
```

## Pinned comment

```text
Trial is 14 days, no card. Export is Pro; free tier is 25 AI requests/day. Ask me anything about the install.
```

## Disclosure

The delivered cut uses an AI-generated voice (Edge TTS
`en-US-AndrewNeural`). The product demo is real screen capture. Keep the
disclosure in the social caption/description; use the platform’s AI-generated
media label where available.

---

## Alternate hooks (for testing)

1. “This is a real `.pbix`, not a screenshot.”
2. “I asked a messy CSV one question. It gave me a Power BI file.”
3. “Most AI tools stop at a chart. This one exports the file.”
4. “Your client data can stay on the laptop — and still become a `.pbix`.”

Keep the same body and CTA; change only the first 3 seconds.
