# Changelog — StonePi platform

Platform version lives in [`VERSION`](VERSION). App zips use each app’s `__version__` (see `scripts/build_release_zips.py`).

## Unreleased

## 0.1.8 — 2026-09-29

### Versions (single source)
- App lists come from `APP_CATALOG`: Dashboard Settings → Updates (every app, grouped System / Apps), service icons, Health journal units, and `scripts/build_release_zips.py` (adds the missing `notify` zip).
- Catalog `ships_with: platform` on the system apps (Dashboard, Auth, Notify, Recover): they take the platform version, have no zip of their own, ship inside `stonepi-platform-*.zip`, and Settings → Updates shows them as "Updates with the StonePi platform". `stonepi_auth.UPDATABLE_APP_IDS` = the user apps.
- `build_release_zips.py --versions-table` prints the System / Apps table for each cut from `VERSION` + `__version__`.
- `stonepi_auth.brand.asset_rev()` / `fonts_rev()`: automatic `?v=` cache-busting in every app (Recover has a local copy); no hand-typed tokens left in templates.
- FileServe / NewsCast hide their in-app updater under StonePi (EventTrakr already did).
- Guard tests (`packages/stonepi_auth/tests/test_platform_consistency.py`): no literal `?v=` tokens, zip ids and Recover's unit list match the catalog, `stonepi_contracts.APP_LABELS` matches catalog names.

### Displays / TRMNL
- **Multiple TRMNL Displays** in Notify, each with its own Private Plugin webhook (Vault `DISPLAY_WEBHOOK_URL` / `DISPLAY_WEBHOOK_URL_<ID>`), interval and device.
- **Drag-and-drop screen builder** with a real TRMNL Framework render (sample or live data).
- **Universal Liquid template** pasted once per plugin; layout is part of each push. Payloads carry only the variables a Display's widgets read (≤ 2 KB).
- `stonepi_display` 0.3: `grid`, `templates`, `render`, `push`, `variables` modules replace `layout` / `service` (fixed Household / Status wall designs and the block catalogue are gone). `stonepi_contracts`: `WIDGET_SIZES` (`small` 1×1, `medium` 2×1, `large` 2×2, `wide` full width), widget `code`, `widget_by_code`; `BLOCK_TO_WIDGET` removed.
- Fixed Notify pushes sending variable names the Liquid never read.

### Platform
- **Easy install:** README and `deploy/INSTALL.md` point to [stonepi-install.vercel.app](https://stonepi-install.vercel.app) — flash Raspberry Pi OS, SSH in, `curl -fsSL https://stonepi-install.vercel.app/install.sh | sudo bash`. Releases attach `stonepi-source-<version>.tar.gz` for that bootstrap. `scripts/*-bootstrap-on-pi.sh` overlay helpers are now local-only (gitignored, excluded from the platform zip).
- **Renamed platform services:** Notifications → **Notify** (`apps/notify`, `stonepi-notify`, `/notify/`, `/var/lib/stonepi/notify`, `/etc/stonepi/notify.env`, `STONEPI_NOTIFY_URL`) and Recovery → **Recover** (`apps/recover`, `stonepi-recover`, `/recover/`, `/etc/stonepi/recover.passwd`, Vault `STONEPI_RECOVER_PASSWORD`). Ports unchanged (8012 / 8099). `deploy/stonepi-migrate-renames.sh` (run by `install.sh`, `stonepi-restore` and the full push overlays) retires the old units and carries venv, data, env, passwd, Vault key and failover snippet forward; nginx 308-redirects `/notifications/` and `/recovery/`; old session cookies, grants and `STONEPI_NOTIFICATIONS_*` env names are still honoured.
- **Remote latency (Phases 1–4):** failover hysteresis (3 misses / 2 passes, curl max-time 8); Dashboard overview collector + loopback health probes; background jobs for restore/drill/update; self-hosted fonts at `/assets/fonts/`; nginx static aliases (HTTP + optional TLS), higher keepalive, `absolute_redirect off`; uvicorn `timeout_keep_alive=15`; Tailscale helper socket timeout + Serve-only when proxying loopback:80; CLI `/sports/` + `/watch/`
- **Latency follow-ups:** Home restores saved launcher order; password change forwards Auth session cookie (`fac` clears); Overview overlays disabled apps into Watch; Studio/PriceScout form POSTs sync; collector skips immediate re-probe after prime
- **Pi resource contention (Phase 3):** scraper units Nice/CPU/IO weight + MemoryMax=400M; auth/dashboard MemoryHigh + OOMScoreAdjust; shared `stonepi_browser` Chromium fcntl lock; EventTrakr deep search backgrounded; SportGuide daily refresh scheduler-only; PriceScout boot refresh delayed 10 min; PriceScout/SportGuide SQLite WAL
- **Request-path performance (Phase 2):** vault + hostname 30s TTL caches; Dashboard drops `/api/me` on render (`fac` claim), shared Auth httpx client, Backup/ACL caches; login `next` prefix for NewsCast/EventTrakr/FileServe; EventTrakr/FileServe skip static/favicon work
- **Appliance hostname** is admin-controlled under Dashboard → Settings → Network (`stonepi-hostname` helper + `/var/lib/stonepi/hostname` hot-read via `stonepi_auth.platform_hostname()`). NewsCast/FileServe no longer own a Hostname settings field.
- **App catalog:** Recover is a system service (with Dashboard, Auth, Notify); Dashboard Services groups System / Apps
- **Local backup:** scheduled single copy at `/var/backups/stonepi/current`; dual local/USB stamps; Recover HTML login + restore picker
- Local backup rotate keeps `current.prev` until new `current` is in place; timer uses `Unit=` (not `Requires=` oneshot)
- Vault `STONEPI_RECOVER_PASSWORD` preferred for Recover login; Vault save syncs `recover.passwd`
- **README** opens with the StonePi pitch and tagline ("The bedrock of your digital home."); the use-case table follows the pitch order and lists Notify; Recover (Recovery Console) is in the Portal table.
- `stonepi_watch.summarize()` / `summary_text()`: one Watch headline for Dashboard Health, the status API and TRMNL displays. Several services down read "Multiple services are not running (NewsCast, Studio)" on plain-text surfaces.

### Updates
- **App updates from Settings → Updates work on the Pi.** Dashboard downloads and checks the release; the new root helper `stonepi-update-helper` (installed by `install.sh`, one sudoers line) installs it into the app's own venv, restarts it, waits for its health check and rolls back automatically if it fails. Releases attach `SHA256SUMS` (written by `build_release_zips.py`) and downloads must match it. Platform updates still use the installer (`--reinstall`) until in-place platform updates land.

### Apps
- **Studio projects are per person:** only the owner can open, chat on, rename, delete or publish a project; admins see everything under **All projects** with a filter by person. Pre-existing projects move to the first admin who opens Studio.

### Platform / nginx
- **Tailscale cellular path:** gzip for proxied text, conservative cache for versioned static (`7d` with `?v=`, `1h` without), upstream keepalive, WebSocket-safe Connection map
- Trust Tailscale Serve only from loopback (`real_ip` + forwarded proto map) so rate limits see `100.x` and HTTPS redirects stay correct
- **stonepi-tailscale:** read-only `status`, honest `ServeHTTP` from `serve status --json`, Serve setup only on `up` with timeout + `ServeEnableURL`

### App versions in this cut

Platform: **0.1.8**

| System (ships with platform) | Version |
|-----|---------|
| dashboard | 0.1.8 |
| auth | 0.1.8 |
| notify | 0.1.8 |
| recover | 0.1.8 |

| Apps | Version |
|-----|---------|
| newscast | 0.0.6 |
| fileserve | 0.0.6 |
| eventtrakr | 0.0.6 |
| pinboard | 0.0.6 |
| studio | 0.0.6 |
| pricescout | 0.0.6 |
| sportguide | 0.0.6 |
| pricewatch | 0.0.6 |

## 0.1.7 — 2026-09-24

### UX
- **Settings hub (phone/tablet):** Dashboard and NewsCast use a grouped hub → section drill-in (with L2 panel icons + short subtexts); laptop+ keeps chips/stacked cards
- NewsCast: briefing/sources/filter sheets, catalog seed vs added feeds, mobile sheet scroll fixes
- PriceScout: safer offer ingest when Tjek pages repeat or omit ids

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.7 |
| auth | 0.1.6 |
| newscast | 0.0.4 |
| eventtrakr | 0.0.3 |
| fileserve | 0.0.0.9 |
| pinboard | 0.0.3 |
| pricescout | 0.0.3 |
| sportguide | 0.0.2 |
| studio | 0.0.5 |

## 0.1.6 — 2026-09-22

### UX
- **Global responsive contract**: shared viewport tiers **720 / 1024 / 1440** (side rail at ≥1024 for portal and all apps — removes the 800–899 cliff)
- Auth login/status: phone safe-area, `--tap` targets, denser card padding
- Portal + all apps: main column fills width beside the rail on laptop/desktop; Settings panels 2–3 columns; list/card pages (Saved, Search, Feeds, Pages, events, offers, projects, listings) use multi-column grids
- NewsCast OPDS/URL wrap; Studio split panes fit short laptop heights; Pinboard notice/reminder grids on tablet+
- Docs: [deploy/responsive-audit.md](deploy/responsive-audit.md), ux-stonepi + new-stonepi-app chrome; optional [scripts/responsive](scripts/responsive/) overflow smoke

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.6 |
| auth | 0.1.6 |
| newscast | 0.0.3 |
| eventtrakr | 0.0.3 |
| fileserve | 0.0.0.9 |
| pinboard | 0.0.3 |
| pricescout | 0.0.2 |
| sportguide | 0.0.2 |
| studio | 0.0.5 |

## 0.1.5 — 2026-09-21

### UX
- Portal: browser layout prefs, denser Home / Health / Services cards, Users expand control aligned with Health/Services
- Auth hub with quieter chrome and install guidance
- Mobile: keep bottom nav visible when Settings and other tall pages overscroll
- NewsCast: Device / Sources shells, PaywallSkip, sync activity, scrape↔RSS for dual-mode sources, Today / Yesterday / All by publish date, catalog cleanup, mobile nav and story kickers

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.5 |
| auth | 0.1.5 |
| newscast | 0.0.2 |

## 0.1.4 — 2026-09-20

### UX
- Admin nav: **Health** (monitor) vs **Services** (manage enable/disable); Overview route kept at `/overview`
- Health cards: Version + URL labels; URLs follow the hostname you opened; long URLs wrap in-card
- Services cards: Route + Port only (no Healthy / Status lines); enable toggles unchanged
- Shared Settings chrome: General sliders icon; About cards flush; Studio Settings pills match NewsCast
- Studio: Settings → About; admins edit LLM prompt markdown under Settings → Prompts
- Docs: [docs/screenshots/](docs/screenshots/) gallery linked from the root README

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.4 |
| auth | 0.1.4 |
| studio | 0.0.4 |
| pinboard | 0.0.2 |

## 0.1.3 — 2026-09-20

### UX
- Overview absorbs Watch: conditional alert banner, disk + backup stats, one services grid; Settings Watch tab redirects to Overview
- Services: per-card Enabled toggle (autosave); Dashboard and Auth stay always on
- Users: Accounts list first (rows collapsed); Add account expands the create form; new users default to Dashboard only
- Studio: Create landing (`/`) vs Projects (`/projects`); three-up bottom nav
- Windows split-port: app **Home** nav uses dashboard `PUBLIC_ORIGIN` (no longer loops to the app’s own port)

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.3 |
| auth | 0.1.3 |
| studio | 0.0.3 |

## 0.1.2 — 2026-09-19

### UX
- Overview backup card honest about failed/skipped stamps
- Path-install Sign out stays on `/auth` (hostname you opened), not loopback `AUTH_URL`
- Settings → General: Appearance + own password for everyone; admins keep **Users** nav for household accounts
- Theme/palette cookies + auth status page theming
- Launcher tiles stay on the hostname you opened (relative paths; no `.home` → `.local` flip)
- FileServe `/browse` scoped: own pages for members; admin/root only for anonymous visitors
- App Settings General no longer offers local password change when SSO is on (use portal Settings → General)

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.2 |
| auth | 0.1.2 |

## 0.1.1 — 2026-09-19

### Display / TRMNL
- Design presets: **Status wall** (ops board), **Household focus**, **Custom**
- Landscape preview scaled for laptop Settings; Copy markup works on LAN HTTP
- Service metadata on Status wall from app `/api/display` (Auth sessions, EventTrakr favourites, Studio projects, FileServe pages, Pinboard notices, …)
- nginx edge deny list includes `/auth/api/display` and `/studio/api/display`

### Home
- Launcher reorder is drag-to-place (not arrow buttons)

### Install / ops
- Avahi: install `avahi-utils`, force IPv4 address publish for `stonepi.local`
- Cockpit URL defaults / upgrades to `https://…:9090`
- Installer refuses to proceed without `stonepi-public-deny-display.conf`
- Release assets named `stonepi-{app}-{version}.zip` (+ `STONEPI.txt`); updater prefers that prefix
- Hostname-agnostic portal: works via `stonepi.local`, Pi IP, or router LAN DNS (e.g. `stonepi.home`)

### Fixes
- EventTrakr favourite toast no longer reports “Removed” on API errors
- Login / “Back to apps” / app **Home** stay on the hostname you opened (e.g. router `stonepi.home` or LAN IP), not forced to `stonepi.local`
- `safe_next` allows `.home` / `.lan` / private IPs; `portal_home_url()` builds absolute portal links so PrefixRewriter does not turn `/` into `/auth/`

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.0.2 |
| auth | 0.0.2 |
| eventtrakr | 0.0.2 |
| studio | 0.0.2 |
| (unchanged) newscast, fileserve, pinboard | see each app |

### Packages
- `stonepi_display` 0.1.1 — Status wall Liquid + fixed layouts
- `stonepi_update` — matches `stonepi-{app}-*.zip` (legacy `{app}-*.zip` still accepted)

## 0.1.0 — prior

- Initial packaged platform baseline
