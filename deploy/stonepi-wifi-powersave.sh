#!/bin/bash
# Keep the Pi's Wi-Fi awake. With power saving on (the Raspberry Pi OS default) the radio
# dozes between beacons: mDNS answers for stonepi.local and requests from Pebbles and phones
# arrive late (100 ms – 1 s spikes) or occasionally not at all. A home server is plugged in,
# so the few hundred mW saved aren't worth it.
#
# Persists through NetworkManager (applies the next time a Wi-Fi connection comes up) and
# switches it off now with `iw`, without restarting the network (an SSH session survives).
# Safe to re-run; does nothing on a Pi without Wi-Fi or without NetworkManager.
# Usage: sudo bash stonepi-wifi-powersave.sh
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

CONF_DIR=/etc/NetworkManager/conf.d
CONF="$CONF_DIR/stonepi-wifi-powersave.conf"

if [[ -d "$CONF_DIR" ]]; then
  cat > "$CONF.tmp" <<'EOF'
# Written by StonePi (deploy/stonepi-wifi-powersave.sh): no Wi-Fi power saving, so mDNS
# (stonepi.local) and device requests don't stall while the radio dozes. 2 = disable.
[connection]
wifi.powersave = 2
EOF
  chmod 644 "$CONF.tmp"
  mv -f "$CONF.tmp" "$CONF"
  echo "wifi-powersave: NetworkManager set to keep Wi-Fi awake ($CONF)"
else
  echo "wifi-powersave: no NetworkManager; switching off for this boot only"
fi

if command -v iw >/dev/null 2>&1; then
  for wireless in /sys/class/net/*/wireless; do
    [[ -e "$wireless" ]] || continue
    dev="$(basename "$(dirname "$wireless")")"
    if iw dev "$dev" set power_save off 2>/dev/null; then
      echo "wifi-powersave: $dev power saving off ($(iw dev "$dev" get power_save 2>/dev/null | tr -d '\n'))"
    else
      echo "wifi-powersave: couldn't switch $dev now (takes effect when it next connects)"
    fi
  done
else
  echo "wifi-powersave: iw not installed; takes effect when Wi-Fi next connects"
fi
