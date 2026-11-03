# DataAgent — Marketing Claim Audit

**Purpose:** Keep every public claim provable, specific, and consistent with the product. Use this before writing captions, recording demos, or editing the website.

**Status:** Living document. Last reviewed 2026-10-26.

---

## 1. Claim Matrix

| # | Claim | Status | What is actually true | Approved wording | Evidence |
|---:|---|---|---|---|---|
| 1 | “Export a real Power BI `.pbix`” | ✅ Verified, paid | Export works on Pro and Lifetime; the 14-day Pro trial unlocks it. Free cannot export. | “Export a real `.pbix`. It’s a Pro feature — the 14-day trial unlocks it.” | `api/billing/seed.py:149,169,189`; `api/dashstudio.py:783` |
| 2 | “Import / rebuild an existing `.pbix`” | ✅ Verified, paid | The build-from-PBIX path requires `FEATURE_RECREATE_FROM_IMAGE`; Pro/Lifetime only. | “Import an existing `.pbix` and ask it questions during the Pro trial.” | `api/billing/seed.py:148,168,188`; `api/dashstudio.py:2535` |
| 3 | “25 AI requests a day free” | ✅ Verified | Free tier is 25/day, 3 dashboards, 7-day history. | “Free: 25 AI requests a day, 3 saved dashboards, 7 days of history.” | `docs/BILLING.md`; `tests/test_launch_site_copy.py:673` |
| 4 | “14-day Pro trial, no card” | ✅ Verified | One-time trial, default 14 days, server-enforced single use; UI says no card required. | “Start the 14-day Pro trial in the app — no card required.” | `api/billing/subs.py:465-470`; `web/js/pages/subscription.js:164` |
| 5 | “Your data stays on your machine” | ⚠️ Conditional | Local DuckDB and local model support; the app still checks GitHub for updates, pulls the Ollama model, and can use optional cloud AI. | “Your data stays on your machine. The app checks GitHub for updates and downloads the model once; cloud AI is optional. Paid plans contact the entitlement service.” | `website/download.html:139`; `docs/decisions/ADR-013-no-analytics-on-the-marketing-site.md` |
| 6 | “Works offline” | ⚠️ Conditional | True after the app and local model are installed; install and model pull need internet. | “After the one-time model download, the local workflow works offline.” | `website/download.html:135,139` |
| 7 | “No telemetry” | ⚠️ Do not use in social | The site scopes the claim to the app; the app uses the network for updates/model pull. Product analytics are not independently verified here. | Use “local-first” or “local processing” instead. If used on site, keep the existing scope. | `tests/test_launch_frontend_claims.py:249-268`; `website/download.html:139` |
| 8 | “Open source under AGPL-3.0” | 🚫 Removed from site (2026-10-26) | The release repo does not publish source. The site now says the source code is not published yet; the test rejects any unbacked open-source/AGPL claim. | Do **not** use in social until the source is published. Resolved 2026-11-03: the source is published at https://github.com/GRG-DA/DataAgent under PolyForm Noncommercial 1.0.0. Approved wording: “Source is available at github.com/GRG-DA/DataAgent under PolyForm Noncommercial 1.0.0.” The repo is live and still **not** an open-source claim — the licence is noncommercial, not OSI open source. | `docs/decisions/ADR-021-private-source-until-published.md`; `website/faq.html`; `tests/test_launch_frontend_claims.py:272-300` |
| 9 | “30+ chart types” | ✅ Verified | Site feature list states 30+ chart types. | “30+ chart types, built from your data.” | `website/features.html`; `website/index.html` |
| 10 | “10 LLM providers” | ✅ Verified, conditional | Provider support exists; hosted providers need your key. | “Bring your own key for hosted models, or run a local model.” | `website/features.html`; `website/faq.html` |
| 11 | “9 file formats” | ✅ Verified | Site states 9 formats; demo CSV/XLSX only. | “CSV, Excel and more — nine formats in the import path.” | `website/features.html` |
| 12 | “Excel-style workbench” | ✅ Verified | Spreadsheet workbench is part of the free workflow. | “An Excel-style workbench for analysts who still live in spreadsheets.” | `website/features.html`; `website/faq.html` |
| 13 | “Pro ₹499/mo” | ✅ Verified | Pro is ₹499/month; 50 AI requests/day; 50 dashboards. | “Pro is ₹499/mo.” | `docs/BILLING.md`; `website/billing.html:107-114` |
| 14 | “Lifetime ₹10,000 once” | ✅ Verified | Lifetime is ₹10,000 one-time; 100 AI requests/day; 1,000 dashboards. | “Lifetime is ₹10,000 once.” | `docs/BILLING.md:22`; `website/billing.html:107-114` |
| 15 | “No card for the free tier” | ✅ Verified | Free tier needs no card. Trial also says no card. | “No card for the free tier or the trial.” | `website/faq.html:61,176`; `web/js/pages/subscription.js:164` |
| 16 | “Windows 10/11 x64” | ✅ Verified | Installer targets Windows 10/11 x64. | “Windows 10/11, 64-bit.” | `website/download.html:115` |
| 17 | “~440 MB installer” | ✅ Verified with rounding | Site says 439 MB; release asset is 442 MB. | “About 440 MB.” Avoid a precise number that conflicts across pages. | `website/download.html:12,153`; GitHub release asset |
| 18 | “One-click Ollama install” | ✅ Verified, caveat | The app can install Ollama; the model is a separate ~4.7 GB download. | “One-click Ollama install, then a one-time ~4.7 GB model download.” | `website/download.html:135` |
| 19 | “Scheduled reports / automation” | ✅ Verified, paid | Automation and scheduled reports are Pro/Lifetime features. | “Scheduled reports are a Pro feature.” | `docs/BILLING.md`; `api/billing/seed.py` |
| 20 | “Live database connections” | ✅ Verified, paid | Live connections are Pro/Lifetime features. | “Live database connections are Pro.” | `docs/BILLING.md`; `api/billing/seed.py` |
| 21 | “Self-host free” | ⚠️ Conditional | Docker self-host removes checkout, but the plan still decides limits; an admin can change it. | “Self-hosting removes checkout; the plan still sets the AI cap and tools.” | `website/download.html:87-90` |
| 22 | “AI agent” | ⚠️ Prefer not | The product is a local-first LLM data analytics agent; “agent” can imply autonomy it does not have. | “Local AI data analyst.” | `README.md`; product docs |

---

## 2. Forbidden Phrases

Never use these in a caption, title, thumbnail, website edit, or release README:

- “Unlimited AI” — caps are 25/50/100 requests per day.
- “Free `.pbix` export” — export is Pro/Lifetime, trial unlocks it.
- “100% private” / “zero telemetry” — absolute claims are not proven and ignore update/model-pull traffic.
- “never leaves your machine” / “fully offline” — absolute privacy claims; cloud AI is optional and the app checks GitHub for updates.
- “Open source” — until the source is actually published. The site claim was removed on 2026-10-26; do not reintroduce it in social or copy.
- “Works on Mac/Linux” — Windows 10/11 x64 only.
- “Replaces Power BI” — it exports to Power BI; it does not replace it.
- “No internet needed” — install and model pull need internet.
- “GDPR compliant” / “HIPAA compliant” — no audit evidence.
- “Enterprise-grade security” — no audit evidence.
- “AI does everything for you” — the analyst still reviews SQL, charts, and DAX.
- “Free forever” / “all features free” — the free tier is limited.
- “ChatGPT killer” / “better than ChatGPT” — different jobs; this invites a fight you do not need.
- “Automatically fixes your data” — the app helps; it does not guarantee clean data.

---

## 3. Where Claims Appear

| Surface | Risk | Action |
|---|---|---|
| `website/index.html` | Pricing/feature claims; footer says free tier, paid licences optional | Keep price and feature claims aligned; do not add new absolutes or an open-source claim. |
| `website/faq.html` | Source-status copy; free/paid details; trial line | Keep “source code is not published yet” accurate; do not restore the AGPL claim. |
| `website/billing.html` | Prices and daily caps | Keep in sync with `docs/BILLING.md`. |
| `website/download.html` | Size, requirements, network usage, source status | Keep the ~440 MB and ~4.7 GB caveats; no open-source claim. |
| `website/features.html` | 30+ charts, 10 providers, 9 formats | Do not inflate counts. |
| `web/js/pages/subscription.js` | Trial copy | Keep “no card required” only while true. |
| `docs/marketing/insta-youtube-captions.md` | Old captions implied free `.pbix`, linked the wrong repo | Replaced by the trial-aware kit. |
| `docs/marketing/ai-video-prompts.md` | Old AI avatar scripts implied free export | Replaced by the revised scripts. |
| Public release README (`java22113344/grg-dataagent`) | Still says v1.0.0, 508 MB, AGPL, “Every feature works”, “Mac/Linux Docker build”, “No trial timer”; contradicts the site. | P0: replace with `release-repo-readme.publish.md` draft. Do not just edit the version. |
| GitHub release assets | Installer size/version | Keep `v1.0.1` and ~440 MB consistent. |

---

## 4. P0 Fixes Before Posting

- [x] **Open-source decision.** Removed the claim from the site and replaced the AGPL-naming test with a no-unbacked-claim guard (2026-10-26). See `docs/decisions/ADR-021-private-source-until-published.md`. Legal follow-up: review the AGPL source-offer obligation before distributing at scale.
- [x] **Publication decision (ADR-023).** 2026-11-03: the source is published at https://github.com/GRG-DA/DataAgent under PolyForm Noncommercial 1.0.0; update the “source code is not published yet” copy to point at the repo + licence, and keep avoiding “open source” wording (the licence is noncommercial, not OSI open source). The AGPL binary-obligation review remains the legal follow-up.
- [ ] **Release README.** Replace the “testing build” warning with the draft in `release-repo-readme.publish.md`, or scope it honestly (“early build; core workflow works; known gaps”).
- [x] **Caption source link.** The current kit links only `data-agent.me/download`; no “Source code” line remains (2026-10-26).
- [ ] **Trial verification.** On a clean Windows VM: install, open Subscription, start the 14-day trial, confirm `.pbix` export unlocks, confirm the trial cannot be started twice.
- [ ] **`.pbix` verification.** Export a real file, open it in Power BI Desktop, confirm the data, measures, and slicers survive.
- [ ] **SmartScreen verification.** Record the “Unknown publisher” flow and include it in the install video.
- [ ] **Installer verification.** Confirm the public asset is v1.0.1, ~440 MB, and downloads without a GitHub login.
- [ ] **Pricing verification.** Confirm Pro ₹499/mo and Lifetime ₹10,000 are the prices the checkout actually charges.

## 5. P1 Improvements

- [x] Add the 14-day Pro trial to `website/download.html` and `website/billing.html` (added 2026-10-26; site-copy tests pass).
- [ ] Add social links to the site footer once the accounts exist.
- [x] Add a “What’s new in v1.0.1” line to the download page (2026-10-26).
- [x] Add a short “Windows-only” badge near the download button — the download page shows “Version 1.0.1 • Windows 64-bit” above the button (2026-10-26).
- [x] Add a one-line “AI chat needs Ollama + ~4.7 GB model” note next to the AI feature claim on the homepage (2026-10-26).

---

## 6. Sign-Off

| Check | Owner | Date | Result |
|---|---|---|---|
| Claim matrix reviewed against product | | | |
| Forbidden phrases removed from captions | | | |
| P0 fixes closed | | | |
| Trial and `.pbix` verified on clean Windows VM | | | |
| Release README updated | | | |
| Source/AGPL decision recorded | | 2026-10-26 | Done — ADR-021; site claim removed, test guard added |

**Rule:** If a claim is not in the “Approved wording” column, do not put it in a video, caption, thumbnail, or website edit.
