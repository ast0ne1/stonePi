#!/bin/bash
set -euo pipefail

# Restore application data and configuration from a StonePi backup
# (local /var/backups/stonepi/current or USB RaspberryPi-Backup/TIMESTAMP).
# This is the granular restore. For a dead SD card, flash Raspberry Pi OS,
# run deploy/install.sh, then this script.

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo stonepi-restore /var/backups/stonepi/current" >&2
  echo "         or: sudo stonepi-restore /mnt/stonepi-backup/RaspberryPi-Backup/TIMESTAMP" >&2
  exit 1
fi

SRC="${1:-}"
if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  echo "Usage: sudo stonepi-restore /path/to/backup" >&2
  exit 1
fi

APPS="stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch"
systemctl stop $APPS || true

if [[ -d "$SRC/applications" ]]; then
  rsync -a "$SRC/applications/" /var/lib/stonepi/
fi
if [[ -d "$SRC/config/stonepi" ]]; then
  rsync -a "$SRC/config/stonepi/" /etc/stonepi/
fi
if [[ -f "$SRC/config/nginx-stonepi" ]]; then
  if grep -qE 'stonepi_(notifications|recovery)\b' "$SRC/config/nginx-stonepi" && [[ -f /opt/stonepi/deploy/nginx/stonepi.conf ]]; then
    # Pre-rename backup: its site routes to the old notifications/recovery paths.
    cp /opt/stonepi/deploy/nginx/stonepi.conf /etc/nginx/sites-available/stonepi
  else
    cp "$SRC/config/nginx-stonepi" /etc/nginx/sites-available/stonepi
  fi
fi
# Pre-rename backups restore notifications/, notifications.env, recovery.passwd — move them forward.
if [[ -f /opt/stonepi/deploy/stonepi-migrate-renames.sh ]]; then
  bash /opt/stonepi/deploy/stonepi-migrate-renames.sh /opt/stonepi
fi

chown -R stonepi-auth:stonepi-auth /var/lib/stonepi/auth
chown -R stonepi-dash:stonepi-dash /var/lib/stonepi/dashboard
[[ -d /var/lib/stonepi/notify ]] && chown -R stonepi-notify:stonepi-notify /var/lib/stonepi/notify
chown -R stonepi-news:stonepi-news /var/lib/stonepi/newscast
chown -R stonepi-files:stonepi-files /var/lib/stonepi/fileserve
chown -R stonepi-events:stonepi-events /var/lib/stonepi/eventtrakr
chown -R stonepi-pin:stonepi-pin /var/lib/stonepi/pinboard
chown -R stonepi-studio:stonepi-studio /var/lib/stonepi/studio
[[ -d /var/lib/stonepi/pricescout ]] && chown -R stonepi-prices:stonepi-prices /var/lib/stonepi/pricescout
[[ -d /var/lib/stonepi/sportguide ]] && chown -R stonepi-sport:stonepi-sport /var/lib/stonepi/sportguide
[[ -d /var/lib/stonepi/pricewatch ]] && chown -R stonepi-watch:stonepi-watch /var/lib/stonepi/pricewatch
if [[ -d /var/lib/stonepi/vault ]]; then
  groupadd --system stonepi-vault 2>/dev/null || true
  for u in stonepi-dash stonepi-auth stonepi-notify stonepi-news stonepi-files stonepi-events stonepi-pin stonepi-studio stonepi-prices stonepi-sport stonepi-watch; do
    id -u "$u" >/dev/null 2>&1 && usermod -aG stonepi-vault "$u" || true
  done
  chown -R stonepi-dash:stonepi-vault /var/lib/stonepi/vault
  chmod 750 /var/lib/stonepi/vault
  chmod g+s /var/lib/stonepi/vault
  find /var/lib/stonepi/vault -type f -exec chmod 640 {} \;
fi

systemctl start $APPS
nginx -t && systemctl reload nginx
echo "Restore complete. Reboot if hostname or boot files were changed."
echo "Verify Destinations: open /notify/settings?tab=destinations (ntfy / TRMNL)."
