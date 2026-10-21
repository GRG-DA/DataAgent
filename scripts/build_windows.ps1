# Builds DataAgent-Setup.exe:
#   1. PyInstaller builds: DataAgent.exe (onefile, headless web) +
#      DataAgentShell.exe (onedir, desktop shell)
#   2. Downloads the opencode CLI (windows-x64) into installers\opencode\
#   3. (Opt-in with -IncludeWarp) Downloads the Cloudflare WARP MSI
#      (universal x64/arm64) into installers\warp\
#   4. Compiles the Inno Setup installer (DataAgent-Setup.iss)
#
# Requires: Python + PyInstaller, Inno Setup 6 (ISCC.exe), internet access.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-IncludeWarp]
# Add -Cythonize for P4 release hardening (needs `pip install cython` +
# Visual Studio Build Tools): license modules compile to .pyd first.
# Add -IncludeWarp to bundle Cloudflare WARP (default: excluded).

param(
  [switch]$Cythonize,
  [switch]$IncludeWarp,
  [switch]$Sign
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($Cythonize) {
  Write-Host "==> [0/4] Cythonizing license modules (P4)..."
  python scripts\cythonize_license.py build
  if ($LASTEXITCODE -ne 0) { throw "Cythonize failed (see scripts/cythonize_license.py check)" }
}

Write-Host "==> [1/4] Building DataAgent.exe (PyInstaller onefile)..."
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

Write-Host "==> [1b/4] Building DataAgentShell.exe (PyInstaller onedir, desktop shell)..."
python -m PyInstaller --noconfirm scripts\DataAgentShell.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller shell build failed" }

Write-Host "==> [2/4] Downloading opencode CLI..."
New-Item -ItemType Directory -Force -Path "installers\opencode" | Out-Null
$ocExe = "installers\opencode\opencode.exe"
$ocSha = "installers\opencode\opencode.exe.sha256"
if (-not (Test-Path $ocExe)) {
  $ocZip = "installers\opencode\opencode-windows-x64.zip"
  Write-Host "    downloading opencode-windows-x64.zip ..."
  Invoke-WebRequest -Uri "https://github.com/anomalyco/opencode/releases/latest/download/opencode-windows-x64.zip" -OutFile $ocZip
  Expand-Archive -Path $ocZip -DestinationPath "installers\opencode" -Force
  Remove-Item $ocZip
} else {
  Write-Host "    opencode.exe already present (skipping)"
}
# Supply-chain: record SHA256 next to the binary so releases are verifiable.
# If installers\opencode\opencode.exe.sha256 exists, verify it matches.
if (Test-Path $ocExe) {
  $actual = (Get-FileHash -Algorithm SHA256 $ocExe).Hash.ToLower()
  if (Test-Path $ocSha) {
    $pinned = (Get-Content $ocSha -Raw).Trim().Split()[0].ToLower()
    if ($actual -ne $pinned) { throw "opencode.exe SHA256 mismatch: expected $pinned got $actual" }
    Write-Host "    opencode.exe SHA256 verified: $actual"
  } else {
    $actual | Out-File -Encoding ascii -NoNewline $ocSha
    Write-Host "    opencode.exe SHA256 recorded: $actual"
  }
}

Write-Host "==> [3/4] Cloudflare WARP MSI (opt-in via -IncludeWarp)..."
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

Write-Host "==> [4/4] Compiling DataAgent-Setup.exe (Inno Setup)..."
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

if ($Sign) {
  Write-Host "==> [5/4] Code-signing (signtool)..."
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
  if (-not $signtool) { throw "signtool.exe not found - install Windows SDK; unsigned build kept in dist/" }
  $targets = @("dist\DataAgent.exe", "dist\DataAgentShell\DataAgentShell.exe", "dist\DataAgent-Setup.exe") | Where-Object { Test-Path $_ }
  foreach ($t in $targets) {
    if ($thumb) { & $signtool sign /fd SHA256 /tr $ts /td SHA256 /sha1 $thumb $t }
    elseif ($pfx) { & $signtool sign /fd SHA256 /tr $ts /td SHA256 /f $pfx /p $pw $t }
    else { throw "Set DATAAENT_CODESIGN_THUMBPRINT or DATAAENT_CODESIGN_PFX for -Sign" }
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $t" }
    Write-Host "    signed: $t"
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

Write-Host ""
Write-Host "Done. Installer: dist\DataAgent-Setup.exe"