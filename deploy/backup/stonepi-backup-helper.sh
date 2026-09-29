#!/bin/bash
# Privileged helper for Dashboard backup/restore actions.
# Usage:
#   stonepi-backup-helper list
#   stonepi-backup-helper drill [BACKUP_DIR]
#   stonepi-backup-helper restore BACKUP_DIR
#   stonepi-backup-helper drill-status
#   stonepi-backup-helper backup-now local|usb
#   stonepi-backup-helper local-schedule
#   stonepi-backup-helper local-schedule-status
#   stonepi-backup-helper recover-passwd-set PASSWORD
#   stonepi-backup-helper recover-passwd-clear
#   stonepi-backup-helper failover-on|failover-off|failover-status
set -euo pipefail

MOUNT="/mnt/stonepi-backup"
LABEL="STONEPI-BACKUP"
LOCAL_ROOT="/var/backups/stonepi"
CONF="/etc/stonepi/backup.conf"
FAILOVER_FLAG="/var/lib/stonepi/failover"
NGINX_FAILOVER="/etc/nginx/snippets/stonepi-failover.conf"
TIMER_UNIT="/etc/systemd/system/stonepi-local-backup.timer"

ensure_mount() {
  mkdir -p "$MOUNT"
  if findmnt "$MOUNT" >/dev/null 2>&1; then
    return 0
  fi
  if blkid -L "$LABEL" >/dev/null 2>&1; then
    mount "LABEL=$LABEL" "$MOUNT"
  fi
}

load_conf() {
  LOCAL_ENABLED="${LOCAL_ENABLED:-0}"
  LOCAL_CADENCE="${LOCAL_CADENCE:-weekly}"
  LOCAL_WEEKDAY="${LOCAL_WEEKDAY:-Sun}"
  LOCAL_TIME="${LOCAL_TIME:-03:30}"
  LOCAL_ROOT="${LOCAL_ROOT:-/var/backups/stonepi}"
  if [[ -f "$CONF" ]]; then
    # shellcheck disable=SC1090
    source "$CONF"
  fi
}

cmd="${1:-}"
case "$cmd" in
  list)
    ensure_mount || true
    load_conf
    LOCAL_ROOT="${LOCAL_ROOT:-/var/backups/stonepi}"
    export LOCAL_ROOT
    python3 - <<'PY'
import json, os
from pathlib import Path

rows = []

def add_dir(p: Path, kind: str, name=None):
    if not p.is_dir():
        return
    info = p / "backup-info.txt"
    meta = {
        "path": str(p),
        "name": name or p.name,
        "kind": kind,
        "status": "unknown",
        "size": "",
        "timestamp": "",
    }
    if info.is_file():
        for line in info.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                meta[k.strip().lower()] = v.strip()
    meta["kind"] = kind
    rows.append(meta)

local_root = Path(os.environ.get("LOCAL_ROOT", "/var/backups/stonepi"))
add_dir(local_root / "current", "local", "local-current")

usb_root = Path("/mnt/stonepi-backup/RaspberryPi-Backup")
if usb_root.is_dir():
    for p in sorted(usb_root.iterdir(), reverse=True):
        if p.is_dir():
            add_dir(p, "usb")

def sort_key(row):
    return row.get("timestamp") or row.get("name") or ""

rows.sort(key=sort_key, reverse=True)
print(json.dumps(rows[:20]))
PY
    ;;
  drill)
    shift || true
    /usr/local/sbin/stonepi-restore-drill "$@"
    ;;
  drill-status)
    if [[ -f /var/lib/stonepi/last-restore-drill.txt ]]; then
      cat /var/lib/stonepi/last-restore-drill.txt
    else
      echo "STATUS=none"
    fi
    ;;
  restore)
    SRC="${2:-}"
    if [[ -z "$SRC" || ! -d "$SRC" ]]; then
      echo "Usage: stonepi-backup-helper restore /path/to/backup" >&2
      exit 1
    fi
    # Safety: local current or USB tree only
    case "$SRC" in
      /var/backups/stonepi/current|/var/backups/stonepi/current/) ;;
      /mnt/stonepi-backup/RaspberryPi-Backup/*) ;;
      *)
        echo "Refusing restore outside local current or USB backup tree" >&2
        exit 1
        ;;
    esac
    /usr/local/sbin/stonepi-restore "$SRC"
    ;;
  backup-now)
    KIND="${2:-local}"
    case "$KIND" in
      local)
        systemctl start stonepi-local-backup.service
        echo "started=stonepi-local-backup"
        ;;
      usb)
        systemctl start stonepi-backup.service
        echo "started=stonepi-backup"
        ;;
      *)
        echo "Usage: stonepi-backup-helper backup-now local|usb" >&2
        exit 1
        ;;
    esac
    ;;
  local-schedule)
    load_conf
    # Persist schedule knobs from env if provided by Dashboard via helper args.
    # Usage: local-schedule [enabled] [cadence] [time] [weekday]
    if [[ $# -ge 2 ]]; then
      LOCAL_ENABLED="${2}"
      LOCAL_CADENCE="${3:-$LOCAL_CADENCE}"
      LOCAL_TIME="${4:-$LOCAL_TIME}"
      LOCAL_WEEKDAY="${5:-$LOCAL_WEEKDAY}"
      mkdir -p "$(dirname "$CONF")"
      if [[ -f "$CONF" ]]; then
        # shellcheck disable=SC1090
        source "$CONF"
      fi
      # Re-apply overrides after source
      LOCAL_ENABLED="${2}"
      LOCAL_CADENCE="${3:-weekly}"
      LOCAL_TIME="${4:-03:30}"
      LOCAL_WEEKDAY="${5:-Sun}"
      LOCAL_ROOT="${LOCAL_ROOT:-/var/backups/stonepi}"
      LABEL="${LABEL:-STONEPI-BACKUP}"
      UUID="${UUID:-}"
      APPS="${APPS:-stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch}"
      cat > "$CONF" <<EOF
LABEL=$LABEL
UUID=$UUID
APPS="$APPS"
LOCAL_ENABLED=$LOCAL_ENABLED
LOCAL_CADENCE=$LOCAL_CADENCE
LOCAL_WEEKDAY=$LOCAL_WEEKDAY
LOCAL_TIME=$LOCAL_TIME
LOCAL_ROOT=$LOCAL_ROOT
EOF
      chmod 644 "$CONF"
    fi
    load_conf
    hour="${LOCAL_TIME%%:*}"
    minute="${LOCAL_TIME##*:}"
    hour="${hour#0}"
    minute="${minute#0}"
    hour="${hour:-0}"
    minute="${minute:-0}"
    if [[ "${LOCAL_CADENCE}" == "daily" ]]; then
      calendar="*-*-* ${hour}:${minute}:00"
    else
      wd="${LOCAL_WEEKDAY:-Sun}"
      calendar="${wd} *-*-* ${hour}:${minute}:00"
    fi
    cat > "$TIMER_UNIT" <<EOF
[Unit]
Description=Scheduled StonePi local backup

[Timer]
Unit=stonepi-local-backup.service
OnCalendar=${calendar}
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    if [[ "${LOCAL_ENABLED}" == "1" || "${LOCAL_ENABLED}" == "true" || "${LOCAL_ENABLED}" == "yes" ]]; then
      # enable + start timer only (avoid Requires=oneshot deactivating the timer;
      # avoid enable --now surprise Persistent catch-up on Save).
      systemctl enable stonepi-local-backup.timer
      systemctl start stonepi-local-backup.timer
      echo "local-schedule=enabled calendar=${calendar}"
    else
      systemctl disable --now stonepi-local-backup.timer >/dev/null 2>&1 || true
      echo "local-schedule=disabled"
    fi
    ;;
  recover-passwd-set|recovery-passwd-set)
    PW="${2:-}"
    if [[ -z "$PW" ]]; then
      echo "Usage: stonepi-backup-helper recover-passwd-set PASSWORD" >&2
      exit 1
    fi
    mkdir -p /etc/stonepi
    printf 'stonepi:%s\n' "$PW" > /etc/stonepi/recover.passwd
    chmod 600 /etc/stonepi/recover.passwd
    rm -f /etc/stonepi/recovery.passwd
    echo "recover-passwd=set"
    ;;
  recover-passwd-clear|recovery-passwd-clear)
    rm -f /etc/stonepi/recover.passwd /etc/stonepi/recovery.passwd
    echo "recover-passwd=cleared"
    ;;
  local-schedule-status)
    load_conf
    enabled=0
    if systemctl is-enabled --quiet stonepi-local-backup.timer 2>/dev/null; then
      enabled=1
    fi
    next=""
    if systemctl list-timers stonepi-local-backup.timer --no-pager 2>/dev/null | awk 'NR==2{print $1" "$2}' | grep -qv '^$'; then
      next="$(systemctl list-timers stonepi-local-backup.timer --no-legend 2>/dev/null | awk '{print $1,$2,$3,$4}' | head -n1)"
    fi
    python3 - <<PY
import json
print(json.dumps({
  "enabled": ${enabled},
  "conf_enabled": "${LOCAL_ENABLED}",
  "cadence": "${LOCAL_CADENCE}",
  "weekday": "${LOCAL_WEEKDAY}",
  "time": "${LOCAL_TIME}",
  "root": "${LOCAL_ROOT}",
  "next": """${next}""".strip(),
}))
PY
    ;;
  failover-on)
    mkdir -p /var/lib/stonepi /etc/nginx/snippets
    printf 'on\n' > "$FAILOVER_FLAG"
    cat > "$NGINX_FAILOVER" <<'EOF'
# StonePi failover mode — / goes to recover when dashboard stack is unhealthy.
location = / {
    return 302 /recover/;
}
EOF
    nginx -t && systemctl reload nginx
    systemctl start stonepi-recover || true
    echo "failover=on"
    ;;
  failover-off)
    printf 'off\n' > "$FAILOVER_FLAG"
    mkdir -p /etc/nginx/snippets
    echo "# failover off" > "$NGINX_FAILOVER"
    nginx -t && systemctl reload nginx
    echo "failover=off"
    ;;
  failover-status)
    if [[ -f "$FAILOVER_FLAG" ]] && grep -qi '^on' "$FAILOVER_FLAG"; then
      echo "failover=on"
    else
      echo "failover=off"
    fi
    ;;
  *)
    echo "Usage: stonepi-backup-helper {list|drill|drill-status|restore|backup-now|local-schedule|local-schedule-status|recover-passwd-set|recover-passwd-clear|failover-on|failover-off|failover-status}" >&2
    exit 1
    ;;
esac
