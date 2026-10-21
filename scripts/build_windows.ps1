# Builds DataAgent-Setup.exe:
#   1. PyInstaller builds: DataAgent.exe (onefile, headless web) +
#      DataAgentShell.exe (onedir, desktop shell)
#   2. Downloads the opencode CLI (windows-x64) into installers\opencode\
#   3. (Opt-in with -IncludeWarp) Downloads the Cloudflare WARP MSI
#      (universal x64/arm64) into installers\warp\
#   4. Compiles the Inno Setup installer (DataAgent-Setup.iss)
#   5. (Opt-in with -Sign) Code-signs the release artefacts with signtool
#
# Requires: Python + PyInstaller, Inno Setup 6 (ISCC.exe), internet access.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-IncludeWarp]
# Add -Cythonize for P4 release hardening (needs `pip install cython` +
# Visual Studio Build Tools): license modules compile to .pyd first.
# Add -IncludeWarp to bundle Cloudflare WARP (default: excluded).
# Add -Sign to code-sign the release (needs signtool + a code-signing cert);
#   without -Sign the build prints an UNSIGNED RELEASE warning at the end.

param(
  [switch]$Cythonize,
  [switch]$IncludeWarp,
  [switch]$Sign
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# ---------------------------------------------------------------------------
# PINNED THIRD-PARTY BINARY: the opencode CLI
# ---------------------------------------------------------------------------
# The CLI is bundled with the installer, so its version MUST be pinned - never
# "latest". A "latest" download moves under the pinned SHA256, the build dies
# at the hash check, and an unpinned binary is never verifiable by a user.
#
# To rotate the pin (two edits):
#   1. bump $OpenCodeVersion below, and
#   2. commit the matching installers\opencode-<version>.sha256, which holds
#      the SHA256 of the *extracted* opencode.exe for that version.
#
# Verification is a hard gate: a mismatch aborts the build. A missing hash
# file is never a silent skip - the hash is written and the build shouts that
# it MUST be verified out of band and committed.
$OpenCodeVersion = "v1.19.3"
$OpenCodeAsset = "opencode-windows-x64.zip"
$OpenCodeUrl = "https://github.com/anomalyco/opencode/releases/download/$OpenCodeVersion/$OpenCodeAsset"

if ($Cythonize) {
  Write-Host "==> [0/5] Cythonizing license modules (P4)..."
  python scripts\cythonize_license.py build
  if ($LASTEXITCODE -ne 0) { throw "Cythonize failed (see scripts/cythonize_license.py check)" }
}

Write-Host "==> [1/5] Building DataAgent.exe (PyInstaller onefile)..."
$collectPkgs = @("api", "agent", "warehouse", "config", "etl", "export")
# Prefer non-leaking collection: collect the compiled modules + non-`.py`
# package data (yaml configs, web assets, sql templates...) but NOT the
# readable `.py` sources (they are compiled into the PYZ archive anyway, so
# shipping them only exposes the source code next to the installed exe).
# PyInstaller regenerates DataAgent.spec from these CLI flags on every run,
# which would clobber the hand-maintained legacy spec (version/icon/codesign
# wiring enforced by tests/test_packaging.py). Back it up and restore it.
$specBackup = Join-Path ([IO.Path]::GetTempPath()) "DataAgent.spec.keep"
Copy-Item "DataAgent.spec" $specBackup -Force
python -m PyInstaller --noconfirm --onefile --windowed --name DataAgent `
  --optimize=2 --strip `
  --exclude-module tkinter --exclude-module pytest `
  --exclude-module pdb --exclude-module doctest `
  --exclude-module torch --exclude-module transformers --exclude-module pygame `
  --exclude-module laya --exclude-module sklearn --exclude-module tensorboard `
  --exclude-module triton `
  --hidden-import api.main `
  --add-data "web;web" `
  --version-file "packaging\windows\version_info.txt" `
  --icon "packaging\windows\app.ico" `
  run.py `
  $(($collectPkgs | ForEach-Object { "--collect-submodules", $_, "--collect-data", $_, "--collect-binaries", $_ }) -ne $null)
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Copy-Item $specBackup "DataAgent.spec" -Force  # restore legacy spec (see above)
# Anti-crack: frozen builds verify against the baked public key only, so fail
# the build if private key material was bundled in dist/.
$leaked = Get-ChildItem -Path dist -Recurse -Filter "*.pem" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch "baked_public_key" -and $_.FullName -match "ed25519|private" }
if ($leaked) { throw ("Private key material bundled: " + (($leaked | ForEach-Object { $_.FullName }) -join ", ")) }

Write-Host "==> [2/5] Building DataAgentShell.exe (PyInstaller onedir, desktop shell)..."
python -m PyInstaller --noconfirm scripts\DataAgentShell.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller shell build failed" }

Write-Host "==> [3/5] Downloading opencode CLI (pinned $OpenCodeVersion)..."
New-Item -ItemType Directory -Force -Path "installers\opencode" | Out-Null
$ocExe = "installers\opencode\opencode.exe"
# One hash file per pinned version - it is committed, so it survives a fresh
# clone (installers\opencode\ is gitignored, a sibling file is not).
$ocSha = "installers\opencode-$OpenCodeVersion.sha256"
$ocZip = "installers\opencode\$OpenCodeAsset"
$ocPinned = $null
if (Test-Path $ocSha) {
  $ocPinned = (((Get-Content $ocSha -Raw).Trim()) -split '\s+')[0].ToLower()
  if ($ocPinned -notmatch '^[0-9a-f]{64}$') {
    throw "opencode hash file $ocSha does not contain a 64-char hex SHA256"
  }
}
$needDownload = $true
if (Test-Path $ocExe) {
  $cached = (Get-FileHash -Algorithm SHA256 $ocExe).Hash.ToLower()
  if ($ocPinned -and $cached -eq $ocPinned) {
    Write-Host "    opencode.exe already present and matches the pin (skipping download)"
    $needDownload = $false
  } else {
    Write-Host "    cached opencode.exe does not match the $OpenCodeVersion pin - re-downloading"
    Remove-Item $ocExe -Force
  }
}
if ($needDownload) {
  Write-Host "    downloading $OpenCodeAsset ($OpenCodeVersion) ..."
  Write-Host "    url: $OpenCodeUrl"
  # A transient network error must not kill a release build: retry a few
  # times before giving up (the CDN behind GitHub resets big transfers
  # often enough to matter).
  $downloaded = $false
  for ($attempt = 1; $attempt -le 3 -and -not $downloaded; $attempt++) {
    try {
      Invoke-WebRequest -Uri $OpenCodeUrl -OutFile $ocZip
      $downloaded = $true
    } catch {
      Write-Warning ("    download attempt $attempt of 3 failed: " + $_.Exception.Message)
      if (Test-Path -LiteralPath $ocZip) { Remove-Item $ocZip -Force -ErrorAction SilentlyContinue }
      if ($attempt -lt 3) { Start-Sleep -Seconds (5 * $attempt) }
    }
  }
  if (-not $downloaded) { throw "could not download $OpenCodeUrl after 3 attempts" }
  Expand-Archive -Path $ocZip -DestinationPath "installers\opencode" -Force
  Remove-Item $ocZip
}
if (-not (Test-Path $ocExe)) { throw "opencode.exe is missing after the download step" }
# Supply-chain gate: the shipped binary MUST match the committed hash.
$actual = (Get-FileHash -Algorithm SHA256 $ocExe).Hash.ToLower()
if ($ocPinned) {
  if ($actual -ne $ocPinned) {
    throw "opencode.exe SHA256 mismatch for $OpenCodeVersion : expected $ocPinned got $actual"
  }
  Write-Host "    opencode.exe SHA256 verified: $actual"
} else {
  # No hash file for this version yet. Verification is NEVER silently
  # disabled: record what we got and make the maintainer verify it.
  "$actual  opencode.exe" | Out-File -Encoding ascii $ocSha
  Write-Warning "=============================================================="
  Write-Warning " opencode $OpenCodeVersion was NOT verified against a committed hash."
  Write-Warning " Recorded $actual in $ocSha."
  Write-Warning " Verify it out of band (publish the release checksum, compare by"
  Write-Warning " hand) and COMMIT $ocSha before releasing this build."
  Write-Warning "=============================================================="
}
Write-Host "    opencode pin: version=$OpenCodeVersion sha256=$actual"

Write-Host "==> [4/5] Cloudflare WARP MSI (opt-in via -IncludeWarp)..."
if ($IncludeWarp) {
  New-Item -ItemType Directory -Force -Path "installers\warp" | Out-Null
  $msi = Join-Path "installers\warp" "Cloudflare_WARP_Release.msi"
  if (-not (Test-Path $msi)) {
    Write-Host "    downloading Cloudflare_WARP_Release.msi (universal x64/arm64) ..."
    Invoke-WebRequest -Uri "https://downloads.cloudflareclient.com/v1/download/windows/ga" -OutFile $msi
  } else {
    Write-Host "    Cloudflare_WARP_Release.msi already present (skipping)"
  }
} else {
  Write-Host "    skipping WARP bundle (default; rebuild with -IncludeWarp to include)"
}

Write-Host "==> [5/5] Compiling DataAgent-Setup.exe (Inno Setup)..."
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
  $candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
  )
  foreach ($c in $candidates) {
    if (Test-Path $c) { $iscc = $c; break }
  }
  if (-not $iscc) { throw "Inno Setup (ISCC.exe) not found - install from https://jrsoftware.org/isinfo.php" }
}
if ($IncludeWarp) {
  & $iscc "/DIncludeWarp" "installers\DataAgent-Setup.iss"
} else {
  & $iscc "installers\DataAgent-Setup.iss"
}
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }

if ($Cythonize) {
  Write-Host "==> Cleaning Cython artifacts (sources stay)..."
  python scripts\cythonize_license.py clean
}

# ---------------------------------------------------------------------------
# CODE SIGNING
# ---------------------------------------------------------------------------
# The certificate must be an EV or OV code-signing certificate and the
# signature must carry an RFC3161 timestamp, otherwise the signature stops
# validating when the cert expires and SmartScreen keeps nagging.
#
# Environment contract (unchanged):
#   DATAAENT_CODESIGN_THUMBPRINT  sha1 thumbprint of the cert in the store
#   DATAAENT_CODESIGN_PFX          path to a .pfx (used when no thumbprint)
#   DATAAENT_CODESIGN_PASSWORD     password for the .pfx
#   DATAAENT_CODESIGN_TIMESTAMP    RFC3161 timestamp URL
#                                  (default http://timestamp.digicert.com)
$signArtefacts = @(
  "dist\DataAgent.exe",
  "dist\DataAgentShell\DataAgentShell.exe",
  "dist\DataAgent-Setup.exe"
)

if ($Sign) {
  Write-Host "==> Code-signing the release artefacts (signtool)..."
  $thumb = $env:DATAAENT_CODESIGN_THUMBPRINT
  $pfx = $env:DATAAENT_CODESIGN_PFX
  $pw = $env:DATAAENT_CODESIGN_PASSWORD
  $ts = $env:DATAAENT_CODESIGN_TIMESTAMP
  if (-not $ts) { $ts = "http://timestamp.digicert.com" }
  $signtool = Get-Command signtool -ErrorAction SilentlyContinue
  if (-not $signtool) {
    $sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($sdk) { $signtool = $sdk.FullName }
  }
  if (-not $signtool) {
    $why = "signtool.exe not found - install the Windows SDK (or put signtool on PATH)."
    throw "$why -Sign was requested, so the build FAILS here: an unsigned release must never be produced silently when the operator explicitly asked for signing."
  }
  if (-not $thumb -and -not $pfx) {
    throw "Set DATAAENT_CODESIGN_THUMBPRINT or DATAAENT_CODESIGN_PFX for -Sign"
  }
  # Every release artefact MUST exist before signing - a missing file must
  # never be silently skipped.
  foreach ($t in $signArtefacts) {
    if (-not (Test-Path $t)) { throw "release artefact missing, cannot sign: $t" }
  }
  foreach ($t in $signArtefacts) {
    if ($thumb) { & $signtool sign /fd SHA256 /tr $ts /td SHA256 /sha1 $thumb $t }
    else { & $signtool sign /fd SHA256 /tr $ts /td SHA256 /f $pfx /p $pw $t }
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $t" }
    Write-Host "    signed: $t"
  }
  # A signature that does not verify is not a signature. Re-verify every
  # artefact and fail the build if the verification fails.
  foreach ($t in $signArtefacts) {
    & $signtool verify /pa /sha1 $t
    if ($LASTEXITCODE -ne 0) { throw "signature verification failed for $t" }
    Write-Host "    signature verified: $t"
  }
}

Write-Host "==> Checksums (SHA256)..."
# Release artifacts only: top-level dist exes + the onedir shell exe.
# (A plain -Recurse would also hash vendored third-party binaries inside
#  dist\DataAgentShell\_internal, e.g. plotly's kaleido/node.)
Remove-Item "dist\SHA256SUMS.txt" -Force -ErrorAction SilentlyContinue
$releaseExes = @(Get-ChildItem dist\*.exe -ErrorAction SilentlyContinue) +
    @(Get-Item "dist\DataAgentShell\DataAgentShell.exe" -ErrorAction SilentlyContinue)
$releaseExes | Where-Object { $_ } | ForEach-Object {
  $h = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLower()
  "$h  $($_.Name)" | Out-File -Append -Encoding ascii "dist\SHA256SUMS.txt"
  Write-Host "    $($_.Name): $h"
}

if (-not $Sign) {
  # Unmissable: this is the last thing in the release log.
  Write-Host ""
  Write-Host "==================================================================="
  Write-Host " UNSIGNED RELEASE"
  Write-Host "-------------------------------------------------------------------"
  Write-Host " This build was NOT code-signed (the -Sign switch was not used)."
  Write-Host " Windows SmartScreen will show 'Unknown publisher' and the"
  Write-Host " user must click 'More info' -> 'Run anyway'."
  Write-Host ""
  Write-Host " Sign it with an EV/OV code-signing certificate:"
  Write-Host "   signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 <THUMBPRINT> `"dist\DataAgent.exe`""
  Write-Host "   signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 <THUMBPRINT> `"dist\DataAgentShell\DataAgentShell.exe`""
  Write-Host "   signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 <THUMBPRINT> `"dist\DataAgent-Setup.exe`""
  Write-Host ""
  Write-Host " (or re-run the build with -Sign and DATAAENT_CODESIGN_THUMBPRINT /"
  Write-Host "  DATAAENT_CODESIGN_PFX set - a missing signtool then fails the build)"
  Write-Host "==================================================================="
}

Write-Host ""
Write-Host "Done. Installer: dist\DataAgent-Setup.exe"
