# Changelog — EventTrakr

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
