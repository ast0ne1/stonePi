# Changelog — StonePi platform

Platform version lives in [`VERSION`](VERSION). App zips use each app’s `__version__` (see `scripts/build_release_zips.py`).

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
