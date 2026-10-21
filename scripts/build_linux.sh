#!/usr/bin/env bash
# build_linux.sh — build DataAgent .deb and .rpm packages with full
# Windows-installer parity: compiled PyInstaller binaries (web + GTK shell),
# opencode CLI and Cloudflare WARP installers bundled.
#
# Must run ON LINUX (PyInstaller cannot cross-compile).
#
# Stages app payload into packaging/linux/stage/, then builds:
#   * packaging/linux/dist/dataagent_<ver>_<arch>.deb   (dpkg-deb)
#   * packaging/linux/dist/dataagent-<ver>-1.<arch>.rpm (rpmbuild, if present)
#
# Usage:  bash scripts/build_linux.sh [--skip-shell] [--skip-web] [--skip-rpm]
#   --skip-shell   skip building the DataAgentShell (GTK) onedir bundle
#   --skip-web     skip building the DataAgent onefile web binary
#   --skip-rpm     skip building the .rpm (rpmbuild may be missing)

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_SHELL=0
SKIP_WEB=0
SKIP_RPM=0
for arg in "$@"; do
  case "$arg" in
    --skip-shell) SKIP_SHELL=1 ;;
    --skip-web)   SKIP_WEB=1 ;;
    --skip-rpm)   SKIP_RPM=1 ;;
  esac
done

PKG_DIR="packaging/linux"
STAGE="$PKG_DIR/stage"
DIST="$PKG_DIR/dist"
VERSION="1.0.0"
ARCH="$(uname -m)"

PYTHON="${PYTHON:-python3}"

check_arch() {
  case "$ARCH" in
    x86_64|amd64|aarch64|arm64)
      pkg_arch() { case "$1" in
        x86_64) echo amd64 ;;
        aarch64) echo arm64 ;;
        *) echo "$1" ;;
      esac; }
      ;;
    *) pkg_arch() { echo "$1"; } ;;
  esac
  DEB_ARCH="$(pkg_arch "$(dpkg --print-architecture 2>/dev/null || echo "$ARCH")")"
  if [ -z "$DEB_ARCH" ]; then DEB_ARCH="$ARCH"; fi
  RPM_ARCH="$ARCH"
}
check_arch

echo "==> Building DataAgent $VERSION for $ARCH (.deb arch: $DEB_ARCH)"

echo "==> [1/5] Building PyInstaller binaries"

if [ "$SKIP_WEB" -eq 0 ]; then
  echo "    -> DataAgent (onefile web base)"
  $PYTHON -m PyInstaller --noconfirm DataAgent.spec
fi

if [ "$SKIP_SHELL" -eq 0 ]; then
  echo "    -> DataAgentShell (onedir GTK shell)"
  $PYTHON -m PyInstaller --noconfirm scripts/DataAgentShell-linux.spec
fi

if [ ! -x dist/DataAgent ] && [ ! -x dist/DataAgentShell/DataAgentShell ]; then
  echo "Error: no binaries were produced. Unskip at least one of --skip-web/--skip-shell." >&2
  exit 1
fi

echo "==> [2/5] Staging payload into $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# Web payload: minimal data dirs the app needs besides the bundled binaries.
for d in data/raw data/staging data/curated logs outputs/charts; do
  mkdir -p "$STAGE/$d"
  : > "$STAGE/$d/.keep"
done

# Binaries.
if [ -x dist/DataAgent ]; then
  install -m 0755 dist/DataAgent "$STAGE/DataAgent"
fi
if [ -d dist/DataAgentShell ]; then
  cp -a dist/DataAgentShell "$STAGE/DataAgentShell"
  [ -f "$STAGE/DataAgentShell/DataAgentShell" ] && chmod 0755 "$STAGE/DataAgentShell/DataAgentShell"
fi

# Bundled installer helpers.
install -m 0755 "$PKG_DIR/warp-install.sh" "$STAGE/warp-install.sh"
install -m 0755 "$PKG_DIR/opencode-install.sh" "$STAGE/opencode-install.sh"
install -m 0755 scripts/dataagent-launch.sh "$STAGE/dataagent-launch.sh"

echo "==> [3/5] Building .deb"
DEB_ROOT="$DIST/deb-root"
rm -rf "$DEB_ROOT"
mkdir -p "$DEB_ROOT/DEBIAN"
mkdir -p "$DEB_ROOT/usr/share/dataagent/app"
mkdir -p "$DEB_ROOT/usr/share/dataagent"
cp "$PKG_DIR/debian/control" "$DEB_ROOT/DEBIAN/control"
cp "$PKG_DIR/debian/postinst" "$DEB_ROOT/DEBIAN/postinst"
cp "$PKG_DIR/debian/prerm" "$DEB_ROOT/DEBIAN/prerm"
chmod 0755 "$DEB_ROOT/DEBIAN/postinst" "$DEB_ROOT/DEBIAN/prerm"
cp -a "$STAGE/." "$DEB_ROOT/usr/share/dataagent/app/"
dpkg-deb --build --root-owner-group "$DEB_ROOT" "$DIST/dataagent_${VERSION}_${DEB_ARCH}.deb"
echo "    -> $DIST/dataagent_${VERSION}_${DEB_ARCH}.deb"

if [ "$SKIP_RPM" -eq 0 ] && command -v rpmbuild >/dev/null 2>&1; then
  echo "==> [4/5] Building .rpm"
  RPM_SRC="$DIST/rpm-src"
  rm -rf "$RPM_SRC"
  mkdir -p "$RPM_SRC"
  tar -C "$STAGE" -czf "$RPM_SRC/dataagent-app.tar.gz" .
  rpmbuild --define "_topdir $RPM_SRC" \
           --define "_sourcedir $RPM_SRC" \
           --define "dataagent_version $VERSION" \
           --define "dataagent_arch $RPM_ARCH" \
           -bb "$PKG_DIR/rpm/dataagent.spec"
  cp "$RPM_SRC"/RPMS/*/dataagent-*.rpm "$DIST/" 2>/dev/null || true
  echo "    -> $DIST/dataagent-${VERSION}-1.${RPM_ARCH}.rpm"
else
  echo "==> [4/5] Skipping .rpm (rpmbuild not found or --skip-rpm)"
fi

echo "==> [5/5] Finishing"
echo ""
echo "Done. Packages in $DIST"
echo "  deb: sudo dpkg -i $DIST/dataagent_${VERSION}_${DEB_ARCH}.deb"
echo "  rpm: sudo rpm -ivh $DIST/dataagent-${VERSION}-1.${RPM_ARCH}.rpm"
echo ""
echo "Desktop shell: launch via the DataAgent desktop menu entry"
echo "               (autostart on login with a graphical session)."
echo "Headless mode: sudo systemctl enable --now dataagent"