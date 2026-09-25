#!/bin/bash
# Runs on the Pi after scp of the overlay zip. Invoked via:
#   sudo bash /tmp/stonepi-eventtrakr-bootstrap.sh
set -euxo pipefail

ZIP=/tmp/stonepi-eventtrakr-overlay.zip
OUT=/tmp/stonepi-eventtrakr-overlay

test -f "$ZIP"
rm -rf "$OUT"
mkdir -p "$OUT"
python3 -m zipfile -e "$ZIP" "$OUT"
test -f "$OUT/apply-eventtrakr-on-pi.sh"
test -d "$OUT/apps/eventtrakr/app"
sed -i 's/\r$//' "$OUT/apply-eventtrakr-on-pi.sh" "$OUT/scripts/apply-eventtrakr-on-pi.sh" 2>/dev/null || true
chmod +x "$OUT/apply-eventtrakr-on-pi.sh"
bash "$OUT/apply-eventtrakr-on-pi.sh" "$OUT"
