# dataagent.spec — RPM spec for DataAgent (compiled app + opencode + WARP).
# Build with: bash scripts/build_linux.sh   (passes _topdir/_sourcedir)
#   or standalone: rpmbuild --define "_topdir ..." --define "_sourcedir ..." -bb this-file

Name:           dataagent
Version:        %{?dataagent_version}%{!?dataagent_version:1.0.0}
Release:        1%{?dist}
Summary:        LLM Data Analytics Agent with Cloudflare WARP support

License:        Proprietary
URL:            https://dataagent.local
Source0:        dataagent-app.tar.gz

BuildArch:      %{?dataagent_arch}%{!?dataagent_arch:x86_64}
Requires:       bash, curl, gnupg2, gtk3, webkit2gtk3

%description
Installs the DataAgent desktop shell and web app with compiled binaries, plus
the opencode CLI and the Cloudflare WARP client so the Data Agent AI's
egress-IP rotation works out of the box.

Launch the DataAgent shell from the desktop menu; for headless servers run:
    sudo systemctl enable --now dataagent

%prep
%setup -q -c -n app

%install
install -d %{buildroot}/opt/dataagent
cp -a . %{buildroot}/opt/dataagent/
install -d %{buildroot}/usr/share/dataagent/app
cp -a %{buildroot}/opt/dataagent %{buildroot}/usr/share/dataagent/app
install -m 0755 %{_sourcedir}/warp-install.sh %{buildroot}/usr/share/dataagent/warp-install.sh
install -m 0755 %{_sourcedir}/opencode-install.sh %{buildroot}/usr/share/dataagent/opencode-install.sh
# The pinned opencode hashes: the install-time helper verifies the release
# tarball against them before it extracts it, and refuses without them.
install -m 0644 opencode-*.sha256 %{buildroot}/usr/share/dataagent/

%post
APP_DIR="/opt/dataagent"
APP_USER="dataagent"
SERVICE_NAME="dataagent"
PKG_DIR="/usr/share/dataagent"

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER" || true
fi
chmod 0755 "$APP_DIR/DataAgentShell/DataAgentShell" 2>/dev/null || true
chmod 0755 "$APP_DIR/DataAgent" 2>/dev/null || true
chmod 0755 "$APP_DIR/dataagent-launch.sh" 2>/dev/null || true
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [ -x "$PKG_DIR/warp-install.sh" ]; then
  bash "$PKG_DIR/warp-install.sh" || true
fi
if command -v warp-cli >/dev/null 2>&1; then
  warp-cli registration new >/dev/null 2>&1 || true
  warp-cli connect >/dev/null 2>&1 || true
fi
if [ -x "$PKG_DIR/opencode-install.sh" ]; then
  bash "$PKG_DIR/opencode-install.sh" || true
fi

if command -v systemctl >/dev/null 2>&1; then
  cat > /etc/systemd/system/$SERVICE_NAME.service <<'UNITE'
[Unit]
Description=DataAgent — LLM Data Analytics Agent web app
After=network-online.target warp-svc.service
Wants=network-online.target

[Service]
Type=simple
User=dataagent
Group=dataagent
WorkingDirectory=/opt/dataagent
ExecStart=/opt/dataagent/dataagent-launch.sh web
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/opt/dataagent
Environment=XDG_DATA_HOME=/opt/dataagent/.local/share
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
UNITE
  systemctl daemon-reload || true
fi

install -d /usr/share/applications
cat > /usr/share/applications/dataagent.desktop <<'DE'
[Desktop Entry]
Type=Application
Name=DataAgent
Comment=LLM Data Analytics Agent
Exec=/opt/dataagent/dataagent-launch.sh
Icon=applications-science
Terminal=false
Categories=Office;Science;
DE
for xdg in /etc/xdg/autostart /usr/share/xdg/autostart; do
  [ -d "$xdg" ] || continue
  cp -f /usr/share/applications/dataagent.desktop "$xdg/dataagent.desktop"
done

%preun
if [ "$1" -eq 0 ]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop dataagent.service || true
    systemctl disable dataagent.service || true
  fi
  rm -f /etc/xdg/autostart/dataagent.desktop
  rm -f /usr/share/xdg/autostart/dataagent.desktop
  rm -f /usr/share/applications/dataagent.desktop
fi

%files
/opt/dataagent
/usr/share/dataagent

%changelog
* Sat Sep 20 2026 DataAgent <support@dataagent.local> - 1.0.0-1
- Compiled binaries (DataAgent + DataAgentShell) with source stripping.
- Linux packaging defaults to the desktop shell; systemd web unit opt-in.
- Desktop entry + autostart; opencode CLI + WARP install on postinstall.