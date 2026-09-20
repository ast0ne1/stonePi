#!/bin/bash
# Runs on the Pi after scp of the overlay zip. Invoked via:
#   sudo bash /tmp/stonepi-display-bootstrap.sh
set -euxo pipefail

ZIP=/tmp/stonepi-display-overlay.zip
OUT=/tmp/stonepi-display-overlay

test -f "$ZIP"
rm -rf "$OUT"
mkdir -p "$OUT"
python3 -m zipfile -e "$ZIP" "$OUT"
test -f "$OUT/apply-display-fixes-on-pi.sh"
test -f "$OUT/apps/dashboard/app/display.py"
sed -i 's/\r$//' "$OUT/apply-display-fixes-on-pi.sh" "$OUT/scripts/apply-display-fixes-on-pi.sh" 2>/dev/null || true
chmod +x "$OUT/apply-display-fixes-on-pi.sh"
bash "$OUT/apply-display-fixes-on-pi.sh" "$OUT"
