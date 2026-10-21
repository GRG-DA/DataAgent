<#
.SYNOPSIS
    Single entry point for the DataAgent test suite on Windows.

.DESCRIPTION
    Installs the right extras, runs the suite with the same coverage scope
    and gate as CI (.github/workflows/pytest.yml) and prints a summary.

    The identity store is redirected to a throwaway directory, so a local run
    never touches data/users.db.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\run_tests.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\run_tests.ps1 -SkipInstall
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [string]$Python = $env:PYTHON,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -Path $RepoRoot

if ([string]::IsNullOrWhiteSpace($Python)) {
    foreach ($candidate in @('python', 'python3')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) { $Python = $found.Source; break }
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    Write-Error 'no python interpreter found - pass -Python /path/to/python'
    exit 2
}

if (-not $SkipInstall) {
    Write-Output '==> installing dependencies'
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m pip install -r requirements.txt -r requirements-test.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$failUnder = 74
try {
    $raw = & $Python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['tool']['coverage']['report']['fail_under'])"
    if ($raw) { $failUnder = [int]$raw }
} catch {
    $failUnder = 74
}

$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("dataagent-ci-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null
$env:DATAAGENT_USER_DB = Join-Path $workDir 'users.db'
$env:DATAAGENT_USER_LEGACY_JSON = Join-Path $workDir 'no_users.json'

$log = Join-Path $workDir 'pytest.log'
Write-Output "==> running the suite (coverage gate: $failUnder%)"
$pytestArgsList = @(
    '-m', 'pytest', '-q', '-rs',
    '--cov=api', '--cov=agent', '--cov=etl', '--cov=analysis', '--cov=warehouse', '--cov=config',
    '--cov-report=term-missing',
    '--cov-report=xml',
    "--cov-fail-under=$failUnder"
) + $PytestArgs
& $Python @pytestArgsList *>&1 | Tee-Object -FilePath $log
$status = $LASTEXITCODE

$summary = (Get-Content -LiteralPath $log -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '\d+ (passed|failed)' } | Select-Object -Last 1)
$coverage = (Get-Content -LiteralPath $log -ErrorAction SilentlyContinue |
    Where-Object { $_ -match 'Required test coverage' } | Select-Object -Last 1)

Write-Output ''
Write-Output '=== DataAgent test summary ==='
Write-Output "interpreter : $Python"
Write-Output "coverage gate: $failUnder% (from pyproject.toml)"
Write-Output "exit code   : $status"
if ($summary) { Write-Output "result       : $summary" }
if ($coverage) { Write-Output "coverage     : $coverage" }
if ($status -eq 0) { Write-Output 'verdict      : PASSED' } else { Write-Output 'verdict      : FAILED' }

Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
exit $status
