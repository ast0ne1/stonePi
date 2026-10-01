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
  libnss-mdns fonts-liberation curl tesseract-ocr \
  libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 \
  libpangocairo-1.0-0 libpango-1.0-0 || apt-get install -y python3 python3-venv python3-pip rsync nginx \
  avahi-daemon avahi-utils sqlite3 cockpit libnss-mdns fonts-liberation curl tesseract-ocr \
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
ensure_user stonepi-notify
ensure_user stonepi-news
ensure_user stonepi-files
ensure_user stonepi-events
ensure_user stonepi-pin
ensure_user stonepi-studio
ensure_user stonepi-prices
ensure_user stonepi-sport
ensure_user stonepi-watch

# Health hardware probes: vcgencmd (video) + journalctl --list-boots (systemd-journal)
for g in video systemd-journal; do
  getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" stonepi-dash || true
done

mkdir -p "$DEST" "$DATA/auth" "$DATA/newscast" "$DATA/fileserve/hosted" "$DATA/eventtrakr" "$DATA/pinboard" "$DATA/studio" "$DATA/pricescout" "$DATA/sportguide" "$DATA/pricewatch" "$DATA/dashboard" "$DATA/notify" "$DATA/vault" "$CONF"
if [[ ! -f "$DATA/exposure" ]]; then
  printf 'lan\n' > "$DATA/exposure"
  chmod 644 "$DATA/exposure"
  chown stonepi-dash:stonepi-dash "$DATA/exposure" 2>/dev/null || true
fi
mkdir -p "$DATA/dashboard"
if [[ ! -f "$DATA/dashboard/tailscale_wanted" ]]; then
  if [[ -f "$DATA/tailscale_wanted" ]]; then
    cp "$DATA/tailscale_wanted" "$DATA/dashboard/tailscale_wanted"
  else
    printf 'off\n' > "$DATA/dashboard/tailscale_wanted"
  fi
  chmod 644 "$DATA/dashboard/tailscale_wanted"
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

# Renamed services (notifications -> notify, recovery -> recover): retire old
# units and carry code/venv, data, env, passwd and Vault key forward. Idempotent.
bash "$DEST/deploy/stonepi-migrate-renames.sh" "$DEST"

if [[ ! -f "$CONF/stonepi.env" ]]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$CONF/stonepi.env" <<EOF
# Prefer Vault when set; installer .env remains bootstrap only.
HOST=127.0.0.1
SESSION_SECRET=$SECRET
STONEPI_SESSION_SECRET=$SECRET
STONEPI_VAULT_DIR=/var/lib/stonepi/vault
STONEPI_EXPOSURE=lan
STONEPI_EXPOSURE_FILE=/var/lib/stonepi/exposure
STONEPI_HOSTNAME=$HOSTNAME_VALUE
STONEPI_HOSTNAME_FILE=/var/lib/stonepi/hostname
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
if ! grep -q '^STONEPI_HOSTNAME_FILE=' "$CONF/stonepi.env" 2>/dev/null; then
  echo 'STONEPI_HOSTNAME_FILE=/var/lib/stonepi/hostname' >> "$CONF/stonepi.env"
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
  chown stonepi-dash:stonepi-dash "$DATA/exposure" 2>/dev/null || true
fi
if [[ ! -f "$DATA/hostname" ]]; then
  printf '%s\n' "$HOSTNAME_VALUE" > "$DATA/hostname"
  chmod 644 "$DATA/hostname"
fi
mkdir -p "$DATA/dashboard"
if [[ ! -f "$DATA/dashboard/tailscale_wanted" ]]; then
  if [[ -f "$DATA/tailscale_wanted" ]]; then
    cp "$DATA/tailscale_wanted" "$DATA/dashboard/tailscale_wanted"
  else
    printf 'off\n' > "$DATA/dashboard/tailscale_wanted"
  fi
  chmod 644 "$DATA/dashboard/tailscale_wanted"
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

write_env "$CONF/notify.env" <<EOF
PORT=8012
STONEPI_DATA_DIR=/var/lib/stonepi/notify
STONEPI_PREFIX=/notify
STONEPI_APP_ID=notify
STONEPI_NOTIFY_URL=http://127.0.0.1:8012
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

write_env "$CONF/pricewatch.env" <<EOF
PORT=8008
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/watch
STONEPI_PREFIX=/watch
STONEPI_APP_ID=pricewatch
STONEPI_DATA_DIR=/var/lib/stonepi/pricewatch
EOF

# Upgrade path: existing env files keep secrets but pick up new keys.
ensure_env_key "$CONF/auth.env" STONEPI_DATA_DIR /var/lib/stonepi/auth
ensure_env_key "$CONF/dashboard.env" STONEPI_DATA_DIR /var/lib/stonepi/dashboard
ensure_env_key "$CONF/notify.env" STONEPI_DATA_DIR /var/lib/stonepi/notify
ensure_env_key "$CONF/notify.env" STONEPI_PREFIX /notify
ensure_env_key "$CONF/newscast.env" STONEPI_DATA_DIR /var/lib/stonepi/newscast
ensure_env_key "$CONF/fileserve.env" STONEPI_DATA_DIR /var/lib/stonepi/fileserve
ensure_env_key "$CONF/eventtrakr.env" STONEPI_DATA_DIR /var/lib/stonepi/eventtrakr
ensure_env_key "$CONF/pinboard.env" STONEPI_DATA_DIR /var/lib/stonepi/pinboard
ensure_env_key "$CONF/studio.env" STONEPI_DATA_DIR /var/lib/stonepi/studio
ensure_env_key "$CONF/pricescout.env" STONEPI_DATA_DIR /var/lib/stonepi/pricescout
ensure_env_key "$CONF/sportguide.env" STONEPI_DATA_DIR /var/lib/stonepi/sportguide
ensure_env_key "$CONF/pricewatch.env" STONEPI_DATA_DIR /var/lib/stonepi/pricewatch
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
ensure_env_key "$CONF/pricewatch.env" STONEPI_PREFIX /watch

# One pip run per app: its requirements plus the shared StonePi packages it imports
# (editable). Skipped when nothing pip would install has changed (requirements, package
# pyproject.toml, Python), so re-running the installer doesn't rebuild every venv.
pip_sync() {
  local name="$1"
  shift
  local dir="$DEST/apps/$name"
  local fresh=0
  if [[ ! -x "$dir/.venv/bin/python" ]]; then
    python3 -m venv "$dir/.venv"
    fresh=1
  fi
  local args=(-r "$dir/requirements.txt")
  local inputs=("$dir/requirements.txt")
  local pkg
  for pkg in "$@"; do
    if [[ -d "$DEST/packages/$pkg" ]]; then
      args+=(-e "$DEST/packages/$pkg")
      inputs+=("$DEST/packages/$pkg/pyproject.toml")
    fi
  done
  local stamp="$dir/.venv/.stonepi-deps"
  local want
  want="$({ "$dir/.venv/bin/python" --version; printf '%s\n' "${args[@]}"; cat "${inputs[@]}"; } 2>&1 | sha256sum | cut -d' ' -f1)"
  if [[ "$fresh" -eq 0 && -f "$stamp" && "$(cat "$stamp")" == "$want" ]]; then
    echo "  $name: Python packages up to date"
    return 0
  fi
  echo "  $name: installing Python packages"
  if [[ "$fresh" -eq 1 ]]; then
    "$dir/.venv/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
  fi
  "$dir/.venv/bin/pip" install --quiet --disable-pip-version-check "${args[@]}"
  printf '%s\n' "$want" > "$stamp"
}

# Chromium for Playwright (EventTrakr, SportGuide): one shared copy, installed by root and
# readable by every service user. Their HOME is /opt/stonepi, so Playwright's default
# browser path ($HOME/.cache/ms-playwright) is this folder.
PW_BROWSERS=/opt/stonepi/.cache/ms-playwright
install_chromium() {
  local dir="$1"
  # Shared Chromium launch lock across scraper apps.
  mkdir -p /run/stonepi
  chmod 1777 /run/stonepi
  mkdir -p "$PW_BROWSERS"
  if ! PLAYWRIGHT_BROWSERS_PATH="$PW_BROWSERS" "$dir/.venv/bin/python" -m playwright install chromium; then
    echo "WARN: playwright install chromium failed — scrape sources need: sudo PLAYWRIGHT_BROWSERS_PATH=$PW_BROWSERS $dir/.venv/bin/python -m playwright install chromium"
  fi
  "$dir/.venv/bin/python" -m playwright install-deps chromium >/dev/null 2>&1 || true
  chown -R root:root /opt/stonepi/.cache
  chmod 755 /opt/stonepi/.cache
  chmod -R a+rX "$PW_BROWSERS"
}

install_app() {
  local name="$1"
  local user="$2"
  local extra="${3:-}"
  local dir="$DEST/apps/$name"
  local pkgs=(stonepi_auth stonepi_contracts stonepi_update stonepi_vault)
  case "$name" in
    dashboard) pkgs+=(stonepi_display stonepi_watch stonepi_automations) ;;
    notify) pkgs+=(stonepi_display stonepi_notify stonepi_watch) ;;
  esac
  if [[ "$extra" == "playwright" ]]; then
    pkgs+=(stonepi_browser)
  fi
  pip_sync "$name" "${pkgs[@]}"
  if [[ "$extra" == "playwright" ]]; then
    install_chromium "$dir"
  fi
  chown -R "$user:$user" "$dir/.venv"
}

echo
echo "Python packages"
install_app auth stonepi-auth
install_app dashboard stonepi-dash
install_app notify stonepi-notify
# Recover is a tiny root-owned escape hatch (HTTP Basic + optional SSO cookie).
if [[ -d "$DEST/apps/recover" ]]; then
  pip_sync recover stonepi_vault stonepi_auth
fi
install_app newscast stonepi-news
install_app fileserve stonepi-files
install_app eventtrakr stonepi-events playwright
install_app pinboard stonepi-pin
install_app studio stonepi-studio
install_app pricescout stonepi-prices
install_app sportguide stonepi-sport playwright
install_app pricewatch stonepi-watch

# Runtime data lives under /var/lib/stonepi (STONEPI_DATA_DIR). Also create
# writable apps/*/data dirs so a missing env key cannot brick boot with EACCES.
ensure_writable_data() {
  local user="$1"
  local var_dir="$2"
  local app_data="$3"
  mkdir -p "$var_dir" "$app_data"
  chown -R "$user:$user" "$var_dir" "$app_data"
}
# Who owns what under /var/lib/stonepi. fix_data_ownership runs before the services start
# and again after anything else wrote there as root (migrations), and the ready check
# below verifies it as each service user.
APP_DATA_OWNERS=(auth:stonepi-auth dashboard:stonepi-dash notify:stonepi-notify newscast:stonepi-news
  fileserve:stonepi-files eventtrakr:stonepi-events pinboard:stonepi-pin studio:stonepi-studio
  pricescout:stonepi-prices sportguide:stonepi-sport pricewatch:stonepi-watch)
fix_data_ownership() {
  local pair dir user
  for pair in "${APP_DATA_OWNERS[@]}"; do
    dir="${pair%%:*}"
    user="${pair#*:}"
    [[ -d "$DATA/$dir" ]] || continue
    chown -R "$user:$user" "$DATA/$dir"
    chmod 700 "$DATA/$dir"
  done
  # Dashboard's Health page reads Notify's status files (destinations.json, displays.json,
  # prefs.json; secrets stay in the Vault): group-readable by stonepi-dash.
  chmod 750 "$DATA/notify"
  find "$DATA/notify" -maxdepth 1 -type f -name '*.json' -exec chmod 640 {} \; 2>/dev/null || true
  # Every service user reads and saves secrets (group stonepi-vault, setgid folder).
  chown -R stonepi-dash:stonepi-vault "$DATA/vault"
  chmod 2770 "$DATA/vault"
  find "$DATA/vault" -type f -exec chmod 660 {} \;
  # Settings → Network (Dashboard) writes the exposure flag; every app reads it.
  if [[ -f "$DATA/exposure" ]]; then
    chown stonepi-dash:stonepi-dash "$DATA/exposure"
    chmod 644 "$DATA/exposure"
  fi
}
ensure_writable_data stonepi-auth "$DATA/auth" "$DEST/apps/auth/data"
ensure_writable_data stonepi-dash "$DATA/dashboard" "$DEST/apps/dashboard/data"
ensure_writable_data stonepi-notify "$DATA/notify" "$DEST/apps/notify/data"
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
ensure_writable_data stonepi-watch "$DATA/pricewatch" "$DEST/apps/pricewatch/data"
mkdir -p "$DATA/fileserve/hosted"
chown -R stonepi-files:stonepi-files "$DATA/fileserve"

# Vault readable by app service users (group stonepi-vault); dashboard can write.
groupadd --system stonepi-vault 2>/dev/null || true
for u in stonepi-dash stonepi-auth stonepi-notify stonepi-news stonepi-files stonepi-events stonepi-pin stonepi-studio stonepi-prices stonepi-sport stonepi-watch; do
  id -u "$u" >/dev/null 2>&1 && usermod -aG stonepi-vault "$u" || true
done
# Apps save their own keys (ntfy token, Bright Data, Google OAuth), so the group writes too;
# setgid keeps new files in stonepi-vault.
chown -R stonepi-dash:stonepi-vault "$DATA/vault" 2>/dev/null || true
chmod 2770 "$DATA/vault" 2>/dev/null || true
find "$DATA/vault" -type f -exec chmod 660 {} \; 2>/dev/null || true
usermod -aG stonepi-notify stonepi-dash 2>/dev/null || true
fix_data_ownership

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
install -m 755 "$DEST/deploy/backup/stonepi-restore-drill.sh" /usr/local/sbin/stonepi-restore-drill
install -m 755 "$DEST/deploy/backup/stonepi-backup-helper.sh" /usr/local/sbin/stonepi-backup-helper
install -m 755 "$DEST/deploy/backup/stonepi-failover-monitor.sh" /usr/local/sbin/stonepi-failover-monitor
install -m 755 "$DEST/deploy/stonepi-tailscale-acl.sh" /usr/local/sbin/stonepi-tailscale-acl
# Settings → Updates: Dashboard downloads, this root helper installs (app code is root-owned).
install -m 755 "$DEST/deploy/stonepi-update-helper.py" /usr/local/sbin/stonepi-update-helper
sed -i 's/\r$//' /usr/local/sbin/stonepi-update-helper  # a CRLF shebang (Windows copy) would not run
mkdir -p "$DATA/updates"
chmod 700 "$DATA/updates"
mkdir -p /etc/nginx/snippets
install -m 644 "$DEST/deploy/nginx/stonepi-failover.conf" /etc/nginx/snippets/stonepi-failover.conf
if [[ -f "$DEST/deploy/udev/99-stonepi-backup.rules" ]]; then
  install -m 644 "$DEST/deploy/udev/99-stonepi-backup.rules" /etc/udev/rules.d/99-stonepi-backup.rules
  udevadm control --reload-rules >/dev/null 2>&1 || true
  udevadm trigger >/dev/null 2>&1 || true
fi
if [[ ! -f "$CONF/backup.conf" ]]; then
  cat > "$CONF/backup.conf" <<'EOF'
LABEL=STONEPI-BACKUP
UUID=
APPS="stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch"
LOCAL_ENABLED=0
LOCAL_CADENCE=weekly
LOCAL_WEEKDAY=Sun
LOCAL_TIME=03:30
LOCAL_ROOT=/var/backups/stonepi
EOF
  chmod 644 "$CONF/backup.conf"
fi
# Keep existing backup.conf APPS in sync with catalog (append missing units).
if [[ -f "$CONF/backup.conf" ]] && ! grep -q 'stonepi-notify' "$CONF/backup.conf" 2>/dev/null; then
  sed -i 's/APPS="/APPS="stonepi-notify /' "$CONF/backup.conf" 2>/dev/null || true
fi
# Ensure local-backup keys exist on upgrades.
if [[ -f "$CONF/backup.conf" ]] && ! grep -q '^LOCAL_ENABLED=' "$CONF/backup.conf" 2>/dev/null; then
  cat >> "$CONF/backup.conf" <<'EOF'
LOCAL_ENABLED=0
LOCAL_CADENCE=weekly
LOCAL_WEEKDAY=Sun
LOCAL_TIME=03:30
LOCAL_ROOT=/var/backups/stonepi
EOF
fi
mkdir -p /var/backups/stonepi
chmod 755 /var/backups/stonepi

# Persistent, size-bounded journal.
mkdir -p /etc/systemd/journald.conf.d
if [[ -f "$DEST/deploy/journald/stonepi.conf" ]]; then
  install -m 644 "$DEST/deploy/journald/stonepi.conf" /etc/systemd/journald.conf.d/stonepi.conf
  systemctl restart systemd-journald >/dev/null 2>&1 || true
fi
if [[ -f "$DEST/deploy/tmpfiles.d/stonepi.conf" ]]; then
  install -m 644 "$DEST/deploy/tmpfiles.d/stonepi.conf" /etc/tmpfiles.d/stonepi.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/stonepi.conf >/dev/null 2>&1 || true
fi

# Runtime/hardware watchdog (no-op on boards without a watchdog device).
mkdir -p /etc/systemd/system.conf.d
if [[ -f "$DEST/deploy/systemd/stonepi-watchdog.conf" ]]; then
  install -m 644 "$DEST/deploy/systemd/stonepi-watchdog.conf" /etc/systemd/system.conf.d/stonepi-watchdog.conf
fi

# Host firewall (nftables) — LAN Cockpit, nginx, Tailscale, SSH.
if [[ -f "$DEST/deploy/nftables/install-firewall.sh" ]]; then
  bash "$DEST/deploy/nftables/install-firewall.sh" || echo "WARNING: nftables firewall install failed — check manually." >&2
fi

# Timezone (household default).
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl set-timezone Europe/Copenhagen >/dev/null 2>&1 || true
fi

# OS security updates only (StonePi app zips stay on Dashboard → Updates).
if [[ -f "$DEST/deploy/apt/51stonepi-unattended" ]]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades >/dev/null 2>&1 || true
  install -m 644 "$DEST/deploy/apt/51stonepi-unattended" /etc/apt/apt.conf.d/51stonepi-unattended
fi

cp "$DEST/deploy/nginx/stonepi.conf" /etc/nginx/sites-available/stonepi
ln -sfn /etc/nginx/sites-available/stonepi /etc/nginx/sites-enabled/stonepi
# Drop stock welcome site so IP/hostname both hit StonePi (reload required if nginx already running).
rm -f /etc/nginx/sites-enabled/default
rm -f /etc/nginx/sites-enabled/default.conf
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx 2>/dev/null || systemctl restart nginx

for unit in stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch stonepi-backup stonepi-local-backup stonepi-recover stonepi-failover-monitor; do
  if [[ -f "$DEST/deploy/systemd/${unit}.service" ]]; then
    cp "$DEST/deploy/systemd/${unit}.service" "/etc/systemd/system/${unit}.service"
  fi
done
if [[ -f "$DEST/deploy/systemd/stonepi.target" ]]; then
  cp "$DEST/deploy/systemd/stonepi.target" /etc/systemd/system/stonepi.target
fi
if [[ -f "$DEST/deploy/systemd/stonepi-backup.timer" ]]; then
  cp "$DEST/deploy/systemd/stonepi-backup.timer" /etc/systemd/system/stonepi-backup.timer
fi
if [[ -f "$DEST/deploy/systemd/stonepi-local-backup.timer" ]]; then
  cp "$DEST/deploy/systemd/stonepi-local-backup.timer" /etc/systemd/system/stonepi-local-backup.timer
fi

install -m 755 "$DEST/deploy/stonepi-cli.sh" /usr/local/bin/stonepi

# Tailscale: preinstall so Network tab is Enable + auth only (no apt from the UI).
# Does not run `tailscale up` — auth is interactive under Settings → Network.
install_tailscale() {
  if command -v tailscale >/dev/null 2>&1; then
    return 0
  fi
  echo "Installing Tailscale (official installer)…"
  if ! curl -fsSL https://tailscale.com/install.sh | sh; then
    echo "ERROR: Tailscale install failed — remote access will not be ready." >&2
    return 1
  fi
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "ERROR: tailscale binary missing after install." >&2
    return 1
  fi
  return 0
}
if ! install_tailscale; then
  echo "Aborting: Tailscale is required for a complete StonePi appliance install." >&2
  exit 1
fi
systemctl enable --now tailscaled >/dev/null 2>&1 || systemctl enable --now tailscaled.service >/dev/null 2>&1 || true
if ! systemctl is-active --quiet tailscaled && ! systemctl is-active --quiet tailscaled.service; then
  echo "WARNING: tailscaled is not active yet; Network connect may start it via the helper." >&2
fi
if [[ ! -f "$DEST/deploy/stonepi-tailscale.sh" ]]; then
  echo "Missing $DEST/deploy/stonepi-tailscale.sh — aborting." >&2
  exit 1
fi
install -m 755 "$DEST/deploy/stonepi-tailscale.sh" /usr/local/sbin/stonepi-tailscale
if ! /usr/local/sbin/stonepi-tailscale install-check >/dev/null 2>&1; then
  echo "ERROR: stonepi-tailscale install-check failed." >&2
  exit 1
fi
if [[ ! -f "$DEST/deploy/stonepi-hostname.sh" ]]; then
  echo "Missing $DEST/deploy/stonepi-hostname.sh — aborting." >&2
  exit 1
fi
install -m 755 "$DEST/deploy/stonepi-hostname.sh" /usr/local/sbin/stonepi-hostname
if ! /usr/local/sbin/stonepi-hostname install-check >/dev/null 2>&1; then
  echo "ERROR: stonepi-hostname install-check failed." >&2
  exit 1
fi

cat > /etc/sudoers.d/stonepi-dash <<'EOF'
stonepi-dash ALL=(root) NOPASSWD: /bin/systemctl start stonepi-*, /bin/systemctl stop stonepi-*, /bin/systemctl restart stonepi-*, /bin/systemctl is-active stonepi-*, /bin/journalctl -u stonepi-*
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-tailscale
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-hostname
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-backup-helper
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-tailscale-acl
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-update-helper
EOF
chmod 440 /etc/sudoers.d/stonepi-dash

systemctl daemon-reload
systemctl enable --now stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch
systemctl enable --now stonepi-recover >/dev/null 2>&1 || true
systemctl enable --now stonepi-failover-monitor >/dev/null 2>&1 || true
systemctl enable stonepi.target >/dev/null 2>&1 || true
# Backup: USB on insert (udev); local schedule only when enabled in Settings.
systemctl disable --now stonepi-backup.timer >/dev/null 2>&1 || true
systemctl reset-failed stonepi-backup.service >/dev/null 2>&1 || true
systemctl disable --now stonepi-local-backup.timer >/dev/null 2>&1 || true
systemctl reset-failed stonepi-local-backup.service >/dev/null 2>&1 || true
# Re-apply local schedule from conf if previously enabled.
if [[ -f "$CONF/backup.conf" ]] && grep -qE '^LOCAL_ENABLED=(1|true|yes)' "$CONF/backup.conf" 2>/dev/null; then
  /usr/local/sbin/stonepi-backup-helper local-schedule >/dev/null 2>&1 || true
fi
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

# Seed recover HTTP Basic password (once).
if [[ ! -f /etc/stonepi/recover.passwd ]]; then
  RECOVERY_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  printf 'stonepi:%s\n' "$RECOVERY_PASS" > /etc/stonepi/recover.passwd
  chmod 600 /etc/stonepi/recover.passwd
  STONEPI_VAULT_DIR="$DATA/vault" "$DEST/apps/dashboard/.venv/bin/python" - <<PY || true
from stonepi_vault import configure, set_secret
configure("$DATA/vault")
set_secret("STONEPI_RECOVER_PASSWORD", "$RECOVERY_PASS")
print("recover password stored in vault (STONEPI_RECOVER_PASSWORD)")
PY
  echo "Recover login: user stonepi — password in Vault key STONEPI_RECOVER_PASSWORD (also /etc/stonepi/recover.passwd)"
fi

# The migrations above ran as root after the services had started and opened their
# databases (SQLite can leave root-owned -wal/-shm files), so put every owner back.
fix_data_ownership

# Pick up stonepi-vault supplementary group + any new env keys.
systemctl restart stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch >/dev/null 2>&1 || true

# Wait briefly for apps, then verify edge + backends (catches welcome-page / 502 installs).
sleep 3
echo
echo "Health checks"
UNITS=(stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch)
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

# Can each service do what it needs, as its own user? Catches the permission problems a
# fresh install can hit, before someone finds them as an error page.
echo
echo "Ready check"
ready_fail=0
rc() {
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  ok   $desc"
  else
    echo "  FAIL $desc"
    ready_fail=$((ready_fail + 1))
  fi
}
for pair in "${APP_DATA_OWNERS[@]}"; do
  dir="${pair%%:*}"
  user="${pair#*:}"
  rc "$user can write its data ($DATA/$dir)" sudo -u "$user" test -w "$DATA/$dir"
  rc "$DATA/$dir is all owned by $user" test -z "$(find "$DATA/$dir" ! -user "$user" -print -quit 2>/dev/null)"
  rc "$user can read and save the Vault" sudo -u "$user" sh -c "test -w '$DATA/vault' && { test ! -e '$DATA/vault/secrets.enc' || { test -r '$DATA/vault/secrets.enc' && test -w '$DATA/vault/secrets.enc'; }; }"
done
rc "Dashboard can read Notify's status files" sudo -u stonepi-dash test -x "$DATA/notify"
rc "Dashboard can save the network exposure setting" sudo -u stonepi-dash test -w "$DATA/exposure"
rc "Dashboard can control services" sudo -u stonepi-dash sudo -n systemctl is-active stonepi-auth
rc "Dashboard can read service logs" sudo -u stonepi-dash sudo -n journalctl -u stonepi-auth -n 1 --no-pager
rc "Dashboard can run backups" sudo -u stonepi-dash sudo -n /usr/local/sbin/stonepi-backup-helper list
rc "Dashboard can install app updates" sudo -u stonepi-dash sudo -n /usr/local/sbin/stonepi-update-helper status pinboard
rc "EventTrakr poster reading (tesseract) is installed" command -v tesseract
for user in stonepi-events stonepi-sport; do
  rc "$user can start Chromium" sudo -u "$user" sh -c 'for f in /opt/stonepi/.cache/ms-playwright/chromium*/chrome-linux*/chrome /opt/stonepi/.cache/ms-playwright/chromium_headless_shell*/chrome-linux*/headless_shell; do test -x "$f" && exit 0; done; exit 1'
done
if [[ "$ready_fail" -eq 0 ]]; then
  echo "  All checks passed."
  printf 'ok\n' > "$DATA/ready-check"
else
  echo "  $ready_fail check(s) failed — re-run the installer, or see $DEST/deploy/INSTALL.md (Troubleshooting)."
  printf 'failed %s\n' "$ready_fail" > "$DATA/ready-check"
  failed_units=$((failed_units + ready_fail))
fi
chmod 644 "$DATA/ready-check"
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
echo "  Backup:    plug in USB labelled STONEPI-BACKUP, or enable local schedule in Settings → Backup"
echo "  Docs:      $DEST/deploy/INSTALL.md"
echo
