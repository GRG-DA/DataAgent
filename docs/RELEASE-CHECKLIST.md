# Release checklist — DataAgent Windows desktop (`.exe`)

**Every box must be ticked before a release build is published.** Items marked **MANUAL** cannot
be automated and must be performed by a human on a clean machine.

- Release version: `<x.y.z>` · Build date: `<date>` · Builder: `<name>`
- Baseline commit: `<sha>` · Installer SHA256: `<hash>`

---

## 1. Gate — code

| # | Check | Command | Pass |
|---|---|---|---|
| 1.1 | Full suite green | `python -m pytest -q` | 0 failures, 0 errors |
| 1.2 | Coverage ≥ 74% | `python -m pytest -q --cov=. --cov-report=term` | gate satisfied |
| 1.3 | Security regressions | `python -m pytest tests/test_security_regressions.py -q` | 0 failures |
| 1.4 | Packaging contract | `python -m pytest tests/test_packaging.py -q` | 0 failures |
| 1.5 | Launch-hardening suites | `python -m pytest tests/test_launch_*.py -q` | 0 failures |
| 1.6 | Billing / entitlement consistency | `python -m pytest tests/test_billing_consistency.py -q` | 0 failures |
| 1.7 | Working tree clean | `git status --porcelain` | empty |
| 1.8 | Version strings agree | `python -m pytest tests/test_packaging.py -k version -q` | 0 failures |

> The version must be identical in `packaging/windows/version_info.txt`,
> `config/settings.yaml` (`app.version`), `installers/DataAgent-Setup.iss` (`MyAppVersion`) and
> `api/main.py` (`app.version` in the FastAPI constructor). `tests/test_packaging.py` enforces
> this — do not "fix" it by editing the test.

## 2. Gate — build

> **Do not run the test suite while the build is running.** `scripts/build_windows.ps1`
> backs up the hand-maintained `DataAgent.spec`, lets PyInstaller regenerate it, and restores
> it afterwards. While the build is in flight, `DataAgent.spec` is the GENERATED one — it has
> no `DATAAENT_CODESIGN_IDENTITY` hook and a relative `version=`/`icon=` path — so
> `tests/test_packaging.py` and `tests/test_exe_release.py` fail, and a `git add -A` in that
> window would commit the generated spec. **After a build, run `git diff --stat DataAgent.spec`
> and confirm it is empty** before committing anything.

| # | Check | Command | Pass |
|---|---|---|---|
| 2.1 | Clean tree before building | `git clean -xdn` | review the list; nothing surprising |
| 2.2 | Desktop preflight | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\preflight_desktop.ps1` | `preflight PASSED` |
| 2.3 | Full build | `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1` | `dist\DataAgent-Setup.exe` exists |
| 2.4 | Installer compiles | — | no ISCC error in the log |
| 2.5 | `dist\DataAgent.exe` (onefile, web) exists | `Test-Path dist\DataAgent.exe` | true |
| 2.6 | `dist\DataAgentShell\DataAgentShell.exe` (onedir, desktop) exists | `Test-Path dist\DataAgentShell\DataAgentShell.exe` | true |
| 2.7 | `dist\SHA256SUMS.txt` generated | `Test-Path dist\SHA256SUMS.txt` | true |
| 2.8 | The hand-maintained spec was restored | `git diff --stat DataAgent.spec` | **empty** |
| 2.9 | No credential state bundled | `python scripts\build_secure_zip.py --check-only` (or the equivalent) | no `users.json`, no `.env`, no `.jwt_secret`, no `.grant_key` in the bundle |
| 2.10 | No private key material in `dist/` | `Get-ChildItem dist -Recurse -Filter *.pem` | only `cacert.pem` |
| 2.11 | Installed size is honest | measure the installer, then run `python scripts/site_installer_size.py --set <MB>` - it rewrites the constant AND every page that quotes the size | `python scripts/site_installer_size.py` exits 0. **Last measured: 439 MB** (2026-10-22) |
| 2.12 | The bundle carries no dead weight | `Get-ChildItem dist\DataAgentShell\_internal -Directory \| %% { [math]::Round((Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum/1MB) }` | no multi-MB directory that no shipped feature uses |
> **Size note.** The installer is built from `dist\DataAgent.exe` (the onefile web build), `dist\DataAgentShell\` (the onedir desktop shell) and the bundled opencode CLI. **Always read the size off the finished installer** ? a file that is still being written reads several times smaller than its final size, and quoting a mid-write number is how the site ended up claiming 175 MB for a 499 MB download.

## 3. Gate — code signing (**blocking**)

An unsigned release is **not publishable**. It will be blocked by SmartScreen with
*"Windows protected your PC"*, and most users will not click through.

| # | Check | Command | Pass |
|---|---|---|---|
| 3.1 | Signing tool available | `Get-Command signtool` (or `scripts\ci\preflight_desktop.ps1` reports it) | found |
| 3.2 | Certificate present | `DATAAENT_CODESIGN_THUMBPRINT` or `DATAAENT_CODESIGN_PFX` set | set |
| 3.3 | Signed build | `powershell -File scripts\build_windows.ps1 -Sign` | all three artefacts signed |
| 3.4 | Signature present on the installer | `signtool verify /pa /v dist\DataAgent-Setup.exe` | exit 0, "Valid" |
| 3.5 | Timestamp present | same output | shows a countersignature timestamp |
| 3.6 | Publisher matches the site | `signtool verify /pa /v` shows the publisher | the name on the site is the same |

> The signing certificate should be an **EV or OV code-signing** certificate with an RFC3161
> timestamp (`/tr http://timestamp.digicert.com /td SHA256`). Never commit the `.pfx` or its
> password — they live only in the build environment.

## 4. Gate — clean-machine smoke test (**MANUAL**)

Performed on a **fresh Windows 10/11 machine** (or a Windows Sandbox VM) with **no Python
installed**. `scripts/ci/smoke_desktop.ps1` automates what it can; the rest is listed explicitly.

| # | Step | Expected |
|---|---|---|
| 4.1 | Install `DataAgent-Setup.exe` | completes without error; installs to `C:\Program Files\DataAgent` |
| 4.2 | Launch **DataAgent** from the Start menu | the desktop window opens; the splash disappears when the backend is ready |
| 4.3 | **Create the first account** | registration succeeds — *no environment variable, no bootstrap token, nothing the user has to look up* |
| 4.4 | Sign out, sign back in | works |
| 4.5 | Close and relaunch the app | **still signed in** (the JWT secret persisted) |
| 4.6 | Restart the machine, relaunch | still signed in |
| 4.7 | Upload a CSV | it appears in the table list; the profile/quality views populate |
| 4.8 | Ask a question with a local model running | an answer comes back with a chart |
| 4.9 | Ask a question with **no** model running | the rule-based fallback answers and says so — it must not hang or crash |
| 4.10 | Build a dashboard and export it | the export downloads and opens |
| 4.11 | Export to CSV and open it in Excel | **no cell is evaluated as a formula** |
| 4.12 | Export to PDF | a real, readable PDF — not a page that says "install Playwright" |
| 4.13 | Activate a paid licence, then restart the app | **the plan is still Pro/Lifetime after the restart** |
| 4.14 | Run the app as a **non-admin** user | everything works (the runtime dir is under `%LOCALAPPDATA%`, not Program Files) |
| 4.15 | Uninstall | the app is removed; `%LOCALAPPDATA%\DataAgent` **survives** (the user's data is not destroyed) |
| 4.16 | Reinstall | the user's data and plan are still there; the app does not start over empty |

## 5. Gate — product truth

| # | Check | Pass |
|---|---|---|
| 5.1 | No feature name promises more than the code delivers | "AutoML" / "Segmentation" / "Forecasting" are renamed |
| 5.2 | Password reset never lies | with SMTP unconfigured it says so instead of claiming success |
| 5.3 | Price lists agree | the in-app plan list and the checkout agree on price, currency and AI cap |
| 5.4 | AI usage has a real ceiling | no plan is "unlimited AI" |
| 5.5 | Marketing matches the architecture | `website/`, `docs/` and the app say the same thing (ADR-010) |
| 5.6 | Legal pages have no placeholders | `config/legal.yaml` has no unfilled markers |
| 5.7 | Dev key cannot sign | `keygen_app` refuses the dev key unless explicitly overridden |
| 5.8 | The baked public key is the production key | the release step is documented and has been performed |

## 6. Gate — publish

| # | Check | Pass |
|---|---|---|
| 6.1 | Public release repository exists and is correct | the download links resolve to a real repo |
| 6.2 | Release tagged | the tag matches the version |
| 6.3 | Installer attached to the release | `DataAgent-Setup.exe` present |
| 6.4 | Portable build attached | `DataAgent.exe` present |
| 6.5 | `SHA256SUMS.txt` attached | present and matches the artefacts |
| 6.6 | Download link on the site resolves | tested from a clean browser session |
| 6.7 | Update check works | the tray's "Check for updates" reports a real result |
| 6.8 | Support path is published | a contact address on the site and a documented support-bundle procedure |

## 7. Rollback

1. The previous release stays published with its `SHA256SUMS.txt` — never delete a release.
2. To roll back, publish the previous artefacts under a new "latest" release, or point the
   download constant back at the previous tag.
3. In-app, the user can reinstall the previous installer over the current one. **User data in
   `%LOCALAPPDATA%\DataAgent` survives an upgrade and a downgrade** — verify this in the
   downgrade path of the smoke test before you need it.

---

## Appendix — support bundle

When a user reports a problem, collect **only** these files from
`%LOCALAPPDATA%\DataAgent`:

- `logs\app.log`
- `logs\audit.log`
- `logs\runtime-stdout.log` (windowed builds redirect stdout here)

**Never** collect or ask the user to send: `data\.jwt_secret`, `data\.grant_key`,
`data\.grant_migrated`, `config\users.json`, `data\users.db`, `data\curated\warehouse.duckdb`,
`data\raw\*` (the customer's own data), or `config\db_connections.json`.
