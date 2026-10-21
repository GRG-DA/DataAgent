#!/usr/bin/env bash
# warp-install.sh — install the Cloudflare WARP client for the host distro.
# Shared by: packaging/linux/install.sh, the .deb postinst and the .rpm %post.
# Idempotent: exits 0 immediately if warp-cli is already present.
#
# NOTE: keep in sync with the embedded scripts in agent/warp_bootstrap.py.

set -e

if command -v warp-cli >/dev/null 2>&1 || [ -x /usr/sbin/warp-cli ] || [ -x /usr/bin/warp-cli ]; then
  echo "warp-cli already installed."
  exit 0
fi

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  CODENAME="$(lsb_release -cs 2>/dev/null || . /etc/os-release && echo "$VERSION_CODENAME")"
  [ -n "$CODENAME" ] || CODENAME="stable"
  install -d /usr/share/keyrings
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  else
    wget -qO- https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  fi
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $CODENAME main" > /etc/apt/sources.list.d/cloudflare-client.list
  apt-get update
  apt-get install -y cloudflare-warp

elif command -v dnf >/dev/null 2>&1; then
  rpm --import https://pkg.cloudflareclient.com/pubkey.gpg
  cat > /etc/yum.repos.d/cloudflare-warp.repo <<'EOF'
[cloudflare-warp]
name=Cloudflare WARP
baseurl=https://pkg.cloudflareclient.com/rpm/
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://pkg.cloudflareclient.com/pubkey.gpg
EOF
  dnf install -y cloudflare-warp

elif command -v zypper >/dev/null 2>&1; then
  rpm --import https://pkg.cloudflareclient.com/pubkey.gpg
  zypper --non-interactive addrepo https://pkg.cloudflareclient.com/rpm/ cloudflare-warp
  zypper --non-interactive install cloudflare-warp

elif command -v pacman >/dev/null 2>&1; then
  if command -v yay >/dev/null 2>&1; then
    yay -S --noconfirm cloudflare-warp-bin
  elif command -v paru >/dev/null 2>&1; then
    paru -S --noconfirm cloudflare-warp-bin
  else
    echo "Arch: install cloudflare-warp-bin from the AUR (yay/paru) first." >&2
    exit 1
  fi

else
  echo "Unsupported distribution (no apt-get/dnf/zypper/pacman)." >&2
  exit 1
fi

# Enable + start the daemon so warp-cli can connect.
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now warp-svc || true
fi

echo "Cloudflare WARP installed."