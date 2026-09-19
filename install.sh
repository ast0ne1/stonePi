#!/usr/bin/env bash
# Convenience wrapper so you can run: sudo bash install.sh
# from the repo root (including a copy on the Pi boot partition).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT/deploy/install.sh" "$@"
