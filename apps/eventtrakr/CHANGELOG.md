# Changelog — EventTrakr

## Unreleased

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
