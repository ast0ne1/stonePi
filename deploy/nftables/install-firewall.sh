#!/bin/bash
# Install / refresh StonePi nftables baseline. Idempotent.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RULES="$ROOT/nftables/stonepi.nft"
DEST_RULES="/etc/nftables.d/stonepi.nft"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

if ! command -v nft >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y nftables
fi

mkdir -p /etc/nftables.d
install -m 644 "$RULES" "$DEST_RULES"

# Ensure main nftables.conf includes drop-ins (Debian Bookworm style).
if [[ -f /etc/nftables.conf ]]; then
  if ! grep -q 'nftables.d/stonepi.nft' /etc/nftables.conf 2>/dev/null; then
    if grep -q 'include "/etc/nftables.d/' /etc/nftables.conf 2>/dev/null; then
      :
    else
      cat >> /etc/nftables.conf <<'EOF'

# StonePi appliance firewall
include "/etc/nftables.d/stonepi.nft"
EOF
    fi
  fi
else
  cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
flush ruleset
include "/etc/nftables.d/stonepi.nft"
EOF
fi

# Prefer nftables over ufw if both present
if systemctl is-enabled ufw >/dev/null 2>&1; then
  systemctl disable --now ufw >/dev/null 2>&1 || true
fi

nft -f "$DEST_RULES"
systemctl enable nftables >/dev/null 2>&1 || true
systemctl restart nftables >/dev/null 2>&1 || systemctl start nftables >/dev/null 2>&1 || true
echo "StonePi nftables rules applied."
