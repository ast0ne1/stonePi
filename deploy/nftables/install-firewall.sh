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
  # No `flush ruleset`: stonepi.nft replaces only its own table.
  cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
include "/etc/nftables.d/stonepi.nft"
EOF
fi

# Prefer nftables over ufw if both present
if systemctl is-enabled ufw >/dev/null 2>&1; then
  systemctl disable --now ufw >/dev/null 2>&1 || true
fi

# Load only StonePi's table now (stonepi.nft deletes and re-adds `table inet stonepi`).
# Do not (re)start nftables.service here: it runs /etc/nftables.conf, whose Debian default
# begins with `flush ruleset` and would wipe tailscaled's tables until tailscaled restarts.
# Enabled, it loads the rules at boot, before tailscaled starts.
nft -f "$DEST_RULES"
systemctl enable nftables >/dev/null 2>&1 || true

# One-time repair: before 0.1.9 this script ran `flush ruleset`, which wiped tailscaled's
# ts-* chains until tailscaled restarted. Restart it once to put them back, unless an SSH
# session is coming in over Tailscale (the restart would cut it); then say what to run.
TS_REPAIRED=/var/lib/stonepi/tailscale-rules-repaired
if [[ ! -f "$TS_REPAIRED" ]] && systemctl is-active --quiet tailscaled \
  && ! nft list ruleset 2>/dev/null | grep -q 'ts-input' \
  && ! iptables-legacy -S 2>/dev/null | grep -q 'ts-input'; then
  if ss -tnH state established '( sport = :22 )' 2>/dev/null | awk '{print $4}' | grep -q '^100\.'; then
    echo "NOTE: Tailscale's firewall rules are missing. From the LAN, run: sudo systemctl restart tailscaled"
  elif systemctl restart tailscaled; then
    mkdir -p "$(dirname "$TS_REPAIRED")"
    touch "$TS_REPAIRED"
    echo "Restarted tailscaled once to restore its firewall rules."
  fi
fi
echo "StonePi nftables rules applied."
