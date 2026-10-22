<#
.SYNOPSIS
    Run the full release gate for the DataAgent Windows desktop build.

.DESCRIPTION
    Executes the gates in docs/RELEASE-CHECKLIST.md in order and prints a
    PASS/FAIL table. Exits non-zero when any gate fails, so it can be used
    as a CI step or as a pre-release script.

    Usage:
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\verify_release.ps1
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\verify_release.ps1 -SkipTests

    The code gates run the pytest suite, which takes ~13 minutes on the
    reference machine. Use -SkipTests for a fast structural check.
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -Path $RepoRoot

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0
$script:Failures = New-Object System.Collections.Generic.List[string]

function Write-Result {
    param([string]$Name, [string]$Status, [string]$Detail = '')
    '{0,-46} {1,-6} {2}' -f $Name, $Status, $Detail
    switch ($Status) {
        'PASS' { $script:Pass++ }
        'FAIL' { $script:Fail++; $script:Failures.Add("$Name - $Detail") }
        'SKIP' { $script:Skip++ }
    }
}

function Invoke-Gate {
    param([string]$Name, [scriptblock]$Action, [string]$FailureDetail = 'command failed')
    try {
        $out = & $Action 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Result $Name 'PASS'
            return $true
        }
        $tail = ($out | Select-Object -Last 3) -join ' | '
        Write-Result $Name 'FAIL' $tail
        return $false
    } catch {
        Write-Result $Name 'FAIL' $_.Exception.Message
        return $false
    }
}

Write-Output 'DataAgent release verification'
Write-Output "repo root : $RepoRoot"
Write-Output ''

# --- 1. working tree ---------------------------------------------------------
$dirty = @(git status --porcelain 2>$null)
if ($dirty.Count -gt 0) {
    Write-Result 'Working tree clean' 'FAIL' "$($dirty.Count) uncommitted change(s)"
} else {
    Write-Result 'Working tree clean' 'PASS'
}

# --- 2. test suite -----------------------------------------------------------
if ($SkipTests) {
    Write-Result 'Test suite' 'SKIP' '-SkipTests'
} else {
    Invoke-Gate 'Test suite' { python -m pytest -q --no-header -p no:cacheprovider } | Out-Null
    Invoke-Gate 'Security regressions' { python -m pytest tests/test_security_regressions.py -q --no-header -p no:cacheprovider } | Out-Null
    Invoke-Gate 'Packaging contract' { python -m pytest tests/test_packaging.py -q --no-header -p no:cacheprovider } | Out-Null
    # The glob must be expanded HERE: neither PowerShell nor pytest expands
# `tests/test_launch_*.py` on its own, so the gate used to run ZERO tests
# and report success.
$launchTests = @(Get-ChildItem (Join-Path $RepoRoot 'tests/test_launch_*.py') | ForEach-Object { $_.FullName })
Invoke-Gate 'Launch hardening' { python -m pytest $launchTests -q --no-header -p no:cacheprovider } | Out-Null
    Invoke-Gate 'Billing consistency' { python -m pytest tests/test_billing_consistency.py -q --no-header -p no:cacheprovider } | Out-Null
}

# --- 3. no credential state would be committed -------------------------------
$tracked = @(git ls-files)
$leaks = @(@($tracked | Where-Object {
    $_ -match '(^|/)\.env$|(^|/)users\.json$|(^|/)db_connections\.json$|(^|/)llm_runtime\.json$|(^|/)google_login_pending|(^|/)revoked_jtis\.json$|(^|/)\.jwt_secret$|(^|/)\.grant_key$|(^|/)users\.db$'
}))
if ($leaks.Count -gt 0) {
    Write-Result 'No credential state tracked' 'FAIL' ($leaks -join ', ')
} else {
    Write-Result 'No credential state tracked' 'PASS'
}

# --- 4. no private key material in the tree ----------------------------------
$pems = @(@(git ls-files | Where-Object { $_ -match '\.pem$' -and $_ -notmatch 'cacert\.pem$' }))
if ($pems.Count -gt 0) {
    Write-Result 'No private key material tracked' 'FAIL' ($pems -join ', ')
} else {
    Write-Result 'No private key material tracked' 'PASS'
}

# --- 5. version strings agree ------------------------------------------------
Invoke-Gate 'Version strings agree' {
    python -m pytest tests/test_packaging.py -q --no-header -p no:cacheprovider -k version
} | Out-Null

# --- 5b. the marketing site ---------------------------------------------------
# The site is a separate deliverable from the app: it has its own links, its
# own accessibility findings and its own third-party-origin budget.
Invoke-Gate 'Site: broken links' {
    python scripts/ci/check_site_links.py
} | Out-Null
Invoke-Gate 'Site: accessibility' {
    python scripts/ci/check_site_a11y.py
} | Out-Null
Invoke-Gate 'Site: palette (rendered pixels)' {
    python scripts/ci/check_site_pixels.py
} | Out-Null
Invoke-Gate 'Site: every page renders' {
    python scripts/ci/check_site_renders.py
} | Out-Null
Invoke-Gate 'Site: cache-busters' {
    python scripts/site_asset_versions.py
} | Out-Null
Invoke-Gate 'Site: one installer size' {
    python scripts/site_installer_size.py
} | Out-Null
Invoke-Gate 'Site: launch artefacts + privacy' {
    python -m pytest tests/test_launch_site_launch_readiness.py tests/test_launch_site_privacy.py -q --no-header -p no:cacheprovider
} | Out-Null

# --- 5c. nothing that carries a credential is tracked or staged --------------
# This is the check that would have caught the 1.9 GB archive of the working
# tree that sat in the repo root during the pre-launch review.
Invoke-Gate 'No credential state tracked or staged' {
    python -m pytest tests/test_launch_no_credentials_committed.py -q --no-header -p no:cacheprovider
} | Out-Null

# --- 6. desktop preflight ----------------------------------------------------
$preflight = Join-Path $PSScriptRoot 'preflight_desktop.ps1'
if (Test-Path -LiteralPath $preflight) {
    Invoke-Gate 'Desktop preflight' {
        powershell -NoProfile -ExecutionPolicy Bypass -File $preflight
    } | Out-Null
} else {
    Write-Result 'Desktop preflight' 'FAIL' 'scripts\ci\preflight_desktop.ps1 is missing'
}

# --- 7. build artefacts (only if already built) ------------------------------
if ($SkipBuild) {
    Write-Result 'Build artefacts' 'SKIP' '-SkipBuild'
} else {
    $setup = Join-Path $RepoRoot 'dist\DataAgent-Setup.exe'
    $web = Join-Path $RepoRoot 'dist\DataAgent.exe'
    $shell = Join-Path $RepoRoot 'dist\DataAgentShell\DataAgentShell.exe'
    $sums = Join-Path $RepoRoot 'dist\SHA256SUMS.txt'
    if (-not (Test-Path -LiteralPath $setup)) {
        Write-Result 'dist\DataAgent-Setup.exe' 'FAIL' 'not built'
    } else {
        Write-Result 'dist\DataAgent-Setup.exe' 'PASS' ("{0:N1} MB" -f ((Get-Item $setup).Length / 1MB))
    }
    if (Test-Path -LiteralPath $web) {
        Write-Result 'dist\DataAgent.exe' 'PASS' ("{0:N1} MB" -f ((Get-Item $web).Length / 1MB))
    } else { Write-Result 'dist\DataAgent.exe' 'FAIL' 'not built' }
    if (Test-Path -LiteralPath $shell) {
        Write-Result 'dist\DataAgentShell\DataAgentShell.exe' 'PASS' ("{0:N1} MB" -f ((Get-Item $shell).Length / 1MB))
    } else { Write-Result 'dist\DataAgentShell\DataAgentShell.exe' 'FAIL' 'not built' }
    if (Test-Path -LiteralPath $sums) {
        Write-Result 'dist\SHA256SUMS.txt' 'PASS'
    } else { Write-Result 'dist\SHA256SUMS.txt' 'FAIL' 'not generated' }
}

# --- 8. signing --------------------------------------------------------------
$isSigned = $false
$setup = Join-Path $RepoRoot 'dist\DataAgent-Setup.exe'
if (Test-Path -LiteralPath $setup) {
    $signtool = Get-Command signtool -ErrorAction SilentlyContinue
    if ($signtool) {
        Invoke-Gate 'Installer signature' { & signtool.Source verify /pa $setup } | Out-Null
        $isSigned = $true
    } else {
        Write-Result 'Installer signature' 'FAIL' 'signtool not found - the release is UNSIGNED'
    }
} else {
    Write-Result 'Installer signature' 'SKIP' 'no installer built'
}

Write-Output ''
Write-Output "checks: $($script:Pass + $script:Fail + $script:Skip)  pass: $($script:Pass)  fail: $($script:Fail)  skip: $($script:Skip)"
if ($script:Fail -gt 0) {
    Write-Output ''
    Write-Output 'FAILED:'
    foreach ($f in $script:Failures) { Write-Output "  - $f" }
    Write-Output ''
    Write-Output 'verification FAILED - see docs/RELEASE-CHECKLIST.md'
    exit 1
}
Write-Output 'verification PASSED'
exit 0
