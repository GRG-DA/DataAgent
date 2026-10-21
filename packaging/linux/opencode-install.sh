#!/usr/bin/env bash
# opencode-install.sh — install the opencode CLI for the Data Agent AI server.
# Shared by: packaging/linux/install.sh, the .deb postinst and the .rpm %post.
# Idempotent: exits 0 immediately if opencode is already on PATH.
#
# Installs to /usr/local/bin/opencode so agent.opencode_bootstrap resolves it
# via PATH (shutil.which) for any user. NOTE: keep the release URL format in
# sync with agent/opencode_bootstrap.py (_OPENCODE_RELEASE_URL).

set -e

if command -v opencode >/dev/null 2>&1 || [ -x /usr/local/bin/opencode ]; then
  echo "opencode already installed."
  exit 0
fi

# Pick the release target: linux-x64 (or -baseline without AVX2) / linux-arm64.
TARGET="linux-x64"
case "$(uname -m)" in
  aarch64|arm64) TARGET="linux-arm64" ;;
  *)
    if [ -r /proc/cpuinfo ] && ! grep -qi avx2 /proc/cpuinfo; then
      TARGET="linux-x64-baseline"
    fi
    ;;
esac

URL="https://github.com/anomalyco/opencode/releases/latest/download/opencode-${TARGET}.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading opencode ($TARGET)…"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$TMP/opencode.tar.gz"
else
  wget -qO "$TMP/opencode.tar.gz" "$URL"
fi

tar -xzf "$TMP/opencode.tar.gz" -C "$TMP"
install -m 0755 "$TMP/opencode" /usr/local/bin/opencode
echo "opencode installed to /usr/local/bin/opencode."