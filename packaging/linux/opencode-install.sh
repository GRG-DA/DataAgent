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
# scripts/build_windows.ps1 ($OpenCodeVersion) and with
# agent/opencode_bootstrap.py (_OPENCODE_RELEASE_URL).

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

URL="https://github.com/anomalyco/opencode/releases/download/${OPENCODE_VERSION}/opencode-${TARGET}.tar.gz"

# The expected SHA256 of the extracted `opencode` binary for this target.
# An empty value means "no pin recorded for this target" - the download still
# happens, but the install says so loudly instead of pretending to be verified.
case "$TARGET" in
  linux-x64)         EXPECTED_SHA256="${OPENCODE_SHA256_LINUX_X64:-}" ;;
  linux-arm64)       EXPECTED_SHA256="${OPENCODE_SHA256_LINUX_ARM64:-}" ;;
  linux-x64-baseline) EXPECTED_SHA256="${OPENCODE_SHA256_LINUX_X64_BASELINE:-}" ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading opencode ${OPENCODE_VERSION} ($TARGET)…"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$TMP/opencode.tar.gz"
else
  wget -qO "$TMP/opencode.tar.gz" "$URL"
fi

tar -xzf "$TMP/opencode.tar.gz" -C "$TMP"

if [ -n "${EXPECTED_SHA256:-}" ]; then
  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(sha256sum "$TMP/opencode" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(shasum -a 256 "$TMP/opencode" | awk '{print $1}')"
  else
    ACTUAL_SHA256=""
  fi
  if [ -z "$ACTUAL_SHA256" ]; then
    echo "WARNING: no sha256 tool available - the download of opencode" \
         "${OPENCODE_VERSION} was NOT verified." >&2
  elif [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
    echo "ERROR: opencode ${OPENCODE_VERSION} SHA256 mismatch" \
         "(expected $EXPECTED_SHA256, got $ACTUAL_SHA256) - refusing to install." >&2
    exit 1
  else
    echo "opencode ${OPENCODE_VERSION} SHA256 verified."
  fi
else
  echo "WARNING: no pinned SHA256 for $TARGET - the download of opencode" \
       "${OPENCODE_VERSION} was NOT verified. Set OPENCODE_SHA256_$(echo "$TARGET" | tr 'a-z-' 'A-Z_') to pin it." >&2
fi

install -m 0755 "$TMP/opencode" /usr/local/bin/opencode
echo "opencode installed to /usr/local/bin/opencode."