#!/bin/bash
# Runs on the Pi after scp of the overlay zip. Invoked via:
#   sudo bash /tmp/stonepi-sportguide-bootstrap.sh
set -euxo pipefail

ZIP=/tmp/stonepi-sportguide-overlay.zip
OUT=/tmp/stonepi-sportguide-overlay

test -f "$ZIP"
rm -rf "$OUT"
mkdir -p "$OUT"
python3 -m zipfile -e "$ZIP" "$OUT"
test -f "$OUT/apply-sportguide-on-pi.sh"
test -d "$OUT/apps/sportguide/app"
sed -i 's/\r$//' "$OUT/apply-sportguide-on-pi.sh" "$OUT/scripts/apply-sportguide-on-pi.sh" 2>/dev/null || true
chmod +x "$OUT/apply-sportguide-on-pi.sh"
bash "$OUT/apply-sportguide-on-pi.sh" "$OUT"
