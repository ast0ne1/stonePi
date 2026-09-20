# Changelog — Auth

## 0.1.4 — 2026-09-20

### Changed
- Version aligned to platform **0.1.4**; Settings General uses shared sliders icon

## 0.1.3 — 2026-09-20

### Changed
- New users default to Dashboard grant only (not every enabled platform app)
- Auth cannot be placed on the platform disabled-apps list (always available)
- Version aligned to platform **0.1.3**

## 0.1.2 — 2026-09-19

### Added
- `POST /api/me/password` — signed-in users change their own password (current + new); other sessions are revoked

### Fixed
- Auth **status** page boots the shared theme/palette (post-login screen matches the rest of StonePi)
- Theme boot prefers cookies then localStorage so palette survives across apps on the same host

### Changed
- Version aligned to platform **0.1.2** (was incorrectly 0.0.3)

## 0.0.2 — 2026-09-19

### Added
- `GET /api/display` — active session count for Dashboard Status wall (loopback scrape; nginx denies at the edge)

### Fixed
- Post-login redirect no longer rewrites to `PUBLIC_ORIGIN` (`stonepi.local`) on path installs — stays on `.home` / IP / `.local`

## 0.0.1 — initial

Shared sign-in for StonePi apps.
