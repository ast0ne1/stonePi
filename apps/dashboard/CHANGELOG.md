# Changelog — Dashboard

## Unreleased

### Fixed
- Health no longer returns an internal error when Notify's data folder is unreadable (`Path.is_file()` raised `PermissionError` outside the try); the Notify line shows as not configured instead.
- Settings → Network → exposure shows a message instead of an error page if the flag can't be saved.

## 0.1.8 — 2026-09-29

### Fixed
- **Settings → Updates now installs on the Pi.** App code under `/opt/stonepi` is root-owned, so Dashboard (`stonepi-dash`) could download an update but not install it. Installs now go through the root `stonepi-update-helper` (one sudoers line, like the backup helper): it checks the zip against the release's `SHA256SUMS`, saves the current code, installs, puts requirements in the app's own venv, restarts the app and waits for its health check — and puts the old code back if that fails.
- Requirements from an app update went into Dashboard's own venv instead of the app's.
- Install with no earlier Check skipped the download and said there was no newer release.
- The Updates page now names release assets `stonepi-<app>-<version>.zip` (plus `SHA256SUMS`). Platform updates on the Pi point you to the installer's `--reinstall` for now.

### Changed
- **Settings → Updates** lists the platform plus every catalog app, grouped System / Apps (was a fixed list missing PriceScout, SportGuide, PriceWatch and Recover). Recover shows its version and updates with the platform zip. Service icons and the Health journal unit list also follow the catalog.
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- **TRMNL:** removed the unused built-in TRMNL designer (JS + `trmnl-*` CSS) and the local single-destination push fallback; automations push through Notify, which owns per-Display TRMNL. Health's TRMNL line counts Displays actually pushing.
- Host readers and merge-variable building moved to the shared `stonepi_display.variables` (Notify uses the same contract); backup stamp parsing moved to `stonepi_watch.read_backup_info`.
- Follows the platform rename Notifications → **Notify** and Recovery → **Recover** (catalog ids, Services, Health probes, Settings links, Vault key `STONEPI_RECOVER_PASSWORD`).
- systemd unit: MemoryHigh=150M + OOMScoreAdjust=-200 (prefer keep-alive under Pi memory pressure)
- Request-path performance: session `fac` claim for factory-admin banner (no `/api/me` on render); Home still loads saved launcher order from `/api/me`; password change forwards Auth `Set-Cookie` so `fac` clears; shared `httpx.Client` for Auth calls; Backup tab helpers parallel + 30s cache; Tailscale ACL status 30s cache; network status API uses cache unless `?fresh=1` while polling connect
- **Health Overview:** disabled apps from Auth overlay into Watch rollup (no false alerts for hidden services)
- **Health Overview:** background collector snapshot (cards/health 20s, temp 30s, ports/journal 5 min); loopback `/healthz` probes; render from cache with “checking…” until primed
- **POST form handlers:** sync `def` + form dependency so blocking work runs in FastAPI's threadpool (event loop stays free for health checks)
- **Services cards:** Route and Port share one row (two columns) so cards are shorter
- **Users:** master/detail layout (list + selected account) on laptop+; list → detail on phone/tablet; Add account side drawer; role segmented control; app toggles with auto-save; Reset password + more menu (disable/delete)
- Overview last-backup shows Local and USB lines
- Vault blurb for `STONEPI_RECOVER_PASSWORD` clarifies Recover console credentials (not portal login)
- Saving/removing Vault `STONEPI_RECOVERY_PASSWORD` also syncs `/etc/stonepi/recovery.passwd` via backup-helper
- Network hub copy covers hostname + exposure + Tailscale
- **Health → Listening:** allowlist includes mDNS (`udp/5353`); Tailscale/`100.x` binds, known platform daemons, and Tailscale companion UDP no longer false-alarm
- **Health → Hardware:** CPU freq from sysfs (vcgencmd fallback); boot count from journalctl with `last reboot` fallback
- Cache Tailscale (~20s) and internet (~60s) status for Overview/Settings; `/api/network/status` polls with `fresh=True`
- Version aligned to platform **0.1.8**
- **Settings → Updates:** Dashboard, Auth and Notify now show "Updates with the StonePi platform" like Recover — system apps take the platform version and ship in the platform zip, so they no longer have their own Check / Install.
- **Settings → About** leads with the StonePi tagline and pitch ("The bedrock of your digital home.") instead of a hand-typed app list.
- **Health** alert: when two or more services are down it reads **Multiple services are not running** with their names underneath in smaller text (was only the first app's name); a single service still reads "NewsCast is not running".

### Added
- **Background jobs:** in-process runner (`app/jobs.py`) for restore, restore drill, and update install so long helpers (up to 600s) no longer block `/healthz`; poll `GET /api/jobs/{id}` or `/settings/backup/job-status`
- **Settings → Backup:** local schedule (daily/weekly), run-now, dual Local / USB status stamps, snapshots labeled by kind
- **Services:** SYSTEM / USER card groups; Recover appears with Dashboard, Auth, and Notify (always on)
- **Health:** same SYSTEM / USER grouping for responding service cards
- **Settings → Backup:** USB snapshot list + restore, restore drill, failover status
- **Settings → Network:** Tailscale ACL apply (Vault `TAILSCALE_API_KEY` + `TAILSCALE_TAILNET`)
- **Settings → Updates:** OS security vs StonePi zip channels explained
- **Health / `/system/health`:** memory, temperature, uptime, Outputs/Destinations flags, listening-port audit, hardware (undervoltage/throttling), NTP, recent journal errors; disk Watch thresholds 75/85/95 with `system.disk_warning` via Outputs
- **Settings → Vault:** configured/missing credential matrix (Outputs keys highlighted; legacy per-app ntfy tokens demoted)
- **Settings → Network → Hostname:** admins set the appliance LAN name (`NAME.local`) once for the household; privileged `stonepi-hostname` helper updates OS hostname, Avahi, `stonepi.env`, and the hot-read `/var/lib/stonepi/hostname` file
- **Settings:** admin link to Notify (after Vault on hub + chip bar) for Displays, Destinations, and event prefs

### Fixed
- **Settings → Network:** advertise `https://…ts.net/` only when Tailscale Serve is actually configured; otherwise `http://100.x/`
- Show **Enable HTTPS for this tailnet** when the helper returns `ServeEnableURL`
- **Health** cards: Version / URL use the full card width, so URLs no longer wrap onto two lines on laptop and desktop; only the title row keeps clear of the Details / Open buttons.
- **Services** cards: same fix for Route / Port, and Port now sits a fixed gap after the route instead of at the half-way point, so routes like `/pinboard/` no longer wrap.
- **Health** cards for apps disabled under Services read **Disabled · <unit state>** with a grey dot (was "Healthy · active", since disabling locks household access but leaves the service running) and no longer get the red unhealthy styling.
- Green success banners (e.g. after enabling or disabling an app in **Services**, or saving a form) fade out after about 4 seconds instead of staying until you reload; error banners stay put.

## 0.1.7 — 2026-09-24

### Added
- **Settings hub (phone/tablet):** grouped hub → section list → panel; L2 rows use the same icons as panel headings plus short subtexts
- **Settings → Network**: network exposure (moved from General) + Tailscale remote access (enable, connect, auth-wait polling)
- **Health**: single Remote access card (internet, enabled, Tailscale, MagicDNS)

### Changed
- Version aligned to platform **0.1.7**

## 0.1.6 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px** (was 900); Health / TRMNL two-column at 1024; Home list/icons and Users access denser at **≥1440**
- Admin `.main-wide` fills the column beside the rail (no width island); Settings panels lay out 2–3 columns on laptop/desktop
- Version aligned to platform **0.1.6**

## 0.1.5 — 2026-09-21

### Changed
- Browser layout / view prefs; denser Home, Health, and Services cards
- Users cards: expand control and footer pills aligned with Health / Services
- Tall Settings pages no longer rubber-band the mobile bottom nav off-screen
- Version aligned to platform **0.1.5**

## 0.1.4 — 2026-09-20

### Changed
- Nav label **Health** (route still `/overview`): monitor copy; Apps responding stat; Version + URL meta; request-host display URLs
- **Services**: manage-only copy; Route + Port meta; no Healthy pill / Status line
- Settings General uses the sliders icon; About cards flush with shared portal chrome

## 0.1.3 — 2026-09-20

### Changed
- Overview absorbs **Settings → Watch**: alert banner when not healthy, disk + backup stats, one click-to-launch grid; `?tab=watch` redirects to Overview
- Services: per-card Enabled toggle autosaves (confirm when hiding); Dashboard and Auth stay always on
- Users: Accounts list first (rows collapsed); Add account expands the form; new users default to Dashboard only
- Version aligned to platform **0.1.3**

## 0.1.2 — 2026-09-19

### Changed
- **Users** nav for admins (create / apps / roles)
- Every signed-in person changes **their own password** under Settings → General → Your password
- Version aligned to platform **0.1.2** (was incorrectly 0.0.3)

### Fixed
- Overview **Last backup** respects `STATUS=failed` / `skipped` (no longer looks OK when only a timestamp exists)
- Sign out / login on path installs use `/auth` even when `AUTH_URL` is loopback (no more `127.0.0.1:8011` on `stonepi.home`)
- **Settings → General → Appearance** for every signed-in household member (not admin-only); other Settings tabs stay admin
- Theme/palette persist via cookies + localStorage so prefs survive logout and follow the hostname
- Home launcher / Services cards use **relative** paths on path installs so `stonepi.home` does not jump to `stonepi.local`
- Settings import of `__author__` / `__github__` no longer 500s the page

## 0.0.2 — 2026-09-19

### Added
- **Display → Status wall** fixed design: header, CPU/RAM/DISK/TEMP/UPTIME strip, Services (with live detail/meta where available), Alerts, Storage & Backup
- Landscape Settings preview for OG (800×480) and V2 (1040×780), scaled to the Settings column
- Home **Edit order**: drag tiles to place (grip handle); order autosaves

### Changed
- Household focus remains a separate fixed design; Custom stays freeform
- **Copy markup** uses Clipboard API with `execCommand` fallback for `http://` LAN contexts
- Fixed-design preview uses a full-width designer column (no clipped palette track)

### Fixed
- Status wall / Household preview no longer renders as a tall narrow strip on desktop
- Home Done / edit bar no longer stuck visible after leaving edit mode
- Cockpit overview/settings link uses `https://` (Cockpit TLS on :9090)

## 0.0.1 — initial

First dashboard cut on the platform path install.
