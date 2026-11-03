# DataAgent — Public Release README Draft

**Draft status:** Do not paste this into the public release repo until the placeholders are replaced and the source-status wording is confirmed. The current release README says the build is a “testing build”; this draft replaces that with a scoped, honest description.

**Replace before publishing:**

- `[SUPPORT_EMAIL]` — a real support address.
- `[SCREENSHOT_URL]` — a screenshot or GIF of the app.
- The source-status section — do not add a licence claim until the source is published and the licence is confirmed.

---

# DataAgent

**A local-first AI data analyst for Windows.** Ask a CSV or Excel file a question in plain English, get the SQL and a chart, then export a real Power BI `.pbix` — on your own machine.

[**Download for Windows 10/11 (64-bit)**](https://github.com/java22113344/grg-dataagent/releases/latest)

About 440 MB · Windows 10/11 x64 · Free tier + 14-day Pro trial, no card

![DataAgent screenshot]([SCREENSHOT_URL])

---

## What it is

DataAgent is a desktop app for analysts who work with CSV, Excel, and Power BI. It combines:

- A local DuckDB warehouse.
- Natural-language questions that generate SQL you can read and edit.
- Dashboards with 30+ chart types.
- An Excel-style workbench.
- A real `.pbix` export that opens in Power BI Desktop.
- Optional local models through Ollama, or hosted model providers with your own key.

It is built for the workflow where the file cannot leave your laptop.

---

## Quick start

1. Download `DataAgent-Setup.exe` from the [latest release](https://github.com/java22113344/grg-dataagent/releases/latest).
2. Run the installer. This build is not code-signed yet, so Windows SmartScreen may show **“Windows protected your PC”** and **“Unknown publisher.”** Choose **More info → Run anyway**.
3. Launch DataAgent and create the first local user.
4. Import a CSV or Excel file.
5. Ask a question in plain English.
6. Open **Subscription** to start the 14-day Pro trial (no card) if you want `.pbix` export/import.
7. Export the `.pbix` and open it in Power BI Desktop.

**AI chat setup:** DataAgent can install Ollama for you in one click from Settings. The local model is a separate one-time download — about 4.7 GB for `qwen2.5:7b`. SQL, Excel, dashboards, and exports do not need a model.

---

## Free, trial, and paid

| Plan | Price | What you get |
|---|---|---|
| **Free** | ₹0 | 25 AI requests/day, 3 saved dashboards, 7 days of history, chat, SQL, file upload, Excel-style workbench |
| **Pro trial** | ₹0 for 14 days | Pro features, including `.pbix` export/import; no card required; one-time per account |
| **Pro** | ₹499/mo | 50 AI requests/day, 50 dashboards, 1 year of history, export/automation/live-connection tools |
| **Lifetime** | ₹10,000 once | 100 AI requests/day, 1,000 dashboards, 10 years of history, the same Pro tools |

`.pbix` export and `.pbix` import/build are paid features. The 14-day trial is the honest way to test them.

Prices are in INR. Check the [pricing page](https://data-agent.me/billing) for the current checkout terms.

---

## Requirements

- **OS:** Windows 10 or Windows 11, 64-bit (x86_64).
- **Storage:** roughly 0.7–1 GB installed, plus your data; about 4.7 GB more for a local model.
- **Memory/GPU:** the app is light; local model speed depends on RAM and GPU. A GPU is optional.
- **Internet:** needed for the installer, the GitHub update check, the Ollama model pull, and optional cloud AI. After setup, the local workflow works offline.
- **Not supported:** macOS and Linux.

---

## What works today

- CSV/XLSX import and a local DuckDB warehouse.
- Natural-language questions with visible SQL.
- Dashboards and 30+ chart types.
- Excel-style workbench.
- `.pbix` export (Pro/trial).
- `.pbix` import/build (Pro/trial).
- Local models through Ollama; hosted providers with your own key.

## Known gaps

- Windows only.
- The installer is not code-signed yet; SmartScreen warns.
- AI chat needs a separate Ollama model download.
- This is an early public build; some edges are still rough. If something breaks, open an issue with the steps to reproduce.

---

## Privacy and network

- The local workflow runs on your machine with a local DuckDB warehouse.
- The app checks GitHub for updates.
- The Ollama model pull downloads the model from Ollama.
- Cloud AI providers are optional and require your own key.
- Paid plans contact the entitlement service; the IP-rotation proxy is only used if that feature is enabled.
- The app does not upload your data to a DataAgent cloud analytics service.

Do not use the app for data you are not authorised to process. You are responsible for your data, model choice, and client confidentiality rules.

---

## Links

- **Website:** https://data-agent.me
- **Download:** https://github.com/java22113344/grg-dataagent/releases/latest
- **Issues:** https://github.com/java22113344/grg-dataagent/issues
- **Support:** [SUPPORT_EMAIL]

---

## Source status

The source code is not published yet. This repository hosts the Windows installer and release notes; it does not contain the application source. There is no public source repository or source licence to point you to today.

When the source is published, this section will link to it and state the licence. Do not assume a licence for the packaged app from this repository.

---

## Contributing

This is an early build. The most useful contributions right now are bug reports with:

- Windows version.
- DataAgent version.
- The file type (CSV/XLSX/PBIX).
- The exact steps.
- The error message or a screenshot.

Please do not attach client data. Use a synthetic sample.
