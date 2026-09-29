#!/bin/bash
# Idempotent on-Pi migration for renamed platform services:
#   notifications -> notify   (stonepi-notifications -> stonepi-notify)
#   recovery      -> recover  (stonepi-recovery      -> stonepi-recover)
#   stonepi_outputs -> stonepi_notify (shared Python package)
#
# A service is migrated only once its new code is on disk ($DEST/apps/<new>/app),
# so overlays that ship just one of the two apps never strand the other.
# Every step is a no-op once migrated; callers run it after syncing code and
# before building venvs / seeding passwords / restarting units.
#
# Usage: sudo bash stonepi-migrate-renames.sh [/opt/stonepi]
set -euo pipefail

DEST="${1:-/opt/stonepi}"
DEST="${DEST%/}"
DATA=/var/lib/stonepi
CONF=/etc/stonepi
SYSTEMD=/etc/systemd/system
NGINX_FAILOVER=/etc/nginx/snippets/stonepi-failover.conf
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[migrate-renames] $*"; }

# Old unit -> new unit. Installs the new unit file when missing and keeps the
# service running if the old one was enabled, so a caller that forgets to
# start it does not leave the service down.
swap_unit() {
  local old="$1" new="$2" was_enabled=0 unit_src=""
  [[ -f "$SYSTEMD/stonepi-$old.service" ]] || return 0
  log "retiring stonepi-$old.service (replaced by stonepi-$new.service)"
  systemctl is-enabled --quiet "stonepi-$old.service" 2>/dev/null && was_enabled=1
  systemctl disable --now "stonepi-$old.service" >/dev/null 2>&1 || true
  rm -f "$SYSTEMD/stonepi-$old.service"
  systemctl reset-failed "stonepi-$old.service" >/dev/null 2>&1 || true
  if [[ ! -f "$SYSTEMD/stonepi-$new.service" ]]; then
    for candidate in "$SCRIPT_DIR/systemd/stonepi-$new.service" "$DEST/deploy/systemd/stonepi-$new.service"; do
      if [[ -f "$candidate" ]]; then
        unit_src="$candidate"
        break
      fi
    done
    [[ -n "$unit_src" ]] && install -m 644 "$unit_src" "$SYSTEMD/stonepi-$new.service"
  fi
  systemctl daemon-reload
  if [[ "$was_enabled" -eq 1 && -f "$SYSTEMD/stonepi-$new.service" ]]; then
    PENDING_START+=("stonepi-$new.service")
  fi
}

# Code dir: keep the venv (rewrite its absolute paths), carry local data, drop the old tree.
move_code() {
  local old_dir="$DEST/apps/$1" new_dir="$DEST/apps/$2"
  [[ -d "$old_dir" ]] || return 0
  log "moving $old_dir -> $new_dir"
  if [[ -d "$old_dir/.venv" && ! -d "$new_dir/.venv" ]]; then
    mv "$old_dir/.venv" "$new_dir/.venv"
    grep -rlI --null "$old_dir/.venv" "$new_dir/.venv/bin" "$new_dir/.venv/pyvenv.cfg" 2>/dev/null \
      | xargs -0 -r sed -i "s|$old_dir/.venv|$new_dir/.venv|g"
  fi
  if [[ -d "$old_dir/data" ]]; then
    mkdir -p "$new_dir/data"
    cp -a "$old_dir/data/." "$new_dir/data/"
    chown --reference="$old_dir/data" "$new_dir/data" 2>/dev/null || true
  fi
  rm -rf "$old_dir"
}

# Runtime data. Old wins, so restoring a pre-rename backup lands in the new dir.
move_data() {
  local old_dir="$DATA/$1" new_dir="$DATA/$2"
  [[ -d "$old_dir" && ! -L "$old_dir" ]] || return 0
  log "moving $old_dir -> $new_dir"
  mkdir -p "$new_dir"
  cp -a "$old_dir/." "$new_dir/"
  chown --reference="$old_dir" "$new_dir" 2>/dev/null || true
  rm -rf "$old_dir"
}

# Per-app env file. Keep the new file if both exist, adding keys only the old one had.
move_env() {
  local old="$CONF/$1.env" new="$CONF/$2.env" line key
  [[ -f "$old" ]] || return 0
  if [[ ! -f "$new" ]]; then
    log "renaming $old -> $new"
    mv "$old" "$new"
  else
    log "merging $old into $new"
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" == *=* && "$line" != \#* ]] || continue
      key="${line%%=*}"
      grep -q "^${key}=" "$new" || echo "$line" >> "$new"
    done < "$old"
    rm -f "$old"
  fi
  chmod 640 "$new" 2>/dev/null || true
}

# sed expressions over every env file (emitters carry the Notify URL too).
rewrite_env_files() {
  local pattern="$1"
  shift
  local envf
  for envf in "$CONF"/*.env; do
    [[ -f "$envf" ]] || continue
    if grep -qE "$pattern" "$envf"; then
      log "updating identity keys in $envf"
      sed -i "$@" "$envf"
    fi
  done
}

rename_backup_unit() {
  local old="$1" new="$2"
  if [[ -f "$CONF/backup.conf" ]] && grep -qE "stonepi-$old\\b" "$CONF/backup.conf"; then
    log "backup.conf: stonepi-$old -> stonepi-$new"
    if grep -qE "stonepi-$new\\b" "$CONF/backup.conf"; then
      sed -i -E "s/ ?stonepi-$old\\b//g" "$CONF/backup.conf"
    else
      sed -i -E "s/stonepi-$old\\b/stonepi-$new/g" "$CONF/backup.conf"
    fi
  fi
}

migrate_notify() {
  [[ -d "$DEST/apps/notify/app" ]] || { log "notify: new code not present — skipped"; return 0; }
  swap_unit notifications notify
  move_code notifications notify
  move_data notifications notify
  move_env notifications notify
  rewrite_env_files '/var/lib/stonepi/notifications|^STONEPI_PREFIX=/notifications$|^STONEPI_APP_ID=notifications$|^STONEPI_NOTIFICATIONS_(URL|PORT)=' \
    -e 's|/var/lib/stonepi/notifications|/var/lib/stonepi/notify|g' \
    -e 's|^STONEPI_PREFIX=/notifications$|STONEPI_PREFIX=/notify|' \
    -e 's|^STONEPI_APP_ID=notifications$|STONEPI_APP_ID=notify|' \
    -e 's|^STONEPI_NOTIFICATIONS_URL=|STONEPI_NOTIFY_URL=|' \
    -e 's|^STONEPI_NOTIFICATIONS_PORT=|STONEPI_NOTIFY_PORT=|'
  rename_backup_unit notifications notify
}

migrate_recover() {
  [[ -d "$DEST/apps/recover/app" ]] || { log "recover: new code not present — skipped"; return 0; }
  swap_unit recovery recover
  move_code recovery recover
  move_data recovery recover
  move_env recovery recover
  rewrite_env_files '^STONEPI_RECOVERY_PASS(WORD|WD)=|/etc/stonepi/recovery\.passwd' \
    -e 's|^STONEPI_RECOVERY_PASSWORD=|STONEPI_RECOVER_PASSWORD=|' \
    -e 's|^STONEPI_RECOVERY_PASSWD=|STONEPI_RECOVER_PASSWD=|' \
    -e 's|/etc/stonepi/recovery\.passwd|/etc/stonepi/recover.passwd|g'
  rename_backup_unit recovery recover

  # Console password file.
  if [[ -f "$CONF/recovery.passwd" ]]; then
    if [[ ! -f "$CONF/recover.passwd" ]]; then
      log "renaming $CONF/recovery.passwd -> $CONF/recover.passwd"
      mv "$CONF/recovery.passwd" "$CONF/recover.passwd"
    else
      rm -f "$CONF/recovery.passwd"
    fi
    chmod 600 "$CONF/recover.passwd"
  fi

  # Vault key STONEPI_RECOVERY_PASSWORD -> STONEPI_RECOVER_PASSWORD (Recover also does this on startup).
  if [[ -d "$DATA/vault" ]]; then
    local vault_py="" candidate
    for candidate in "$DEST/apps/dashboard/.venv/bin/python" "$DEST/apps/recover/.venv/bin/python"; do
      if [[ -x "$candidate" ]] && "$candidate" -c 'import stonepi_vault' >/dev/null 2>&1; then
        vault_py="$candidate"
        break
      fi
    done
    if [[ -n "$vault_py" ]]; then
      STONEPI_VAULT_DIR="$DATA/vault" "$vault_py" - "$DATA/vault" <<'PY' || log "WARN: vault key migration failed (Recover retries on startup)"
import sys
from stonepi_vault import configure

vault = configure(sys.argv[1])
old, new = "STONEPI_RECOVERY_PASSWORD", "STONEPI_RECOVER_PASSWORD"
legacy = (vault.get(old, "") or "").strip()
if legacy:
    if not (vault.get(new, "") or "").strip():
        vault.set(new, legacy)
    vault.delete(old)
    print(f"[migrate-renames] vault: {old} -> {new}")
PY
      chown -R stonepi-dash:stonepi-vault "$DATA/vault" 2>/dev/null || true
      find "$DATA/vault" -type f -exec chmod 640 {} \; 2>/dev/null || true
    fi
  fi

  # Active failover snippet written by an older stonepi-backup-helper.
  if [[ -f "$NGINX_FAILOVER" ]] && grep -q '/recovery/' "$NGINX_FAILOVER"; then
    log "updating $NGINX_FAILOVER"
    sed -i 's|/recovery/|/recover/|g; s|goes to recovery|goes to recover|' "$NGINX_FAILOVER"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  fi
}

# Shared package stonepi_outputs -> stonepi_notify. Uninstall the old editable
# install from every app venv (smoke-all installed it widely) and drop its tree;
# callers pip-install stonepi_notify afterwards.
migrate_notify_package() {
  [[ -d "$DEST/packages/stonepi_notify" ]] || { log "stonepi_notify: new package not present — skipped"; return 0; }
  local venv
  for venv in "$DEST"/apps/*/.venv; do
    [[ -x "$venv/bin/pip" ]] || continue
    if "$venv/bin/pip" show stonepi-outputs >/dev/null 2>&1; then
      log "uninstalling stonepi-outputs from $venv"
      "$venv/bin/pip" uninstall -y stonepi-outputs >/dev/null 2>&1 || log "WARN: could not uninstall stonepi-outputs from $venv"
    fi
  done
  if [[ -d "$DEST/packages/stonepi_outputs" ]]; then
    log "removing $DEST/packages/stonepi_outputs"
    rm -rf "$DEST/packages/stonepi_outputs"
  fi
}

PENDING_START=()
migrate_notify
migrate_recover
migrate_notify_package

for unit in "${PENDING_START[@]}"; do
  log "enabling $unit"
  systemctl enable --now "$unit" >/dev/null 2>&1 || log "WARN: could not start $unit yet (caller restarts it)"
done

log "done"
