# Changelog — EventTrakr

## 0.0.7 — 2026-10-04

### Added
- Per-person permissions (Dashboard → Users): Manage sources, Instagram & Facebook, Share agenda publicly, Google Calendar sync. Members need the matching one to add/edit/remove sources, track Instagram or use Facebook sources and "Check now", make their agenda or favourites public, or connect Google Calendar (and the webcal feed); refused actions say who to ask and the controls are hidden. Existing members keep everything except Instagram & Facebook (paid Bright Data).
- `/api/display?items=N` (loopback) adds `card` + `items` for the Car Thing panel: upcoming favourites with venue, cost and description. The plain call is unchanged.
- **Bright Data monthly limit** (default 5,000 records, shared with PriceWatch on the same account): every Facebook and Instagram call books its maximum against the platform quota first and is skipped, with the reason on the source or account, once the limit is reached. Scheduled syncs and polls also keep to the month's share so far; **Fetch Details** and **Check now** can use the rest. **Settings → Data Providers** shows this period's usage per app and sets the limit and the day of the month it resets (default 1, to match the Bright Data billing date).
- **Instagram polling controls** (Settings → Discovery), all counted within the Bright Data limit:
  - **Instagram allowance**: Instagram's own share of the limit (default 2,000 records a period; blank shares the whole limit; 0 stops Instagram checks), so polling can't use up what Facebook sources and PriceWatch need. Scheduled checks spread it across the period; **Check now** can use what's left.
  - **Polling hours** (e.g. 08:00 to 23:00): scheduled interval checks only run in the window, which may cross midnight. Accounts on fixed weekly times keep them.
  - A **worst-case estimate** for the period from the tracked accounts, their schedules, the polling hours and posts per check, with a warning when it's above the allowance.
- **Jungle Copenhagen** (`https://jungle.am/events`, Food & Drink) in the recommended sources. Jungle is a SvelteKit site, so events are read from the page's `__data.json` over plain HTTP (no headless browser): title, date and end time (per occurrence for multi-date events), host and address, price, poster and the event's own page.

### Changed
- Capability roster refresh uses the shared stonepi_auth roster helper (behaviour unchanged).
- Paid social polling and Facebook source syncs skip people without Instagram & Facebook; public agenda and favourites go private when the owner loses Share agenda publicly. Revokes reach background jobs within 5 minutes via Auth's roster.
- **Facebook search via Bright Data:** at most 30 events per run (was unlimited), and a result is reused for 12 hours, by every user with the same search and by **Sync all**, instead of being bought again every hour. **Fetch Details** reuses a lookup of the same event for 24 hours.
- A Bright Data request that is refused outright (bad key, network) no longer counts against the limit.

### Security
- Source favicons: SVG/HTML icons are no longer cached (raster only, whatever type the site claims), and `/favicon-cache/` is served with a sandbox CSP and nosniff, so an icon can't run script on the StonePi origin.
- Listens on `127.0.0.1` by default (the platform already set this).
- Requirements pinned exactly; `pytest` is no longer a runtime dependency.

### Fixed
- Search matches every word in any order across title, place, source, category and description, ignoring case and accents (æ/ø/å); results rank title matches first, upcoming first. Your default location is no longer applied silently: it's a one-tap chip, and a location filter shows as a removable chip.
- On phones, running a search no longer squashes the filter card (date, location, category) under the results; the page scrolls with the filters intact.
- Source icons heal themselves: a cached favicon whose file is missing (or an old SVG) is refetched in the background at startup or when Sources opens, and the placeholder shows meanwhile instead of a broken "?"; cached icons are served with an explicit image type.

## 0.0.6 — 2026-09-29

_Moves from 0.0.3 to the shared user-app version, 0.0.6 (numbers in between were skipped)._

### Added
- **Stop** button beside Refresh (header) and on Sources while a manual refresh runs; the sync finishes the source it is on, then stops and reports “Stopped after x/y sources”
- Per-account Instagram poll schedules (Global Discovery interval, or custom interval / weekly days & times — same pattern as URL sources)
- Instagram social discovery & enrichment (full v1): track public accounts, Bright Data poll (capped posts/check), local OCR, match-then-discover pipeline, Discoveries UI, Sources → Instagram admin, Settings → Discovery, event updates/cancellations, and optional ntfy envelopes
- Under SSO, Settings shows the Auth factory-password banner (links to StonePi → Settings → General) from the shared session flag
- Approaching alerts for favourited events via Notify (schedule tick every 5 min; Settings → Notifications for lead time + Destinations pointer)

### Changed
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- Deep search past the Agenda horizon runs in a background thread (pending “searching…” state) so Chromium scrapes no longer block `/search`; ICS-only deep search still finishes in-request
- Headless Chromium launches hold a shared appliance lock (`stonepi_browser`) so EventTrakr and SportGuide never run two browsers at once
- systemd unit: Nice/CPU/IO weight + MemoryMax=400M (Pi resource contention)
- Login `next` includes the app path prefix under SSO; skip session load for `/static` and `/favicon*`
- Settings uses hub → section on laptop/desktop as well as phone (no horizontal chip scroller)
- Topbar Sync renamed to Refresh (status pill Refreshing); icon-only Refresh at ≤420px to match NewsCast
- Public/TLS URLs and Network tab copy use the platform appliance hostname from Dashboard → Settings → Network (no per-app hostname field)

### Fixed
- Refresh no longer ends in Error once an Instagram-linked event goes past: the daily past-event purge now clears its social links/updates and detaches discoveries first (FK constraint)
- Refresh no longer fails instantly on the Pi when SportGuide created the shared Chromium lock first (`stonepi_browser` opens an existing lock without `O_CREAT`; tmpfiles pre-creates it root-owned)
- Header Refresh, favourites, per-source refresh, and Facebook import work under the `/events` prefix now that nginx serves `app.js` directly (requests apply the app prefix in the browser)
- Laptop/desktop: main column no longer shrink-wraps to page content (auto margins in the grid), so page headers stay put between Agenda, Faves, Search, Add, Sources and Settings
- Laptop/desktop: sticky Settings chips sit below the sticky topbar instead of under it; scrollbar gutter reserved so short vs long pages don't shift sideways; toast moves top-right like NewsCast
- Phone/tablet: buttons and form controls inherit the chrome font (button chips no longer render in the browser's default font) and inputs are 16px so iOS doesn't zoom on focus
- Bottom nav labels 0.7rem (was 0.58–0.62rem) with safe-area side padding for landscape notches; global h1–h3 styles; size/padding no longer animate on load (`transition: all` removed)
- Topbar height re-measured on `pageshow` (iOS back/forward cache)

## 0.0.3 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px**; wide main / events grid density at **≥1440px** (was 860 / 1100)
- Laptop/desktop: main column fills width beside the rail; Settings cards lay out in 2–3 columns; agenda/search event cards 2+ columns from 1024

## 0.0.2 — 2026-09-19

### Added
- `GET /api/display` includes `favourites` count alongside `next` for Status wall Services metadata

### Fixed
- Favourite star toggle: only toast “Removed” when the API returns `favourited: false`; errors no longer look like a successful unfavourite

## 0.0.1 — prior

- See app history / README
