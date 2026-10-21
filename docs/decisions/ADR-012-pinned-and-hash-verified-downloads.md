# ADR-012 — every fetched binary is version-pinned and hash-verified

- **Status:** Accepted
- **Date:** 2026-10-21
- **Relates to:** `docs/LAUNCH-CHECKLIST.md` §2, `docs/LAUNCH-FIX-PLAN.md` item D4

---

## Context

The Windows build bundles the **opencode CLI** so the agent's free-tier
provider works offline. The build script downloaded it like this:

```powershell
Invoke-WebRequest "https://github.com/anomalyco/opencode/releases/latest/download/opencode-windows-x64.zip"
# …then verified it against a committed hash
if ($actual -ne $pinned) { throw "opencode.exe SHA256 mismatch" }
```

Two things were wrong with that:

1. **"latest" and a pinned hash are not the same thing.** The hash was
   recorded for one build; `latest` moves. The build therefore aborted with
   *"opencode.exe SHA256 mismatch"* as soon as upstream published a new
   release — the build was not reproducible, it was a race.
2. **The pin was never in the repository.** The hash file lived in
   `installers/opencode/`, which is gitignored, so a fresh clone had a
   download step and no hash to check against.

The same class of problem applies to anything else fetched at build time.

## Decision

**Every fetched artefact is pinned to a concrete version and verified against a
committed hash.** The opencode CLI is pinned to `v1.19.3`:

- `scripts/build_windows.ps1` — `$OpenCodeVersion = "v1.19.3"`, downloaded from
  `releases/download/<version>/opencode-windows-x64.zip` (never `latest`).
- `installers/opencode-v1.19.3.sha256` — the SHA256 of the extracted
  `opencode.exe`, **committed** (it lives outside the gitignored
  `installers/opencode/` directory).
- A mismatch is a hard `throw`. A missing hash file is written and loudly
  reported — verification is never silently skipped.

Rotation is two edits: bump `$OpenCodeVersion`, and commit the new
`installers/opencode-<version>.sha256`.

## Consequences

- The build is reproducible: the same tree produces the same binary.
- A compromised or truncated download fails the build instead of shipping.
- Upgrading opencode is a deliberate, reviewable commit rather than a surprise
  at build time.
- **Known gap:** `packaging/linux/opencode-install.sh` still downloads
  `releases/latest` at *install* time, so the .deb/.rpm install is not
  hash-verifiable. `scripts/build_linux.sh` prints that warning. The fix is to
  pin it to the same version and ship the same hash.
- The download is retried three times: the CDN resets large transfers.
