#!/usr/bin/env bash
# Installed to /usr/local/bin/stonepi by deploy/install.sh
set -euo pipefail

UNITS=(
  stonepi-auth
  stonepi-dashboard
  stonepi-notify
  stonepi-newscast
  stonepi-fileserve
  stonepi-eventtrakr
  stonepi-pinboard
  stonepi-studio
  stonepi-pricescout
  stonepi-sportguide
  stonepi-pricewatch
  stonepi-library
)
# stonepi-kiwix (Library reader) shows in status/logs but is never started/stopped here:
# the Library helper runs it only once content exists.
STATUS_UNITS=("${UNITS[@]}" stonepi-kiwix stonepi-carthing)

usage() {
  cat <<'EOF'
Usage: stonepi <command>

  status     Show systemd status for StonePi app units
  restart    Restart all app units
  stop       Stop all app units
  start      Start all app units
  logs       Follow journals for all app units
  urls       Print local URLs
EOF
}

cmd="${1:-}"
case "$cmd" in
  status)
    systemctl --no-pager --full status "${STATUS_UNITS[@]}" || true
    ;;
  restart)
    sudo systemctl restart "${UNITS[@]}"
    ;;
  stop)
    sudo systemctl stop "${UNITS[@]}"
    ;;
  start)
    sudo systemctl start "${UNITS[@]}"
    ;;
  logs)
    sudo journalctl \
      -u stonepi-auth \
      -u stonepi-dashboard \
      -u stonepi-notify \
      -u stonepi-newscast \
      -u stonepi-fileserve \
      -u stonepi-eventtrakr \
      -u stonepi-pinboard \
      -u stonepi-studio \
      -u stonepi-pricescout \
      -u stonepi-sportguide \
      -u stonepi-pricewatch \
      -u stonepi-library \
      -u stonepi-kiwix \
      -f
    ;;
  urls)
    host="$(hostname 2>/dev/null || echo stonepi)"
    lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo "http://${host}.local/"
    echo "http://${host}.local/auth/login"
    echo "http://${host}.local/notify/"
    echo "http://${host}.local/news/"
    echo "http://${host}.local/files/"
    echo "http://${host}.local/events/"
    echo "http://${host}.local/pinboard/"
    echo "http://${host}.local/studio/"
    echo "http://${host}.local/prices/"
    echo "http://${host}.local/sports/"
    echo "http://${host}.local/watch/"
    echo "http://${host}.local/library/"
    echo "https://${host}.local:9090  # Cockpit"
    if [[ -n "${lan_ip}" ]]; then
      echo "https://${lan_ip}:9090      # Cockpit (LAN IP)"
    fi
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
