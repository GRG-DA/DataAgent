<#
.SYNOPSIS
    Clean-room smoke test of an INSTALLED DataAgent desktop app.

.DESCRIPTION
    Runs against an installed app (not the source tree) and proves the two
    regressions that broke the first release are fixed:

      * D2 - the JWT signing secret must PERSIST across two launches. Before
        the fix it resolved from __file__ (inside the unwritable Program
        Files bundle), so every launch minted a new secret and every session
        was invalidated.
      * D3 - the paid-plan grant key must PERSIST across two launches. Same
        failure mode, same consequence: paid entitlements died on restart.

    It also checks that the shell starts, that the per-user runtime dir is
    created, that the health endpoint answers, and that a fresh account can
    register and log in.

    It NEVER prints a secret value. The two key files are compared through a
    SHA256 digest that is never written to the output - only "identical" or
    "CHANGED" is reported.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\smoke_desktop.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\smoke_desktop.ps1 -InstallDir "D:\Apps\DataAgent" -AppPort 8002

.NOTES
    MANUAL STEPS (these need a browser and are NOT automated here - do not
    fake them):

      1. Open the DataAgent window. The SPA must load and show the sign-in
         screen (no white screen, no "cannot reach the API" banner).
      2. Register through the UI with the account the script created
         (see the script output for the username) and confirm you land on
         the dashboard.
      3. Upload a small CSV, run one query against it, and export the
         result. Confirm the export file opens.
      4. Close the app, start it again and confirm you are STILL signed in
         (this is the D2 regression seen from the UI).
      5. Quit, restart once more and confirm the app still starts without a
         "port already in use" error.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [int]$AppPort = 8002,
    [int]$StartupTimeoutSeconds = 90,
    [string]$SmokeUser = "smoke-tester",
    [string]$SmokePassword = "DataAgent-Smoke-2026!"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Results = New-Object System.Collections.ArrayList

function Add-Result {
    param(
        [string]$Name,
        [ValidateSet('PASS', 'FAIL', 'MANUAL')][string]$Status,
        [string]$Detail
    )
    [void]$script:Results.Add([pscustomobject]@{
            Name   = $Name
            Status = $Status
            Detail = $Detail
        })
    $color = 'Gray'
    if ($Status -eq 'PASS') { $color = 'Green' }
    if ($Status -eq 'FAIL') { $color = 'Red' }
    if ($Status -eq 'MANUAL') { $color = 'Yellow' }
    Write-Host ("[{0}] {1} - {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Get-RuntimeDir {
    if ($env:DATAAGENT_DATA_DIR) { return $env:DATAAGENT_DATA_DIR }
    return (Join-Path $env:LOCALAPPDATA 'DataAgent')
}

# Compare a key file WITHOUT revealing it: only a SHA256 digest is computed
# and it is never printed.
function Get-FileFingerprint {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
}

function Stop-App {
    param($Process)
    if ($null -eq $Process) { return }
    try {
        if (-not $Process.HasExited) { Stop-Process -Id $Process.Id -Force }
    } catch {
        # already gone
    }
    Start-Sleep -Seconds 2
}

function Wait-PortFree {
    param([int]$Port)
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 2 | Out-Null
        } catch {
            return
        }
        Start-Sleep -Milliseconds 500
    }
}

function Start-App {
    param([string]$Exe, [int]$Port, [int]$TimeoutSeconds)
    $proc = Start-Process -FilePath $Exe -PassThru
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            throw ("the app exited during startup (exit code " + $proc.ExitCode + ")")
        }
        try {
            $health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 3
            return @{ Process = $proc; Health = $health }
        } catch {
            Start-Sleep -Milliseconds 750
        }
    }
    Stop-App $proc
    throw ("the app did not answer /health within " + $TimeoutSeconds + " seconds")
}

Write-Host ''
Write-Host '=== DataAgent installed-app smoke test ==='
Write-Host ''

# ---------------------------------------------------------------------------
# 1. the installed shell exe
# ---------------------------------------------------------------------------
if (-not $InstallDir) {
    foreach ($candidate in @(
            (Join-Path $env:ProgramFiles 'DataAgent'),
            (Join-Path ${env:ProgramFiles(x86)} 'DataAgent')
        )) {
        if (Test-Path -LiteralPath $candidate) { $InstallDir = $candidate; break }
    }
}
if (-not $InstallDir) {
    Add-Result 'installed shell exe' 'FAIL' 'no install directory found - pass -InstallDir'
    Write-Host ''
    Write-Host 'smoke test FAILED (see above)'
    exit 1
}
$shellExe = Join-Path $InstallDir 'DataAgentShell.exe'
if (-not (Test-Path -LiteralPath $shellExe)) {
    Add-Result 'installed shell exe' 'FAIL' ("not found: " + $shellExe)
    Write-Host ''
    Write-Host 'smoke test FAILED (see above)'
    exit 1
}
Add-Result 'installed shell exe' 'PASS' $shellExe

$runtimeDir = Get-RuntimeDir
$dataDir = Join-Path $runtimeDir 'data'

# ---------------------------------------------------------------------------
# 2. launch 1 - the runtime dir, the health endpoint and the key files
# ---------------------------------------------------------------------------
$launch1 = $null
try {
    $launch1 = Start-App -Exe $shellExe -Port $AppPort -TimeoutSeconds $StartupTimeoutSeconds
    Add-Result 'launch 1: the app starts' 'PASS' ("the shell is up on 127.0.0.1:" + $AppPort)
} catch {
    Add-Result 'launch 1: the app starts' 'FAIL' $_.Exception.Message
    Write-Host ''
    Write-Host 'smoke test FAILED (see above)'
    exit 1
}

if (Test-Path -LiteralPath $runtimeDir) {
    Add-Result 'runtime directory' 'PASS' ("created: " + $runtimeDir)
} else {
    Add-Result 'runtime directory' 'FAIL' ("not created: " + $runtimeDir)
}

$logsDir = Join-Path $runtimeDir 'logs'
if (Test-Path -LiteralPath $logsDir) {
    Add-Result 'log directory' 'PASS' ("present: " + $logsDir)
} else {
    Add-Result 'log directory' 'FAIL' ("missing: " + $logsDir)
}

try {
    $version = [string]$launch1.Health.version
    Add-Result 'health endpoint' 'PASS' ("answered /health (version " + $version + ")")
} catch {
    Add-Result 'health endpoint' 'FAIL' $_.Exception.Message
}

# The two key files. Their VALUES are never printed - only a digest, which is
# used for the equality comparison and never written out.
$jwtPath = Join-Path $dataDir '.jwt_secret'
$grantPath = Join-Path $dataDir '.grant_key'
$jwtDigest1 = Get-FileFingerprint $jwtPath
$grantDigest1 = Get-FileFingerprint $grantPath
if ($jwtDigest1) {
    Add-Result 'JWT secret present (launch 1)' 'PASS' ("written under " + $dataDir)
} else {
    Add-Result 'JWT secret present (launch 1)' 'FAIL' ("not written under " + $dataDir)
}
if ($grantDigest1) {
    Add-Result 'grant key present (launch 1)' 'PASS' ("written under " + $dataDir)
} else {
    Add-Result 'grant key present (launch 1)' 'FAIL' ("not written under " + $dataDir)
}

# ---------------------------------------------------------------------------
# 3. register + log in through the API
# ---------------------------------------------------------------------------
try {
    $registerBody = @{
        username = $SmokeUser
        password = $SmokePassword
        role     = 'admin'
    } | ConvertTo-Json
    $registered = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/auth/register" -f $AppPort) `
        -Method Post -ContentType 'application/json' -Body $registerBody -TimeoutSec 15
    if ($registered.success) {
        Add-Result 'register' 'PASS' ("the account " + $SmokeUser + " was created")
    } else {
        $why = [string]$registered.error
        if ($why -match 'exists|already') {
            Add-Result 'register' 'PASS' ("the account " + $SmokeUser + " already exists")
        } else {
            Add-Result 'register' 'FAIL' ("the API refused the registration: " + $why)
        }
    }
} catch {
    Add-Result 'register' 'FAIL' ("the registration call failed: " + $_.Exception.Message)
}

try {
    $loginBody = @{ username = $SmokeUser; password = $SmokePassword } | ConvertTo-Json
    $login = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/auth/login" -f $AppPort) `
        -Method Post -ContentType 'application/json' -Body $loginBody -TimeoutSec 15
    # The session token is never printed.
    if ($login.success -and $login.token) {
        Add-Result 'log in' 'PASS' ("the API issued a session token for " + $SmokeUser)
    } else {
        Add-Result 'log in' 'FAIL' ("the API refused the login: " + [string]$login.error)
    }
} catch {
    Add-Result 'log in' 'FAIL' ("the login call failed: " + $_.Exception.Message)
}

# ---------------------------------------------------------------------------
# 4. launch 2 - the keys must be IDENTICAL (D2 / D3 regressions)
# ---------------------------------------------------------------------------
Stop-App $launch1.Process
Wait-PortFree -Port $AppPort

$launch2 = $null
try {
    $launch2 = Start-App -Exe $shellExe -Port $AppPort -TimeoutSeconds $StartupTimeoutSeconds
    Add-Result 'launch 2: the app restarts' 'PASS' ("the shell is up again on 127.0.0.1:" + $AppPort)
} catch {
    Add-Result 'launch 2: the app restarts' 'FAIL' $_.Exception.Message
    Write-Host ''
    Write-Host 'smoke test FAILED (see above)'
    exit 1
}

$jwtDigest2 = Get-FileFingerprint $jwtPath
$grantDigest2 = Get-FileFingerprint $grantPath

if (-not $jwtDigest2) {
    Add-Result 'D2: the JWT secret persists' 'FAIL' ("the file is gone after the restart: " + $jwtPath)
} elseif ($jwtDigest1 -and $jwtDigest2 -eq $jwtDigest1) {
    Add-Result 'D2: the JWT secret persists' 'PASS' 'the same value after the restart'
} else {
    Add-Result 'D2: the JWT secret persists' 'FAIL' 'the value CHANGED after the restart - every session was invalidated (D2 regression)'
}

if (-not $grantDigest2) {
    Add-Result 'D3: the grant key persists' 'FAIL' ("the file is gone after the restart: " + $grantPath)
} elseif ($grantDigest1 -and $grantDigest2 -eq $grantDigest1) {
    Add-Result 'D3: the grant key persists' 'PASS' 'the same value after the restart'
} else {
    Add-Result 'D3: the grant key persists' 'FAIL' 'the value CHANGED after the restart - paid entitlements were invalidated (D3 regression)'
}

Stop-App $launch2.Process
Wait-PortFree -Port $AppPort

# ---------------------------------------------------------------------------
# 5. the steps that need a browser - clearly marked, never faked
# ---------------------------------------------------------------------------
Add-Result 'MANUAL: sign-in through the UI' 'MANUAL' 'open the DataAgent window, sign in with the account above, confirm the dashboard loads'
Add-Result 'MANUAL: upload -> query -> export' 'MANUAL' 'upload a small CSV, run one query, export the result and open the file'
Add-Result 'MANUAL: stay signed in after a restart' 'MANUAL' 'restart the app and confirm the session is still valid (the D2 regression seen from the UI)'
Add-Result 'MANUAL: upgrade over an existing install' 'MANUAL' 'run DataAgent-Setup.exe again over the existing install and confirm the app still starts'

$failures = @($script:Results | Where-Object { $_.Status -eq 'FAIL' })
Write-Host ''
Write-Host '=== summary ==='
Write-Host ("checks: " + $script:Results.Count + ", failures: " + $failures.Count)
Write-Host ("smoke account: " + $SmokeUser)
Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host 'SMOKE TEST FAILED - do NOT ship this build.' -ForegroundColor Red
    exit 1
}
Write-Host 'SMOKE TEST PASSED - the automated checks are green.' -ForegroundColor Green
Write-Host 'The MANUAL steps above still have to be done by a human.' -ForegroundColor Yellow
exit 0
