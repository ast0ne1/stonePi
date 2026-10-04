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
  echo "Usage: sudo stonepi-restore /path/to/backup [--no-library-content]" >&2
  exit 1
fi
LIBRARY_CONTENT=1
[[ "${2:-}" == "--no-library-content" ]] && LIBRARY_CONTENT=0

APPS="stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch stonepi-library"
systemctl stop $APPS stonepi-kiwix 2>/dev/null || true

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
if [[ -d /var/lib/stonepi/library ]] && id -u stonepi-library >/dev/null 2>&1; then
  chown -R stonepi-library:stonepi-library /var/lib/stonepi/library
  chmod 2750 /var/lib/stonepi/library
fi
if [[ -d /var/lib/stonepi/vault ]]; then
  groupadd --system stonepi-vault 2>/dev/null || true
  for u in stonepi-dash stonepi-auth stonepi-notify stonepi-news stonepi-files stonepi-events stonepi-pin stonepi-studio stonepi-prices stonepi-sport stonepi-watch stonepi-library; do
    id -u "$u" >/dev/null 2>&1 && usermod -aG stonepi-vault "$u" || true
  done
  # Same as install.sh: every service user reads and saves secrets (setgid group folder).
  chown -R stonepi-dash:stonepi-vault /var/lib/stonepi/vault
  chmod 2770 /var/lib/stonepi/vault
  find /var/lib/stonepi/vault -type f -exec chmod 660 {} \;
fi

# ---------- Library: reader, drive mount, content ----------
if [[ -f /var/lib/stonepi/library/library.sqlite ]]; then
  if ! command -v kiwix-serve >/dev/null 2>&1; then
    if compgen -G "$SRC/packages/kiwix/*.deb" >/dev/null; then
      echo "Installing Kiwix from the backup's packages"
      apt-get install -y -qq "$SRC"/packages/kiwix/*.deb >/dev/null 2>&1 || apt-get install -y -qq kiwix-tools || true
    else
      apt-get install -y -qq kiwix-tools || echo "Kiwix not installed — run Set up in the Library."
    fi
  fi
  if [[ -d "$SRC/config/systemd/stonepi-kiwix.service.d" ]]; then
    mkdir -p /etc/systemd/system/stonepi-kiwix.service.d
    rsync -a "$SRC/config/systemd/stonepi-kiwix.service.d/" /etc/systemd/system/stonepi-kiwix.service.d/
  fi
  # Library drive mount (only if that drive is plugged in and fstab lacks it).
  if [[ -s "$SRC/manifests/fstab-library.txt" ]]; then
    while read -r line; do
      uuid="$(awk '{print $1}' <<<"$line")"; uuid="${uuid#UUID=}"
      mp="$(awk '{print $2}' <<<"$line")"
      if blkid -U "$uuid" >/dev/null 2>&1 && ! grep -q "UUID=$uuid " /etc/fstab; then
        # exFAT lines carry the Library's numeric uid/gid from the old Pi;
        # on this one stonepi-library may have other ids.
        if id -u stonepi-library >/dev/null 2>&1; then
          line="$(sed -E "s/uid=[0-9]+/uid=$(id -u stonepi-library)/; s/gid=[0-9]+/gid=$(id -g stonepi-library)/" <<<"$line")"
        fi
        echo "$line" >> /etc/fstab
        mkdir -p "$mp"
      fi
    done < "$SRC/manifests/fstab-library.txt"
    systemctl daemon-reload
    mount -a 2>/dev/null || true
  fi
  # Content: USB backups keep ZIMs in a shared store beside the timestamped runs.
  STORE="$(dirname "$SRC")/library-content"
  MANIFEST=/var/lib/stonepi/library/backup-manifest.json
  if [[ "$LIBRARY_CONTENT" == "1" && -f "$STORE/manifest.json" && -f "$MANIFEST" && -x /usr/local/sbin/stonepi-backup-library ]]; then
    # The manifest was written by the Library app, so the helper reads and
    # checks content_dir itself (allowed folders only, falls back to the
    # microSD when the drive is missing) and sets ownership file by file.
    echo "Restoring library content (this can take a while)"
    result="$(/usr/local/sbin/stonepi-backup-library restore "$STORE" "$MANIFEST" /var/log/stonepi-backup.log || true)"
    read -r status _bytes target <<<"$result"
    echo "Library content restore: ${status:-failed} → ${target:-/var/lib/stonepi/library/zim}"
  fi
  # The Library app reconciles on start (finds moved files, restarts the reader).
fi

systemctl start $APPS
nginx -t && systemctl reload nginx
echo "Restore complete. Reboot if hostname or boot files were changed."
echo "Verify Destinations: open /notify/settings?tab=destinations (ntfy / TRMNL)."
