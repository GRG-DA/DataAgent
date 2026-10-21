#!/usr/bin/env bash
# Wrapper for the `agentshield scan --path .` gate that AGENTS.md requires
# before a commit.
#
# agentshield ships as the npm package `ecc-agentshield` (the binary is
# `agentshield`):
#
#     npm install -g ecc-agentshield@1.6.0
#
# The scan reports every open finding in the agent configuration. The tree
# has accepted findings today, so the verdict comes from the drift gate
# (scripts/ci/agentshield_gate.py): a NEW critical/high finding fails.
#
# Usage:
#   scripts/ci/agentshield_scan.sh                  # scan + drift gate
#   scripts/ci/agentshield_scan.sh --save-baseline  # re-baseline (review the diff!)
#
# Exit codes:
#   0   - no new critical/high finding
#   1   - new critical/high finding(s)
#   127 - agentshield was not found

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 2

SAVE_BASELINE=0
for arg in "$@"; do
    case "$arg" in
        --save-baseline) SAVE_BASELINE=1 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

BIN="${AGENTSHIELD_BIN:-}"
if [ -z "$BIN" ]; then
    BIN="$(command -v agentshield 2>/dev/null || true)"
fi
if [ -z "$BIN" ] && [ -x "$HOME/AppData/Roaming/npm/agentshield.cmd" ]; then
    BIN="$HOME/AppData/Roaming/npm/agentshield.cmd"
fi
if [ -z "$BIN" ]; then
    echo "agentshield was not found on PATH." >&2
    echo "Install it with: npm install -g ecc-agentshield@1.6.0" >&2
    echo "or point AGENTSHIELD_BIN at the binary." >&2
    exit 127
fi

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi
if [ -z "$PYTHON_BIN" ]; then
    echo "no python interpreter found - set PYTHON=/path/to/python" >&2
    exit 127
fi

REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT

echo "==> agentshield scan --path . (repo: $REPO_ROOT)"
"$BIN" scan --path . --format json --output "$REPORT"
SCAN_STATUS=$?
if [ "$SCAN_STATUS" -ne 0 ] && [ ! -s "$REPORT" ]; then
    echo "agentshield failed (exit $SCAN_STATUS)" >&2
    exit "$SCAN_STATUS"
fi

if [ "$SAVE_BASELINE" -eq 1 ]; then
    "$PYTHON_BIN" scripts/ci/agentshield_gate.py --report "$REPORT" \
        --save-baseline scripts/ci/agentshield-baseline.json
    exit $?
fi

"$PYTHON_BIN" scripts/ci/agentshield_gate.py --report "$REPORT" \
    --baseline scripts/ci/agentshield-baseline.json
exit $?
