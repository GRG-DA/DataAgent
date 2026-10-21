#!/usr/bin/env bash
# install.sh — universal Linux installer for DataAgent.
#
# Installs the app to /opt/dataagent, installs + connects Cloudflare WARP
# (so the Data Agent AI's egress-IP rotation works), and registers a systemd
# service for the web app.
#
# Usage:  sudo bash install.sh [--no-warp] [--no-service]
#   --no-warp     skip Cloudflare WARP install/connect
#   --no-service  skip creating the systemd unit
#
# The .deb / .rpm packages call this same logic via their postinst scripts.

set -e

APP_DIR="/opt/dataagent"
APP_USER="dataagent"
SERVICE_NAME="dataagent"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NO_WARP=0
NO_SERVICE=0

for arg in "$@"; do
  case "$arg" in
    --no-warp) NO_WARP=1 ;;
    --no-service) NO_SERVICE=1 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

echo "==> Installing DataAgent to $APP_DIR"
install -d "$APP_DIR"
# App payload: a staged bundle (dist/DataAgent binary or source tree).
if [ -f "$SCRIPT_DIR/../dist/DataAgent" ]; then
  install -m 0755 "$SCRIPT_DIR/../dist/DataAgent" "$APP_DIR/DataAgent"
  APP_CMD="$APP_DIR/DataAgent web"
elif [ -d "$SCRIPT_DIR/../stage" ]; then
  cp -a "$SCRIPT_DIR/../stage/." "$APP_DIR/"
  APP_CMD="python3 $APP_DIR/run.py web"
else
  echo "No app payload found (dist/DataAgent or stage/). Install aborted." >&2
  exit 1
fi

# Dedicated service user.
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER" || true
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# Cloudflare WARP — install + connect (single sudo elevation, already root).
if [ "$NO_WARP" -eq 0 ]; then
  echo "==> Installing Cloudflare WARP"
  bash "$SCRIPT_DIR/warp-install.sh"
  echo "==> Registering + connecting WARP"
  if command -v warp-cli >/dev/null 2>&1; then
    warp-cli registration new >/dev/null 2>&1 || true
    warp-cli connect >/dev/null 2>&1 || true
  elif [ -x /usr/sbin/warp-cli ]; then
    /usr/sbin/warp-cli registration new >/dev/null 2>&1 || true
    /usr/sbin/warp-cli connect >/dev/null 2>&1 || true
  fi
fi

# opencode CLI — install so the AI server works out of the box.
echo "==> Installing opencode CLI"
bash "$SCRIPT_DIR/opencode-install.sh"

# systemd unit for the web app.
if [ "$NO_SERVICE" -eq 0 ] && command -v systemctl >/dev/null 2>&1; then
  echo "==> Registering systemd service ($SERVICE_NAME)"
  cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=DataAgent — LLM Data Analytics Agent web app
After=network-online.target warp-svc.service
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_CMD
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME" || true
fi

# Desktop entry.
install -d /usr/share/applications
cat > /usr/share/applications/dataagent.desktop <<EOF
[Desktop Entry]
Type=Application
Name=DataAgent
Comment=LLM Data Analytics Agent
Exec=$APP_CMD
Terminal=false
Categories=Office;Science;
EOF

echo ""
echo "DataAgent installed. Web app: http://localhost:8002"
echo "WARP: $(warp-cli status 2>/dev/null | grep -i status || echo 'check with: warp-cli status')"