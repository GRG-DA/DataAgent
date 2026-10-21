#!/bin/sh
# Single entry point for the DataAgent test suite on POSIX.
#
# Installs the right extras, runs the suite with the same coverage scope and
# gate as CI (.github/workflows/pytest.yml) and prints a summary.
#
# Usage:
#   sh scripts/ci/run_tests.sh [--skip-install] [-- <extra pytest args>]
#
# The identity store is redirected to a throwaway directory, so a local run
# never touches data/users.db.

set -u

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT" || exit 2

SKIP_INSTALL=0
PYTHON_BIN="${PYTHON:-}"
PYTEST_ARGS=""

usage() {
    echo "usage: sh scripts/ci/run_tests.sh [--skip-install] [-- <extra pytest args>]"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-install) SKIP_INSTALL=1; shift ;;
        --) shift; PYTEST_ARGS="$*"; break ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
    esac
done

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
    exit 2
fi

if [ "$SKIP_INSTALL" -eq 0 ]; then
    echo "==> installing dependencies"
    "$PYTHON_BIN" -m pip install --upgrade pip || exit 1
    "$PYTHON_BIN" -m pip install -r requirements.txt -r requirements-test.txt || exit 1
fi

FAIL_UNDER=$("$PYTHON_BIN" -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['tool']['coverage']['report']['fail_under'])" 2>/dev/null)
if [ -z "$FAIL_UNDER" ]; then
    FAIL_UNDER=74
fi

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
DATAAGENT_USER_DB="$WORK_DIR/users.db"
DATAAGENT_USER_LEGACY_JSON="$WORK_DIR/no_users.json"
export DATAAGENT_USER_DB DATAAGENT_USER_LEGACY_JSON

LOG="$WORK_DIR/pytest.log"
STATUS_FILE="$WORK_DIR/status"

echo "==> running the suite (coverage gate: $FAIL_UNDER%)"
# shellcheck disable=SC2086
{ "$PYTHON_BIN" -m pytest -q -rs \
    --cov=api --cov=agent --cov=etl --cov=analysis --cov=warehouse --cov=config \
    --cov-report=term-missing \
    --cov-report=xml \
    --cov-fail-under="$FAIL_UNDER" $PYTEST_ARGS
  echo $? > "$STATUS_FILE"; } 2>&1 | tee "$LOG"

STATUS=$(cat "$STATUS_FILE" 2>/dev/null || echo 1)

echo
echo "=== DataAgent test summary ==="
echo "interpreter : $PYTHON_BIN"
echo "coverage gate: $FAIL_UNDER% (from pyproject.toml)"
echo "exit code   : $STATUS"
grep -E "^[0-9]+ (passed|failed)|passed,|failed," "$LOG" | tail -1 | sed 's/^/result       : /'
grep -E "Required test coverage" "$LOG" | tail -1 | sed 's/^/coverage     : /'
if [ "$STATUS" -eq 0 ]; then
    echo "verdict      : PASSED"
else
    echo "verdict      : FAILED"
fi
exit "$STATUS"
