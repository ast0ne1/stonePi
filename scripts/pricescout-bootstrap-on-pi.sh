#!/bin/bash
# Runs on the Pi after scp of the overlay zip. Invoked via:
#   sudo bash /tmp/stonepi-pricescout-bootstrap.sh
set -euxo pipefail

ZIP=/tmp/stonepi-pricescout-overlay.zip
OUT=/tmp/stonepi-pricescout-overlay

test -f "$ZIP"
rm -rf "$OUT"
mkdir -p "$OUT"
python3 -m zipfile -e "$ZIP" "$OUT"
test -f "$OUT/apply-pricescout-on-pi.sh"
test -d "$OUT/apps/pricescout/app"
sed -i 's/\r$//' "$OUT/apply-pricescout-on-pi.sh" "$OUT/scripts/apply-pricescout-on-pi.sh" 2>/dev/null || true
chmod +x "$OUT/apply-pricescout-on-pi.sh"
bash "$OUT/apply-pricescout-on-pi.sh" "$OUT"
