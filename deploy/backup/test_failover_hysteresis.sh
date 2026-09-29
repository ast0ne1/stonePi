#!/bin/bash
# Unit test for failover_hysteresis (no curl / systemctl).
set -euo pipefail
SCRIPT="$(cd "$(dirname "$0")" && pwd)/stonepi-failover-monitor.sh"
run() { bash "$SCRIPT" --test-hysteresis "$@"; }

assert_eq() {
  local got="$1" want="$2" label="$3"
  if [[ "$got" != "$want" ]]; then
    echo "FAIL $label: got='$got' want='$want'" >&2
    exit 1
  fi
  echo "ok $label"
}

# miss 1/2/3 → trip on third
assert_eq "$(run 0 0 0 0 3 2)" "1 0 none" "miss1"
assert_eq "$(run 0 1 0 0 3 2)" "2 0 none" "miss2"
assert_eq "$(run 0 2 0 0 3 2)" "0 0 on" "miss3_trip"

# pass while on: need 2
assert_eq "$(run 1 0 0 1 3 2)" "0 1 none" "pass1_still_on"
assert_eq "$(run 1 0 1 1 3 2)" "0 0 off" "pass2_clear"

# success while off resets misses
assert_eq "$(run 1 2 0 0 3 2)" "0 1 none" "ready_resets_misses"

# miss while already on increments but does not re-trip
assert_eq "$(run 0 0 0 1 3 2)" "1 0 none" "miss_while_on"

echo "all failover hysteresis tests passed"
