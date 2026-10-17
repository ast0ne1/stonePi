# Changelog — Recover

## 0.3.0 — 2026-10-17

### Changed
- **Home page tidy-up** (backlog U11). From the top:
  - A status card: "All N services running" or "2 services need attention", plus disk, failover and network (Wi-Fi / Ethernet).
  - Services: only the ones that need attention are listed; the healthy ones fold into **Show all services**, grouped Core / Apps / Background with friendly names (unit name underneath), two columns on wide screens.
  - Restart stays on each row. Stop and the service's logs move into a ⋯ menu; Stop is never offered for nginx or Auth.
  - Restore from backup moves below Services and shows the newest 3 backups, with **Show all backups** for the rest.
  - A Tools row: Logs, Network details, Reboot. **Clear failover** only shows while failover is on.
- New services in the list: Offline library (`stonepi-kiwix`), Failover monitor and Car Thing panel. A service that is switched off (disabled, like the Car Thing or Kiwix while unused) shows as **Off**, not as a problem. All statuses come from one `systemctl show` call instead of one call per unit.
- **Logs:** pick a service; by default it shows the failed services, else Dashboard, Auth, Notify and nginx as before.
- **Network details:** a readable summary on top (connection, addresses, whether the router answers, Tailscale up or down); the raw output folds under **Raw output**.
- **Shared StonePi header** (backlog U17): the Dashboard button and sign-out icon are replaced by the app switcher and avatar menu (Log out lives there). Recover still has to render when the shared packages are broken, so it falls back to its own plain Log out button when `stonepi_auth` can't load. The switcher only shows while Auth is up and you're signed in through StonePi. Signed in with the Recover password, the avatar shows "stonepi" and a Dashboard button stands in for the switcher's Home. No bell: its panel would ask Notify, which may be down.

## 0.2.0 — 2026-10-11

### Changed
- **New look** (StonePi design system): Recover icon in the header and as favicon, new colours, fonts, buttons and status pills; the console still works on :8099 when nginx is down (it serves the brand files itself).

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
- Phone overscroll no longer shows a mismatched band: the page background covers the whole root and rubber-band scrolling is off.
- Upgrades restart Recover (installer fix), so the new version, Dashboard button and Sign out take effect without a reboot.
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
