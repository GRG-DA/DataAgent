# DataAgent — Marketing Pre-Flight Checklist

**Rule:** Do not publish the first short until every **P0** item is green. A post that drives traffic into a broken or misleading funnel is worse than no post.

**Owner:** Solo founder.
**Last reviewed:** 2026-10-26.

---

## P0 — Must Be True Before Posting

### 1. Source / AGPL decision

- [x] Decision (2026-10-26): the source stays private for now; remove the open-source/AGPL claim from the site and tests. See `docs/decisions/ADR-021-private-source-until-published.md`.
- [x] Removed the claim from `website/faq.html`, `website/download.html`, `website/about.html`, `website/index.html`, `website/changelog.html`, `website/docs.html`, `website/contact.html`, `website/comparison.html`, `website/usecases.html`, `website/blog.html`, `website/blog-post.html`, and the footer across the site.
- [x] Replaced the AGPL-naming test with a guard that rejects any unbacked open-source/AGPL claim. The Metabase/Superset reference on `comparison.html` is the only allowed use.
- [x] Recorded the decision in `docs/decisions/ADR-021-private-source-until-published.md`.
- [ ] Legal follow-up: review the AGPL source-offer obligation for the distributed binary, or publish the source/change the licence. This is not a launch blocker for the marketing copy, but it must be resolved before distributing at scale.
- [ ] Do not add “open source” to social until a source repository is live.

**Why:** the site no longer claims AGPL-3.0. The source is not published, so the honest copy says so.

### 2. Public release README

- [ ] Replace the current README (v1.0.0, 508 MB, AGPL, “Every feature works”, “Mac/Linux Docker build”, “No trial timer”) with `release-repo-readme.publish.md`. Do not just edit the version number.
- [ ] Confirm the README’s download link resolves to the v1.0.1 installer.
- [ ] Confirm the README does not claim free `.pbix` export, unlimited AI, an open-source/AGPL licence, macOS/Linux support, a Docker build, “every feature works”, or an absolute “your files never leave your computer” promise.
- [ ] Use “about 440 MB” and “Windows 10/11 x64” consistently with the site.
- [ ] Set the repo **About** description to: “Local-first AI data analyst for Windows. Ask CSV/Excel in plain English, get SQL + charts, export Power BI .pbix. Free tier + 14-day Pro trial.”
- [ ] Set the repo website to `https://data-agent.me` and add topics: `data-analytics`, `power-bi`, `duckdb`, `local-first`, `ai`, `windows`, `csv`, `excel`, `sql`, `data-visualization`.

### 3. Installer and release link

- [ ] `https://github.com/java22113344/grg-dataagent/releases/latest` resolves.
- [ ] The latest release is `v1.0.1`.
- [ ] `DataAgent-Setup.exe` downloads without a GitHub login.
- [ ] The asset is about 440 MB (site says 439 MB; release asset is 442 MB — use “about 440 MB” in marketing).
- [ ] `website/js/main.js` points at the same release base (`v1.0.1`).
- [ ] Record the SHA-256 hash for support replies:

```powershell
Get-FileHash .\DataAgent-Setup.exe -Algorithm SHA256
```

### 4. SmartScreen flow

- [ ] On a clean Windows 11 VM, run the installer.
- [ ] Confirm the “Windows protected your PC / Unknown publisher” dialog appears.
- [ ] Confirm **More info → Run anyway** completes the install.
- [ ] Record this 10-second clip for the install video.
- [ ] Add the warning to the install caption and pinned comment.

### 5. Trial

- [ ] On a clean install, open **Subscription**.
- [ ] Confirm “Start 14-day Pro trial” is visible and says “No card required.”
- [ ] Start the trial; confirm Pro features unlock.
- [ ] Confirm the trial cannot be started twice on the same account.
- [ ] Confirm the trial end date is 14 days out.
- [ ] Confirm what happens when the trial expires (features lock; no surprise charge).

### 6. `.pbix` export

- [ ] With the trial active, import a real CSV.
- [ ] Build a dashboard with at least one KPI and one chart.
- [ ] Export the `.pbix`.
- [ ] Open the file in Power BI Desktop.
- [ ] Confirm the table, measures, and slicers survive.
- [ ] Confirm the same export is blocked on the free tier.
- [ ] Record the proof clip.

### 7. `.pbix` import / build

- [ ] Import an existing `.pbix` (no client data).
- [ ] Ask a question; confirm a chart and SQL/DAX are returned.
- [ ] Export the updated file; reopen it in Power BI Desktop.
- [ ] Confirm the free tier blocks this path.
- [ ] Record the proof clip.

### 8. Pricing and checkout

- [ ] Pro checkout charges ₹499/mo.
- [ ] Lifetime checkout charges ₹10,000 once.
- [ ] The checkout attaches the plan to the email used to pay.
- [ ] The plan appears in the app after payment.
- [ ] Refund/cancellation wording on the site matches the actual policy.
- [ ] Test with a real low-value transaction or the payment provider’s test mode; do not use a live card for a dry run if test mode exists.

### 9. Claims

- [ ] Read `claim-audit.md`.
- [ ] Remove every forbidden phrase from the 12 captions and scripts.
- [ ] Confirm every post says Windows 10/11 x64.
- [ ] Confirm every post that mentions `.pbix` says it is Pro/trial.
- [ ] Confirm no post says “unlimited,” “free `.pbix`,” “100% private,” “zero telemetry,” or “open source.”
- [ ] Confirm the AI disclosure is present if an avatar is used.

### 10. Link hygiene

- [ ] Instagram bio points to `https://data-agent.me/download`.
- [ ] YouTube channel description points to the same.
- [ ] Every caption uses the release URL, not the website repo.
- [ ] No link points to `github.com/java22113344/data-agent` as the app source.
- [ ] Test every link in a private browser window.

### 11. Accounts

- [ ] Instagram account created; handle reserved.
- [ ] YouTube channel created; handle matches.
- [ ] Profile photo, banner, bio, and link set.
- [ ] Two-factor authentication enabled on both.
- [ ] Recovery email and phone set.
- [ ] Business/creator account selected on Instagram (for insights).
- [ ] Channel is not marked “made for kids.”
- [ ] Comment filters set to block spam links.

---

## P1 — Do Before Week 2

- [x] Add the 14-day Pro trial to `website/download.html` and `website/billing.html` (done 2026-10-26; site-copy tests pass).
- [ ] Add social links to the site footer.
- [x] Update the download page’s “What’s in version 1.0.0” link to v1.0.1 (2026-10-26).
- [x] Align the v1.0.0/v1.0.1 references across `website/index.html`, `website/changelog.html`, the page toasts, and `website/download.html` (2026-10-26).
- [x] Add a “Windows 10/11 x64” badge near the download button — the download page shows “Version 1.0.1 • Windows 64-bit” above the button (2026-10-26).
- [ ] Add “AI chat needs Ollama + ~4.7 GB model” next to the AI feature claim.
- [ ] Create a `data-agent.me/download` short link for spoken CTAs (optional; no analytics by default).
- [ ] Prepare a 2-video buffer so a sick day does not break the cadence.
- [ ] Write the “where did you find us?” question for trial follow-up.

---

## Clean Windows VM Test Script

Use a fresh Windows 11 VM with no Python, no Ollama, no Power BI.

1. Open `https://data-agent.me/download` in Edge.
2. Download `DataAgent-Setup.exe`.
3. Run it; capture the SmartScreen dialog.
4. Install to the default path.
5. Launch DataAgent.
6. Create the first local user.
7. Open **Subscription**; start the 14-day trial.
8. Import a public CSV.
9. Ask: “Show total profit by region as a bar chart”
10. Confirm SQL + 4-region bar chart, West highest.
11. Export `.pbix`.
12. Open the `.pbix` in Power BI Desktop (install it if needed).
13. Confirm the table, measures, and slicers.
14. Return to DataAgent; check the trial status.
15. Uninstall; confirm no orphaned service or locked DuckDB file.

**Pass condition:** every step works without a terminal, a Python install, or a hidden config file.

---

## Stop Conditions

Do not post if any of these are true:

- The installer link 404s or requires a GitHub login.
- The trial cannot be started on a clean install.
- `.pbix` export fails or the file does not open in Power BI Desktop.
- The release README still says “testing build” without context.
- A caption says “open source” but no public source repository is live.
- The caption says “free `.pbix`,” “unlimited,” or “100% private.”
- The only download link is the website repo, not the release.

---

## Verification Commands

**Check the latest release and asset size:**

```powershell
$release = Invoke-RestMethod https://api.github.com/repos/java22113344/grg-dataagent/releases/latest
$release.tag_name
$release.assets | Select-Object name, size
```

**Run the website claim tests if you edit site copy:**

```powershell
python -m pytest tests/test_launch_frontend_claims.py tests/test_launch_site_copy.py -q
```

**Do not run these against production billing with a live card.** Use the provider’s test mode where available.
