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

# ---------------------------------------------------------------------------
# PINNED THIRD-PARTY BINARY: the opencode CLI (ADR-012)
# ---------------------------------------------------------------------------
# The .deb/.rpm does NOT bundle the CLI (~180 MB); the install-time helper
# downloads it. That download is PINNED and HASH-VERIFIED, so the build has
# to know the SHA256 of the release tarball: it downloads the pinned
# tarball, computes the hash and compares it with the committed pin in
# installers/opencode-<version>-<target>.sha256. A mismatch fails the build
# (the upstream artefact changed under the pin); a missing pin is written
# and loudly reported - it MUST be verified out of band and committed
# before the package is released.
#
# To rotate the pin (two edits):
#   1. bump OPENCODE_VERSION below (and $OpenCodeVersion in
#      scripts/build_windows.ps1 + OPENCODE_VERSION in
#      agent/opencode_bootstrap.py + packaging/linux/opencode-install.sh),
#      and
#   2. commit installers/opencode-<version>-<target>.sha256.
OPENCODE_VERSION="v1.19.3"

# The Linux release targets this build ships a pin for. The x64 build ships
# both variants: the install-time helper picks the baseline one on a CPU
# without AVX2, and both tarballs must be verifiable there.
opencode_targets() {
  case "$ARCH" in
    x86_64|amd64) echo "linux-x64 linux-x64-baseline" ;;
    aarch64|arm64) echo "linux-arm64" ;;
    *) echo "linux-x64" ;;
  esac
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo ""
  fi
}

# Produce (or verify) the committed pin for one target; print its SHA256 on
# stdout (the progress lines go to stderr, so the caller can capture it).
pin_opencode_target() {
  local target="$1"
  local asset="opencode-${target}.tar.gz"
  local url="https://github.com/anomalyco/opencode/releases/download/${OPENCODE_VERSION}/${asset}"
  local pin="installers/opencode-${OPENCODE_VERSION}-${target}.sha256"
  local tmp actual expected attempt
  tmp="$(mktemp -d)"
  echo "    downloading $asset ($OPENCODE_VERSION) to compute its SHA256 ..." >&2
  # A transient network error must not kill a release build: the CDN behind
  # GitHub resets big transfers often enough to matter.
  for attempt in 1 2 3; do
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL "$url" -o "$tmp/$asset" && break
    else
      wget -qO "$tmp/$asset" "$url" && break
    fi
    echo "    download attempt $attempt of 3 failed for $asset" >&2
    sleep 5
  done
  if [ ! -s "$tmp/$asset" ]; then
    echo "ERROR: could not download $url after 3 attempts" >&2
    exit 1
  fi
  actual="$(sha256_of "$tmp/$asset")"
  rm -rf "$tmp"
  if [ -z "$actual" ]; then
    echo "ERROR: no sha256 tool available - cannot pin $asset" >&2
    exit 1
  fi
  if [ -f "$pin" ]; then
    expected="$(awk 'NR==1 {print $1}' "$pin")"
    if [ "$actual" != "$expected" ]; then
      echo "ERROR: $asset SHA256 mismatch for $OPENCODE_VERSION :" \
           "expected $expected got $actual" >&2
      exit 1
    fi
    echo "    $asset SHA256 verified: $actual" >&2
  else
    # No pin for this version yet. Verification is NEVER silently skipped:
    # record what we got and make the maintainer verify it out of band.
    printf '%s  %s\n' "$actual" "$asset" > "$pin"
    echo "==============================================================" >&2
    echo " opencode $OPENCODE_VERSION ($target) was NOT verified against a" >&2
    echo " committed hash." >&2
    echo " Recorded $actual in $pin." >&2
    echo " Verify it out of band (publish the release checksum, compare by" >&2
    echo " hand) and COMMIT $pin before releasing this build." >&2
    echo "==============================================================" >&2
  fi
  echo "$actual"
}

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

# Pinned opencode hashes: the install-time helper verifies the release
# tarball against these BEFORE it extracts it, and refuses to install
# without them (ADR-012). The pins live next to the Windows one in
# installers/ and are shipped inside the package.
for target in $(opencode_targets); do
  sha="$(pin_opencode_target "$target")"
  case "$target" in
    linux-x64)          export OPENCODE_SHA256_LINUX_X64="$sha" ;;
    linux-arm64)        export OPENCODE_SHA256_LINUX_ARM64="$sha" ;;
    linux-x64-baseline) export OPENCODE_SHA256_LINUX_X64_BASELINE="$sha" ;;
  esac
  install -m 0644 "installers/opencode-${OPENCODE_VERSION}-${target}.sha256" "$STAGE/"
done
echo "    opencode CLI: pinned to $OPENCODE_VERSION, hash-verified at install time."

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
  # The spec installs the install-time helpers from the source dir; the
  # pinned opencode hashes travel inside the app payload.
  cp "$PKG_DIR/warp-install.sh" "$PKG_DIR/opencode-install.sh" "$RPM_SRC/"
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