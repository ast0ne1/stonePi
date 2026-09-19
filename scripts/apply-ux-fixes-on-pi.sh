#!/bin/bash
# Apply UX-fix overlay files from /tmp onto /opt/stonepi and restart units.
# Run as root: sudo bash /tmp/apply-ux-fixes-on-pi.sh
set -euo pipefail

DEST=/opt/stonepi

cp_owner() {
  local src="$1" dest="$2" owner="$3"
  if [[ ! -f "$src" ]]; then
    echo "Missing overlay file: $src" >&2
    exit 1
  fi
  install -o "$owner" -g "$owner" -m 0644 "$src" "$dest"
}

echo "Applying StonePi UX fixes under $DEST ..."

if [[ -f /tmp/stonepi-VERSION ]]; then
  install -o root -g root -m 0644 /tmp/stonepi-VERSION "$DEST/VERSION"
fi

cp_owner /tmp/sp-http.py "$DEST/packages/stonepi_auth/stonepi_auth/http.py" root
cp_owner /tmp/sp-auth-init.py "$DEST/packages/stonepi_auth/stonepi_auth/__init__.py" root

cp_owner /tmp/dash-config.py "$DEST/apps/dashboard/app/config.py" stonepi-dash
cp_owner /tmp/dash-routes.py "$DEST/apps/dashboard/app/routes.py" stonepi-dash
cp_owner /tmp/dash-services.py "$DEST/apps/dashboard/app/services.py" stonepi-dash
cp_owner /tmp/dash-base.html "$DEST/apps/dashboard/app/templates/base.html" stonepi-dash
cp_owner /tmp/dash-overview.html "$DEST/apps/dashboard/app/templates/overview.html" stonepi-dash
cp_owner /tmp/dash-settings.html "$DEST/apps/dashboard/app/templates/settings.html" stonepi-dash
cp_owner /tmp/dash-theme-boot.html "$DEST/apps/dashboard/app/templates/_theme_boot.html" stonepi-dash
if [[ -f /tmp/dash-household-users.html ]]; then
  cp_owner /tmp/dash-household-users.html "$DEST/apps/dashboard/app/templates/_household_users.html" stonepi-dash
fi
if [[ -f /tmp/dash-home.html ]]; then
  cp_owner /tmp/dash-home.html "$DEST/apps/dashboard/app/templates/home.html" stonepi-dash
fi
if [[ -f /tmp/dash-users.html ]]; then
  cp_owner /tmp/dash-users.html "$DEST/apps/dashboard/app/templates/users.html" stonepi-dash
fi
cp_owner /tmp/dash-app.js "$DEST/apps/dashboard/app/static/js/app.js" stonepi-dash
cp_owner /tmp/dash-app.css "$DEST/apps/dashboard/app/static/css/app.css" stonepi-dash
cp_owner /tmp/dash-init.py "$DEST/apps/dashboard/app/__init__.py" stonepi-dash

cp_owner /tmp/auth-login.html "$DEST/apps/auth/app/templates/login.html" stonepi-auth
cp_owner /tmp/auth-status.html "$DEST/apps/auth/app/templates/status.html" stonepi-auth
cp_owner /tmp/auth-theme-boot.html "$DEST/apps/auth/app/templates/_theme_boot.html" stonepi-auth
cp_owner /tmp/auth-routes.py "$DEST/apps/auth/app/routes.py" stonepi-auth
cp_owner /tmp/auth-init.py "$DEST/apps/auth/app/__init__.py" stonepi-auth
if [[ -f /tmp/auth-users.py ]]; then
  cp_owner /tmp/auth-users.py "$DEST/apps/auth/app/users.py" stonepi-auth
fi

cp_owner /tmp/news-auth.py "$DEST/apps/newscast/app/auth.py" stonepi-news
if [[ -f /tmp/news-ui.py ]]; then
  cp_owner /tmp/news-ui.py "$DEST/apps/newscast/app/routers/ui.py" stonepi-news
fi
cp_owner /tmp/news-base.html "$DEST/apps/newscast/app/templates/base.html" stonepi-news
cp_owner /tmp/news-login.html "$DEST/apps/newscast/app/templates/login.html" stonepi-news
if [[ -f /tmp/news-settings.html ]]; then
  cp_owner /tmp/news-settings.html "$DEST/apps/newscast/app/templates/settings.html" stonepi-news
fi
cp_owner /tmp/news-theme-boot.html "$DEST/apps/newscast/app/templates/_theme_boot.html" stonepi-news
cp_owner /tmp/news-app.js "$DEST/apps/newscast/app/static/js/app.js" stonepi-news

cp_owner /tmp/files-auth.py "$DEST/apps/fileserve/app/auth.py" stonepi-files
cp_owner /tmp/files-pages.py "$DEST/apps/fileserve/app/services/pages.py" stonepi-files
cp_owner /tmp/files-main.py "$DEST/apps/fileserve/app/main.py" stonepi-files
cp_owner /tmp/files-base.html "$DEST/apps/fileserve/app/templates/base.html" stonepi-files
cp_owner /tmp/files-pages.html "$DEST/apps/fileserve/app/templates/pages.html" stonepi-files
cp_owner /tmp/files-browse.html "$DEST/apps/fileserve/app/templates/browse.html" stonepi-files
cp_owner /tmp/files-login.html "$DEST/apps/fileserve/app/templates/login.html" stonepi-files
if [[ -f /tmp/files-settings.html ]]; then
  cp_owner /tmp/files-settings.html "$DEST/apps/fileserve/app/templates/settings.html" stonepi-files
fi
cp_owner /tmp/files-theme-boot.html "$DEST/apps/fileserve/app/templates/_theme_boot.html" stonepi-files
cp_owner /tmp/files-app.js "$DEST/apps/fileserve/app/static/js/app.js" stonepi-files

cp_owner /tmp/events-auth.py "$DEST/apps/eventtrakr/app/services/auth.py" stonepi-events
if [[ -f /tmp/events-settings-routes.py ]]; then
  cp_owner /tmp/events-settings-routes.py "$DEST/apps/eventtrakr/app/routes/settings.py" stonepi-events
fi
cp_owner /tmp/events-base.html "$DEST/apps/eventtrakr/app/templates/base.html" stonepi-events
cp_owner /tmp/events-login.html "$DEST/apps/eventtrakr/app/templates/login.html" stonepi-events
if [[ -f /tmp/events-settings.html ]]; then
  cp_owner /tmp/events-settings.html "$DEST/apps/eventtrakr/app/templates/settings.html" stonepi-events
fi
cp_owner /tmp/events-theme-boot.html "$DEST/apps/eventtrakr/app/templates/_theme_boot.html" stonepi-events
cp_owner /tmp/events-app.js "$DEST/apps/eventtrakr/app/static/js/app.js" stonepi-events

cp_owner /tmp/pin-routes.py "$DEST/apps/pinboard/app/routes.py" stonepi-pin
cp_owner /tmp/pin-base.html "$DEST/apps/pinboard/app/templates/base.html" stonepi-pin
cp_owner /tmp/pin-theme-boot.html "$DEST/apps/pinboard/app/templates/_theme_boot.html" stonepi-pin
cp_owner /tmp/pin-app.js "$DEST/apps/pinboard/app/static/js/app.js" stonepi-pin

cp_owner /tmp/studio-routes.py "$DEST/apps/studio/app/routes.py" stonepi-studio
cp_owner /tmp/studio-base.html "$DEST/apps/studio/app/templates/base.html" stonepi-studio
cp_owner /tmp/studio-theme-boot.html "$DEST/apps/studio/app/templates/_theme_boot.html" stonepi-studio
cp_owner /tmp/studio-app.js "$DEST/apps/studio/app/static/js/app.js" stonepi-studio

# Editable installs point at $DEST/packages/stonepi_auth — no pip needed for http.py.

echo "Restarting units..."
systemctl restart \
  stonepi-auth \
  stonepi-dashboard \
  stonepi-newscast \
  stonepi-fileserve \
  stonepi-eventtrakr \
  stonepi-pinboard \
  stonepi-studio

sleep 2
systemctl is-active \
  stonepi-auth \
  stonepi-dashboard \
  stonepi-newscast \
  stonepi-fileserve \
  stonepi-eventtrakr \
  stonepi-pinboard \
  stonepi-studio

echo "Done. Hard-refresh http://stonepi.home/ and smoke-test:"
echo "  - Launcher tiles stay on .home (not flip to .local)"
echo "  - Overview backup card (failed/skipped honest)"
echo "  - Sign out stays on /auth/logout (not 127.0.0.1:8011)"
echo "  - Non-admin Settings → Appearance"
echo "  - FileServe /browse: non-admin sees own pages only; anon sees admin/root only"
echo "  - Ocean palette survives logout/login on the same hostname"
