#!/usr/bin/env bash
# dataagent-launch.sh — launch DataAgent on Linux.
#
# Default: the GTK/Qt desktop shell (parity with the Windows installer).
#   * `dataagent-launch.sh`          -> shell when a graphical session exists
#   * `dataagent-launch.sh web`      -> always run the headless web server
#   * `dataagent-launch.sh shell`    -> always run the shell (GUI required)
#
# Installed to /opt/dataagent by the .deb/.rpm packages; referenced by the
# desktop entry and the opt-in systemd web unit (which passes "web").

set -e

APP_DIR="/opt/dataagent"
SHELL_BIN="$APP_DIR/DataAgentShell/DataAgentShell"
WEB_BIN="$APP_DIR/DataAgent"

MODE="${1:-auto}"

case "$MODE" in
  web)
    if [ ! -x "$WEB_BIN" ]; then
      echo "DataAgent web binary not found: $WEB_BIN" >&2
      exit 1
    fi
    cd "$APP_DIR"
    exec "$WEB_BIN" web
    ;;
  shell)
    if [ ! -x "$SHELL_BIN" ]; then
      echo "DataAgent shell binary not found: $SHELL_BIN" >&2
      exit 1
    fi
    cd "$APP_DIR"
    exec "$SHELL_BIN"
    ;;
  auto|"")
    if [ -x "$SHELL_BIN" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
      cd "$APP_DIR"
      exec "$SHELL_BIN"
    fi
    if [ -x "$WEB_BIN" ]; then
      cd "$APP_DIR"
      exec "$WEB_BIN" web
    fi
    echo "DataAgent binaries not found in $APP_DIR" >&2
    exit 1
    ;;
  *)
    echo "Usage: $0 [auto|shell|web]" >&2
    exit 2
    ;;
esac