# Changelog — Recover

## Unreleased

## 0.1.8 — 2026-09-29

_First release as Recover (was Recovery). Carries the platform version (system app); ships inside the platform zip._

### Changed
- Console shows the Recover version.
- Ships inside the platform zip (catalog `ships_with: platform`); a Dashboard platform install overlays `apps/recover`.
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- Renamed from Recovery: `apps/recover`, unit `stonepi-recover`, path `/recover/`, `/etc/stonepi/recover.passwd`, Vault `STONEPI_RECOVER_PASSWORD`. Startup migrates the old passwd file and Vault key; old `/recovery/` URLs redirect.
- Console left-aligned and full-width on laptop/desktop; portal-style primary Refresh + icon Sign out
- Services card: coloured status pills; icon Logs/Network/Reboot/Clear failover and Restart/Stop
- Restore empty state: plain-language “No backups yet” (local + USB), with Dashboard next steps

### Added
- Portal-styled HTML login (Recovery console); credentials `stonepi` + Vault `STONEPI_RECOVER_PASSWORD`
- Restore picker: local `/var/backups/stonepi/current` and USB snapshots via `stonepi-backup-helper list`
- Signed Recover session cookie (replaces browser HTTP Basic popup)
- Confirm sheet for Restore / Reboot / Stop / Restart / Clear failover
- Refresh control beside Sign out; CSS/JS cache-bust (`?v=`)

### Fixed
- Confirm-sheet native submit now preserves `op` (hidden fields + JS submitter copy) so Restart/Stop/Reboot work after confirm
- Login prefers Vault `STONEPI_RECOVER_PASSWORD` over `/etc/stonepi/recover.passwd` (passwd is fallback)
