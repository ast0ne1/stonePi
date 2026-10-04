# Changelog — Recover

## Unreleased

### Fixed
- Phone overscroll no longer shows a mismatched band: the page background covers the whole root and rubber-band scrolling is off.
- Upgrades restart Recover (installer fix), so the new version, Dashboard button and Sign out take effect without a reboot.

## 0.1.9 — 2026-10-04

_Platform 0.1.9._

### Added
- Dashboard button beside Refresh and Sign out (portal Home; the same host on port 80 when Recover is opened on :8099).
- `stonepi-library` in the console's service list (status, restart, logs).

### Security
- Trusts X-Real-IP / X-Forwarded-Host only from loopback requests carrying nginx's `X-StonePi-Proxy` token (constant-time check, re-read when the token file changes); other local callers are keyed on the socket peer, so forging X-Real-IP no longer dodges the per-address login lockout. A missing token file means no request counts as proxied.
- CSRF tokens on every form; sign-in rate-limited per IP and overall; restore paths resolved against the backup roots (no `..` or symlink escapes).
- Password lives only in root-only `/etc/stonepi/recover.passwd`; any Vault copy is moved there on startup and deleted from the Vault. Port 8099 accepts LAN and tailnet addresses only.

### Fixed
- Sign out works for admins who came in with their portal session (they were sent straight back in): it signs out of the portal too, through Auth when it's up or by clearing the portal cookies, and shows "Signed out.".

### Changed
- Phone layout: one-line header with 44px tap targets, one-line service rows with full-size Restart/Stop, no gap under backups, phone card padding applies, 16px login gutter, long names wrap, Logs/Network output full width.

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
