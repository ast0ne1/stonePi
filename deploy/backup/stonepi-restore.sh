#!/bin/bash
set -euo pipefail

# Restore application data and configuration from a StonePi USB backup.
# This is the granular restore. For a dead SD card, flash Raspberry Pi OS,
# run deploy/install.sh, then this script.

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo stonepi-restore /mnt/stonepi-backup/RaspberryPi-Backup/TIMESTAMP"
  exit 1
fi

SRC="${1:-}"
if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  echo "Usage: sudo stonepi-restore /path/to/RaspberryPi-Backup/TIMESTAMP"
  exit 1
fi

APPS="stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-dashboard stonepi-auth"
systemctl stop $APPS || true

if [[ -d "$SRC/applications" ]]; then
  rsync -a "$SRC/applications/" /var/lib/stonepi/
fi
if [[ -d "$SRC/config/stonepi" ]]; then
  rsync -a "$SRC/config/stonepi/" /etc/stonepi/
fi
if [[ -f "$SRC/config/nginx-stonepi" ]]; then
  cp "$SRC/config/nginx-stonepi" /etc/nginx/sites-available/stonepi
fi

chown -R stonepi-auth:stonepi-auth /var/lib/stonepi/auth
chown -R stonepi-dash:stonepi-dash /var/lib/stonepi/dashboard
chown -R stonepi-news:stonepi-news /var/lib/stonepi/newscast
chown -R stonepi-files:stonepi-files /var/lib/stonepi/fileserve
chown -R stonepi-events:stonepi-events /var/lib/stonepi/eventtrakr
chown -R stonepi-pin:stonepi-pin /var/lib/stonepi/pinboard
chown -R stonepi-studio:stonepi-studio /var/lib/stonepi/studio
if [[ -d /var/lib/stonepi/vault ]]; then
  groupadd --system stonepi-vault 2>/dev/null || true
  for u in stonepi-dash stonepi-auth stonepi-news stonepi-files stonepi-events stonepi-pin stonepi-studio; do
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
