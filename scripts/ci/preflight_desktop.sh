#!/usr/bin/env bash
# preflight_desktop.sh — the POSIX twin of scripts/ci/preflight_desktop.ps1.
#
# Checks that a machine can produce a shippable DataAgent desktop package.
# Exits NON-ZERO when a check FAILs.
#
# Usage:
#   bash scripts/ci/preflight_desktop.sh [--require-signing] [--root DIR]
#
# The Windows-only checks (Inno Setup, signtool, Edge WebView2) are reported
# as SKIP here — the Linux package (.deb/.rpm) does not need them.
#
# SUPPORT BUNDLE PROCEDURE (C10)
# ================================
# A user's support bundle is collected from the per-user runtime directory:
#   %LOCALAPPDATA%\DataAgent on Windows, $XDG_DATA_HOME/DataAgent (or
#   ~/.local/share/DataAgent) elsewhere. The tray menu item
#   "Open logs folder" opens it directly.
#
# COLLECT these files:
#     logs/app.log                application log
#     logs/audit.log              audit trail
#     logs/runtime-stdout.log     stdout/stderr of the frozen process
#     config/llm_runtime.json     LLM provider runtime state - REDACTED
#                                 (provider + model only, never a key)
#
# NEVER collect these (they are secrets or the customer's own data):
#     data/.jwt_secret            session signing key
#     data/.grant_key             plan-grant signing key
#     data/.grant_migrated        grant migration marker
#     config/users.json           password hashes, plaintext API keys
#     data/users.db               password hashes, salts, MFA secrets
#     data/curated/warehouse.duckdb   the customer's data
#     data/raw/                   the customer's uploaded files
#     data/staging/               the customer's staged files
#
# scripts/ci/support_bundle.ps1 implements exactly this: it collects the
# three log files, redacts config/llm_runtime.json to provider + model and
# never touches anything on the never-collect list.
#
# It NEVER prints a secret value: no credential file is ever read, only its
# NAME and its presence.

set -u

REQUIRE_SIGNING=0
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --require-signing) REQUIRE_SIGNING=1 ;;
    --root) shift; ROOT="${1:-}" ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$ROOT" ]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

FAILURES=0
WARNINGS=0
CHECKS=0

report() { # status name detail
  CHECKS=$((CHECKS + 1))
  case "$1" in
    FAIL) FAILURES=$((FAILURES + 1)); printf '[FAIL] %s - %s\n' "$2" "$3" >&2 ;;
    WARN) WARNINGS=$((WARNINGS + 1)); printf '[WARN] %s - %s\n' "$2" "$3" ;;
    SKIP) printf '[SKIP] %s - %s\n' "$2" "$3" ;;
    *)    printf '[PASS] %s - %s\n' "$2" "$3" ;;
  esac
}

echo ""
echo "=== DataAgent desktop preflight ==="
echo "repository: $ROOT"
echo ""

# ---------------------------------------------------------------------------
# 1. python + PyInstaller
# ---------------------------------------------------------------------------
PY="${PYTHON:-python3}"
if "$PY" -c "import PyInstaller;print(PyInstaller.__version__)" >/dev/null 2>&1; then
  PYI_VERSION="$("$PY" -c "import PyInstaller;print(PyInstaller.__version__)" 2>/dev/null)"
  PY_VERSION="$("$PY" -V 2>&1)"
  report PASS "python + PyInstaller" "$PY_VERSION, PyInstaller $PYI_VERSION"
else
  report FAIL "python + PyInstaller" "python -m PyInstaller is not importable (pip install pyinstaller)"
fi

# ---------------------------------------------------------------------------
# 2. Inno Setup 6 (ISCC.exe) — Windows only
# ---------------------------------------------------------------------------
report SKIP "Inno Setup 6 (ISCC.exe)" "Windows-only tool (the .deb/.rpm build does not need it)"

# ---------------------------------------------------------------------------
# 3. signtool.exe — Windows only
# ---------------------------------------------------------------------------
if [ "$REQUIRE_SIGNING" -eq 1 ]; then
  report FAIL "signtool.exe" "Windows-only tool and -RequireSigning was requested"
else
  report SKIP "signtool.exe" "Windows-only tool (code signing is a Windows release step)"
fi

# ---------------------------------------------------------------------------
# 4. the pinned opencode version + its committed SHA256 file
# ---------------------------------------------------------------------------
BUILD_SCRIPT="$ROOT/scripts/build_windows.ps1"
if [ ! -f "$BUILD_SCRIPT" ]; then
  report FAIL "opencode pin" "scripts/build_windows.ps1 is missing"
else
  OC_VERSION="$(grep -o '\$OpenCodeVersion[[:space:]]*=[[:space:]]*"[^"]*"' "$BUILD_SCRIPT" | head -1 | sed 's/.*"\(.*\)"/\1/')"
  if [ -z "$OC_VERSION" ]; then
    report FAIL "opencode pin" "scripts/build_windows.ps1 does not define \$OpenCodeVersion"
  else
    SHA_FILE="$ROOT/installers/opencode-$OC_VERSION.sha256"
    if [ ! -f "$SHA_FILE" ]; then
      report FAIL "opencode pin" "no committed hash file for $OC_VERSION (expected installers/opencode-$OC_VERSION.sha256)"
    else
      DIGEST="$(awk '{print $1}' "$SHA_FILE" | head -1 | tr 'A-Z' 'a-z')"
      case "$DIGEST" in
        [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
        *) DIGEST="" ;;
      esac
      if [ ${#DIGEST} -ne 64 ]; then
        report FAIL "opencode pin" "the hash file for $OC_VERSION is not a 64-char hex SHA256"
      else
        report PASS "opencode pin" "version $OC_VERSION, sha256 $DIGEST"
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 5. no credential state would be bundled
# ---------------------------------------------------------------------------
REQUIRED_CREDENTIAL_NAMES="users.json db_connections.json google_login_pending.json llm_runtime.json revoked_jtis.json .jwt_secret .grant_key .grant_migrated .env"
for spec_rel in "DataAgent.spec" "scripts/DataAgentShell.spec"; do
  spec_path="$ROOT/$spec_rel"
  if [ ! -f "$spec_path" ]; then
    report FAIL "credential exclusion: $spec_rel" "spec file is missing"
    continue
  fi
  missing=""
  for name in $REQUIRED_CREDENTIAL_NAMES; do
    if ! grep -qF "'$name'" "$spec_path" && ! grep -qF "\"$name\"" "$spec_path"; then
      missing="$missing $name"
    fi
  done
  if [ -n "$missing" ]; then
    report FAIL "credential exclusion: $spec_rel" "does not exclude:$missing"
  elif ! grep -q "_is_credential_state" "$spec_path"; then
    report FAIL "credential exclusion: $spec_rel" "the data list is not filtered through _is_credential_state"
  else
    report PASS "credential exclusion: $spec_rel" "all runtime credential state is filtered out of the bundle"
  fi
done

# ---------------------------------------------------------------------------
# 6. the release version agrees everywhere
# ---------------------------------------------------------------------------
ISS_FILE="$ROOT/installers/DataAgent-Setup.iss"
SETTINGS_FILE="$ROOT/config/settings.yaml"
VERSION_INFO="$ROOT/packaging/windows/version_info.txt"
ISS_VERSION="$(grep -o '#define MyAppVersion[[:space:]]*"[^"]*"' "$ISS_FILE" 2>/dev/null | head -1 | sed 's/.*"\(.*\)"/\1/')"
APP_VERSION="$(awk '
  /^app:[[:space:]]*$/ { inapp = 1; next }
  inapp && /^[^[:space:]]/ { inapp = 0 }
  inapp && /^[[:space:]]+version:/ {
    gsub(/["[:space:]]/, "", $2); print $2; exit
  }
' "$SETTINGS_FILE" 2>/dev/null)"
if [ -z "$ISS_VERSION" ] || [ -z "$APP_VERSION" ]; then
  report FAIL "release version agreement" "the version could not be read from the Inno Setup script or settings.yaml"
elif [ "$ISS_VERSION" != "$APP_VERSION" ]; then
  report FAIL "release version agreement" "settings.yaml says $APP_VERSION, the Inno Setup script says $ISS_VERSION"
elif ! grep -q "u'$APP_VERSION'" "$VERSION_INFO" 2>/dev/null; then
  report FAIL "release version agreement" "version_info.txt does not carry $APP_VERSION"
else
  report PASS "release version agreement" "settings.yaml, version_info.txt and MyAppVersion all say $APP_VERSION"
fi

# ---------------------------------------------------------------------------
# 7. the Edge WebView2 runtime — Windows only
# ---------------------------------------------------------------------------
report SKIP "Edge WebView2 runtime" "Windows-only runtime (the end user needs it)"

# ---------------------------------------------------------------------------
# 8. free disk space
# ---------------------------------------------------------------------------
FREE_KB="$(df -Pk "$ROOT" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -z "${FREE_KB:-}" ]; then
  report WARN "free disk space" "could not measure the free space of $ROOT"
elif [ "$FREE_KB" -ge 1048576 ]; then
  report PASS "free disk space" "$((FREE_KB / 1024)) MB free (a ~500 MB installer plus the build output needs about 1 GB)"
else
  report FAIL "free disk space" "$((FREE_KB / 1024)) MB free - a ~500 MB installer needs about 1 GB"
fi

# ---------------------------------------------------------------------------
# 9. the support log directory
# ---------------------------------------------------------------------------
if [ -n "${XDG_DATA_HOME:-}" ]; then
  RUNTIME_DIR="$XDG_DATA_HOME/DataAgent"
else
  RUNTIME_DIR="$HOME/.local/share/DataAgent"
fi
LOGS_DIR="$RUNTIME_DIR/logs"
if mkdir -p "$LOGS_DIR" 2>/dev/null && printf 'ok' > "$LOGS_DIR/.preflight_write_test" 2>/dev/null; then
  rm -f "$LOGS_DIR/.preflight_write_test"
  report PASS "support log directory" "creatable and writable: $LOGS_DIR"
else
  report FAIL "support log directory" "not writable: $LOGS_DIR - a support bundle cannot be collected"
fi

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
echo ""
echo "=== summary ==="
echo "checks: $CHECKS, warnings: $WARNINGS, failures: $FAILURES"
echo ""
if [ "$FAILURES" -gt 0 ]; then
  echo "PREFLIGHT FAILED - fix the failures above before building a release." >&2
  exit 1
fi
echo "PREFLIGHT PASSED - the build machine can produce a release."
exit 0
