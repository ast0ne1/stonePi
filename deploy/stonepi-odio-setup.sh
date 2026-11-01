#!/bin/bash
# Odio (odio.love) for Radio, run by Odio's own Ansible installer from a pinned release whose
# files are checked against the hashes below. Installed: the audio server (PulseAudio), MPD,
# myMPD (WebradioDB station search) and the Odio API for Radio, plus AirPlay (shairport-sync)
# and Spotify Connect (spotifyd), so the Pi is also a speaker for phones on the home network.
# DLNA, Snapcast, Bluetooth, the disc player, the kiosk display and Odio's login banner are
# left out.
#
# Everything runs as the dedicated user "odio" (systemd user units, lingering). The Odio API
# (:8018, no sign-in) is set back to loopback only with a conf.d drop-in (Odio's installer sets
# `bind: all`); if it won't answer that way the drop-in is removed again. myMPD (:8080) and
# MPD (:6600) still listen on all interfaces. StonePi's nftables firewall drops all three
# from the LAN either way; only Radio reaches them, over loopback. The firewall lets AirPlay
# and Spotify Connect in from LAN and tailnet addresses only (deploy/nftables).
#
# Usage: sudo stonepi-odio-setup [--force | --status]
#   --force    re-run even when this Odio version is already installed
#   --status   one line: installed version, and whether the API and myMPD answer
set -euo pipefail

ODIOS_VERSION=2026.10.0b3
ODIOS_REPO=b0bbywan/odios
INSTALL_SHA256=1f1aafe4ef853ae10c9f6f134f24988b2943b6d5c50c388f8177103045c0f569
ARCHIVE_SHA256=baf53403d96ca7fceba2626e43007291d88bfddea0b164481cadf1618879a986
ODIO_USER=odio
MARKER=/var/lib/stonepi/odio-version
API_URL=http://127.0.0.1:8018
MYMPD_URL=http://127.0.0.1:8080

mode=install
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) mode=force; shift ;;
    --status) mode=status; shift ;;
    -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option $1" >&2; exit 2 ;;
  esac
done

log() { echo "[odio] $*"; }

answers() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$1" 2>/dev/null | grep -q '^[234]'; }

api_answers_within() {
  local _
  for _ in $(seq 1 "$1"); do
    answers "$API_URL/server" && return 0
    sleep 2
  done
  return 1
}

# Odio API on loopback only (go-odio-api's `bind`, default `lo`; Odio's installer sets `all`).
# A drop-in goes next to each config odio-api may load (~odio/.config/odio-api, /etc/odio-api).
API_DROPIN=90-stonepi-loopback.yaml
api_loopback_only() {
  local home dirs=() dir listen unit
  listen="$(ss -ltnH 'sport = :8018' 2>/dev/null | awk '{print $4}')"
  if [[ -n "$listen" ]] && ! grep -qvE '^(127\.0\.0\.1|\[::1\]):8018$' <<<"$listen"; then
    log "Odio API already listens on loopback only"
    return 0
  fi
  home="$(getent passwd "$ODIO_USER" | cut -d: -f6)"
  [[ -n "$home" && -d "$home/.config/odio-api" ]] && dirs+=("$home/.config/odio-api")
  [[ -d /etc/odio-api ]] && dirs+=(/etc/odio-api)
  if [[ ${#dirs[@]} -eq 0 ]]; then
    log "WARN: no odio-api config folder found; the API stays on all interfaces (the firewall drops it)"
    return 0
  fi
  for dir in "${dirs[@]}"; do
    install -d -m 755 "$dir/conf.d"
    printf '# StonePi: Radio reaches the Odio API over loopback only.\nbind: lo\n' > "$dir/conf.d/$API_DROPIN"
    if [[ "$dir" == "$home"/* ]]; then chown -R "$ODIO_USER:" "$dir/conf.d"; fi
  done
  unit="$(systemctl --machine="$ODIO_USER@" --user list-unit-files --no-legend 'odio-api*.service' 2>/dev/null | awk 'NR == 1 {print $1}')"
  unit="${unit:-odio-api.service}"
  systemctl --machine="$ODIO_USER@" --user restart "$unit" 2>/dev/null \
    || systemctl restart "$unit" 2>/dev/null || true
  if api_answers_within 15; then
    log "Odio API now listens on loopback only ($(ss -ltnH 'sport = :8018' 2>/dev/null | awk '{print $4}' | tr '\n' ' '))"
    return 0
  fi
  log "WARN: Odio API didn't come back on loopback; undoing (the firewall still drops it from the LAN)"
  for dir in "${dirs[@]}"; do rm -f "$dir/conf.d/$API_DROPIN"; done
  systemctl --machine="$ODIO_USER@" --user restart "$unit" 2>/dev/null \
    || systemctl restart "$unit" 2>/dev/null || true
  api_answers_within 15 || true
}

if [[ "$mode" == status ]]; then
  installed="$(cat "$MARKER" 2>/dev/null || echo none)"
  api=down; answers "$API_URL/server" && api=up
  mympd=down; answers "$MYMPD_URL/" && mympd=up
  echo "odio=$installed api=$api mympd=$mympd"
  [[ "$api" == up ]]
  exit
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo stonepi-odio-setup" >&2
  exit 1
fi

if [[ "$mode" != force && "$(cat "$MARKER" 2>/dev/null)" == "$ODIOS_VERSION" ]] && answers "$API_URL/server"; then
  log "Odio $ODIOS_VERSION already installed and answering."
  api_loopback_only
  exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
base="https://github.com/$ODIOS_REPO/releases/download/$ODIOS_VERSION"

fetch_checked() {
  local url="$1" out="$2" want="$3"
  curl -fsSL --retry 3 -o "$out" "$url"
  if [[ "$(sha256sum "$out" | cut -d' ' -f1)" != "$want" ]]; then
    echo "[odio] checksum mismatch for $url; not installing." >&2
    exit 1
  fi
}

log "Fetching Odio $ODIOS_VERSION"
fetch_checked "$base/install.sh" "$work/install.sh" "$INSTALL_SHA256"
# Odio's installer downloads this archive again itself; checking it here first means a
# replaced release asset stops the install instead of running.
fetch_checked "$base/odio-$ODIOS_VERSION.tar.gz" "$work/odio.tar.gz" "$ARCHIVE_SHA256"

# Odio's installer apt-installs these without -y in live mode; have them already.
apt-get -o DPkg::Lock::Timeout=300 install -y -qq python3-jinja2 python3-cryptography curl sudo >/dev/null

# Setting TARGET_USER skips every prompt; each component is then chosen here. The installer is
# written for `curl | bash` (when stdin isn't a terminal it reads the rest of itself from stdin),
# so it gets itself on a pipe; that also keeps it away from this script's own stdin, which under
# `curl ... | sudo bash` is the StonePi installer.
log "Installing (this takes 10-20 minutes on a Pi)"
cat "$work/install.sh" | env \
  ODIOS_VERSION="$ODIOS_VERSION" \
  INSTALL_MODE=live \
  TARGET_USER="$ODIO_USER" \
  AUDIOSERVER=pulseaudio \
  INSTALL_AUDIOSERVER=Y \
  INSTALL_MPD=Y \
  INSTALL_MYMPD=Y \
  INSTALL_ODIO_API=Y \
  INSTALL_BLUETOOTH=N \
  INSTALL_MPD_DISCPLAYER=N \
  INSTALL_SHAIRPORT_SYNC=Y \
  INSTALL_SNAPCLIENT=N \
  INSTALL_UPMPDCLI=N \
  INSTALL_SPOTIFYD=Y \
  INSTALL_QBZD=N \
  INSTALL_DISPLAY=N \
  INSTALL_BRANDING=N \
  bash

mkdir -p "$(dirname "$MARKER")"
printf '%s\n' "$ODIOS_VERSION" > "$MARKER"

if api_answers_within 30; then
  log "Odio API is answering on $API_URL"
  api_loopback_only
  exit 0
fi
echo "[odio] installed, but the Odio API isn't answering on $API_URL yet." >&2
exit 1
