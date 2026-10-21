<#
.SYNOPSIS
    Collect a DataAgent support bundle from a user's machine.

.DESCRIPTION
    Gathers the three diagnostic logs and a redacted runtime summary so a
    support request can be diagnosed without the user hand-rolling a zip.

    It NEVER collects secrets or customer data. The following are explicitly
    excluded and are never read, copied, listed or hashed:

      data\.jwt_secret                  session signing key
      data\.grant_key                   plan-grant signing key
      data\.grant_migrated              grant migration marker
      data\users.db                     password hashes, salts, MFA secrets
      data\audit.db                     audit sink
      data\curated\warehouse.duckdb     the customer's data
      data\raw\                         the customer's uploaded files
      data\staging\                     the customer's staged files
      config\users.json                 password hashes, plaintext API keys
      config\db_connections.json        saved database credentials
      config\google_login_pending.json  OAuth flow state

    `config\llm_runtime.json` is REDACTED rather than collected: only the
    provider name and the model are kept, never a key or a secret.

    Requires Windows PowerShell 5.1 or newer (no PowerShell 7-only syntax).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\support_bundle.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\support_bundle.ps1 -OutDir D:\bundles
#>
[CmdletBinding()]
param(
    [string]$OutDir = (Join-Path $env:TEMP 'dataagent-support')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-EnvOrNull {
    param([string]$Name)
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ($null -eq $v) { return '' }
    return $v
}

function Get-RuntimeDir {
    $explicit = Get-EnvOrNull 'DATAAGENT_DATA_DIR'
    if ($explicit) { return $explicit }
    if ([System.Environment]::OSVersion.Platform -eq 'Win32NT') {
        $local = Get-EnvOrNull 'LOCALAPPDATA'
        if (-not $local) { $local = Join-Path $HOME 'AppData\Local' }
        return (Join-Path $local 'DataAgent')
    }
    $xdg = Get-EnvOrNull 'XDG_DATA_HOME'
    if (-not $xdg) { $xdg = Join-Path $HOME '.local/share' }
    return (Join-Path $xdg 'DataAgent')
}

$runtime = Get-RuntimeDir
if (-not (Test-Path -LiteralPath $runtime)) {
    Write-Error "DataAgent runtime directory not found: $runtime"
    exit 1
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stage = Join-Path $OutDir ('dataagent-support-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# Files that are safe to collect.
$safeFiles = @(
    'logs\app.log',
    'logs\audit.log',
    'logs\runtime-stdout.log'
)

# Never collected. Listed here so the exclusion is auditable, not accidental.
$neverCollect = @(
    'data\.jwt_secret', 'data\.grant_key', 'data\.grant_migrated',
    'data\users.db', 'data\audit.db',
    'data\curated\warehouse.duckdb', 'data\curated\warehouse.duckdb.wal',
    'data\raw', 'data\staging',
    'config\users.json', 'config\db_connections.json',
    'config\google_login_pending.json'
)

$copied = 0
foreach ($rel in $safeFiles) {
    $src = Join-Path $runtime $rel
    if (Test-Path -LiteralPath $src -PathType Leaf) {
        $dest = Join-Path $stage $rel
        $parent = Split-Path -Parent $dest
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        # Tail the log: a multi-hundred-MB log is not a support bundle.
        Get-Content -LiteralPath $src -Tail 2000 -ErrorAction SilentlyContinue |
            Set-Content -LiteralPath $dest -Encoding UTF8
        $copied++
        Write-Host "  collected: $rel"
    }
}

# Redacted runtime configuration: provider + model only, never a key.
$llmRuntime = Join-Path $runtime 'config\llm_runtime.json'
$summary = [ordered]@{
    generated_by   = 'support_bundle.ps1'
    generated_at   = (Get-Date).ToString('o')
    runtime_dir    = $runtime
    app_version    = ''
    platform       = [System.Environment]::OSVersion.VersionString
    llm_provider   = ''
    llm_model      = ''
}
try {
    $settings = Join-Path $runtime 'config\settings.yaml'
    if (Test-Path -LiteralPath $settings) {
        $m = Select-String -LiteralPath $settings -Pattern '^\s{2}version:\s*"?([0-9.]+)"?\s*$' |
            Select-Object -First 1
        if ($m) { $summary.app_version = $m.Matches[0].Groups[1].Value }
    }
} catch { }
if (Test-Path -LiteralPath $llmRuntime) {
    try {
        $cfg = Get-Content -LiteralPath $llmRuntime -Raw | ConvertFrom-Json
        foreach ($k in @('provider', 'model')) {
            if ($cfg.PSObject.Properties.Name -contains $k) {
                $val = [string]$cfg.$k
                if ($val) { $summary[$k] = $val }
            }
        }
    } catch { }
}
$redacted = Join-Path $stage 'config\llm_runtime.REDACTED.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $redacted) | Out-Null
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $redacted -Encoding UTF8
$copied++

$readme = @"
DataAgent support bundle
=========================
Generated : $($summary.generated_at)
Version   : $($summary.app_version)
Platform  : $($summary.platform)

This bundle contains LOGS ONLY. It deliberately excludes:
  - the session signing key and the plan-grant signing key
  - the identity database (password hashes, salts, MFA secrets)
  - the warehouse and every uploaded/staged file (your data)
  - saved database credentials and OAuth state
  - the LLM provider configuration (redacted to provider + model)

Do not post it publicly. Send it to support through the channel you were
told to use.
"@
$readme | Set-Content -LiteralPath (Join-Path $stage 'README.txt') -Encoding UTF8

$zip = "$stage.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
Remove-Item -LiteralPath $stage -Recurse -Force

Write-Host ''
Write-Host "support bundle : $zip"
Write-Host "files collected : $copied"
Write-Host "never collected :"
foreach ($x in $neverCollect) { Write-Host "    - $x" }
exit 0
