#!/bin/sh
# Pre-deploy check for a hosted DataAgent deployment.
#
# Mirrors docs/SAAS-OPERATIONS.md section 2 (minimum configuration for a
# hosted deployment) plus the identity store, the warehouse, the audit sink
# and the LLM provider.
#
# It prints a PASS/FAIL table and exits non-zero when a check FAILs.
# It NEVER prints the value of a variable - only whether it is set.
#
# Usage:
#   sh scripts/ci/preflight.sh
#
# The checks read the process environment only. Load your deployment
# environment first, e.g.
#   set -a; . ./.env; set +a; sh scripts/ci/preflight.sh

set -u

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT" || exit 2

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

row() {
    printf "%-32s %-6s %s\n" "$1" "$2" "$3"
    case "$2" in
        PASS) PASS_COUNT=$((PASS_COUNT + 1)) ;;
        WARN) WARN_COUNT=$((WARN_COUNT + 1)) ;;
        FAIL) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
    esac
}

is_set() {
    eval "value=\${$1:-}"
    [ -n "$value" ]
}

resolve_path() {
    case "$1" in
        # A Windows path (Git Bash on Windows): convert it to a POSIX path so
        # the existence checks below work.
        [A-Za-z]:[\\/]*)
            if command -v cygpath >/dev/null 2>&1; then
                cygpath -u "$1"
            else
                printf '%s' "$1"
            fi
            ;;
        /*) printf '%s' "$1" ;;
        *) printf '%s/%s' "$REPO_ROOT" "$1" ;;
    esac
}

echo "DataAgent hosted-deployment preflight"
echo "repo root : $REPO_ROOT"
echo

# --- required environment ---------------------------------------------------
if is_set DATAAGENT_BOOTSTRAP_TOKEN; then
    row "DATAAGENT_BOOTSTRAP_TOKEN" "PASS" "set (value not shown)"
else
    row "DATAAGENT_BOOTSTRAP_TOKEN" "FAIL" "missing - the first account cannot become admin"
fi

if is_set BILLING_EXTERNAL_URL; then
    row "BILLING_EXTERNAL_URL" "PASS" "set (value not shown)"
else
    row "BILLING_EXTERNAL_URL" "FAIL" "missing - there is no checkout to upgrade to"
fi

if is_set TRUSTED_HOSTS; then
    hosts_value=$(eval "printf '%s' \"\${TRUSTED_HOSTS:-}\"")
    if [ "$hosts_value" = "*" ]; then
        row "TRUSTED_HOSTS" "FAIL" "is '*' - the host-header allow-list is disabled"
    else
        row "TRUSTED_HOSTS" "PASS" "set (value not shown)"
    fi
else
    row "TRUSTED_HOSTS" "FAIL" "missing - DNS-rebinding guard is off"
fi

if is_set TRUSTED_PROXY_NETWORKS; then
    row "TRUSTED_PROXY_NETWORKS" "PASS" "set (value not shown)"
else
    row "TRUSTED_PROXY_NETWORKS" "FAIL" "missing - clients behind the proxy share one rate-limit bucket"
fi

# --- identity store ---------------------------------------------------------
user_store=$(eval "printf '%s' \"\${DATAAGENT_USER_STORE:-sqlite}\"")
case "$user_store" in
    postgres)
        if is_set DATABASE_URL || is_set POSTGRES_DSN; then
            row "Identity store" "PASS" "postgres backend configured"
        else
            row "Identity store" "FAIL" "DATAAGENT_USER_STORE=postgres but DATABASE_URL is not set"
        fi
        ;;
    json)
        row "Identity store" "WARN" "DATAAGENT_USER_STORE=json is the legacy per-process store"
        ;;
    *)
        user_db=$(eval "printf '%s' \"\${DATAAGENT_USER_DB:-data/users.db}\"")
        user_db_path=$(resolve_path "$user_db")
        if [ -d "$(dirname "$user_db_path")" ]; then
            row "Identity store" "PASS" "sqlite backend, directory present"
        else
            row "Identity store" "FAIL" "sqlite directory is missing - the store cannot be created"
        fi
        ;;
esac

# --- warehouse (never opened: DuckDB is single-writer) ----------------------
warehouse_path=$(eval "printf '%s' \"\${DUCKDB_DATABASE_PATH:-data/curated/warehouse.duckdb}\"")
warehouse_file=$(resolve_path "$warehouse_path")
if [ -f "$warehouse_file" ] && [ -s "$warehouse_file" ]; then
    row "Warehouse" "PASS" "warehouse file present"
else
    row "Warehouse" "FAIL" "warehouse file is missing or empty - initialise it before going live"
fi

# --- audit sink -------------------------------------------------------------
if is_set REDIS_URL; then
    row "Audit sink" "PASS" "shared backend available (redis)"
elif [ "$(eval "printf '%s' \"\${AUDIT_SINK:-}\"")" = "sqlite" ]; then
    row "Audit sink" "PASS" "sqlite sink configured"
elif [ "$(eval "printf '%s' \"\${AUDIT_SINK:-}\"")" = "redis" ]; then
    row "Audit sink" "FAIL" "AUDIT_SINK=redis but REDIS_URL is not set"
else
    row "Audit sink" "FAIL" "per-container file sink - set AUDIT_SINK=sqlite or REDIS_URL"
fi

# --- LLM provider -----------------------------------------------------------
llm_configured=""
for llm_var in LLM_PROVIDER LLM_OPENAI_KEY LLM_ANTHROPIC_KEY LLM_GEMINI_KEY \
                LLM_GROQ_KEY LLM_XAI_KEY LLM_OPENROUTER_KEY LLM_DEEPSEEK_KEY \
                LLM_OPENCODE_BASE_URL LLM_OPENCODE_SERVER_URL; do
    if is_set "$llm_var"; then
        llm_configured=$llm_var
        break
    fi
done
if [ -n "$llm_configured" ]; then
    row "LLM provider" "PASS" "configured via $llm_configured (value not shown)"
elif [ -f "$REPO_ROOT/config/llm_runtime.json" ]; then
    row "LLM provider" "PASS" "configured via config/llm_runtime.json"
else
    row "LLM provider" "FAIL" "no provider configured - /ask cannot answer a question"
fi

# --- recommended (never fatal) ----------------------------------------------
if is_set BILLING_SYNC_URL && is_set BILLING_SYNC_SECRET; then
    row "Billing entitlement sync" "PASS" "configured"
else
    row "Billing entitlement sync" "WARN" "set BILLING_SYNC_URL and BILLING_SYNC_SECRET"
fi

if is_set REDIS_URL; then
    row "Redis" "PASS" "configured"
else
    row "Redis" "WARN" "set REDIS_URL to share the limiter and the audit sink"
fi

if is_set DATAAGENT_GRANT_KEY || is_set DATAAGENT_GRANT_KEY_FILE; then
    row "Grant signing key" "PASS" "configured"
else
    row "Grant signing key" "WARN" "set DATAAGENT_GRANT_KEY outside the data host"
fi

echo
echo "checks: $((PASS_COUNT + WARN_COUNT + FAIL_COUNT))  pass: $PASS_COUNT  warn: $WARN_COUNT  fail: $FAIL_COUNT"
if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "preflight FAILED - fix every FAIL row above before deploying"
    exit 1
fi
echo "preflight PASSED"
exit 0
