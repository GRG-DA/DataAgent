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

The pin is a **v1** release on purpose. The provider speaks the opencode v1
session API (`POST /session`, `POST /session/{id}/message`) and refuses a v2
server outright (ADR-015); the CLI this project ships is the one that serves
that dialect natively.

The npm package `opencode-ai` publishes the **same** binary as the GitHub
release for a given version, so the pin can be verified out of band without
the CDN: `npm install opencode-ai@1.19.3` and hash the installed binary — it
matches the committed `installers/opencode-v1.19.3.sha256` digest.

The Linux install path follows the same contract. The `.deb`/`.rpm` does not
bundle the CLI (~180 MB); the install-time helper
`packaging/linux/opencode-install.sh` downloads it, and that download is
pinned and verified too:

- `scripts/build_linux.sh` — `OPENCODE_VERSION = "v1.19.3"`, and
  `packaging/linux/opencode-install.sh` — `OPENCODE_VERSION = "v1.19.3"`:
  the same release, downloaded from `releases/download/<version>/...`.
- `installers/opencode-<version>-<target>.sha256` — the SHA256 of the release
  **tarball**, committed next to the Windows pin. The committed files are
  `installers/opencode-v1.19.3-linux-x64.sha256`,
  `installers/opencode-v1.19.3-linux-arm64.sha256` and
  `installers/opencode-v1.19.3-linux-x64-baseline.sha256`.
- The tarball is verified **before** it is extracted: an unverified archive
  is never unpacked, so a crafted tarball cannot write outside the temp dir.
- With no pinned hash for the target the install **refuses** (non-zero exit,
  nothing installed) — an unverified binary must never become a root-owned
  one. A warning is not enough.
- `scripts/build_linux.sh` produces and exports those hashes (it downloads
  the pinned tarball, compares the SHA256 with the committed pin and fails
  the build on a mismatch) and ships the pin files inside the package.

Rotation is two edits: bump `$OpenCodeVersion` (and the two
`OPENCODE_VERSION` constants), and commit the new
`installers/opencode-<version>.sha256` (plus the Linux per-target pins).

## Consequences

- The build is reproducible: the same tree produces the same binary.
- A compromised or truncated download fails the build instead of shipping.
- Upgrading opencode is a deliberate, reviewable commit rather than a surprise
  at build time.
- The app installs the **pinned** release on every platform, and names the
  installed file after the version it actually holds: the runtime copy
  `opencode-<version>.exe` carries the version of the bytes inside it, so
  the name and the reported version can never disagree. A newer upstream
  release is reported as information only — adopting it is a pin bump, never
  a silent install.
- The download is retried three times: the CDN resets large transfers.
