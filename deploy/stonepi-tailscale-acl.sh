#!/bin/bash
# Apply or fetch Tailscale ACL via Tailscale cloud API (not LocalAPI).
# Requires Vault/env: TAILSCALE_API_KEY, TAILSCALE_TAILNET.
# Usage:
#   stonepi-tailscale-acl status|show|apply [acl-file]
set -euo pipefail

ACL_DEFAULT="/opt/stonepi/deploy/tailscale/acl.example.hujson"
PY="${STONEPI_PYTHON:-/opt/stonepi/apps/dashboard/.venv/bin/python}"

read_vault() {
  local key="$1"
  if [[ -x "$PY" ]]; then
    "$PY" -c "from stonepi_vault import get_secret; print(get_secret('$key', env_name='$key', default='') or '')" 2>/dev/null || true
  else
    printenv "$key" 2>/dev/null || true
  fi
}

API_KEY="$(read_vault TAILSCALE_API_KEY)"
TAILNET="$(read_vault TAILSCALE_TAILNET)"
API_KEY="${API_KEY:-${TAILSCALE_API_KEY:-}}"
TAILNET="${TAILNET:-${TAILSCALE_TAILNET:-}}"

cmd="${1:-}"
case "$cmd" in
  status)
    if [[ -n "$API_KEY" && -n "$TAILNET" ]]; then
      echo "configured=yes"
      echo "tailnet=$TAILNET"
    else
      echo "configured=no"
      echo "hint=Set TAILSCALE_API_KEY and TAILSCALE_TAILNET in Vault (Settings → Vault)"
    fi
    ;;
  show)
    [[ -n "$API_KEY" && -n "$TAILNET" ]] || { echo "Missing API credentials" >&2; exit 1; }
    curl -fsS -H "Authorization: Bearer ${API_KEY}" \
      "https://api.tailscale.com/api/v2/tailnet/${TAILNET}/acl"
    echo
    ;;
  apply)
    FILE="${2:-$ACL_DEFAULT}"
    [[ -n "$API_KEY" && -n "$TAILNET" ]] || { echo "Missing API credentials" >&2; exit 1; }
    [[ -f "$FILE" ]] || { echo "ACL file not found: $FILE" >&2; exit 1; }
    BODY="$(python3 -c '
from pathlib import Path
import re, sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
out = []
for line in text.splitlines():
    s = line.strip()
    if s.startswith("//"):
        continue
    out.append(re.sub(r"\s+//.*$", "", line))
print("\n".join(out))
' "$FILE")"
    curl -fsS -X POST -H "Authorization: Bearer ${API_KEY}" \
      -H "Content-Type: application/json" \
      --data "$BODY" \
      "https://api.tailscale.com/api/v2/tailnet/${TAILNET}/acl"
    echo
    echo "ACL applied to ${TAILNET}"
    ;;
  *)
    echo "Usage: stonepi-tailscale-acl {status|show|apply [file]}" >&2
    exit 1
    ;;
esac
