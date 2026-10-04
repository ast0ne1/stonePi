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
apt-get -o DPkg::Lock::Timeout=300 update -y
# Everything in one apt run, including Chromium's libraries (so Playwright's
# install-deps finds nothing left to do). Trixie renamed some of them (t64).
if apt-cache show libasound2t64 >/dev/null 2>&1; then
  CHROMIUM_LIBS=(libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libxkbcommon0
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 libpangocairo-1.0-0 libpango-1.0-0)
else
  CHROMIUM_LIBS=(libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 libpango-1.0-0)
fi
apt-get -o DPkg::Lock::Timeout=300 install -y python3 python3-venv python3-pip rsync nginx avahi-daemon \
  avahi-utils sqlite3 cockpit libnss-mdns fonts-liberation curl tesseract-ocr acl unattended-upgrades \
  "${CHROMIUM_LIBS[@]}"
# adb is only for the optional Car Thing panel: a missing package must not stop the install.
if ! apt-get -o DPkg::Lock::Timeout=300 install -y adb; then
  echo "WARN: adb did not install — the Car Thing panel needs it: sudo apt-get install adb" >&2
fi

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
ensure_user stonepi-library
# Car Thing panel (optional; Notify → Displays switches it on). Talks to the device over adb.
ensure_user stonepi-carthing
# Kiwix reader (stonepi-kiwix): system user, read-only on Library content via the
# stonepi-library group (data dir is setgid 2750).
ensure_user stonepi-kiwix
usermod -aG stonepi-library stonepi-kiwix 2>/dev/null || true

# Health hardware probes: vcgencmd (video) + journalctl --list-boots (systemd-journal)
for g in video systemd-journal; do
  getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" stonepi-dash || true
done

mkdir -p "$DEST" "$DATA/auth" "$DATA/newscast" "$DATA/fileserve/hosted" "$DATA/eventtrakr" "$DATA/pinboard" "$DATA/studio" "$DATA/pricescout" "$DATA/sportguide" "$DATA/pricewatch" "$DATA/library" "$DATA/carthing" "$DATA/dashboard" "$DATA/notify" "$DATA/vault" "$CONF"
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


# Install progress for the bootstrap (install-stonepi/install.sh): "running" until the very
# end, so an interrupted run is resumed rather than mistaken for a finished install.
printf 'running\n' > "$DATA/install-state"
chmod 644 "$DATA/install-state"

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

write_env "$CONF/library.env" <<EOF
PORT=8009
PUBLIC_BASE_URL=http://${HOSTNAME_VALUE}.local/library
STONEPI_PREFIX=/library
STONEPI_APP_ID=library
STONEPI_DATA_DIR=/var/lib/stonepi/library
EOF

# Car Thing panel: loopback only (the device reaches it through `adb reverse`).
write_env "$CONF/carthing.env" <<EOF
PORT=8013
STONEPI_APP_ID=carthing
STONEPI_DATA_DIR=/var/lib/stonepi/carthing
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
ensure_env_key "$CONF/library.env" STONEPI_DATA_DIR /var/lib/stonepi/library
ensure_env_key "$CONF/carthing.env" STONEPI_DATA_DIR /var/lib/stonepi/carthing
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
ensure_env_key "$CONF/library.env" STONEPI_PREFIX /library

# First admin password. Auth creates the first admin from ADMIN_PASSWORD when it starts with
# no accounts, so only then: a random password in auth.env (Auth alone reads it, unlike
# stonepi.env, which every app loads) and in a root-only file so it can be looked up again.
# Installs that already have accounts keep theirs; the "change your password" banner stays
# until the admin changes it. A resumed install reuses the password it generated before.
INITIAL_ADMIN="$CONF/initial-admin.txt"
NEW_ADMIN_PASSWORD=""
auth_has_users() {
  local db="$DATA/auth/users.sqlite"
  [[ -s "$db" ]] || return 1
  [[ "$(sqlite3 "$db" 'SELECT COUNT(*) FROM users;' 2>/dev/null || echo 0)" != "0" ]]
}
if ! auth_has_users; then
  if [[ -s "$INITIAL_ADMIN" ]]; then
    NEW_ADMIN_PASSWORD="$(sed -n 's/^password: //p' "$INITIAL_ADMIN" | head -n1)"
  fi
  if [[ -z "$NEW_ADMIN_PASSWORD" ]]; then
    NEW_ADMIN_PASSWORD="$(python3 -c 'import secrets, string; a = string.ascii_letters + string.digits; print("".join(secrets.choice(a) for _ in range(16)))')"
  fi
  ADMIN_USER_VALUE="$(sed -n 's/^ADMIN_USERNAME=//p' "$CONF/stonepi.env" | tail -n1)"
  (
    umask 077
    printf 'StonePi first sign-in (written by the installer; not updated when the password changes)\nusername: %s\npassword: %s\n' \
      "${ADMIN_USER_VALUE:-admin}" "$NEW_ADMIN_PASSWORD" > "$INITIAL_ADMIN"
  )
  chown root:root "$INITIAL_ADMIN"
  chmod 600 "$INITIAL_ADMIN"
  sed -i '/^ADMIN_PASSWORD=/d' "$CONF/stonepi.env" "$CONF/auth.env"
  printf 'ADMIN_PASSWORD=%s\n' "$NEW_ADMIN_PASSWORD" >> "$CONF/auth.env"
fi
# Upgrades: older installers wrote ADMIN_PASSWORD into stonepi.env, which every app loads.
# Only Auth needs it (the factory-password banner), so move it to auth.env.
if grep -q '^ADMIN_PASSWORD=' "$CONF/stonepi.env" 2>/dev/null; then
  if ! grep -q '^ADMIN_PASSWORD=' "$CONF/auth.env" 2>/dev/null; then
    grep '^ADMIN_PASSWORD=' "$CONF/stonepi.env" | tail -n1 >> "$CONF/auth.env"
  fi
  sed -i '/^ADMIN_PASSWORD=/d' "$CONF/stonepi.env"
fi
# auth.env holds the admin password; systemd reads EnvironmentFile as root, so keep it root-only.
if [[ -f "$CONF/auth.env" ]]; then
  chown root:root "$CONF/auth.env"
  chmod 600 "$CONF/auth.env"
fi

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
  # The shared StonePi packages install editable without a throwaway build
  # environment each (that cost a setuptools install per package per app), so
  # the venv needs setuptools itself (70.1+ builds wheels without the `wheel`
  # package); Python 3.12+ venvs ship without it.
  if ! "$dir/.venv/bin/python" -c 'import setuptools, sys; sys.exit(tuple(int(x) for x in setuptools.__version__.split(".")[:2]) < (70, 1))' 2>/dev/null; then
    "$dir/.venv/bin/pip" install --quiet --disable-pip-version-check "setuptools>=70.1"
  fi
  "$dir/.venv/bin/pip" install --quiet --disable-pip-version-check --no-build-isolation "${args[@]}"
  printf '%s\n' "$want" > "$stamp"
}

# Chromium for Playwright (EventTrakr, SportGuide): one shared copy, installed by root and
# readable by every service user. Their HOME is /opt/stonepi, so Playwright's default
# browser path ($HOME/.cache/ms-playwright) is this folder.
PW_BROWSERS=/opt/stonepi/.cache/ms-playwright
CHROMIUM_DONE=0
install_chromium() {
  local dir="$1"
  # EventTrakr and SportGuide pin the same Playwright: one download, one check.
  [[ "$CHROMIUM_DONE" -eq 1 ]] && return 0
  CHROMIUM_DONE=1
  # Shared Chromium launch lock across scraper apps.
  mkdir -p /run/stonepi
  chmod 1777 /run/stonepi
  mkdir -p "$PW_BROWSERS"
  # Every scraper launches headless, which only uses the headless shell: skip
  # the full browser (~150 MB less to download and unpack).
  if ! PLAYWRIGHT_BROWSERS_PATH="$PW_BROWSERS" "$dir/.venv/bin/python" -m playwright install --only-shell chromium; then
    echo "WARN: playwright install chromium failed — scrape sources need: sudo PLAYWRIGHT_BROWSERS_PATH=$PW_BROWSERS $dir/.venv/bin/python -m playwright install --only-shell chromium"
  fi
  # The main apt run already installed Chromium's libraries; install-deps (its
  # own apt-get update + install) runs only if Chromium can't start without it.
  if ! PLAYWRIGHT_BROWSERS_PATH="$PW_BROWSERS" "$dir/.venv/bin/python" -c 'from playwright.sync_api import sync_playwright
with sync_playwright() as p: p.chromium.launch().close()' >/dev/null 2>&1; then
    "$dir/.venv/bin/python" -m playwright install-deps chromium >/dev/null 2>&1 || true
  fi
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
    carthing) pkgs+=(stonepi_display) ;;
  esac
  if [[ "$extra" == "playwright" ]]; then
    pkgs+=(stonepi_browser)
  fi
  pip_sync "$name" "${pkgs[@]}"
  if [[ "$extra" == "playwright" ]]; then
    install_chromium "$dir"
  fi
  lock_venv "$dir/.venv"
}

# Venvs stay root:root and read-only to the service users: root runs their pip and python
# here and in stonepi-update-helper, so a service user able to write its venv could get
# code run as root. Nothing writes a venv at runtime (Chromium lives in $PW_BROWSERS;
# a skipped __pycache__ write is harmless). Also takes back venvs older installs chowned.
lock_venv() {
  local venv="$1"
  [[ -d "$venv" ]] || return 0
  chown -R root:root "$venv"
  # Dirs 755, files 644, executables keep 755.
  chmod -R u+rwX,go+rX,go-w "$venv"
}

echo
echo "Python packages"
install_app auth stonepi-auth
install_app dashboard stonepi-dash
install_app notify stonepi-notify
# Recover is a tiny root-owned escape hatch (HTTP Basic + optional SSO cookie).
if [[ -d "$DEST/apps/recover" ]]; then
  pip_sync recover stonepi_vault stonepi_auth
  lock_venv "$DEST/apps/recover/.venv"
fi
install_app newscast stonepi-news
install_app fileserve stonepi-files
install_app eventtrakr stonepi-events playwright
install_app pinboard stonepi-pin
install_app studio stonepi-studio
install_app pricescout stonepi-prices
install_app sportguide stonepi-sport playwright
install_app pricewatch stonepi-watch
install_app library stonepi-library
# Car Thing panel is optional: a tree without it still installs.
if [[ -f "$DEST/apps/carthing/requirements.txt" ]]; then
  install_app carthing stonepi-carthing
else
  echo "WARN: apps/carthing missing — Car Thing panel not installed." >&2
fi

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
  pricescout:stonepi-prices sportguide:stonepi-sport pricewatch:stonepi-watch library:stonepi-library
  carthing:stonepi-carthing)
fix_data_ownership() {
  local pair dir user
  for pair in "${APP_DATA_OWNERS[@]}"; do
    dir="${pair%%:*}"
    user="${pair#*:}"
    [[ -d "$DATA/$dir" ]] || continue
    chown -R "$user:$user" "$DATA/$dir"
    chmod 700 "$DATA/$dir"
  done
  # Kiwix (group stonepi-library) reads Library content: setgid 2750 instead of 700.
  if [[ -d "$DATA/library" ]]; then
    chown stonepi-library:stonepi-library "$DATA/library"
    chmod 2750 "$DATA/library"
  fi
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
ensure_writable_data stonepi-library "$DATA/library" "$DEST/apps/library/data"
ensure_writable_data stonepi-carthing "$DATA/carthing" "$DEST/apps/carthing/data"
mkdir -p "$DATA/fileserve/hosted"
chown -R stonepi-files:stonepi-files "$DATA/fileserve"

# One-time move of files an app kept under /opt/stonepi/apps/<app>/data before its env file
# had STONEPI_DATA_DIR (Pis set up from early overlays): e.g. FileServe's hosted pages and
# NewsCast's fetched favicons, which otherwise 404 once the app reads /var/lib/stonepi/<app>.
# Never overwrites, never copies databases, secrets or keys (their real copies live in
# /var/lib via DATABASE_URL and the Vault); the /opt copy is left as it was.
for pair in "${APP_DATA_OWNERS[@]}"; do
  app="${pair%%:*}"
  old="$DEST/apps/$app/data"
  stamp="$DATA/$app/.moved-from-opt"
  [[ -d "$old" && -d "$DATA/$app" && ! -e "$stamp" ]] || continue
  if [[ -n "$(find "$old" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    if rsync -a --ignore-existing \
      --exclude '*.db' --exclude '*.db-*' --exclude '*.sqlite' --exclude '*.sqlite-*' \
      --exclude 'session.secret' --exclude 'tls/' --exclude '*.key' --exclude '*.pem' \
      --exclude 'backups/' --exclude 'updates/' --exclude '*.log' --exclude '.env' \
      "$old/" "$DATA/$app/"; then
      echo "Moved $app files from $old to $DATA/$app (kept the original)."
    else
      # No stamp: the next installer run tries again.
      echo "WARN: could not copy $app files from $old to $DATA/$app; re-run the installer to retry." >&2
      continue
    fi
  fi
  touch "$stamp"
done

# Vault readable by app service users (group stonepi-vault); dashboard can write.
groupadd --system stonepi-vault 2>/dev/null || true
for u in stonepi-dash stonepi-auth stonepi-notify stonepi-news stonepi-files stonepi-events stonepi-pin stonepi-studio stonepi-prices stonepi-sport stonepi-watch stonepi-library stonepi-carthing; do
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
install -m 755 "$DEST/deploy/backup/stonepi-backup-library.py" /usr/local/sbin/stonepi-backup-library
install -m 755 "$DEST/deploy/backup/stonepi-failover-monitor.sh" /usr/local/sbin/stonepi-failover-monitor
install -m 755 "$DEST/deploy/stonepi-tailscale-acl.sh" /usr/local/sbin/stonepi-tailscale-acl
# Settings → Updates: Dashboard downloads, this root helper installs (app code is root-owned).
install -m 755 "$DEST/deploy/stonepi-update-helper.py" /usr/local/sbin/stonepi-update-helper
sed -i 's/\r$//' /usr/local/sbin/stonepi-update-helper  # a CRLF shebang (Windows copy) would not run
# Services page: start/stop/restart/is-active/logs for one stonepi-* unit. A fixed helper
# instead of sudoers wildcards, which would match extra units and arguments.
install -o root -g root -m 755 "$DEST/deploy/stonepi-service-helper.sh" /usr/local/sbin/stonepi-service-helper
sed -i 's/\r$//' /usr/local/sbin/stonepi-service-helper  # a CRLF shebang (Windows copy) would not run
mkdir -p "$DATA/updates"
chmod 700 "$DATA/updates"
mkdir -p /etc/nginx/snippets
install -m 644 "$DEST/deploy/nginx/stonepi-failover.conf" /etc/nginx/snippets/stonepi-failover.conf
# Recover (root) only trusts nginx's X-Real-IP / X-Forwarded-Host when the request also
# carries this shared secret, so another local process can't forge a client IP to dodge
# the per-IP login lockout. Token generated once (root 0600); the snippet nginx includes in
# location /recover/ is rebuilt from it every run, before nginx -t below. nginx's master
# process reads config as root, so the snippet stays root 0600 too.
RECOVER_PROXY_TOKEN_FILE="$CONF/recover-proxy.token"
if [[ ! -s "$RECOVER_PROXY_TOKEN_FILE" ]]; then
  (umask 077; python3 -c 'import secrets; print(secrets.token_hex(32))' > "$RECOVER_PROXY_TOKEN_FILE")
fi
chown root:root "$RECOVER_PROXY_TOKEN_FILE"
chmod 600 "$RECOVER_PROXY_TOKEN_FILE"
RECOVER_PROXY_TOKEN="$(tr -dc '0-9a-fA-F' < "$RECOVER_PROXY_TOKEN_FILE")"
(
  umask 077
  printf '# Generated by deploy/install.sh from %s; do not edit.\nproxy_set_header X-StonePi-Proxy "%s";\n' \
    "$RECOVER_PROXY_TOKEN_FILE" "$RECOVER_PROXY_TOKEN" > /etc/nginx/snippets/stonepi-recover-proxy.conf.tmp
)
chown root:root /etc/nginx/snippets/stonepi-recover-proxy.conf.tmp
chmod 600 /etc/nginx/snippets/stonepi-recover-proxy.conf.tmp
mv -f /etc/nginx/snippets/stonepi-recover-proxy.conf.tmp /etc/nginx/snippets/stonepi-recover-proxy.conf
RECOVER_PROXY_TOKEN=""
if [[ -f "$DEST/deploy/udev/99-stonepi-backup.rules" ]]; then
  install -m 644 "$DEST/deploy/udev/99-stonepi-backup.rules" /etc/udev/rules.d/99-stonepi-backup.rules
  udevadm control --reload-rules >/dev/null 2>&1 || true
  udevadm trigger >/dev/null 2>&1 || true
fi
if [[ ! -f "$CONF/backup.conf" ]]; then
  cat > "$CONF/backup.conf" <<'EOF'
LABEL=STONEPI-BACKUP
UUID=
APPS="stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch stonepi-library"
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
if [[ -f "$CONF/backup.conf" ]] && ! grep -q 'stonepi-library' "$CONF/backup.conf" 2>/dev/null; then
  sed -i 's/^APPS="\(.*\)"/APPS="\1 stonepi-library"/' "$CONF/backup.conf" 2>/dev/null || true
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

for unit in stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch stonepi-library stonepi-kiwix stonepi-carthing stonepi-backup stonepi-local-backup stonepi-recover stonepi-failover-monitor; do
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
# From Tailscale's signed apt repository (pinned to this OS release, updated by apt like
# everything else) rather than piping its install script to sh. Optional: a failure only
# warns (the Network tab shows Tailscale as not installed) and the ready check below
# reports it, so re-running the installer retries.
TS_KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
install_tailscale() {
  if command -v tailscale >/dev/null 2>&1; then
    return 0
  fi
  local codename
  codename="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_CODENAME:-}")"
  if [[ -z "$codename" ]]; then
    echo "WARN: unknown OS release — cannot pick Tailscale's apt repository." >&2
    return 1
  fi
  echo "Installing Tailscale (pkgs.tailscale.com, $codename)…"
  if ! curl -fsSL --retry 3 "https://pkgs.tailscale.com/stable/debian/${codename}.noarmor.gpg" -o "$TS_KEYRING.part"; then
    rm -f "$TS_KEYRING.part"
    echo "WARN: could not download Tailscale's signing key." >&2
    return 1
  fi
  mv "$TS_KEYRING.part" "$TS_KEYRING"
  chmod 644 "$TS_KEYRING"
  printf 'deb [signed-by=%s] https://pkgs.tailscale.com/stable/debian %s main\n' "$TS_KEYRING" "$codename" \
    > /etc/apt/sources.list.d/tailscale.list
  if ! apt-get -o DPkg::Lock::Timeout=300 update -y \
    || ! apt-get -o DPkg::Lock::Timeout=300 install -y tailscale; then
    echo "WARN: apt could not install tailscale." >&2
    return 1
  fi
  command -v tailscale >/dev/null 2>&1
}
TAILSCALE_OK=1
if ! install_tailscale; then
  TAILSCALE_OK=0
  echo "WARNING: Tailscale is not installed — remote access stays off until it is." >&2
  echo "         Re-run this installer to retry (the ready check below lists it)." >&2
fi
if [[ "$TAILSCALE_OK" -eq 1 ]]; then
  systemctl enable --now tailscaled >/dev/null 2>&1 || systemctl enable --now tailscaled.service >/dev/null 2>&1 || true
  if ! systemctl is-active --quiet tailscaled && ! systemctl is-active --quiet tailscaled.service; then
    echo "WARNING: tailscaled is not active yet; Network connect may start it via the helper." >&2
  fi
fi
if [[ ! -f "$DEST/deploy/stonepi-tailscale.sh" ]]; then
  echo "Missing $DEST/deploy/stonepi-tailscale.sh — aborting." >&2
  exit 1
fi
# The helper reports "not installed" to the Network tab when tailscale is missing.
install -m 755 "$DEST/deploy/stonepi-tailscale.sh" /usr/local/sbin/stonepi-tailscale
if [[ "$TAILSCALE_OK" -eq 1 ]] && ! /usr/local/sbin/stonepi-tailscale install-check >/dev/null 2>&1; then
  echo "WARNING: stonepi-tailscale install-check failed — see the ready check below." >&2
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

# Services controls go through stonepi-service-helper (one validated stonepi-* unit, no
# pager), never wildcard systemctl/journalctl rules. Checked with visudo before it goes live:
# sudo ignores files in sudoers.d whose name contains a dot, so the draft is inert.
cat > /etc/sudoers.d/.stonepi-dash.new <<'EOF'
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-service-helper
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-tailscale
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-hostname
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-backup-helper
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-tailscale-acl
stonepi-dash ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-update-helper
EOF
chmod 440 /etc/sudoers.d/.stonepi-dash.new
if command -v visudo >/dev/null 2>&1 && ! visudo -cf /etc/sudoers.d/.stonepi-dash.new >/dev/null 2>&1; then
  rm -f /etc/sudoers.d/.stonepi-dash.new
  echo "ERROR: new /etc/sudoers.d/stonepi-dash failed visudo check — kept the previous one." >&2
else
  mv -f /etc/sudoers.d/.stonepi-dash.new /etc/sudoers.d/stonepi-dash
fi

# Car Thing panel (optional): Notify switches stonepi-carthing on/off through this root helper;
# the unit is installed above but never enabled here. udev lets the service user reach the
# device's adb. A tree without the Car Thing files skips them with a warning.
if [[ -f "$DEST/deploy/stonepi-carthing-helper.sh" ]]; then
  install -m 755 "$DEST/deploy/stonepi-carthing-helper.sh" /usr/local/sbin/stonepi-carthing-helper
  sed -i 's/\r$//' /usr/local/sbin/stonepi-carthing-helper  # a CRLF shebang (Windows copy) would not run
  cat > /etc/sudoers.d/stonepi-carthing <<'EOF'
stonepi-notify ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-carthing-helper
EOF
  chmod 440 /etc/sudoers.d/stonepi-carthing
  if command -v visudo >/dev/null 2>&1 && ! visudo -cf /etc/sudoers.d/stonepi-carthing >/dev/null 2>&1; then
    echo "WARN: /etc/sudoers.d/stonepi-carthing failed visudo check, removing it." >&2
    rm -f /etc/sudoers.d/stonepi-carthing
  fi
else
  echo "WARN: deploy/stonepi-carthing-helper.sh missing — Car Thing panel cannot be switched on." >&2
fi
if [[ -f "$DEST/deploy/carthing/51-stonepi-carthing.rules" ]]; then
  install -m 644 "$DEST/deploy/carthing/51-stonepi-carthing.rules" /etc/udev/rules.d/51-stonepi-carthing.rules
  sed -i 's/\r$//' /etc/udev/rules.d/51-stonepi-carthing.rules
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger --subsystem-match=usb 2>/dev/null || true
else
  echo "WARN: deploy/carthing/51-stonepi-carthing.rules missing — Car Thing adb access not set up." >&2
fi

# Library: root helper for apt (kiwix-tools), storage mounts, and stonepi-kiwix control.
# stonepi-kiwix.service is installed above but never enabled here: the helper enables
# and starts it once content exists.
install -m 755 "$DEST/deploy/stonepi-library-helper.sh" /usr/local/sbin/stonepi-library-helper
sed -i 's/\r$//' /usr/local/sbin/stonepi-library-helper  # a CRLF shebang (Windows copy) would not run
cat > /etc/sudoers.d/stonepi-library <<'EOF'
stonepi-library ALL=(root) NOPASSWD: /usr/local/sbin/stonepi-library-helper
EOF
chmod 440 /etc/sudoers.d/stonepi-library
if command -v visudo >/dev/null 2>&1 && ! visudo -cf /etc/sudoers.d/stonepi-library >/dev/null 2>&1; then
  echo "WARN: /etc/sudoers.d/stonepi-library failed visudo check, removing it." >&2
  rm -f /etc/sudoers.d/stonepi-library
fi

systemctl daemon-reload
systemctl enable --now stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch stonepi-library
systemctl enable --now stonepi-recover >/dev/null 2>&1 || true
systemctl enable --now stonepi-failover-monitor >/dev/null 2>&1 || true
# enable --now leaves an already-running unit on its old code: restart so an upgrade applies.
systemctl restart stonepi-recover >/dev/null 2>&1 || true
systemctl try-restart stonepi-failover-monitor >/dev/null 2>&1 || true
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

# Recover's HTTP Basic password (a root console) lives only in root-only recover.passwd,
# never in the Vault (every app service user can read the Vault) or stonepi.env (every app
# loads it). Older releases kept it in the Vault and that copy was the live one: move it into
# recover.passwd and drop it from the Vault.
RECOVER_PASSWD="$CONF/recover.passwd"
RECOVER_FROM_VAULT="$(STONEPI_VAULT_DIR="$DATA/vault" "$DEST/apps/dashboard/.venv/bin/python" - <<'PY' 2>/dev/null || true
import os
from pathlib import Path
from stonepi_vault import configure

vault = configure(Path(os.environ["STONEPI_VAULT_DIR"]))
value = ""
for key in ("STONEPI_RECOVER_PASSWORD", "STONEPI_RECOVERY_PASSWORD"):
    value = value or (vault.get(key, "") or "").strip()
    vault.delete(key)
print(value)
PY
)"
if [[ -n "$RECOVER_FROM_VAULT" ]]; then
  (umask 077; printf 'stonepi:%s\n' "$RECOVER_FROM_VAULT" > "$RECOVER_PASSWD")
  echo "Recover password moved out of the Vault into $RECOVER_PASSWD"
fi
RECOVER_FROM_VAULT=""
sed -i '/^STONEPI_RECOVERY\{0,1\}_PASSWORD=/d' "$CONF/stonepi.env"
if [[ ! -s "$RECOVER_PASSWD" ]]; then
  (umask 077; python3 -c 'import secrets; print("stonepi:" + secrets.token_urlsafe(12))' > "$RECOVER_PASSWD")
  echo "Recover login: user stonepi — password in $RECOVER_PASSWD (sudo cat it; change it in Dashboard → Settings)"
fi
chown root:root "$RECOVER_PASSWD"
chmod 600 "$RECOVER_PASSWD"

# The migrations above ran as root after the services had started and opened their
# databases (SQLite can leave root-owned -wal/-shm files), so put every owner back.
fix_data_ownership

# Pick up stonepi-vault supplementary group + any new env keys.
systemctl restart stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch stonepi-library >/dev/null 2>&1 || true
# Optional services: only restart what the household switched on.
if systemctl is-enabled --quiet stonepi-carthing 2>/dev/null; then
  systemctl restart stonepi-carthing >/dev/null 2>&1 || true
fi
# The reader only runs once content is installed; restart it if it is, so a
# changed unit applies now rather than at the next reboot.
systemctl try-restart stonepi-kiwix >/dev/null 2>&1 || true

# Wait briefly for apps, then verify edge + backends (catches welcome-page / 502 installs).
sleep 3
echo
echo "Health checks"
UNITS=(stonepi-auth stonepi-dashboard stonepi-notify stonepi-newscast stonepi-fileserve stonepi-eventtrakr stonepi-pinboard stonepi-studio stonepi-pricescout stonepi-sportguide stonepi-pricewatch stonepi-library)
failed_units=0
for u in "${UNITS[@]}"; do
  if systemctl is-active --quiet "$u"; then
    echo "  ok  $u"
  else
    echo "  FAIL $u (not active)"
    failed_units=$((failed_units + 1))
  fi
done
# Twelve Python apps restarting together take a while on a Pi: poll until each answers,
# sharing one deadline, instead of trusting a fixed sleep.
HEALTH_DEADLINE=$((SECONDS + 120))
check() {
  local url="$1"
  until curl -fs -o /dev/null -m 5 "$url"; do
    if (( SECONDS >= HEALTH_DEADLINE )); then
      echo "  WARN $url"
      return 0
    fi
    sleep 2
  done
  echo "  ok  $url"
}
# Each enabled service's own /healthz, on the port its unit or /etc/stonepi/<app>.env sets.
# Kiwix (no /healthz, runs only with content) and the Car Thing panel unless switched on
# are not enabled by the installer, so they are left out.
unit_port() {
  local unit="$1" port
  port="$(systemctl show -p Environment --value "$unit" 2>/dev/null | tr ' ' '\n' | sed -n 's/^PORT=//p' | tail -n1)"
  if [[ -z "$port" && -f "$CONF/${unit#stonepi-}.env" ]]; then
    port="$(sed -n 's/^PORT=//p' "$CONF/${unit#stonepi-}.env" | tail -n1)"
  fi
  printf '%s' "$port"
}
HEALTH_UNITS=("${UNITS[@]}")
for u in stonepi-recover stonepi-carthing; do
  if systemctl is-enabled --quiet "$u" 2>/dev/null; then
    HEALTH_UNITS+=("$u")
  fi
done
for u in "${HEALTH_UNITS[@]}"; do
  port="$(unit_port "$u")"
  if [[ "$port" =~ ^[0-9]+$ ]]; then
    check "http://127.0.0.1:${port}/healthz"
  else
    echo "  WARN $u (no PORT found to check)"
  fi
done
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
rc "Dashboard can control services" sudo -u stonepi-dash sudo -n /usr/local/sbin/stonepi-service-helper is-active stonepi-auth
rc "Dashboard can read service logs" sudo -u stonepi-dash sudo -n /usr/local/sbin/stonepi-service-helper logs stonepi-auth 1
rc "Dashboard can run backups" sudo -u stonepi-dash sudo -n /usr/local/sbin/stonepi-backup-helper list
rc "Library can run its helper" sudo -u stonepi-library sudo -n -l /usr/local/sbin/stonepi-library-helper
rc "Library reader (Kiwix) is in the Library group" sh -c "id -nG stonepi-kiwix | grep -qw stonepi-library"
rc "Library reader (Kiwix) can read Library data" sudo -u stonepi-kiwix sh -c "test -x '$DATA/library' && { test ! -e '$DATA/library/library.xml' || test -r '$DATA/library/library.xml'; }"
if systemctl is-enabled --quiet stonepi-kiwix 2>/dev/null; then
  rc "Library reader (Kiwix) is running" systemctl is-active --quiet stonepi-kiwix
fi
if [[ -x /usr/local/sbin/stonepi-carthing-helper ]]; then
  rc "Notify can switch the Car Thing panel" sudo -u stonepi-notify sudo -n /usr/local/sbin/stonepi-carthing-helper status
  # The sudo above runs outside the unit; inside it NoNewPrivileges=yes would block sudo.
  rc "Notify's service may use sudo (NoNewPrivileges=no)" test "$(systemctl show -p NoNewPrivileges --value stonepi-notify 2>/dev/null)" = no
fi
if systemctl is-enabled --quiet stonepi-carthing 2>/dev/null; then
  rc "Car Thing panel has adb" command -v adb
  rc "Car Thing panel is running" systemctl is-active --quiet stonepi-carthing
fi
rc "Tailscale is installed (remote access; re-run the installer to retry)" command -v tailscale
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
echo "  Library    http://${HOSTNAME_VALUE}.local/library/"
echo "  Cockpit    https://${HOSTNAME_VALUE}.local:9090  (or https://<pi-lan-ip>:9090)"
if [[ -n "$NEW_ADMIN_PASSWORD" ]]; then
  echo "  Sign in:   ${ADMIN_USER_VALUE:-admin} / $NEW_ADMIN_PASSWORD  — change it under Settings → General → Your password."
  echo "             Saved for root only in $INITIAL_ADMIN (sudo cat $INITIAL_ADMIN)."
else
  echo "  Sign in with your StonePi account (first password: sudo cat $INITIAL_ADMIN, if present)."
fi
echo "  Recover    http://${HOSTNAME_VALUE}.local/recover/  (or http://<pi-ip>:8099/ if the dashboard is down)"
echo "             User stonepi; password: sudo cut -d: -f2- $RECOVER_PASSWD  — change it in Dashboard → Settings → Vault."
echo "  Helper:    stonepi status | stonepi urls | stonepi restart | stonepi logs"
echo "  Backup:    plug in USB labelled STONEPI-BACKUP, or enable local schedule in Settings → Backup"
echo "  Docs:      $DEST/deploy/INSTALL.md"
echo
# Last step: the bootstrap only treats StonePi as installed once this says "complete".
printf 'complete\n' > "$DATA/install-state"
chmod 644 "$DATA/install-state"
