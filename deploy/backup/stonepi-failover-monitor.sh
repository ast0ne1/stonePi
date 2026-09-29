#!/bin/bash
# If dashboard healthz fails after boot grace, enable nginx failover → /recover/
# Requires consecutive misses before trip / consecutive passes before clear (hysteresis).
set -euo pipefail

FLAG="/var/lib/stonepi/failover"
HELPER="/usr/local/sbin/stonepi-backup-helper"
GRACE_SEC="${STONEPI_FAILOVER_GRACE:-90}"
INTERVAL="${STONEPI_FAILOVER_INTERVAL:-30}"
MISS_NEED="${STONEPI_FAILOVER_MISS_NEED:-3}"
PASS_NEED="${STONEPI_FAILOVER_PASS_NEED:-2}"
CURL_MAX="${STONEPI_FAILOVER_CURL_MAX:-8}"
PROBE_URL="${STONEPI_FAILOVER_PROBE_URL:-http://127.0.0.1:8010/healthz}"

# Pure hysteresis: given ready (0/1), current miss/pass counts and whether
# failover is already on, print: new_misses new_passes action
# action is one of: none | on | off
failover_hysteresis() {
  local ready="$1" misses="$2" passes="$3" is_on="$4"
  local miss_need="${5:-$MISS_NEED}" pass_need="${6:-$PASS_NEED}"
  local action="none"

  if [[ "$ready" == "1" ]]; then
    misses=0
    passes=$((passes + 1))
    if [[ "$is_on" == "1" ]] && (( passes >= pass_need )); then
      action="off"
      passes=0
    fi
  else
    passes=0
    misses=$((misses + 1))
    if [[ "$is_on" != "1" ]] && (( misses >= miss_need )); then
      action="on"
      misses=0
    fi
  fi
  printf '%s %s %s\n' "$misses" "$passes" "$action"
}

failover_flag_on() {
  [[ -f "$FLAG" ]] && grep -qi '^on' "$FLAG"
}

probe_dashboard() {
  curl -fsS --max-time "$CURL_MAX" "$PROBE_URL" >/dev/null 2>&1
}

# Allow: stonepi-failover-monitor --test-hysteresis ready misses passes is_on [miss_need] [pass_need]
if [[ "${1:-}" == "--test-hysteresis" ]]; then
  shift
  failover_hysteresis "$@"
  exit 0
fi

sleep "$GRACE_SEC"

misses=0
passes=0

while true; do
  if probe_dashboard; then
    ready=1
  else
    ready=0
    # Keep recover warm while unhealthy (even before trip threshold).
    systemctl start stonepi-recover >/dev/null 2>&1 || true
  fi

  if failover_flag_on; then
    is_on=1
  else
    is_on=0
  fi

  read -r misses passes action < <(failover_hysteresis "$ready" "$misses" "$passes" "$is_on")

  case "$action" in
    on)
      systemctl start stonepi-recover >/dev/null 2>&1 || true
      "$HELPER" failover-on >/dev/null 2>&1 || true
      ;;
    off)
      "$HELPER" failover-off >/dev/null 2>&1 || true
      ;;
  esac

  sleep "$INTERVAL"
done
