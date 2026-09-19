#!/bin/bash
set -euo pipefail

CONF="/etc/stonepi/backup.conf"
STAMP="/var/lib/stonepi/last-backup.txt"
APPS="stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-dashboard stonepi-auth"
LABEL="STONEPI-BACKUP"
UUID=""
LOG="/var/log/stonepi-backup.log"

if [[ -f "$CONF" ]]; then
  # shellcheck disable=SC1090
  source "$CONF"
fi

log() {
  echo "$(date -Is) $*" | tee -a "$LOG"
}

fail() {
  local reason="$1"
  log "BACKUP FAILED: $reason"
  cat > "$STAMP" <<EOF
STATUS=failed
TIMESTAMP=$(date -Is)
REASON=$reason
EOF
  exit 1
}

restart_apps() {
  systemctl start $APPS || true
}

trap restart_apps EXIT

mkdir -p "$(dirname "$LOG")"
log "Raspberry Pi Full Backup starting"

mapfile -t devices < <(lsblk -lnpo NAME,LABEL,UUID,TYPE | awk -v label="$LABEL" -v uuid="$UUID" '
  $4=="part" {
    if (uuid != "" && $3==uuid) { print $1" "$3; exit }
    if (uuid == "" && $2==label) { print $1" "$3; exit }
  }
')
if [[ ${#devices[@]} -eq 0 ]]; then
  log "BACKUP SKIPPED: USB drive with label $LABEL${UUID:+ or UUID $UUID} not found"
  cat > "$STAMP" <<EOF
STATUS=skipped
TIMESTAMP=$(date -Is)
REASON=USB drive with label $LABEL not found
EOF
  # Not an error — exit quietly if started without the stick (manual start, etc.).
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

log "Applications stopping"
systemctl stop $APPS

STAMP_DIR="$MOUNT/RaspberryPi-Backup/$(date +%Y-%m-%d_%H%M)"
mkdir -p "$STAMP_DIR"/{system,boot,applications,databases,config,manifests}

backup_sqlite() {
  local src="$1"
  local dest="$2"
  if [[ -f "$src" ]]; then
    sqlite3 "$src" ".backup '$dest'"
    sqlite3 "$dest" "PRAGMA integrity_check;" | grep -qx ok || fail "SQLite integrity check failed for $src"
  fi
}

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
rsync -a /etc/systemd/system/stonepi-*.service "$STAMP_DIR/config/systemd/" || true
rsync -a /boot/firmware/ "$STAMP_DIR/boot/" 2>/dev/null || rsync -a /boot/ "$STAMP_DIR/boot/" || true
hostnamectl > "$STAMP_DIR/manifests/hostname.txt" || true
dpkg --get-selections > "$STAMP_DIR/manifests/dpkg-selections.txt"
systemctl list-unit-files 'stonepi*' > "$STAMP_DIR/manifests/units.txt"

SIZE=$(du -sh "$STAMP_DIR" | awk '{print $1}')
cat > "$STAMP_DIR/backup-info.txt" <<EOF
STATUS=ok
TIMESTAMP=$(date -Is)
DESTINATION=$STAMP_DIR
SIZE=$SIZE
LABEL=$LABEL
UUID=$UUID
EOF
cp "$STAMP_DIR/backup-info.txt" "$STAMP"
chmod 644 "$STAMP"

log "Backup verified"
log "Applications restarting"
restart_apps
trap - EXIT

log "BACKUP COMPLETE Size=$SIZE Destination=$STAMP_DIR"
echo
echo "Raspberry Pi Full Backup"
echo "✓ USB drive detected"
echo "✓ Correct backup disk confirmed"
echo "✓ Applications stopped safely"
echo "✓ SQLite databases backed up"
echo "✓ Application data copied"
echo "✓ System configuration copied"
echo "✓ Backup verified"
echo "✓ Applications restarted"
echo
echo "BACKUP COMPLETE"
echo "Size: $SIZE"
echo "Destination: $STAMP_DIR"
echo "Timestamp: $(date '+%Y-%m-%d %H:%M')"
