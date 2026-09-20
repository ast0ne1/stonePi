#!/bin/bash
set -euo pipefail

DEST="/opt/stonepi"
DATA="/var/lib/stonepi"
CONF="/etc/stonepi"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
HOSTNAME_VALUE="stonepi"

usage() {
  echo "Usage: sudo bash deploy/install.sh [--hostname NAME]"
  echo "Fresh Pi OS walkthrough: deploy/INSTALL.md"
}

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install.sh"
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname) HOSTNAME_VALUE="${2:-stonepi}"; shift 2 ;;
    --hostname=*) HOSTNAME_VALUE="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option $1"; usage; exit 1 ;;
  esac
done

if [[ ! -d "$SRC/apps" || ! -d "$SRC/deploy" || ! -d "$SRC/packages" ]]; then
  echo "This does not look like a StonePi tree: $SRC"
  echo "Expected apps/, deploy/, and packages/ under the project root."
  exit 1
fi

# Copies from a Windows FAT boot partition often have CRLF line endings.
normalize_scripts() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  find "$dir" -type f \( -name '*.sh' -o -name 'install.sh' \) -print0 2>/dev/null \
    | xargs -0 -r sed -i 's/\r$//' || true
}
normalize_scripts "$SRC/deploy"
normalize_scripts "$SRC/scripts"
normalize_scripts "$SRC"
sed -i 's/\r$//' "$0" 2>/dev/null || true

if [[ ! -f /etc/os-release ]] || ! grep -qi "raspberry\|debian\|ubuntu" /etc/os-release; then
  echo "This installer targets Raspberry Pi OS / Debian."
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync nginx avahi-daemon avahi-utils sqlite3 cockpit \
  libnss-mdns fonts-liberation curl \
  libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 \
  libpangocairo-1.0-0 libpango-1.0-0 || apt-get install -y python3 python3-venv python3-pip rsync nginx \
  avahi-daemon avahi-utils sqlite3 cockpit libnss-mdns fonts-liberation curl \
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 libpango-1.0-0

# Avoid stock welcome page if apt starts nginx before StonePi site is installed.
rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/default.conf 2>/dev/null || true

ensure_user() {
  local user="$1"
  if ! id -u "$user" >/dev/null 2>&1; then
    useradd --system --home "$DEST" --shell /usr/sbin/nologin "$user"
  fi
}

ensure_user stonepi-auth
ensure_user stonepi-dash
ensure_user stonepi-news
ensure_user stonepi-files
ensure_user stonepi-events
ensure_user stonepi-pin
ensure_user stonepi-studio
ensure_user stonepi-prices
ensure_user stonepi-sport
ensure_user stonepi-sport

mkdir -p "$DEST" "$DATA/auth" "$DATA/newscast" "$DATA/fileserve/hosted" "$DATA/eventtrakr" "$DATA/pinboard" "$DATA/studio" "$DATA/pricescout" "$DATA/sportguide" "$DATA/dashboard" "$DATA/vault" "$CONF"
if [[ ! -f "$DATA/exposure" ]]; then
  printf 'lan\n' > "$DATA/exposure"
  chmod 644 "$DATA/exposure"
fi


if [[ "$SRC" != "$DEST" ]]; then
  rsync -a \
    --exclude '.venv' \
    --exclude '/data' \
    --exclude 'apps/*/data' \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '.git' \
    --exclude 'dist' \
    --exclude 'deploy/packages' \
    "$SRC/" "$DEST/"
fi

if [[ ! -f "$DEST/deploy/nginx/stonepi-public-deny-display.conf" ]]; then
  echo "Missing $DEST/deploy/nginx/stonepi-public-deny-display.conf — aborting (Display scrape must stay private)."
  exit 1
fi

if [[ ! -f "$CONF/stonepi.env" ]]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$CONF/stonepi.env" <<EOF
HOST=127.0.0.1
SESSION_SECRET=$SECRET
STONEPI_SESSION_SECRET=$SECRET
STONEPI_VAULT_DIR=/var/lib/stonepi/vault
STONEPI_EXPOSURE=lan
STONEPI_EXPOSURE_FILE=/var/lib/stonepi/exposure
STONEPI_HOSTNAME=$HOSTNAME_VALUE
STONEPI_AUTH_URL=http://127.0.0.1:8011
STONEPI_PUBLIC_ORIGIN=http://${HOSTNAME_VALUE}.local
PUBLIC_ORIGIN=http://${HOSTNAME_VALUE}.local
AUTH_URL=http://127.0.0.1:8011
COCKPIT_URL=https://${HOSTNAME_VALUE}.local:9090
ROUTING=path
BACKUP_STAMP=$DATA/last-backup.txt
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin
EOF
  chmod 640 "$CONF/stonepi.env"
fi
# Upgrade path: ensure exposure file + env keys exist without rewriting secrets.
if ! grep -q '^STONEPI_EXPOSURE=' "$CONF/stonepi.env" 2>/dev/null; then
  echo 'STONEPI_EXPOSURE=lan' >> "$CONF/stonepi.env"
fi
if ! grep -q '^STONEPI_EXPOSURE_FILE=' "$CONF/stonepi.env" 2>/dev/null; then
  echo 'STONEPI_EXPOSURE_FILE=/var/lib/stonepi/exposure' >> "$CONF/stonepi.env"
fi
if ! grep -q '^STONEPI_VAULT_DIR=' "$CONF/stonepi.env" 2>/dev/null; then
  echo 'STONEPI_VAULT_DIR=/var/lib/stonepi/vault' >> "$CONF/stonepi.env"
fi
# Cockpit serves HTTPS on :9090 — upgrade legacy http://…:9090 links.
if grep -q '^COCKPIT_URL=http://' "$CONF/stonepi.env" 2>/dev/null; then
  sed -i 's|^COCKPIT_URL=http://|COCKPIT_URL=https://|' "$CONF/stonepi.env"
fi
if [[ -f "$CONF/dashboard.env" ]] && grep -q '^COCKPIT_URL=http://' "$CONF/dashboard.env" 2>/dev/null; then
  sed -i 's|^COCKPIT_URL=http://|COCKPIT_URL=https://|' "$CONF/dashboard.env"
fi
if [[ ! -f "$DATA/exposure" ]]; then
  printf 'lan\n' > "$DATA/exposure"
  chmod 644 "$DATA/exposure"
fi

ensure_env_key() {
  local file="$1" key="$2" value="$3"
  [[ -f "$file" ]] || return 0
  if ! grep -q "^${key}=" "$file" 2>/dev/null; then
    echo "${key}=${value}" >> "$file"
  fi
}

write_env() {
  local file="$1"
  if [[ -f "$file" ]]; then
    return
  fi
  cat > "$file"
  chmod 640 "$file"
}

write_env "$CONF/auth.env" <<EOF
PORT=8011
DATABASE_URL=sqlite:////var/lib/stonepi/auth/users.sqlite
STONEPI_DATA_DIR=/var/lib/stonepi/auth
STONEPI_PREFIX=/auth
EOF

write_env "$CONF/dashboard.env" <<EOF
PORT=8010
STONEPI_DATA_DIR=/var/lib/stonepi/dashboard
STONEPI_PREFIX=
EOF

write_env "$CONF/newscast.env" <<EOF
PORT=8001
DATABASE_URL=sqlite:////var/lib/stonepi/newscast/newscast.sqlite
STONEPI_DATA_DIR=/var/lib/stonepi/newscast
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/news
STONEPI_PREFIX=/news
STONEPI_APP_ID=newscast
EOF

write_env "$CONF/fileserve.env" <<EOF
PORT=8002
DATABASE_URL=sqlite:////var/lib/stonepi/fileserve/fileserve.sqlite
STONEPI_DATA_DIR=/var/lib/stonepi/fileserve
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/files
STONEPI_PREFIX=/files
STONEPI_APP_ID=fileserve
EOF

write_env "$CONF/eventtrakr.env" <<EOF
PORT=8003
DATABASE_URL=sqlite:////var/lib/stonepi/eventtrakr/eventtrakr.sqlite
STONEPI_DATA_DIR=/var/lib/stonepi/eventtrakr
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/events
GOOGLE_REDIRECT_URI=http://${HOSTNAME_VALUE}.local/events/calendar/google/callback
STONEPI_PREFIX=/events
STONEPI_APP_ID=eventtrakr
EOF

write_env "$CONF/pinboard.env" <<EOF
PORT=8004
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/pinboard
STONEPI_PREFIX=/pinboard
STONEPI_APP_ID=pinboard
STONEPI_DATA_DIR=/var/lib/stonepi/pinboard
EOF

write_env "$CONF/studio.env" <<EOF
PORT=8005
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/studio
STONEPI_PREFIX=/studio
STONEPI_APP_ID=studio
STONEPI_DATA_DIR=/var/lib/stonepi/studio
FILESERVE_URL=http://127.0.0.1:8002
EOF

write_env "$CONF/pricescout.env" <<EOF
PORT=8006
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/prices
STONEPI_PREFIX=/prices
STONEPI_APP_ID=pricescout
STONEPI_DATA_DIR=/var/lib/stonepi/pricescout
PINBOARD_URL=http://127.0.0.1:8004
EOF

write_env "$CONF/sportguide.env" <<EOF
PORT=8007
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/sports
STONEPI_PREFIX=/sports
STONEPI_APP_ID=sportguide
STONEPI_DATA_DIR=/var/lib/stonepi/sportguide
EOF

# Upgrade path: existing env files keep secrets but pick up new keys.
ensure_env_key "$CONF/auth.env" STONEPI_DATA_DIR /var/lib/stonepi/auth
ensure_env_key "$CONF/dashboard.env" STONEPI_DATA_DIR /var/lib/stonepi/dashboard
ensure_env_key "$CONF/newscast.env" STONEPI_DATA_DIR /var/lib/stonepi/newscast
ensure_env_key "$CONF/fileserve.env" STONEPI_DATA_DIR /var/lib/stonepi/fileserve
ensure_env_key "$CONF/eventtrakr.env" STONEPI_DATA_DIR /var/lib/stonepi/eventtrakr
ensure_env_key "$CONF/pinboard.env" STONEPI_DATA_DIR /var/lib/stonepi/pinboard
ensure_env_key "$CONF/studio.env" STONEPI_DATA_DIR /var/lib/stonepi/studio
ensure_env_key "$CONF/pricescout.env" STONEPI_DATA_DIR /var/lib/stonepi/pricescout
ensure_env_key "$CONF/sportguide.env" STONEPI_DATA_DIR /var/lib/stonepi/sportguide
ensure_env_key "$CONF/newscast.env" PUBLIC_BASE_URL "http://${HOSTNAME_VALUE}.local/news"
ensure_env_key "$CONF/fileserve.env" PUBLIC_BASE_URL "http://${HOSTNAME_VALUE}.local/files"
ensure_env_key "$CONF/eventtrakr.env" PUBLIC_BASE_URL "http://${HOSTNAME_VALUE}.local/events"
ensure_env_key "$CONF/fileserve.env" STONEPI_PREFIX /files
ensure_env_key "$CONF/newscast.env" STONEPI_PREFIX /news
ensure_env_key "$CONF/eventtrakr.env" STONEPI_PREFIX /events
ensure_env_key "$CONF/pinboard.env" STONEPI_PREFIX /pinboard
ensure_env_key "$CONF/studio.env" STONEPI_PREFIX /studio
ensure_env_key "$CONF/pricescout.env" STONEPI_PREFIX /prices
ensure_env_key "$CONF/pricescout.env" PINBOARD_URL http://127.0.0.1:8004
ensure_env_key "$CONF/sportguide.env" STONEPI_PREFIX /sports

install_app() {
  local name="$1"
  local user="$2"
  local extra="${3:-}"
  local dir="$DEST/apps/$name"
  if [[ ! -d "$dir/.venv" ]]; then
    python3 -m venv "$dir/.venv"
  fi
  "$dir/.venv/bin/pip" install --upgrade pip
  "$dir/.venv/bin/pip" install -r "$dir/requirements.txt"
  "$dir/.venv/bin/pip" install -e "$DEST/packages/stonepi_auth"
  if [[ -d "$DEST/packages/stonepi_update" ]]; then
    "$dir/.venv/bin/pip" install -e "$DEST/packages/stonepi_update"
  fi
  if [[ -d "$DEST/packages/stonepi_vault" ]]; then
    "$dir/.venv/bin/pip" install -e "$DEST/packages/stonepi_vault"
  fi
  if [[ "$name" == "dashboard" ]]; then
    for pkg in stonepi_display stonepi_watch stonepi_automations; do
      if [[ -d "$DEST/packages/$pkg" ]]; then
        "$dir/.venv/bin/pip" install -e "$DEST/packages/$pkg"
      fi
    done
  fi
  if [[ "$extra" == "playwright" ]]; then
    # Browsers must live under the service user's home (/opt/stonepi), not root's cache.
    mkdir -p /opt/stonepi/.cache/ms-playwright
    chown -R "$user:$user" /opt/stonepi/.cache
    if ! sudo -u "$user" env HOME=/opt/stonepi \
      "$dir/.venv/bin/python" -m playwright install chromium; then
      echo "WARN: playwright install chromium failed — scrape sources need: sudo -u $user HOME=/opt/stonepi $dir/.venv/bin/python -m playwright install chromium"
    fi
    "$dir/.venv/bin/python" -m playwright install-deps chromium >/dev/null 2>&1 || true
  fi
  chown -R "$user:$user" "$dir/.venv"
}

install_app auth stonepi-auth
install_app dashboard stonepi-dash
install_app newscast stonepi-news
install_app fileserve stonepi-files
install_app eventtrakr stonepi-events playwright
install_app pinboard stonepi-pin
install_app studio stonepi-studio
install_app pricescout stonepi-prices
install_app sportguide stonepi-sport playwright

# Runtime data lives under /var/lib/stonepi (STONEPI_DATA_DIR). Also create
# writable apps/*/data dirs so a missing env key cannot brick boot with EACCES.
ensure_writable_data() {
  local user="$1"
  local var_dir="$2"
  local app_data="$3"
  mkdir -p "$var_dir" "$app_data"
  chown -R "$user:$user" "$var_dir" "$app_data"
}
ensure_writable_data stonepi-auth "$DATA/auth" "$DEST/apps/auth/data"
ensure_writable_data stonepi-dash "$DATA/dashboard" "$DEST/apps/dashboard/data"
ensure_writable_data stonepi-news "$DATA/newscast" "$DEST/apps/newscast/data"
# Bundled catalog/favicons under app/data must be readable by the service user.
if [[ -d "$DEST/apps/newscast/app/data" ]]; then
  chown -R stonepi-news:stonepi-news "$DEST/apps/newscast/app/data"
fi
ensure_writable_data stonepi-files "$DATA/fileserve" "$DEST/apps/fileserve/data"
ensure_writable_data stonepi-events "$DATA/eventtrakr" "$DEST/apps/eventtrakr/data"
ensure_writable_data stonepi-pin "$DATA/pinboard" "$DEST/apps/pinboard/data"
ensure_writable_data stonepi-studio "$DATA/studio" "$DEST/apps/studio/data"
ensure_writable_data stonepi-prices "$DATA/pricescout" "$DEST/apps/pricescout/data"
ensure_writable_data stonepi-sport "$DATA/sportguide" "$DEST/apps/sportguide/data"
mkdir -p "$DATA/fileserve/hosted"
chown -R stonepi-files:stonepi-files "$DATA/fileserve"

# Vault readable by app service users (group stonepi-vault); dashboard can write.
groupadd --system stonepi-vault 2>/dev/null || true
for u in stonepi-dash stonepi-auth stonepi-news stonepi-files stonepi-events stonepi-pin stonepi-studio stonepi-prices stonepi-sport; do
  id -u "$u" >/dev/null 2>&1 && usermod -aG stonepi-vault "$u" || true
done
chown -R stonepi-dash:stonepi-vault "$DATA/vault" 2>/dev/null || true
chmod 750 "$DATA/vault" 2>/dev/null || true
# New vault files inherit stonepi-vault group.
chmod g+s "$DATA/vault" 2>/dev/null || true
find "$DATA/vault" -type f -exec chmod 640 {} \; 2>/dev/null || true
chmod 700 "$DATA/auth" "$DATA/newscast" "$DATA/fileserve" "$DATA/eventtrakr" "$DATA/pinboard" "$DATA/studio" "$DATA/pricescout" "$DATA/sportguide" "$DATA/dashboard"
chmod 750 "$DATA/vault" 2>/dev/null || true
chmod g+s "$DATA/vault" 2>/dev/null || true

hostnamectl set-hostname "$HOSTNAME_VALUE" || true
# Publish IPv4 mDNS so stonepi.local works for browsers (not only fe80:: link-local).
if [[ -f /etc/avahi/avahi-daemon.conf ]]; then
  sed -i 's/^#\?use-ipv4=.*/use-ipv4=yes/' /etc/avahi/avahi-daemon.conf
  sed -i 's/^#\?use-ipv6=.*/use-ipv6=yes/' /etc/avahi/avahi-daemon.conf
  sed -i 's/^#\?publish-addresses=.*/publish-addresses=yes/' /etc/avahi/avahi-daemon.conf
fi
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
systemctl restart avahi-daemon >/dev/null 2>&1 || true

install -m 755 "$DEST/deploy/backup/stonepi-backup.sh" /usr/local/sbin/stonepi-backup
install -m 755 "$DEST/deploy/backup/stonepi-restore.sh" /usr/local/sbin/stonepi-restore
if [[ -f "$DEST/deploy/udev/99-stonepi-backup.rules" ]]; then
  install -m 644 "$DEST/deploy/udev/99-stonepi-backup.rules" /etc/udev/rules.d/99-stonepi-backup.rules
  udevadm control --reload-rules >/dev/null 2>&1 || true
  udevadm trigger >/dev/null 2>&1 || true
fi
if [[ ! -f "$CONF/backup.conf" ]]; then
  cat > "$CONF/backup.conf" <<'EOF'
LABEL=STONEPI-BACKUP
UUID=
APPS="stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-dashboard stonepi-auth"
EOF
  chmod 644 "$CONF/backup.conf"
fi

cp "$DEST/deploy/nginx/stonepi.conf" /etc/nginx/sites-available/stonepi
ln -sfn /etc/nginx/sites-available/stonepi /etc/nginx/sites-enabled/stonepi
# Drop stock welcome site so IP/hostname both hit StonePi (reload required if nginx already running).
rm -f /etc/nginx/sites-enabled/default
rm -f /etc/nginx/sites-enabled/default.conf
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx 2>/dev/null || systemctl restart nginx

for unit in stonepi-auth stonepi-dashboard stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-backup; do
  cp "$DEST/deploy/systemd/${unit}.service" "/etc/systemd/system/${unit}.service"
done
if [[ -f "$DEST/deploy/systemd/stonepi-backup.timer" ]]; then
  cp "$DEST/deploy/systemd/stonepi-backup.timer" /etc/systemd/system/stonepi-backup.timer
fi

install -m 755 "$DEST/deploy/stonepi-cli.sh" /usr/local/bin/stonepi

cat > /etc/sudoers.d/stonepi-dash <<'EOF'
stonepi-dash ALL=(root) NOPASSWD: /bin/systemctl start stonepi-*, /bin/systemctl stop stonepi-*, /bin/systemctl restart stonepi-*, /bin/systemctl is-active stonepi-*, /bin/journalctl -u stonepi-*
EOF
chmod 440 /etc/sudoers.d/stonepi-dash

systemctl daemon-reload
systemctl enable --now stonepi-auth stonepi-dashboard stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide
# Backup runs on USB insert (udev), not on a calendar timer.
systemctl disable --now stonepi-backup.timer >/dev/null 2>&1 || true
systemctl reset-failed stonepi-backup.service >/dev/null 2>&1 || true
systemctl enable cockpit.socket >/dev/null 2>&1 || true
systemctl start cockpit.socket >/dev/null 2>&1 || true

"$DEST/apps/auth/.venv/bin/python" "$DEST/scripts/migrate_users.py" \
  --auth-db "$DATA/auth/users.sqlite" \
  --newscast-db "$DATA/newscast/newscast.sqlite" \
  --fileserve-db "$DATA/fileserve/fileserve.sqlite" \
  --eventtrakr-db "$DATA/eventtrakr/eventtrakr.sqlite" || true

STONEPI_VAULT_DIR="$DATA/vault" "$DEST/apps/dashboard/.venv/bin/python" \
  "$DEST/scripts/migrate_secrets_to_vault.py" \
  --vault-dir "$DATA/vault" \
  --newscast-db "$DATA/newscast/newscast.sqlite" \
  --eventtrakr-db "$DATA/eventtrakr/eventtrakr.sqlite" || true

# Migrator may create files as root; restore group-readable vault ownership.
chown -R stonepi-dash:stonepi-vault "$DATA/vault" 2>/dev/null || true
chmod 750 "$DATA/vault" 2>/dev/null || true
chmod g+s "$DATA/vault" 2>/dev/null || true
find "$DATA/vault" -type f -exec chmod 640 {} \; 2>/dev/null || true

# Pick up stonepi-vault supplementary group + any new env keys.
systemctl restart stonepi-auth stonepi-dashboard stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide >/dev/null 2>&1 || true

# Wait briefly for apps, then verify edge + backends (catches welcome-page / 502 installs).
sleep 3
echo
echo "Health checks"
UNITS=(stonepi-auth stonepi-dashboard stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide)
failed_units=0
for u in "${UNITS[@]}"; do
  if systemctl is-active --quiet "$u"; then
    echo "  ok  $u"
  else
    echo "  FAIL $u (not active)"
    failed_units=$((failed_units + 1))
  fi
done
check() {
  local url="$1"
  if curl -fsS -o /dev/null -m 5 "$url"; then
    echo "  ok  $url"
  else
    echo "  WARN $url"
  fi
}
check "http://127.0.0.1:8011/healthz"
check "http://127.0.0.1:8010/healthz"
check "http://127.0.0.1:8001/healthz"
check "http://127.0.0.1:8002/healthz"
check "http://127.0.0.1:8003/healthz"
check "http://127.0.0.1:8004/healthz"
check "http://127.0.0.1:8005/healthz"
# Edge must be StonePi, not the stock nginx welcome page.
edge_code="$(curl -sS -o /tmp/stonepi-edge-check.html -m 5 -w '%{http_code}' http://127.0.0.1/ 2>/dev/null || echo 000)"
if [[ -f /tmp/stonepi-edge-check.html ]] && grep -qi "Welcome to nginx" /tmp/stonepi-edge-check.html; then
  echo "  FAIL http://127.0.0.1/ still shows nginx welcome — check /etc/nginx/sites-enabled"
  failed_units=$((failed_units + 1))
elif [[ "$edge_code" =~ ^(200|301|302|303|307|308)$ ]]; then
  echo "  ok  http://127.0.0.1/ (HTTP $edge_code)"
else
  echo "  WARN http://127.0.0.1/ (HTTP $edge_code)"
fi
rm -f /tmp/stonepi-edge-check.html
if [[ "$failed_units" -gt 0 ]]; then
  echo
  echo "Install finished with warnings — run: stonepi status"
  echo "Logs: sudo journalctl -u stonepi-auth -u stonepi-dashboard -n 40 --no-pager"
fi
echo
echo "StonePi is installed and enabled on boot."
echo "  Dashboard  http://${HOSTNAME_VALUE}.local/  (or http://<pi-ip>/)"
echo "  Auth       http://${HOSTNAME_VALUE}.local/auth/login"
echo "  NewsCast   http://${HOSTNAME_VALUE}.local/news/"
echo "  FileServe  http://${HOSTNAME_VALUE}.local/files/"
echo "  EventTrakr http://${HOSTNAME_VALUE}.local/events/"
echo "  Pinboard   http://${HOSTNAME_VALUE}.local/pinboard/"
echo "  Studio     http://${HOSTNAME_VALUE}.local/studio/"
echo "  PriceScout http://${HOSTNAME_VALUE}.local/prices/"
echo "  Cockpit    https://${HOSTNAME_VALUE}.local:9090  (or https://<pi-lan-ip>:9090)"
echo "  Sign in with admin / admin and change the password."
echo "  Helper:    stonepi status | stonepi urls | stonepi restart | stonepi logs"
echo "  Backup:    plug in a USB labelled STONEPI-BACKUP (auto-starts), or: sudo systemctl start stonepi-backup"
echo "  Docs:      $DEST/deploy/INSTALL.md"
echo
