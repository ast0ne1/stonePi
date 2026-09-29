# PriceScout changelog

## Unreleased

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
