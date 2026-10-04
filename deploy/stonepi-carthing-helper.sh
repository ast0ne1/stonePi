#!/bin/bash
# Privileged helper for the Car Thing panel (sudo, user stonepi-notify only).
# Notify -> Displays -> Car Thing switches the panel service on and off with it.
# Every action prints one JSON line last; non-zero exit on failure.
# Usage:
#   stonepi-carthing-helper status
#   stonepi-carthing-helper enable | disable | restart
set -euo pipefail

UNIT="stonepi-carthing.service"

json_escape() {
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/ }"
  printf '%s' "$s"
}

die() {
  printf '{"ok": false, "message": "%s"}\n' "$(json_escape "$1")"
  exit 1
}

ok() {
  if [[ -n "${1:-}" ]]; then
    printf '{"ok": true, %s}\n' "$1"
  else
    printf '{"ok": true}\n'
  fi
}

[[ -f "/etc/systemd/system/$UNIT" ]] || die "$UNIT is not installed. Re-run the StonePi installer."

cmd="${1:-}"
case "$cmd" in
  status)
    active="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
    enabled="$(systemctl is-enabled "$UNIT" 2>/dev/null || true)"
    adb="false"
    command -v adb >/dev/null 2>&1 && adb="true"
    ok "\"active\": \"$(json_escape "$active")\", \"enabled\": \"$(json_escape "$enabled")\", \"adb\": $adb"
    ;;
  enable)
    command -v adb >/dev/null 2>&1 || die "adb is not installed (sudo apt install adb)."
    systemctl enable --now "$UNIT" >/dev/null 2>&1 || die "Could not start $UNIT."
    ok
    ;;
  disable)
    # Stopping hands the Car Thing its own web app back (the service unmounts on the way down).
    systemctl disable --now "$UNIT" >/dev/null 2>&1 || die "Could not stop $UNIT."
    ok
    ;;
  restart)
    systemctl restart "$UNIT" >/dev/null 2>&1 || die "Could not restart $UNIT."
    ok
    ;;
  *)
    die "Usage: stonepi-carthing-helper status|enable|disable|restart"
    ;;
esac
