# DataAgent — 30-Day Instagram + YouTube Plan

**Status:** Ready for execution after the P0 gates in `preflight-checklist.md`.
**Window:** 4 weeks, target start Monday 2 November 2026.
**Owner:** Solo founder.
**Goal:** First 30 days of distribution for DataAgent — free Windows downloads, 14-day Pro trial starts, and an audience of Power BI / Excel analysts.

Targets are hypotheses to validate, not guarantees:

| Metric | 30-day target |
|---|---:|
| Unique shorts (cross-posted to Reels + Shorts) | 12 (24 platform posts) |
| Long-form YouTube videos | 1 |
| GitHub release downloads | 75+ |
| Pro trial starts | 15+ |
| YouTube subscribers | 100+ |
| Instagram followers | 250+ |
| Real conversations (DM/comment/email) | 5+ |
| Pieces of product feedback | 3+ |

---

## 1. Funnel

```
Short / Reel / Long-form
        ↓
Profile or video description
        ↓
data-agent.me/download
        ↓
DataAgent-Setup.exe (~440 MB, Windows 10/11 x64)
        ↓
Ollama one-click + ~4.7 GB local model (optional for AI chat)
        ↓
First question on a real CSV / XLSX
        ↓
Subscription page → Start 14-day Pro trial (no card)
        ↓
.pbix export / import unlocked
        ↓
Pro ₹499/mo or Lifetime ₹10,000
```

**Known leaks to fix before launch:**

1. The public release README says v1.0.0, 508 MB, AGPL, “Every feature works”, “Mac/Linux Docker build” — it contradicts the site. Rewrite it from `release-repo-readme.publish.md`.
2. ~~The captions link “Source code” to the website repo, not the app release.~~ Resolved: the current kit links only `data-agent.me/download` and has no “Source code” line.
3. ~~The site claims AGPL-3.0 but the public release repo does not publish source.~~ Resolved 2026-10-26: the site claim was removed, the source stays private for now, and a test now rejects unbacked open-source/AGPL claims. See `docs/decisions/ADR-021-private-source-until-published.md`. Do not use “open source” in social until a source repo is live. Resolved 2026-11-03: the source repo went live at github.com/GRG-DA/DataAgent under PolyForm Noncommercial 1.0.0; keep avoiding “open source” wording (the licence is noncommercial, not OSI open source).
4. SmartScreen shows “Unknown publisher” because the installer is not code-signed. Show this in the install video instead of letting users discover it alone.
5. AI chat needs Ollama plus a ~4.7 GB model download. State this before the CTA.
6. `.pbix` export and import are Pro features. Never imply they are free; the 14-day trial is the honest bridge.
7. The marketing site has no analytics by design. Trial starts are the north star; platform insights and GitHub download counts are supporting signals.

---

## 2. Positioning

**Category:** Local AI data analyst for Windows.

**One-liner:** Ask your CSV or Excel file a question in plain English, get a chart and the SQL, then export a real Power BI `.pbix` — on your own machine.

**Enemy:** Cloud AI tools that ask you to upload client data, and chat tools that stop at a screenshot.

**Differentiators to show, not tell:**

- A real `.pbix` that opens in Power BI Desktop — not an image.
- Local DuckDB warehouse and local model support.
- The SQL is visible and editable.
- Excel-style workbench for analysts who still live in spreadsheets.
- Free tier is real: 25 AI requests/day, 3 dashboards, 7 days of history.
- 14-day Pro trial, no card, unlocks `.pbix` export/import.

**Voice:** Analyst-to-analyst. Specific. Show the file, the SQL, the error, the fix. No “revolutionary AI” language.

**Primary CTA:**

> Free Windows download + 14-day Pro trial, no card: https://data-agent.me/download

**Secondary CTA:** “Comment `PBIX` and I’ll send the install guide.”

---

## 3. Content Pillars

| Pillar | Promise | Shorts | Long-form |
|---|---|---:|---|
| **A. CSV → real `.pbix`** | “One messy CSV becomes a Power BI file you can open and edit.” | 4 | 1 |
| **B. Existing `.pbix`, DAX, and getting started** | “Ask the report you already have a question — then get the app installed.” | 4 | — |
| **C. Local, offline, honest free tier** | “Your data stays on your laptop; the trial is real; the limits are real.” | 4 | — |

Each pillar is one 20–30 minute screen-recording session. Cut four vertical shorts from each session. Do not record 12 separate sessions. B4 also needs a separate 3-minute install clip.

---

## 4. Production Workflow

**Batch one pillar per week.**

1. **Sunday (90 min):** pick dataset, write the 4 hooks, rehearse the demo once.
2. **Monday (90 min):** record one clean screen session at 1080p or 1440p, 16:9. Capture the full workflow, including one mistake.
3. **Tuesday (90 min):** cut four 9:16 shorts in CapCut. Hook in the first 1.5 seconds. Burn in captions. Add end card.
4. **Wednesday (45 min):** write captions, titles, pinned comments from `insta-youtube-captions.md`.
5. **Daily (15 min):** reply to comments and DMs.
6. **Friday (30 min):** schedule next week, review numbers, note one change.

**Recording stack:** OBS Studio or Windows Game Bar, a USB or lav mic, CapCut, Canva for end cards. Total cash cost: ₹0–2,500.

**Demo dataset:** use a public, relatable dataset — supermarket sales, coffee-shop orders, or a messy Excel export with duplicate headers. Never show client data.

---

## 5. Four-Week Calendar

| Week | Day | # | Pillar | Short title | Primary platform |
|---|---|---|---:|---|---|
| 1 | Mon 2 Nov | A1 | A | “ChatGPT gives you a chart. This gives you a `.pbix`.” | Reels + Shorts |
| 1 | Wed 4 Nov | A2 | A | “This is a real `.pbix`, not a screenshot.” | Reels + Shorts |
| 1 | Fri 6 Nov | A3 | A | “What you actually get free — and what the trial unlocks.” | Reels + Shorts |
| 1 | Sun 8 Nov | L1 | A | Long-form: “CSV → Power BI in one sitting” | YouTube |
| 2 | Mon 9 Nov | A4 | A | “25 free AI requests a day — what that actually does.” | Reels + Shorts |
| 2 | Wed 11 Nov | B1 | B | “I asked my existing `.pbix` a question.” | Reels + Shorts |
| 2 | Fri 13 Nov | B2 | B | “It writes the DAX I used to Google.” | Reels + Shorts |
| 3 | Mon 16 Nov | C1 | C | “Client data can stay on the laptop.” | Reels + Shorts |
| 3 | Wed 18 Nov | C2 | C | “ChatGPT vs DataAgent: chart vs `.pbix`.” | Reels + Shorts |
| 3 | Fri 20 Nov | B3 | B | “3 reports I stopped rebuilding.” | Reels + Shorts |
| 4 | Mon 23 Nov | C3 | C | “I don’t know SQL.” | Reels + Shorts |
| 4 | Wed 25 Nov | C4 | C | “Excel analysts: the upgrade path.” | Reels + Shorts |
| 4 | Fri 27 Nov | B4 | B | “Install + first question in 3 minutes.” | Reels + Shorts |

**Posting time:** 7:30–9:00 pm IST for Instagram; 6:30–8:00 pm IST for YouTube. Test one morning slot in week 3.

**Long-form:** publish Sunday 8 November, 8–12 minutes, chapters:

- 00:00 The problem
- 00:45 Install and first launch
- 02:00 Import the CSV
- 04:00 Ask the first question
- 06:00 Fix one wrong chart
- 08:00 Export the `.pbix`
- 10:00 Open it in Power BI Desktop
- 11:00 Trial and pricing

---

## 6. Instagram Playbook

**Handle:** pick one and reserve the same name on YouTube: `@dataagent.app`, `@dataagent.me`, or `@dataagent.hq`.

**Name field:** `DataAgent | Local AI for Power BI` — searchable, not just a logo.

**Bio:**

> Local AI data analyst for Windows.
> CSV/XLSX → real `.pbix`.
> 14-day Pro trial, no card.
> ↓ Free download
> data-agent.me/download

**Pinned posts:** A1, A3, C1.

**Format rules:**

- 20–35 seconds. Hook in the first 1.5 seconds.
- 9:16, 1080×1920, burned-in captions.
- Show the screen 80% of the time. Face only in the hook and CTA if comfortable.
- One idea per Reel. One CTA per Reel.
- End card: “Free download + 14-day trial — link in bio.”
- Hashtags: 5–8 targeted tags, not 30. Rotate sets from the caption kit.
- No watermarks from other apps. No reposted TikToks.
- Add AI-generated presenter disclosure if you use an avatar.

**Stories (3×/week):**

- Monday: poll — “Would you upload client data to an AI tool?” Yes / No.
- Wednesday: 15-second behind-the-scenes of the demo.
- Friday: Q&A box — “What’s the messiest CSV you deal with?”

**Highlights:** `Install`, `Demos`, `Pricing`, `FAQ`.

**Engagement:**

- Reply to every comment in the first hour.
- DM everyone who comments `PBIX` with the download link and a one-line install note.
- Do not buy followers, engagement pods, or mass-follow.

---

## 7. YouTube Playbook

**Channel name:** DataAgent.
**Handle:** same as Instagram.
**Banner text:** “Local AI data analyst for Windows. CSV → real `.pbix`.”

**Channel description:**

> DataAgent is a local-first AI data analyst for Windows. Ask a CSV or Excel file a question, get the SQL and a chart, then export a real Power BI `.pbix`. Free download, 14-day Pro trial, no card. Windows 10/11 x64.
> https://data-agent.me/download

**Shorts:**

- Same 12 videos as Reels, uploaded natively. Do not add a TikTok watermark.
- Title: keyword first, under 60 characters.
- Description: 2–3 lines + trial disclosure + link + `#Shorts`.
- Pinned comment: trial link and one install tip.
- Playlist: `DataAgent in 30 seconds`.

**Long-form:**

- One 8–12 minute walkthrough in month 1.
- Keyword targets: `CSV to Power BI AI`, `Power BI local AI`, `chat with CSV offline`, `DataAgent tutorial`.
- Thumbnail: screen + big text “CSV → .PBIX” + small Windows badge.
- End screen: subscribe + next short.
- Description: chapters, trial disclosure, install requirements, links.

**Community tab:** one poll per week — “What should I demo next?” with 3 concrete options.

**SEO keywords:** `Power BI AI`, `local AI data analyst`, `CSV to Power BI`, `chat with Excel`, `offline AI analytics`, `DuckDB`, `DAX generator`, `Windows data analysis`.

---

## 8. Measurement

**North star:** Pro trial starts per week.

**Weekly scorecard:**

| Metric | Source | Week 1 | Week 2 | Week 3 | Week 4 |
|---|---|---:|---:|---:|---:|
| Shorts/Reels published | Manual | | | | |
| Instagram views | IG insights | | | | |
| Instagram profile visits | IG insights | | | | |
| Instagram link clicks | IG insights | | | | |
| YouTube Shorts views | YT Studio | | | | |
| YouTube subscribers | YT Studio | | | | |
| GitHub downloads | Release asset count (not unique users) | | | | |
| Trial starts | App/billing DB | | | | |
| Paid conversions | Billing DB | | | | |
| Comments/DMs | Manual | | | | |

**Decision rules:**

- **3-second view rate < 50%** → rewrite hooks; show the `.pbix` file in the first frame.
- **High views, low profile visits** → add a clearer verbal CTA and on-screen handle.
- **High profile visits, low link clicks** → fix bio and pinned post.
- **High downloads, low trial starts** → improve the in-app trial prompt and the install video’s “what next” step.
- **High trial starts, low paid** → interview 3 trial users; inspect the moment the trial ends.
- **One format wins twice** → make two more of that format before inventing a new one.

**Attribution caveat:** the marketing site has no analytics by design (ADR-013). Platform insights, GitHub download counts, and trial starts are the honest signals. Ask trial users “where did you find us?” in the first-week check-in.

---

## 9. Weekly Review

**Monday, 30 minutes:**

1. Fill the scorecard.
2. Name the best and worst post.
3. Write one sentence: “Next week I will do more of ___ and less of ___.”
4. Check the P0 gates are still true — especially the release README and source claim.

**Friday, 15 minutes:** schedule next week, confirm the demo dataset, charge the mic.

**End of month, 60 minutes:** decide one of three:

- **Double down:** two formats are working; increase cadence to 4/week.
- **Adjust:** views are fine but trial starts are weak; fix the funnel before making more content.
- **Stop/reshape:** no signal after 12 honest posts; change the offer or the audience, not just the thumbnails.

---

## 10. Budget and Time

| Item | Cost |
|---|---:|
| OBS, CapCut, Canva, Windows Game Bar | ₹0 |
| USB/lav mic (optional) | ₹1,500–2,500 |
| Paid boost test (week 4 only, optional) | ₹500–1,000 |
| Total | ₹0–3,500 |

**Time:** 7–9 hours/week.

- Record: 2 h
- Edit: 2 h
- Captions/titles: 1 h
- Engagement: 1 h
- Review/planning: 1 h
- Buffer: 1–2 h

---

## 11. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Open-source claim is not backed by published source | Resolved for launch: claim removed from the site (ADR-021); do not use “open source” in social until a source repo is live. Legal review of the binary’s AGPL obligation remains a follow-up. Resolved 2026-11-03: the source repo went live at github.com/GRG-DA/DataAgent under PolyForm Noncommercial 1.0.0; keep avoiding “open source” wording (the licence is noncommercial, not OSI open source). |
| Release README says “testing build” | P0: replace with the draft in `release-repo-readme.publish.md` |
| SmartScreen “Unknown publisher” | Show it in the install video; add a one-line warning in the caption |
| 4.7 GB model download | State it before the CTA; offer a cloud provider as an alternative |
| Windows-only | Say “Windows 10/11 x64” in every video and caption |
| No site analytics | Use trial starts and platform insights; ask new users how they found you |
| Trial users don’t convert | Interview 3 trial users; fix the value moment before increasing spend |
| Solo-founder burnout | Batch one pillar per week; keep a 2-video buffer |
| Negative “AI slop” comments | Reply with a screen recording, not a debate; pin the best proof |

---

## 12. 30 / 60 / 90

**Days 1–30:** ship 12 shorts, 1 long-form, 75+ downloads, 15+ trial starts. Establish baselines. Fix the funnel leaks.

**Days 31–60:** double down on the two best formats. Publish 2 long-forms. Add one customer case study. Pitch 5 micro-creators in the Power BI/Excel niche with a free Lifetime licence.

**Days 61–90:** run a live demo/webinar. Test ₹500–1,000 paid boost on the best organic post. Add a referral or affiliate offer. Decide whether Lifetime or Pro is the primary conversion.

---

## 13. Definition of Done — Month 1

- [ ] All P0 gates in `preflight-checklist.md` are green.
- [ ] 12 shorts/Reels published.
- [ ] 1 long-form published with chapters.
- [ ] 75+ GitHub release downloads.
- [ ] 15+ Pro trial starts.
- [ ] 100+ YouTube subscribers.
- [ ] 250+ Instagram followers.
- [ ] 5+ real conversations with analysts.
- [ ] 3+ pieces of product feedback logged.
- [ ] 4 weekly reviews completed.
- [ ] Claim audit signed off.

---

## Related docs

- `preflight-checklist.md` — what must be true before posting.
- `claim-audit.md` — approved and forbidden wording.
- `insta-youtube-captions.md` — 12 copy-paste captions, titles, pinned comments.
- `ai-video-prompts.md` — screen-first shot lists and revised AI avatar scripts.
- `release-repo-readme.publish.md` — public release README draft.
- `../BILLING.md` — plans, limits, trial, feature keys.
- `../decisions/ADR-013-no-analytics-on-the-marketing-site.md` — why the site has no analytics.
