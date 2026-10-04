#!/bin/bash
# Privileged helper for Dashboard → Services (sudo, user stonepi-dash only).
# One StonePi unit per call, checked before anything runs: sudoers wildcards on
# systemctl/journalctl would also match extra units and arguments (and journalctl's pager).
# Usage:
#   stonepi-service-helper start|stop|restart|is-active UNIT
#   stonepi-service-helper logs UNIT [LINES]      (LINES 1..2000, default 40)
# UNIT is stonepi-<name> or stonepi-<name>.service, installed in /etc/systemd/system.
set -euo pipefail

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export SYSTEMD_PAGER=
export PAGER=cat
export SYSTEMD_COLORS=0

usage() {
  echo "Usage: stonepi-service-helper start|stop|restart|is-active UNIT | logs UNIT [LINES]" >&2
  exit 2
}

action="${1:-}"
unit="${2:-}"
[[ $# -ge 2 ]] || usage

if [[ ! "$unit" =~ ^stonepi-[a-z0-9-]+(\.service)?$ ]]; then
  echo "Not a StonePi service: $unit" >&2
  exit 2
fi
unit="${unit%.service}.service"
if [[ ! -f "/etc/systemd/system/$unit" ]]; then
  echo "$unit is not installed." >&2
  exit 2
fi

case "$action" in
  start|stop|restart|is-active)
    [[ $# -eq 2 ]] || usage
    exec systemctl "$action" -- "$unit"
    ;;
  logs)
    [[ $# -le 3 ]] || usage
    lines="${3:-40}"
    if [[ ! "$lines" =~ ^[0-9]{1,4}$ ]] || (( 10#$lines < 1 || 10#$lines > 2000 )); then
      echo "LINES must be a number from 1 to 2000." >&2
      exit 2
    fi
    exec journalctl --no-pager -u "$unit" -n "$((10#$lines))"
    ;;
  *)
    usage
    ;;
esac
