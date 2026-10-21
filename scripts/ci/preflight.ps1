<#
.SYNOPSIS
    Pre-deploy check for a hosted DataAgent deployment.

.DESCRIPTION
    Mirrors docs/SAAS-OPERATIONS.md section 2 (minimum configuration for a
    hosted deployment) plus the identity store, the warehouse, the audit sink
    and the LLM provider.

    It prints a PASS/FAIL table and exits non-zero when a check FAILs.
    It NEVER prints the value of a variable - only whether it is set.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\preflight.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -Path $RepoRoot

$script:PassCount = 0
$script:WarnCount = 0
$script:FailCount = 0

function Test-VariableSet {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name)
    return (-not [string]::IsNullOrWhiteSpace($value))
}

function Resolve-RepoPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $RepoRoot $Path)
}

function Write-Check {
    param(
        [string]$Name,
        [ValidateSet('PASS', 'WARN', 'FAIL')]
        [string]$Status,
        [string]$Detail
    )
    '{0,-32} {1,-6} {2}' -f $Name, $Status, $Detail
    switch ($Status) {
        'PASS' { $script:PassCount++ }
        'WARN' { $script:WarnCount++ }
        'FAIL' { $script:FailCount++ }
    }
}

Write-Output "DataAgent hosted-deployment preflight"
Write-Output "repo root : $RepoRoot"
Write-Output ''

# --- required environment ---------------------------------------------------
if (Test-VariableSet 'DATAAGENT_BOOTSTRAP_TOKEN') {
    Write-Check 'DATAAGENT_BOOTSTRAP_TOKEN' 'PASS' 'set (value not shown)'
} else {
    Write-Check 'DATAAGENT_BOOTSTRAP_TOKEN' 'FAIL' 'missing - the first account cannot become admin'
}

if (Test-VariableSet 'BILLING_EXTERNAL_URL') {
    Write-Check 'BILLING_EXTERNAL_URL' 'PASS' 'set (value not shown)'
} else {
    Write-Check 'BILLING_EXTERNAL_URL' 'FAIL' 'missing - there is no checkout to upgrade to'
}

if (Test-VariableSet 'TRUSTED_HOSTS') {
    $hosts = [Environment]::GetEnvironmentVariable('TRUSTED_HOSTS')
    if ($hosts.Trim() -eq '*') {
        Write-Check 'TRUSTED_HOSTS' 'FAIL' "is '*' - the host-header allow-list is disabled"
    } else {
        Write-Check 'TRUSTED_HOSTS' 'PASS' 'set (value not shown)'
    }
} else {
    Write-Check 'TRUSTED_HOSTS' 'FAIL' 'missing - DNS-rebinding guard is off'
}

if (Test-VariableSet 'TRUSTED_PROXY_NETWORKS') {
    Write-Check 'TRUSTED_PROXY_NETWORKS' 'PASS' 'set (value not shown)'
} else {
    Write-Check 'TRUSTED_PROXY_NETWORKS' 'FAIL' 'missing - clients behind the proxy share one rate-limit bucket'
}

# --- identity store ---------------------------------------------------------
$userStore = [Environment]::GetEnvironmentVariable('DATAAGENT_USER_STORE')
if ([string]::IsNullOrWhiteSpace($userStore)) { $userStore = 'sqlite' }
switch ($userStore.ToLowerInvariant()) {
    'postgres' {
        if ((Test-VariableSet 'DATABASE_URL') -or (Test-VariableSet 'POSTGRES_DSN')) {
            Write-Check 'Identity store' 'PASS' 'postgres backend configured'
        } else {
            Write-Check 'Identity store' 'FAIL' 'DATAAGENT_USER_STORE=postgres but DATABASE_URL is not set'
        }
    }
    'json' {
        Write-Check 'Identity store' 'WARN' 'DATAAGENT_USER_STORE=json is the legacy per-process store'
    }
    default {
        $userDb = [Environment]::GetEnvironmentVariable('DATAAGENT_USER_DB')
        if ([string]::IsNullOrWhiteSpace($userDb)) { $userDb = 'data/users.db' }
        $userDbPath = Resolve-RepoPath $userDb
        if (Test-Path -LiteralPath (Split-Path -Parent $userDbPath) -PathType Container) {
            Write-Check 'Identity store' 'PASS' 'sqlite backend, directory present'
        } else {
            Write-Check 'Identity store' 'FAIL' 'sqlite directory is missing - the store cannot be created'
        }
    }
}

# --- warehouse (never opened: DuckDB is single-writer) ----------------------
$warehousePath = [Environment]::GetEnvironmentVariable('DUCKDB_DATABASE_PATH')
if ([string]::IsNullOrWhiteSpace($warehousePath)) { $warehousePath = 'data/curated/warehouse.duckdb' }
$warehouseFile = Resolve-RepoPath $warehousePath
if ((Test-Path -LiteralPath $warehouseFile -PathType Leaf) -and ((Get-Item -LiteralPath $warehouseFile).Length -gt 0)) {
    Write-Check 'Warehouse' 'PASS' 'warehouse file present'
} else {
    Write-Check 'Warehouse' 'FAIL' 'warehouse file is missing or empty - initialise it before going live'
}

# --- audit sink -------------------------------------------------------------
$auditSink = [Environment]::GetEnvironmentVariable('AUDIT_SINK')
if (Test-VariableSet 'REDIS_URL') {
    Write-Check 'Audit sink' 'PASS' 'shared backend available (redis)'
} elseif ($auditSink -eq 'sqlite') {
    Write-Check 'Audit sink' 'PASS' 'sqlite sink configured'
} elseif ($auditSink -eq 'redis') {
    Write-Check 'Audit sink' 'FAIL' 'AUDIT_SINK=redis but REDIS_URL is not set'
} else {
    Write-Check 'Audit sink' 'FAIL' 'per-container file sink - set AUDIT_SINK=sqlite or REDIS_URL'
}

# --- LLM provider -----------------------------------------------------------
$llmVars = @(
    'LLM_PROVIDER', 'LLM_OPENAI_KEY', 'LLM_ANTHROPIC_KEY', 'LLM_GEMINI_KEY',
    'LLM_GROQ_KEY', 'LLM_XAI_KEY', 'LLM_OPENROUTER_KEY', 'LLM_DEEPSEEK_KEY',
    'LLM_OPENCODE_BASE_URL', 'LLM_OPENCODE_SERVER_URL'
)
$llmConfigured = $null
foreach ($llmVar in $llmVars) {
    if (Test-VariableSet $llmVar) { $llmConfigured = $llmVar; break }
}
if ($llmConfigured) {
    Write-Check 'LLM provider' 'PASS' "configured via $llmConfigured (value not shown)"
} elseif (Test-Path -LiteralPath (Join-Path $RepoRoot 'config\llm_runtime.json') -PathType Leaf) {
    Write-Check 'LLM provider' 'PASS' 'configured via config/llm_runtime.json'
} else {
    Write-Check 'LLM provider' 'FAIL' 'no provider configured - /ask cannot answer a question'
}

# --- recommended (never fatal) ----------------------------------------------
if ((Test-VariableSet 'BILLING_SYNC_URL') -and (Test-VariableSet 'BILLING_SYNC_SECRET')) {
    Write-Check 'Billing entitlement sync' 'PASS' 'configured'
} else {
    Write-Check 'Billing entitlement sync' 'WARN' 'set BILLING_SYNC_URL and BILLING_SYNC_SECRET'
}

if (Test-VariableSet 'REDIS_URL') {
    Write-Check 'Redis' 'PASS' 'configured'
} else {
    Write-Check 'Redis' 'WARN' 'set REDIS_URL to share the limiter and the audit sink'
}

if ((Test-VariableSet 'DATAAGENT_GRANT_KEY') -or (Test-VariableSet 'DATAAGENT_GRANT_KEY_FILE')) {
    Write-Check 'Grant signing key' 'PASS' 'configured'
} else {
    Write-Check 'Grant signing key' 'WARN' 'set DATAAGENT_GRANT_KEY outside the data host'
}

$total = $script:PassCount + $script:WarnCount + $script:FailCount
Write-Output ''
Write-Output "checks: $total  pass: $($script:PassCount)  warn: $($script:WarnCount)  fail: $($script:FailCount)"
if ($script:FailCount -gt 0) {
    Write-Output 'preflight FAILED - fix every FAIL row above before deploying'
    exit 1
}
Write-Output 'preflight PASSED'
exit 0
