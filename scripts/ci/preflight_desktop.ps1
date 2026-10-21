<#
.SYNOPSIS
    Preflight checks for a frozen DataAgent desktop build (Windows).

.DESCRIPTION
    Run this BEFORE scripts\build_windows.ps1. It answers the question
    "would this machine produce a shippable installer?" and exits NON-ZERO
    when a check FAILs, so a broken release can never be produced silently.

    Checks:
      1. python + PyInstaller importable (and the PyInstaller version)
      2. Inno Setup 6 (ISCC.exe) discoverable - which path was found
      3. signtool.exe availability (FAIL with -RequireSigning, else WARN)
      4. the pinned opencode version + its committed SHA256 file
      5. NO credential state would be bundled (the two PyInstaller specs)
      6. the release version agrees across settings.yaml, version_info.txt
         and the Inno Setup script
      7. the Edge WebView2 runtime (the desktop shell needs it)
      8. free disk space for a ~500 MB installer
      9. the support log directory is creatable and writable

    It NEVER prints a secret value: no credential file is ever read, only
    its NAME and its presence.

.PARAMETER RequireSigning
    Treat a missing signtool.exe as a FAIL instead of a WARN. Use it when
    the build is going to be signed (-Sign).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\preflight_desktop.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\preflight_desktop.ps1 -RequireSigning

.NOTES
    SUPPORT BUNDLE PROCEDURE (C10)
    ================================
    A user's support bundle is collected from the per-user runtime directory
    %LOCALAPPDATA%\DataAgent (the frozen app chdirs there on startup). The
    tray menu item "Open logs folder" opens it directly.

    COLLECT these files:
        logs/app.log                application log
        logs/audit.log              audit trail
        logs/runtime-stdout.log     stdout/stderr of the frozen process
        config/llm_runtime.json     LLM provider runtime state - REDACTED
                                    (provider + model only, never a key)

    NEVER collect these (they are secrets or the customer's own data):
        data/.jwt_secret            session signing key
        data/.grant_key             plan-grant signing key
        data/.grant_migrated        grant migration marker
        config/users.json           password hashes, plaintext API keys
        data/users.db               password hashes, salts, MFA secrets
        data/curated/warehouse.duckdb   the customer's data
        data/raw/                   the customer's uploaded files
        data/staging/               the customer's staged files

    scripts\ci\support_bundle.ps1 implements exactly this: it collects the
    three log files, redacts config/llm_runtime.json to provider + model and
    never touches anything on the never-collect list.
#>
[CmdletBinding()]
param(
    [switch]$RequireSigning,
    [string]$Root = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $Root) {
    $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$Root = (Resolve-Path -LiteralPath $Root).Path

$script:Results = New-Object System.Collections.ArrayList

function Add-Result {
    param(
        [string]$Name,
        [ValidateSet('PASS', 'WARN', 'FAIL')][string]$Status,
        [string]$Detail
    )
    [void]$script:Results.Add([pscustomobject]@{
            Name   = $Name
            Status = $Status
            Detail = $Detail
        })
    $color = 'Gray'
    if ($Status -eq 'PASS') { $color = 'Green' }
    if ($Status -eq 'WARN') { $color = 'Yellow' }
    if ($Status -eq 'FAIL') { $color = 'Red' }
    Write-Host ("[{0}] {1} - {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Get-AppVersion {
    <#
      Read app.version out of config/settings.yaml without a YAML parser:
      the "app:" block is scanned until the next top-level key.
    #>
    param([string]$SettingsPath)
    if (-not (Test-Path -LiteralPath $SettingsPath)) { return $null }
    $inApp = $false
    foreach ($line in [System.IO.File]::ReadAllLines($SettingsPath)) {
        if ($line -match '^app:\s*$') { $inApp = $true; continue }
        if ($inApp -and $line -match '^\S') { break }
        if ($inApp -and $line -match '^\s+version:\s*"?([0-9.]+)"?\s*$') {
            return $Matches[1]
        }
    }
    return $null
}

Write-Host ''
Write-Host '=== DataAgent desktop preflight ==='
Write-Host ("repository: " + $Root)
Write-Host ''

# ---------------------------------------------------------------------------
# 1. Python + PyInstaller
# ---------------------------------------------------------------------------
$pyVersion = ''
$pyInstallerVersion = ''
try {
    $pyVersion = ((& python -V 2>&1) | Out-String).Trim()
    $pyInstallerVersion = ((& python -c "import PyInstaller;print(PyInstaller.__version__)" 2>&1) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { $pyInstallerVersion = '' }
} catch {
    $pyVersion = ''
}
if ($pyInstallerVersion) {
    Add-Result 'python + PyInstaller' 'PASS' ("$pyVersion, PyInstaller $pyInstallerVersion")
} else {
    Add-Result 'python + PyInstaller' 'FAIL' 'python -m PyInstaller is not importable (pip install pyinstaller)'
}

# ---------------------------------------------------------------------------
# 2. Inno Setup 6 (ISCC.exe)
# ---------------------------------------------------------------------------
$isccPath = $null
$isccCmd = Get-Command iscc -ErrorAction SilentlyContinue
if ($isccCmd) { $isccPath = $isccCmd.Source }
if (-not $isccPath) {
    foreach ($candidate in @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
            "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
        )) {
        if (Test-Path -LiteralPath $candidate) { $isccPath = $candidate; break }
    }
}
if ($isccPath) {
    Add-Result 'Inno Setup 6 (ISCC.exe)' 'PASS' ("found: " + $isccPath)
} else {
    Add-Result 'Inno Setup 6 (ISCC.exe)' 'FAIL' 'ISCC.exe not found - install Inno Setup 6 from https://jrsoftware.org/isinfo.php'
}

# ---------------------------------------------------------------------------
# 3. signtool.exe
# ---------------------------------------------------------------------------
$signtoolPath = $null
$signtoolCmd = Get-Command signtool -ErrorAction SilentlyContinue
if ($signtoolCmd) { $signtoolPath = $signtoolCmd.Source }
if (-not $signtoolPath) {
    $sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1
    if ($sdk) { $signtoolPath = $sdk.FullName }
}
if ($signtoolPath) {
    Add-Result 'signtool.exe' 'PASS' ("found: " + $signtoolPath)
} elseif ($RequireSigning) {
    Add-Result 'signtool.exe' 'FAIL' 'not found and -RequireSigning was requested - install the Windows SDK'
} else {
    Add-Result 'signtool.exe' 'WARN' 'not found - only needed for a signed (-Sign) release'
}

# ---------------------------------------------------------------------------
# 4. the pinned opencode version + its committed SHA256 file
# ---------------------------------------------------------------------------
$buildScript = Join-Path $Root 'scripts\build_windows.ps1'
$buildText = [System.IO.File]::ReadAllText($buildScript)
$versionMatch = [regex]::Match($buildText, '\$OpenCodeVersion\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    Add-Result 'opencode pin' 'FAIL' 'scripts/build_windows.ps1 does not define $OpenCodeVersion'
} else {
    $ocVersion = $versionMatch.Groups[1].Value
    $shaPath = Join-Path $Root ('installers\opencode-' + $ocVersion + '.sha256')
    if (-not (Test-Path -LiteralPath $shaPath)) {
        Add-Result 'opencode pin' 'FAIL' ("no committed hash file for " + $ocVersion + " (expected installers\opencode-" + $ocVersion + ".sha256)")
    } else {
        $shaText = ([System.IO.File]::ReadAllText($shaPath)).Trim()
        $digest = ($shaText -split '\s+')[0].ToLower()
        if ($digest -notmatch '^[0-9a-f]{64}$') {
            Add-Result 'opencode pin' 'FAIL' ("the hash file for " + $ocVersion + " is not a 64-char hex SHA256")
        } else {
            $ocExe = Join-Path $Root 'installers\opencode\opencode.exe'
            if (Test-Path -LiteralPath $ocExe) {
                $have = (Get-FileHash -Algorithm SHA256 $ocExe).Hash.ToLower()
                if ($have -ne $digest) {
                    Add-Result 'opencode pin' 'FAIL' ("the downloaded opencode.exe does not match the " + $ocVersion + " pin")
                } else {
                    Add-Result 'opencode pin' 'PASS' ("version " + $ocVersion + ", sha256 " + $digest + " (binary verified)")
                }
            } else {
                Add-Result 'opencode pin' 'PASS' ("version " + $ocVersion + ", sha256 " + $digest + " (binary not downloaded yet)")
            }
        }
    }
}

# ---------------------------------------------------------------------------
# 5. no credential state would be bundled
# ---------------------------------------------------------------------------
$requiredCredentialNames = @(
    'users.json', 'db_connections.json', 'google_login_pending.json',
    'llm_runtime.json', 'revoked_jtis.json',
    '.jwt_secret', '.grant_key', '.grant_migrated', '.env'
)
foreach ($specRel in @('DataAgent.spec', 'scripts\DataAgentShell.spec')) {
    $specPath = Join-Path $Root $specRel
    if (-not (Test-Path -LiteralPath $specPath)) {
        Add-Result ('credential exclusion: ' + $specRel) 'FAIL' 'spec file is missing'
        continue
    }
    $specText = [System.IO.File]::ReadAllText($specPath)
    $blockMatch = [regex]::Match($specText, '_CREDENTIAL_BASENAMES\s*=\s*\{(?<body>[^}]*)\}',
        [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if (-not $blockMatch.Success) {
        Add-Result ('credential exclusion: ' + $specRel) 'FAIL' 'no _CREDENTIAL_BASENAMES block'
        continue
    }
    $declared = @()
    foreach ($nameMatch in [regex]::Matches($blockMatch.Groups['body'].Value, "['""]([^'""]+)['""]")) {
        $declared += $nameMatch.Groups[1].Value
    }
    $missing = @($requiredCredentialNames | Where-Object { $declared -notcontains $_ })
    $filtersData = ($specText -match '_is_credential_state') -and
        (($specText -match 'a\.datas\s*=\s*\[\(d,\s*s,\s*k\)') -or
            ($specText -match 'datas\s*=\s*\[\(d,\s*s\)'))
    if ($missing.Count -gt 0) {
        Add-Result ('credential exclusion: ' + $specRel) 'FAIL' ('does not exclude: ' + ($missing -join ', '))
    } elseif (-not $filtersData) {
        Add-Result ('credential exclusion: ' + $specRel) 'FAIL' 'the data list is not filtered through _is_credential_state'
    } else {
        Add-Result ('credential exclusion: ' + $specRel) 'PASS' 'all runtime credential state is filtered out of the bundle'
    }
}

# ---------------------------------------------------------------------------
# 6. the release version agrees everywhere
# ---------------------------------------------------------------------------
$issPath = Join-Path $Root 'installers\DataAgent-Setup.iss'
$settingsPath = Join-Path $Root 'config\settings.yaml'
$versionInfoPath = Join-Path $Root 'packaging\windows\version_info.txt'
$issText = [System.IO.File]::ReadAllText($issPath)
$issMatch = [regex]::Match($issText, '#define MyAppVersion\s+"([^"]+)"')
$issVersion = if ($issMatch.Success) { $issMatch.Groups[1].Value } else { '' }
$appVersion = Get-AppVersion $settingsPath
$versionInfoText = [System.IO.File]::ReadAllText($versionInfoPath)
if (-not $issVersion -or -not $appVersion) {
    Add-Result 'release version agreement' 'FAIL' 'the version could not be read from the Inno Setup script or settings.yaml'
} elseif ($issVersion -ne $appVersion) {
    Add-Result 'release version agreement' 'FAIL' ("settings.yaml says " + $appVersion + ", the Inno Setup script says " + $issVersion)
} elseif ($versionInfoText -notmatch [regex]::Escape("u'" + $appVersion + "'")) {
    Add-Result 'release version agreement' 'FAIL' ("version_info.txt does not carry " + $appVersion)
} else {
    Add-Result 'release version agreement' 'PASS' ("settings.yaml, version_info.txt and MyAppVersion all say " + $appVersion)
}

# ---------------------------------------------------------------------------
# 7. the Edge WebView2 runtime (the desktop shell needs it)
# ---------------------------------------------------------------------------
$webviewDetail = ''
$webviewFound = $false
foreach ($regKey in @(
        'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        'HKCU:\Software\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    )) {
    if (Test-Path -LiteralPath $regKey) {
        $regProps = Get-ItemProperty -LiteralPath $regKey -ErrorAction SilentlyContinue
        if ($regProps) {
            $pvProperty = $regProps.PSObject.Properties['pv']
            if ($pvProperty -and $pvProperty.Value) {
                $webviewFound = $true
                $webviewDetail = "runtime " + $pvProperty.Value
                break
            }
        }
    }
}
if (-not $webviewFound) {
    foreach ($candidate in @(
            "${env:ProgramFiles(x86)}\Microsoft\EdgeWebView\Application",
            "${env:ProgramFiles}\Microsoft\EdgeWebView\Application"
        )) {
        if (Test-Path -LiteralPath $candidate) {
            $webviewFound = $true
            $webviewDetail = "installed under " + $candidate
            break
        }
    }
}
if ($webviewFound) {
    Add-Result 'Edge WebView2 runtime' 'PASS' $webviewDetail
} else {
    Add-Result 'Edge WebView2 runtime' 'WARN' 'not found on this build machine - the end user needs it (https://go.microsoft.com/fwlink/p/?LinkId=2124703)'
}

# ---------------------------------------------------------------------------
# 8. free disk space
# ---------------------------------------------------------------------------
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$driveName = (Split-Path -Qualifier $rootPath).TrimEnd(':')
if (-not $driveName) { $driveName = 'C' }
$drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
if (-not $drive) {
    Add-Result 'free disk space' 'WARN' ('could not measure the free space of ' + $driveName + ':')
} else {
    $freeGb = [math]::Round($drive.Free / 1GB, 2)
    if ($freeGb -ge 1.0) {
        Add-Result 'free disk space' 'PASS' ("$freeGb GB free on " + $driveName + ": (a ~500 MB installer plus the build output needs about 1 GB)")
    } else {
        Add-Result 'free disk space' 'FAIL' ("only $freeGb GB free on " + $driveName + ": - a ~500 MB installer needs about 1 GB")
    }
}

# ---------------------------------------------------------------------------
# 9. the support log directory
# ---------------------------------------------------------------------------
$runtimeDir = Join-Path $env:LOCALAPPDATA 'DataAgent'
$logsDir = Join-Path $runtimeDir 'logs'
try {
    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
    $probeFile = Join-Path $logsDir '.preflight_write_test'
    Set-Content -LiteralPath $probeFile -Value 'ok' -Encoding ASCII
    Remove-Item -LiteralPath $probeFile -Force
    Add-Result 'support log directory' 'PASS' ("creatable and writable: " + $logsDir)
} catch {
    Add-Result 'support log directory' 'FAIL' ("not writable: " + $logsDir + " - a support bundle cannot be collected")
}

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
$failures = @($script:Results | Where-Object { $_.Status -eq 'FAIL' })
$warnings = @($script:Results | Where-Object { $_.Status -eq 'WARN' })
Write-Host ''
Write-Host '=== summary ==='
Write-Host ("checks: " + $script:Results.Count + ", warnings: " + $warnings.Count + ", failures: " + $failures.Count)
Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host 'PREFLIGHT FAILED - fix the failures above before building a release.' -ForegroundColor Red
    exit 1
}
Write-Host 'PREFLIGHT PASSED - the build machine can produce a release.' -ForegroundColor Green
exit 0
