# Changelog — StonePi platform

Platform version lives in [`VERSION`](VERSION). App zips use each app’s `__version__` (see `scripts/build_release_zips.py`).

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
