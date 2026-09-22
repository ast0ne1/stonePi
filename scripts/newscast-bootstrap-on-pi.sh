#!/bin/bash
# Runs on the Pi after scp of the overlay zip. Invoked via:
#   sudo bash /tmp/stonepi-newscast-bootstrap.sh
set -euxo pipefail

ZIP=/tmp/stonepi-newscast-overlay.zip
OUT=/tmp/stonepi-newscast-overlay

test -f "$ZIP"
rm -rf "$OUT"
mkdir -p "$OUT"
python3 -m zipfile -e "$ZIP" "$OUT"
test -f "$OUT/apply-newscast-on-pi.sh"
test -d "$OUT/apps/newscast/app"
sed -i 's/\r$//' "$OUT/apply-newscast-on-pi.sh" "$OUT/scripts/apply-newscast-on-pi.sh" 2>/dev/null || true
chmod +x "$OUT/apply-newscast-on-pi.sh"
bash "$OUT/apply-newscast-on-pi.sh" "$OUT"
