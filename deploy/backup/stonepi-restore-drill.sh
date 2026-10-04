#!/bin/bash
# StonePi restore drill — verify backup integrity + stack readiness without restoring.
# Usage:
#   sudo stonepi-restore-drill
#   sudo stonepi-restore-drill /mnt/stonepi-backup/RaspberryPi-Backup/TIMESTAMP
set -euo pipefail

STAMP="/var/lib/stonepi/last-restore-drill.txt"
MOUNT="/mnt/stonepi-backup"
LABEL="STONEPI-BACKUP"
SRC="${1:-}"
FAILS=0

log() { echo "$*"; }
fail() { log "FAIL: $*"; FAILS=$((FAILS + 1)); }
ok() { log "OK: $*"; }

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo stonepi-restore-drill [BACKUP_DIR]" >&2
  exit 1
fi

# Resolve backup directory
if [[ -z "$SRC" ]]; then
  LOCAL_CURRENT="/var/backups/stonepi/current"
  if [[ ! -d "$MOUNT" ]]; then
    mkdir -p "$MOUNT"
  fi
  if ! findmnt "$MOUNT" >/dev/null 2>&1; then
    # Try mount by label
    if blkid -L "$LABEL" >/dev/null 2>&1; then
      mount "LABEL=$LABEL" "$MOUNT" 2>/dev/null || true
    fi
  fi
  USB_SRC=""
  if [[ -d "$MOUNT/RaspberryPi-Backup" ]]; then
    USB_SRC="$(find "$MOUNT/RaspberryPi-Backup" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1 || true)"
  fi
  # Prefer newest by backup-info timestamp across local + USB
  CANDIDATES=()
  [[ -d "$LOCAL_CURRENT" ]] && CANDIDATES+=("$LOCAL_CURRENT")
  [[ -n "$USB_SRC" && -d "$USB_SRC" ]] && CANDIDATES+=("$USB_SRC")
  BEST=""
  BEST_TS=""
  for c in "${CANDIDATES[@]+"${CANDIDATES[@]}"}"; do
    ts=""
    if [[ -f "$c/backup-info.txt" ]]; then
      ts="$(awk -F= '/^TIMESTAMP=/{print $2; exit}' "$c/backup-info.txt" || true)"
    fi
    ts="${ts:-$c}"
    if [[ -z "$BEST" || "$ts" > "$BEST_TS" ]]; then
      BEST="$c"
      BEST_TS="$ts"
    fi
  done
  SRC="$BEST"
fi

log "=== StonePi restore drill ==="
log "Backup: ${SRC:-none}"

if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  fail "No backup directory found (enable local backup, plug STONEPI-BACKUP USB, or pass path)"
else
  [[ -f "$SRC/backup-info.txt" ]] && ok "backup-info.txt present" || fail "missing backup-info.txt"
  [[ -d "$SRC/applications" ]] && ok "applications/ present" || fail "missing applications/"
  [[ -d "$SRC/config/stonepi" ]] && ok "config/stonepi present" || fail "missing config/stonepi"
  [[ -d "$SRC/applications/notify" || -d "$SRC/applications/notifications" ]] && ok "notify data in backup" || fail "notify/ missing from backup"
  [[ -d "$SRC/applications/vault" ]] && ok "vault in backup" || fail "vault/ missing from backup"
  if [[ -f "$SRC/applications/notify/destinations.json" || -f "$SRC/applications/notifications/destinations.json" ]]; then
    ok "destinations.json in backup"
  else
    fail "destinations.json missing (Destinations may be empty after restore)"
  fi
  # Library: verify the opt-in content store by manifest + sizes (never copy 100 GB here).
  if grep -q '^LIBRARY_CONTENT=ok$' "$SRC/backup-info.txt" 2>/dev/null; then
    STORE="$(dirname "$SRC")/library-content"
    if [[ -f "$STORE/manifest.json" ]] && python3 - "$STORE" <<'PY'
import json, os, sys
store = sys.argv[1]
files = json.load(open(os.path.join(store, "manifest.json")))["files"]
bad = [f["file_name"] for f in files if not os.path.isfile(os.path.join(store, f["file_name"])) or os.path.getsize(os.path.join(store, f["file_name"])) != int(f["size"])]
sys.exit(1 if bad else 0)
PY
    then
      ok "library content store matches its manifest"
    else
      fail "library content store incomplete (sizes don't match manifest)"
    fi
  fi
fi

# Live stack checks
UNITS=(stonepi-auth stonepi-dashboard stonepi-notify nginx)
for u in "${UNITS[@]}"; do
  if systemctl is-active --quiet "$u"; then
    ok "$u active"
  else
    fail "$u not active"
  fi
done

curl -fsS --max-time 3 http://127.0.0.1:8010/healthz >/dev/null && ok "dashboard /healthz" || fail "dashboard /healthz"
curl -fsS --max-time 3 http://127.0.0.1:8011/healthz >/dev/null && ok "auth /healthz" || fail "auth /healthz"
curl -fsS --max-time 3 http://127.0.0.1:8012/healthz >/dev/null && ok "notify /healthz" || fail "notify /healthz"
curl -fsS --max-time 3 http://127.0.0.1:8099/healthz >/dev/null && ok "recover /healthz" || fail "recover /healthz"

if [[ -f /var/lib/stonepi/notify/destinations.json ]]; then
  ok "live destinations.json"
else
  fail "live destinations.json missing"
fi

if [[ -d /var/lib/stonepi/vault ]]; then
  ok "live vault dir"
else
  fail "live vault dir missing"
fi

STATUS=ok
if [[ "$FAILS" -gt 0 ]]; then
  STATUS=failed
fi

mkdir -p "$(dirname "$STAMP")"
cat > "$STAMP" <<EOF
STATUS=$STATUS
TIMESTAMP=$(date -Is)
FAILS=$FAILS
BACKUP=${SRC:-}
EOF
chmod 644 "$STAMP"

log "=== Drill $STATUS ($FAILS failures) ==="
[[ "$FAILS" -eq 0 ]]
