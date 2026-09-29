#!/usr/bin/env bash
# Privileged hostname helper for stonepi-dash (NOPASSWD via sudoers).
# Sets OS hostname, Avahi-friendly /etc/hosts, stonepi.env, and the hot-read file.
# Usage: stonepi-hostname NAME
#        stonepi-hostname install-check
set -euo pipefail

cmd="${1:-}"
HOSTNAME_FILE="${STONEPI_HOSTNAME_FILE:-/var/lib/stonepi/hostname}"
ENV_FILE="${STONEPI_ENV_FILE:-/etc/stonepi/stonepi.env}"

normalize() {
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  raw="${raw%.local}"
  raw="${raw%.}"
  # trim whitespace
  raw="$(printf '%s' "$raw" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  printf '%s' "$raw"
}

valid_name() {
  local name="$1"
  [[ "$name" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

set_env_key() {
  local file="$1" key="$2" value="$3"
  [[ -f "$file" ]] || return 0
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

case "$cmd" in
  install-check)
    if command -v hostnamectl >/dev/null 2>&1; then
      echo "ok"
      exit 0
    fi
    echo "hostnamectl missing" >&2
    exit 1
    ;;
  ""|-h|--help)
    echo "Usage: stonepi-hostname NAME" >&2
    echo "       stonepi-hostname install-check" >&2
    exit 1
    ;;
esac

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo stonepi-hostname NAME" >&2
  exit 1
fi

name="$(normalize "$cmd")"
if ! valid_name "$name"; then
  echo "Hostname must be 1-63 letters, digits, or hyphens, and not start or end with a hyphen." >&2
  exit 1
fi

hostnamectl set-hostname "$name"

if grep -qE '^127\.0\.1\.1\b' /etc/hosts; then
  sed -i "s/^127\\.0\\.1\\.1.*/127.0.1.1\t${name}/" /etc/hosts
else
  printf '127.0.1.1\t%s\n' "$name" >> /etc/hosts
fi

mkdir -p "$(dirname "$HOSTNAME_FILE")"
printf '%s\n' "$name" > "$HOSTNAME_FILE"
chmod 644 "$HOSTNAME_FILE" 2>/dev/null || true

if [[ -f "$ENV_FILE" ]]; then
  set_env_key "$ENV_FILE" STONEPI_HOSTNAME "$name"
  set_env_key "$ENV_FILE" STONEPI_PUBLIC_ORIGIN "http://${name}.local"
  set_env_key "$ENV_FILE" PUBLIC_ORIGIN "http://${name}.local"
  set_env_key "$ENV_FILE" COCKPIT_URL "https://${name}.local:9090"
  set_env_key "$ENV_FILE" STONEPI_HOSTNAME_FILE "$HOSTNAME_FILE"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
  systemctl restart avahi-daemon >/dev/null 2>&1 || true
fi

echo "ok ${name}"
