# DataAgent — AI Video Prompts and Screen-First Shot Lists

**Updated:** 2026-10-26. Replaces the earlier avatar-first scripts that implied a free `.pbix` export.

**Principle:** The product is the proof. Record the screen first; use an AI presenter only as a hook or voice, never as a substitute for the demo.

**Use with:** `30-day-plan.md`, `insta-youtube-captions.md`, `claim-audit.md`.

---

## 1. What Changed

- The old scripts said or implied “export a `.pbix`” without saying it is a Pro feature. The new scripts say: “Pro feature; the 14-day trial unlocks it; no card.”
- The old scripts leaned on an AI avatar. The new plan is screen-first: real CSV, real SQL, real `.pbix`, opened in Power BI Desktop.
- The old scripts did not mention Windows-only, installer size, Ollama, or the model download. The new scripts set expectations before the CTA.
- No “open source,” “unlimited,” “100% private,” or “zero telemetry” language.
- If an AI-generated presenter is used, disclose it on screen and in the caption.

---

## 2. Production Stack

| Job | Tool | Cost |
|---|---|---|
| Screen recording | OBS Studio or Windows Game Bar | ₹0 |
| Editing / captions | CapCut | ₹0 |
| End cards / thumbnails | Canva | ₹0 |
| Mic | USB or lav | ₹0–2,500 |
| Demo data | Public CSV/XLSX | ₹0 |
| Power BI proof | Power BI Desktop | ₹0 |

**Recording settings:** 1920×1080 or 2560×1440, 30 fps, clean desktop, notifications off, browser bookmarks hidden.

**Demo data:** use a public supermarket-sales or coffee-shop dataset. Make a messy copy with duplicate headers, mixed date formats, and blank rows. Never show client data.

---

## 3. Pillar A — CSV → Real `.pbix`

**Record one 25–30 minute session. Cut four shorts + one long-form.**

### Shot list

| Time | Shot | Voiceover beat |
|---:|---|---|
| 0:00–0:03 | Messy CSV open in Explorer; scroll past duplicate headers | “This CSV is a mess.” |
| 0:03–0:08 | Open DataAgent; drag the CSV in | “I’m importing it into DataAgent — a local Windows app.” |
| 0:08–0:15 | Type: “Show total profit by region as a bar chart” | “One question, in plain English.” |
| 0:15–0:22 | SQL panel appears; 4-region bar chart renders, West highest | “It writes the SQL, runs it, and gives me the chart.” |
| 0:22–0:30 | Change the dimension; fix a wrong chart | “When it’s wrong, I fix it in one click.” |
| 0:30–0:40 | Click Export → `.pbix`; show the trial banner | “Then I export a real `.pbix`. That’s a Pro feature — the 14-day trial unlocks it, no card.” |
| 0:40–0:50 | Double-click the `.pbix`; Power BI Desktop opens; show table + measure | “Here it is in Power BI Desktop. Real file, real measures.” |
| 0:50–1:00 | End card: “Free download + 14-day trial” + URL | “Free Windows download. Link in bio.” |

### Cutdowns

- **A1:** hook + import + export + Power BI proof.
- **A2:** double-click the `.pbix`; prove it is a file, not an image.
- **A3:** free-vs-trial comparison; show the subscription page.
- **A4:** request counter; ask three questions; show the cap.

---

## 4. Pillar B — Existing `.pbix`, DAX, and Getting Started

**Record one 25–30 minute session. Cut three shorts (B1–B3). B4 is a separate 3-minute install clip.**

### Shot list

| Time | Shot | Voiceover beat |
|---:|---|---|
| 0:00–0:04 | Existing `.pbix` open in Power BI Desktop | “This is a report I already had.” |
| 0:04–0:10 | Import it into DataAgent | “I’m importing the `.pbix` — not a CSV.” |
| 0:10–0:18 | Ask: “Which region declined last quarter?” | “I ask the report a question.” |
| 0:18–0:25 | Chart + SQL/DAX panel | “It gives me the chart and the SQL behind it.” |
| 0:25–0:35 | Show a generated DAX measure; open it in Power BI Desktop | “It writes DAX for the KPI widgets. I still review it.” |
| 0:35–0:45 | Export the updated `.pbix`; reopen | “Then I export the updated file — Pro trial, no card.” |
| 0:45–0:55 | End card | “Free download. Link in bio.” |

### Cutdowns

- **B1:** existing `.pbix` → question → chart.
- **B2:** generated DAX → review in Power BI.
- **B3:** three saved dashboards; reuse instead of rebuild.
- **B4:** install + first question; include the SmartScreen warning.

---

## 5. Pillar C — Local, Offline, Honest Free Tier

**Record one 20–25 minute session. Cut four shorts.**

### Shot list

| Time | Shot | Voiceover beat |
|---:|---|---|
| 0:00–0:04 | Wi-Fi icon off; laptop on desk | “Client data can stay on the laptop.” |
| 0:04–0:10 | Ollama running; local model loaded | “Local model running.” |
| 0:10–0:18 | Ask a question; answer + chart | “I ask a question and get the answer.” |
| 0:18–0:25 | Show DuckDB file / local warehouse | “The warehouse is a local DuckDB file.” |
| 0:25–0:35 | Free vs Pro vs Lifetime table | “The free tier is real: 25 requests a day. The trial is 14 days, no card.” |
| 0:35–0:45 | Subscription page; Start trial button | “The trial unlocks `.pbix` export and import.” |
| 0:45–0:55 | End card | “Free Windows download. Link in bio.” |

### Cutdowns

- **C1:** Wi-Fi off; offline question.
- **C2:** ChatGPT chart vs DataAgent `.pbix`.
- **C3:** “I don’t know SQL” — show the generated SQL.
- **C4:** Excel → workbench → `.pbix` handoff.

---

## 6. Twelve Shorts — Visual Recipe

| # | First frame (0.0s) | Middle proof | End card |
|---|---|---|---|
| A1 | `.pbix` open in Power BI Desktop | CSV import → question → export | Trial + URL |
| A2 | Double-click the `.pbix` file | Power BI Desktop loads table + measure | Trial + URL |
| A3 | Split screen “Free” / “Trial” | Subscription page + trial button | Trial + URL |
| A4 | Request counter at 25 | Three questions + chart | Trial + URL |
| B1 | Existing `.pbix` in Power BI | Import → question → chart | Trial + URL |
| B2 | DAX formula bar | Generated measure → review | Trial + URL |
| C1 | Wi-Fi off | Question → answer, offline | Trial + URL |
| C2 | ChatGPT chart | Side-by-side with `.pbix` | Trial + URL |
| B3 | Three dashboards | Open one, reuse it | Trial + URL |
| C3 | Plain-English question | Generated SQL → chart | Trial + URL |
| C4 | Excel sheet | Import → workbench → dashboard | Trial + URL |
| B4 | Download page | Installer → SmartScreen → first question | Trial + URL |

**Hook rule:** the first frame must show the proof — the `.pbix`, the messy CSV, the Wi-Fi-off icon, or the DAX. Never open on a logo.

---

## 7. Revised PixVerse / AI Avatar Scripts

Use these only if you want an AI presenter. Keep the screen demo as the primary visual. Add the disclosure line.

### Prompt 1 — “Chart vs `.pbix`” (15 s, 9:16)

```text
Vertical 9:16 cinematic screen-demo style. A clean Windows laptop on a desk, soft daylight. On screen: a messy CSV opens, then the DataAgent interface, a question typed in plain English, SQL and a bar chart appear, then a file named report.pbix is exported and opened in Power BI Desktop. Calm, precise, no flashy AI hype. Leave the lower third clear for captions. No logos other than DataAgent. 15 seconds.
```

**Voiceover**

```text
ChatGPT gives you a chart. This gives you a .pbix.

I imported a messy CSV, asked one question, and exported a real Power BI file.

The export is a Pro feature — the 14-day trial unlocks it, no card.

Free Windows download. Link in bio.
```

**On-screen disclosure:** `AI-generated presenter; product demo is real screen capture.`

### Prompt 2 — “Your existing report” (15 s, 9:16)

```text
Vertical 9:16. An analyst's desk, screen recording inserts. An existing Power BI .pbix opens, is imported into DataAgent, a question is typed, a chart and SQL appear, a DAX measure is shown, then the updated .pbix is exported and reopened in Power BI Desktop. Clean, credible, no sci-fi visuals. Leave the lower third clear. 15 seconds.
```

**Voiceover**

```text
If you already have a Power BI report, you don't need to rebuild it.

Import the .pbix, ask a question, get the SQL and a chart, then export the updated file.

That's the Pro trial. No card.

Free Windows download. Link in bio.
```

### Prompt 3 — “Offline” (15 s, 9:16)

```text
Vertical 9:16. A laptop on a desk, Wi-Fi router switched off, network icon showing no connection. The DataAgent interface answers a question and renders a chart with a local model. Calm, private, no cloud imagery. Leave the lower third clear. 15 seconds.
```

**Voiceover**

```text
Client data can stay on the laptop.

Wi-Fi off. Local model running. I asked a question and got the answer.

DataAgent runs a local DuckDB warehouse and supports local models through Ollama.

Free Windows download. 14-day Pro trial, no card. Link in bio.
```

### Prompt 4 — B-roll (10 s, 9:16)

```text
Macro shots: a messy CSV, an Excel sheet, a SQL query, a .pbix file icon, a laptop keyboard, a coffee cup. No people. Vertical 9:16. Calm, editorial, shallow depth of field. Leave the center clear for text. 10 seconds.
```

---

## 8. Editing Recipe

**Format:** 1080×1920, 30 fps, H.264, 8–12 Mbps.

**Safe zones:**

- Top 220 px: keep clear of platform UI.
- Bottom 420 px: keep clear of captions and CTA.
- Right 120 px: keep clear of like/comment buttons.

**Captions:**

- 2–4 words per line; bold sans-serif; white with dark outline.
- Highlight one key word per line (`.pbix`, `trial`, `local`).
- Burn in captions; do not rely on auto-captions alone.

**Pacing:**

- Hook in 1.5 s.
- Cut every 2–4 s; no dead air.
- One proof shot per 5 s.
- End card 2.5 s.

**Audio:**

- Voice at −14 LUFS; music at −28 to −24 LUFS.
- Noise reduction on; no music during the SQL/DAX explanation.
- No trending audio that fights the voice.

**Screen recording:**

- Record 16:9; punch in 1.2× for vertical.
- Do not crop UI text; reframe around the panel.
- Hide bookmarks, notifications, and personal files.

**End card:**

```text
Free Windows download
14-day Pro trial, no card
data-agent.me/download
```

No QR code in a short. No “link in comments” bait.

---

## 9. Do Not Say

- “Free `.pbix` export.”
- “Unlimited AI.”
- “100% private” / “zero telemetry.”
- “Open source” until the source is published.
- “Works on Mac/Linux.”
- “Replaces Power BI.”
- “No internet needed.”
- “GDPR/HIPAA compliant.”
- “AI does everything.”
- “Set it and forget it.”

---

## 10. Asset Checklist

- [ ] Public demo CSV + messy copy (no client data).
- [ ] Existing `.pbix` sample (no client data).
- [ ] DataAgent installed on the recording machine.
- [ ] 14-day Pro trial active; `.pbix` export verified.
- [ ] Power BI Desktop installed.
- [ ] Ollama + qwen2.5:7b downloaded (for offline shots).
- [ ] OBS/Game Bar tested; mic levels checked.
- [ ] End card template in Canva.
- [ ] Thumbnail template: screen + “CSV → .PBIX” + Windows badge.
- [ ] Captions from `insta-youtube-captions.md`.
- [ ] Claim audit open; forbidden phrases checked.
- [ ] AI disclosure ready if using an avatar.
