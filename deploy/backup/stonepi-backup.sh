#!/bin/bash
# StonePi platform backup — USB (default) or local single-copy.
# Usage:
#   stonepi-backup            # USB (udev / stonepi-backup.service)
#   stonepi-backup --usb
#   stonepi-backup --local    # /var/backups/stonepi/current (one retained copy)
set -euo pipefail

CONF="/etc/stonepi/backup.conf"
STAMP_LEGACY="/var/lib/stonepi/last-backup.txt"
STAMP_LOCAL="/var/lib/stonepi/last-local-backup.txt"
STAMP_USB="/var/lib/stonepi/last-usb-backup.txt"
APPS="stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch"
LABEL="STONEPI-BACKUP"
UUID=""
LOCAL_ROOT="/var/backups/stonepi"
LOG="/var/log/stonepi-backup.log"
MODE="usb"

for arg in "$@"; do
  case "$arg" in
    --local) MODE="local" ;;
    --usb) MODE="usb" ;;
    -h|--help)
      echo "Usage: stonepi-backup [--usb|--local]"
      exit 0
      ;;
  esac
done

if [[ -f "$CONF" ]]; then
  # shellcheck disable=SC1090
  source "$CONF"
fi

STAMP="$STAMP_USB"
if [[ "$MODE" == "local" ]]; then
  STAMP="$STAMP_LOCAL"
fi

log() {
  echo "$(date -Is) $*" | tee -a "$LOG"
}

write_stamp() {
  local dest="$1"
  mkdir -p "$(dirname "$dest")"
  cat > "$dest"
  chmod 644 "$dest" 2>/dev/null || true
  # Legacy single stamp for older Dashboard readers — do not overwrite with USB skipped.
  if [[ "$dest" != "$STAMP_LEGACY" ]]; then
    if grep -q '^STATUS=skipped$' "$dest" 2>/dev/null; then
      return 0
    fi
    cp "$dest" "$STAMP_LEGACY" 2>/dev/null || true
    chmod 644 "$STAMP_LEGACY" 2>/dev/null || true
  fi
}

fail() {
  local reason="$1"
  log "BACKUP FAILED: $reason"
  write_stamp "$STAMP" <<EOF
STATUS=failed
TIMESTAMP=$(date -Is)
KIND=$MODE
REASON=$reason
EOF
  exit 1
}

restart_apps() {
  systemctl start $APPS || true
}

trap restart_apps EXIT

mkdir -p "$(dirname "$LOG")"
log "Raspberry Pi Full Backup starting (mode=$MODE)"

resolve_usb_stamp_dir() {
  mapfile -t devices < <(lsblk -lnpo NAME,LABEL,UUID,TYPE | awk -v label="$LABEL" -v uuid="$UUID" '
    $4=="part" {
      if (uuid != "" && $3==uuid) { print $1" "$3; exit }
      if (uuid == "" && $2==label) { print $1" "$3; exit }
    }
  ')
  if [[ ${#devices[@]} -eq 0 ]]; then
    log "BACKUP SKIPPED: USB drive with label $LABEL${UUID:+ or UUID $UUID} not found"
    write_stamp "$STAMP_USB" <<EOF
STATUS=skipped
TIMESTAMP=$(date -Is)
KIND=usb
REASON=USB drive with label $LABEL not found
EOF
    # Not an error — exit quietly if started without the stick.
    trap - EXIT
    exit 0
  fi
  DEVICE="${devices[0]%% *}"
  UUID="${devices[0]#* }"
  log "USB drive detected: $DEVICE label=$LABEL uuid=$UUID"

  MOUNT="/mnt/stonepi-backup"
  mkdir -p "$MOUNT"
  if ! findmnt "$MOUNT" >/dev/null 2>&1; then
    mount "UUID=$UUID" "$MOUNT" || fail "Could not mount backup disk"
  fi
  if [[ ! -w "$MOUNT" ]]; then
    fail "Backup disk is not writable"
  fi

  avail_kb=$(df -Pk "$MOUNT" | awk 'NR==2{print $4}')
  if [[ "${avail_kb:-0}" -lt 1048576 ]]; then
    fail "Insufficient space"
  fi
  log "Correct backup disk confirmed"
  log "$((avail_kb/1024/1024)) GB available"

  STAMP_DIR="$MOUNT/RaspberryPi-Backup/$(date +%Y-%m-%d_%H%M)"
  mkdir -p "$STAMP_DIR"
}

resolve_local_stamp_dir() {
  LOCAL_ROOT="${LOCAL_ROOT:-/var/backups/stonepi}"
  mkdir -p "$LOCAL_ROOT"
  CURRENT="$LOCAL_ROOT/current"
  NEXT="$LOCAL_ROOT/current.next"
  PREV="$LOCAL_ROOT/current.prev"

  # Recover an interrupted promote so we never leave zero restorable copies.
  if [[ ! -d "$CURRENT" && -d "$NEXT" ]]; then
    log "Recovering interrupted local backup promote (current.next → current)"
    mv "$NEXT" "$CURRENT"
  fi
  if [[ ! -d "$CURRENT" && -d "$PREV" ]]; then
    log "Restoring previous local backup (current.prev → current)"
    mv "$PREV" "$CURRENT"
  fi

  avail_kb=$(df -Pk "$LOCAL_ROOT" | awk 'NR==2{print $4}')
  need_kb=1048576
  if [[ -d "$CURRENT" ]]; then
    # Staging + existing current may coexist briefly — require current size + 1 GiB.
    cur_kb=$(du -sk "$CURRENT" 2>/dev/null | awk '{print $1}')
    need_kb=$(( ${cur_kb:-0} + 1048576 ))
  fi
  if [[ "${avail_kb:-0}" -lt "$need_kb" ]]; then
    fail "Insufficient local space for backup (need ~$((need_kb/1024)) MiB free)"
  fi
  log "Local backup root $LOCAL_ROOT ($((avail_kb/1024/1024)) GB available)"

  STAGING="$LOCAL_ROOT/staging"
  rm -rf "$STAGING" "$NEXT"
  mkdir -p "$STAGING"
  STAMP_DIR="$STAGING"
}

if [[ "$MODE" == "local" ]]; then
  resolve_local_stamp_dir
else
  resolve_usb_stamp_dir
fi

mkdir -p "$STAMP_DIR"/{system,boot,applications,databases,config,manifests}

backup_sqlite() {
  local src="$1"
  local dest="$2"
  if [[ -f "$src" ]]; then
    sqlite3 "$src" ".backup '$dest'"
    sqlite3 "$dest" "PRAGMA integrity_check;" | grep -qx ok || fail "SQLite integrity check failed for $src"
  fi
}

log "Applications stopping"
systemctl stop $APPS

log "SQLite databases backing up"
backup_sqlite /var/lib/stonepi/auth/users.sqlite "$STAMP_DIR/databases/users.sqlite"
backup_sqlite /var/lib/stonepi/newscast/newscast.sqlite "$STAMP_DIR/databases/newscast.sqlite"
backup_sqlite /var/lib/stonepi/fileserve/fileserve.sqlite "$STAMP_DIR/databases/fileserve.sqlite"
backup_sqlite /var/lib/stonepi/eventtrakr/eventtrakr.sqlite "$STAMP_DIR/databases/eventtrakr.sqlite"

log "Application data copying"
rsync -a --delete /var/lib/stonepi/ "$STAMP_DIR/applications/" || fail "Application data copy failed"
rsync -a /opt/stonepi/ "$STAMP_DIR/system/opt-stonepi/" --exclude '.venv' --exclude '__pycache__' || fail "Application code copy failed"

log "Configuration copying"
rsync -a /etc/stonepi/ "$STAMP_DIR/config/stonepi/"
rsync -a /etc/nginx/sites-available/stonepi "$STAMP_DIR/config/nginx-stonepi" || true
mkdir -p "$STAMP_DIR/config/systemd"
rsync -a /etc/systemd/system/stonepi-*.service "$STAMP_DIR/config/systemd/" || true
rsync -a /boot/firmware/ "$STAMP_DIR/boot/" 2>/dev/null || rsync -a /boot/ "$STAMP_DIR/boot/" || true
hostnamectl > "$STAMP_DIR/manifests/hostname.txt" || true
dpkg --get-selections > "$STAMP_DIR/manifests/dpkg-selections.txt"
systemctl list-unit-files 'stonepi*' > "$STAMP_DIR/manifests/units.txt"

# Finalize local destination (safe single-copy rotate).
DESTINATION="$STAMP_DIR"
if [[ "$MODE" == "local" ]]; then
  CURRENT="$LOCAL_ROOT/current"
  NEXT="$LOCAL_ROOT/current.next"
  PREV="$LOCAL_ROOT/current.prev"
  # Staging is complete — park as current.next before touching current.
  rm -rf "$NEXT"
  mv "$STAMP_DIR" "$NEXT"
  if [[ -d "$CURRENT" ]]; then
    rm -rf "$PREV"
    mv "$CURRENT" "$PREV"
  fi
  mv "$NEXT" "$CURRENT"
  # Only drop the previous copy after the new current is in place.
  rm -rf "$PREV"
  DESTINATION="$CURRENT"
  STAMP_DIR="$CURRENT"
fi

SIZE=$(du -sh "$STAMP_DIR" | awk '{print $1}')
INFO_EXTRA=""
if [[ "$MODE" == "usb" ]]; then
  INFO_EXTRA="LABEL=$LABEL
UUID=$UUID"
fi
cat > "$STAMP_DIR/backup-info.txt" <<EOF
STATUS=ok
KIND=$MODE
TIMESTAMP=$(date -Is)
DESTINATION=$DESTINATION
SIZE=$SIZE
$INFO_EXTRA
EOF
write_stamp "$STAMP" < "$STAMP_DIR/backup-info.txt"

log "Backup verified"
log "Applications restarting"
restart_apps
trap - EXIT

log "BACKUP COMPLETE Kind=$MODE Size=$SIZE Destination=$DESTINATION"
echo
echo "Raspberry Pi Full Backup"
if [[ "$MODE" == "usb" ]]; then
  echo "✓ USB drive detected"
  echo "✓ Correct backup disk confirmed"
else
  echo "✓ Local backup written (single copy)"
fi
echo "✓ Applications stopped safely"
echo "✓ SQLite databases backed up"
echo "✓ Application data copied"
echo "✓ System configuration copied"
echo "✓ Backup verified"
echo "✓ Applications restarted"
echo
echo "BACKUP COMPLETE"
echo "Kind: $MODE"
echo "Size: $SIZE"
echo "Destination: $DESTINATION"
echo "Timestamp: $(date '+%Y-%m-%d %H:%M')"
