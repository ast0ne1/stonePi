# PriceScout changelog

## 0.0.8 — 2026-10-11

### Changed
- **New look** (StonePi design system): PriceScout icon in the header and as favicon and home-screen icon; new colours, fonts, buttons and cards in light and dark; the old palettes are gone.
- New shared StonePi header: Search, Refresh, the bell, an app switcher and your avatar sit together on the right. Search opens as an overlay with your recent searches.
- The bell opens a PriceScout notifications panel showing which alerts are on, the last one sent and a Preferences button.
- Log out and light/dark mode move into the avatar menu; the old sign-out icon is gone.
- The bottom bar is now Offers · List · Settings. Home moves to the app switcher, Search to the header, and Sources to the first section of Settings (with a "← Settings" link back).

## 0.0.7 — 2026-10-04

### Security
- On a platform install with no session secret every page returns 503 instead of opening up (solo runs unchanged).

### Changed
- Refresh needs "Manage sources" (the button is hidden otherwise). Madspild offers use one household postcode set by an admin instead of whoever clicked Refresh; the personal postcode field is gone. The unused "Offer alerts" permission is retired.

## 0.0.6 — 2026-09-29

_Moves from 0.0.3 to the shared user-app version, 0.0.6 (numbers in between were skipped)._

### Changed
- Salling food-waste User-Agent carries the real PriceScout version.
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- Boot refresh waits 10 minutes before `refresh_all` so startup does not contend with other apps
- SQLite uses WAL + busy_timeout=5000
- systemd unit: Nice/CPU/IO weight + MemoryMax=400M (Pi resource contention)
- Form POST handlers (list, sources, settings, logout) and Send shopping list to Pinboard run as sync routes (threadpool) so SQLite/HTTP work does not block PriceScout's event loop

### Added
- Settings shows the Auth factory-password banner (links to StonePi → Settings → General)
- Leaflet alerts via Notify when a store catalog id is newly seen after refresh (`pricescout.publication_released`); Settings → Notifications points at Destinations / Event prefs

## 0.0.3 — 2026-09-24

### Fixed
- Tjek offer refresh: skip duplicate external ids across pages; synthesize stable ids when the API omits one (avoids UNIQUE collisions on replace)

## 0.0.2 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px** (aligned with portal)
- Laptop/desktop: main column fills width beside the rail; Settings cards lay out in 2–3 columns; offer/search results in 2–3 columns

## 0.0.1

- Initial StonePi app: Offers, Search, Shopping List, Sources, Settings
- eTilbudsavis JSON collectors for Netto, Lidl, 365discount, føtex, Kvickly
- Optional Salling food-waste stream (Vault token + postcode)
- Mock seed fallback when live refresh is empty or `PRICESCOUT_MOCK=1`
