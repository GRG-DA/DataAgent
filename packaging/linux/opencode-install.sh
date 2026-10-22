#!/usr/bin/env bash
# opencode-install.sh — install the opencode CLI for the Data Agent AI server.
# Shared by: packaging/linux/install.sh, the .deb postinst and the .rpm %post.
# Idempotent: exits 0 immediately if opencode is already on PATH.
#
# Installs to /usr/local/bin/opencode so agent.opencode_bootstrap resolves it
# via PATH (shutil.which) for any user.
#
# The release is PINNED and HASH-VERIFIED, the same contract as the Windows
# build (ADR-012). It used to download "releases/latest", which made the
# install unreproducible and unverifiable. Keep the version in sync with
# scripts/build_windows.ps1 ($OpenCodeVersion), scripts/build_linux.sh
# (OPENCODE_VERSION) and agent/opencode_bootstrap.py (OPENCODE_VERSION).
#
# The SHA256 of the release TARBALL is pinned in
# installers/opencode-<version>-<target>.sha256 (committed, next to the
# Windows pin) and shipped next to this script inside the .deb/.rpm.
#
# Two rules make the pin real:
#   * the tarball is verified BEFORE it is extracted — an unverified archive
#     must never be unpacked (a crafted tarball can write outside the temp
#     dir before a mismatch is noticed);
#   * with no pinned hash the install REFUSES (non-zero exit, nothing
#     installed) — an unverified binary must never become a root-owned one.

set -e

if command -v opencode >/dev/null 2>&1 || [ -x /usr/local/bin/opencode ]; then
  echo "opencode already installed."
  exit 0
fi

# Pinned opencode release. Bump together with the Windows build.
OPENCODE_VERSION="v1.19.3"

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

ASSET="opencode-${TARGET}.tar.gz"
URL="https://github.com/anomalyco/opencode/releases/download/${OPENCODE_VERSION}/${ASSET}"
PIN_FILE="opencode-${OPENCODE_VERSION}-${TARGET}.sha256"

# The pinned SHA256 of the release tarball for this target. An explicit
# environment override wins; otherwise the committed pin is looked up next
# to this script (how the .deb/.rpm ship it), in the share directory (the
# RPM layout) and finally in the source checkout.
case "$TARGET" in
  linux-x64)          PINNED_SHA256="${OPENCODE_SHA256_LINUX_X64:-}" ;;
  linux-arm64)        PINNED_SHA256="${OPENCODE_SHA256_LINUX_ARM64:-}" ;;
  linux-x64-baseline) PINNED_SHA256="${OPENCODE_SHA256_LINUX_X64_BASELINE:-}" ;;
  *)                  PINNED_SHA256="" ;;
esac

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "$PINNED_SHA256" ]; then
  for candidate in \
    "$SELF_DIR/$PIN_FILE" \
    "/usr/share/dataagent/$PIN_FILE" \
    "$SELF_DIR/../../installers/$PIN_FILE"
  do
    if [ -r "$candidate" ]; then
      PINNED_SHA256="$(awk 'NR==1 {print $1}' "$candidate")"
      break
    fi
  done
fi

if [ -z "$PINNED_SHA256" ]; then
  echo "ERROR: no pinned SHA256 for ${TARGET} (opencode ${OPENCODE_VERSION})." >&2
  echo "       Commit installers/${PIN_FILE} (or export" \
       "OPENCODE_SHA256_$(echo "$TARGET" | tr 'a-z-' 'A-Z_')) and retry." >&2
  echo "       Refusing to install an unverified binary." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading opencode ${OPENCODE_VERSION} (${TARGET})…"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$TMP/$ASSET"
else
  wget -qO "$TMP/$ASSET" "$URL"
fi

# Verify the TARBALL before it is extracted.
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL_SHA256="$(sha256sum "$TMP/$ASSET" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  ACTUAL_SHA256="$(shasum -a 256 "$TMP/$ASSET" | awk '{print $1}')"
else
  echo "ERROR: no sha256 tool available (sha256sum/shasum) - refusing to" \
       "install an unverified opencode." >&2
  exit 1
fi

if [ "$ACTUAL_SHA256" != "$PINNED_SHA256" ]; then
  echo "ERROR: opencode ${OPENCODE_VERSION} (${ASSET}) SHA256 mismatch" \
       "(expected $PINNED_SHA256, got $ACTUAL_SHA256) - refusing to install." >&2
  exit 1
fi
echo "opencode ${OPENCODE_VERSION} (${ASSET}) SHA256 verified."

tar -xzf "$TMP/$ASSET" -C "$TMP"

install -m 0755 "$TMP/opencode" /usr/local/bin/opencode
echo "opencode installed to /usr/local/bin/opencode."
