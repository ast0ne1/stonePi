# Changelog — Dashboard

## 0.4.0 — 2026-10-21

### Added
- **Settings → Network → Turn on HTTPS** (backlog U23). Shown while Tailscale is connected but its MagicDNS name has no HTTPS yet: sets up Tailscale Serve and fetches the certificate (new `stonepi-tailscale serve`), so the first visit isn't the slow one. Serve was only tried on Connect, so turning HTTPS Certificates on in the Tailscale admin console afterwards never took effect. If they're still off, the page links to where to turn them on.
- **Notifications: ← Back** above the title (backlog U29). It returns to the app you opened the bell from, or goes Home when there's nothing to go back to (opened from a phone alert or a bookmark).
- Signed `POST /api/internal/units/details` (catalog units only) for the Car Thing's service page: state, since when, memory, restarts by itself and the last few log lines. `systemctl show` needs no privileges; the log lines go through the existing `stonepi-service-helper`.

## 0.3.0 — 2026-10-17

### Added
- **Vault → Music**: **Spotify client ID** and **Spotify sign-in** (backlog F7), both set from Notify → Displays → Car Thing.
- **Settings → AI** (House group, admins only; backlog F13). One page for AI on the whole Pi:
  - **Providers:** Gemini, Anthropic, OpenAI and Ollama, each with whether it's set up and Free or Paid. Keys link to the Vault. Ollama takes a server URL, and **load the server's models** fills the model list. With a Gemini key the page asks once: "Free tier, no billing on this key?"
  - **Model order by tier:** Cheap, Standard and Best, each a list you drag to reorder (arrow buttons on touch and keyboard). A warning shows when a Free or small local model is in Best.
  - **Jobs:** every app's AI jobs (NewsCast summaries, translation and importance; Studio apps, stories and games) with the tier each asks for and the model it would use first. A job can move to another tier or get its own list.
  - **Models:** add any model ID, with a Free/Paid badge, rough cost and a status line each ("Gemini 3.5 Flash-Lite: free quota hit 14:02, cooling until 15:00"). **Test** sends one tiny request; **Try again** clears a cooling model.
  - **Monthly AI requests:** used this month per app, and the limit (default 20,000).
- **Vault → AI** gains **Gemini API key**. The three AI keys are now optional, and the group points to Settings → AI. **Studio LLM base URL** moves to Legacy: set an Ollama or other OpenAI-compatible server on Settings → AI instead.

### Fixed
- **Settings → Displays line counts the Car Thing** (backlog B10). While the Car Thing is switched on it counts as a screen and the line says whether it's connected, e.g. "2 screens · TRMNL pushing · Car Thing connected". With the Car Thing off (or an older Notify) the TRMNL-only wording is unchanged.

## 0.2.0 — 2026-10-11

### Changed
- **Home on phones fits on one screen** (iPhone 13 mini included): Cards become a 3-across home-screen grid of app icons with their names, the greeting is tighter, and nothing needs scrolling. List and Icons layouts from View options still apply.
- **Alias:** Home greets you by your alias (the account's display name) exactly as set, never your username or full name. Set your own under Settings → General → Appearance ("Your name on Home"); admins can edit anyone's on Users (it was only settable when an account was created).
- **New look** (StonePi design system): Home shows the date, your welcome message and Your apps as cards with the app icons; Settings → General → Appearance has Theme and the welcome message (palettes and App colours removed); Health widgets with meters; fixed app icons on Health, Services, Users and Updates; new sidebar and header.
- **Shared StonePi header:** the name pill, the light/dark/system buttons and the sign-out icon are replaced by the same app switcher and avatar menu as every app (appearance and Log out live in the avatar menu).
- **Bell opens a small panel:** an overview of your alerts across all apps (what's on, grouped by app, and your last five notifications with whether each reached your phone), or, with nothing set up or switched on, a message and a button to Notifications. A dot marks a new alert since you last opened it.

### Fixed
- **Health said "ntfy off" while Phone alerts worked.** It still looked for a household ntfy topic, which went away when topics became per person; it now follows Notify's own connected switch. The Notify card also says whether the Car Thing panel is on, alongside TRMNL. Memory, Temperature, Uptime, Notify, Hardware & time and Recent errors get their own icons instead of the shared placeholder.
- **Users: one tap on User on your own page demoted you** (the Role switch autosaves). Your own Role switch is now locked, Disable and Delete are hidden on your own page, and a save without a role field no longer counts as "User".
- **Personal alerts page moved to `/me/notifications`.** Browsers that visited before the Notifications → Notify rename had cached a permanent redirect from `/notifications` to Notify, so the bell (and typing the address) opened Notify's admin page on `stonepi.home`/`stonepi.local` but not on the IP. The bell and all links use the new address; the old one now sends a temporary redirect to it.

### Added
- Home → Reorder: **Default order** puts the tiles back in the platform order and saves it.

### Changed
- **Settings → Vault redesigned as one list.** Each secret appears once (status, env var name and action on the same row) instead of across Credential status, the add form and Stored secrets. **Add value** / **Replace** opens a password field under that row; no dropdown. A summary shows "x of y secrets set" with **All / Missing / Set** filters, and each group (Platform, AI, NewsCast, Data providers, Calendars) has its own count. The household ntfy token and TRMNL webhooks link to **Phone alerts** / **Displays** instead of being edited here. **Custom secret** has its own button (inline on desktop, a popup on phone and tablet); custom keys, extra Display webhooks and legacy keys are listed under **Other stored secrets**.
- Default Home order (anyone who hasn't reordered): NewsCast, EventTrakr, SportGuide, PriceScout, PriceWatch, Pinboard, Library, FileServe, Studio (`DEFAULT_LAUNCHER_ORDER` in `stonepi_auth`; catalog order is unchanged).

## 0.1.9 — 2026-10-04

_Platform 0.1.9._

### Added
- Signed `POST /api/internal/units/restart` (service-to-service; the Car Thing panel's System screen). Only catalog `stonepi-*` units; Dashboard restarts itself after replying. Logged with the caller.
- Platform updates also overlay `apps/carthing` (`PLATFORM_SERVICE_APP_IDS`).
- **Library** tile with live status ("Installing Wikipedia · 71%") through a new catalog flag, `live_status`; Backup summary shows partial runs (Library content that didn't fit).
- Settings → Network: **Show QR code** under the Tailscale login link, so the device can be approved from a phone that already has the Tailscale app signed in. The code is made on the Pi from its own pending link (`GET /api/network/tailscale/qr`, admin only).

### Changed
- Users → new person form starts each app permission from its catalog default.
- Health banner and app states update live: every few seconds while something is starting or down, easing to 20 s when all is clear, paused in background tabs; Services start/stop/restart triggers an immediate re-check (rate-limited for CPU).
- Library tile keeps its standard description; it shows progress only while a download runs.
- Settings → Vault: new **Data providers** group with **Bright Data API key (EventTrakr)** (`BRIGHTDATA_API_KEY`, moved out of NewsCast and re-described) and **Bright Data API key (PriceWatch)** (`PRICEWATCH_BRIGHTDATA_API_KEY`, for PriceWatch 0.0.7 trust scores).

### Fixed
- A session ended elsewhere (password change, account disabled, signed out on another device, restore) goes to sign-in and back to the page instead of a "Not signed in" banner; while Auth restarts, pages show "Auth is restarting — retrying…" and reload on their own.
- Home re-order shows Saving… / Saved and reports failures (expired page, signed out, Auth errors) instead of silently looking saved; a drop made during a save is no longer lost; a failed save puts the previous order back; re-ordering pauses while Auth restarts.
- Home no longer waits on a slow or stopped app: live tile status is fetched in the background and shows the last known line.
- Health no longer returns an internal error when Notify's data folder is unreadable (`Path.is_file()` raised `PermissionError` outside the try); the Notify line shows as not configured instead.
- Settings → Network → exposure shows a message instead of an error page if the flag can't be saved.

### Security
- Settings → Vault refuses a Recover password shorter than 12 characters.
- Services start/stop/restart/logs go through the validated root helper `stonepi-service-helper` instead of wildcard `systemctl`/`journalctl` sudo rules.
- Settings → Vault → Recover password writes root-only `recover.passwd` through the backup helper (password on stdin) and never stores it in the Vault; any old Vault copy is deleted.
- Factory-password banner no longer quotes `admin / admin`.

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
