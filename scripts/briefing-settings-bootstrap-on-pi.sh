#!/bin/bash
# Runs on the Pi after scp of the overlay zip. Invoked via:
#   sudo bash /tmp/stonepi-briefing-settings-bootstrap.sh
set -euxo pipefail

ZIP=/tmp/stonepi-briefing-settings-overlay.zip
OUT=/tmp/stonepi-briefing-settings-overlay

test -f "$ZIP"
rm -rf "$OUT"
mkdir -p "$OUT"
python3 -m zipfile -e "$ZIP" "$OUT"
test -f "$OUT/apply-briefing-settings-on-pi.sh"
test -d "$OUT/apps/newscast/app"
test -d "$OUT/apps/dashboard/app"
test -d "$OUT/apps/pricescout/app"
sed -i 's/\r$//' "$OUT/apply-briefing-settings-on-pi.sh" "$OUT/scripts/apply-briefing-settings-on-pi.sh" 2>/dev/null || true
chmod +x "$OUT/apply-briefing-settings-on-pi.sh"
bash "$OUT/apply-briefing-settings-on-pi.sh" "$OUT"
