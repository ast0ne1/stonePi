# Changelog — Auth

## Unreleased

## 0.1.8 — 2026-09-29

_Moves to the platform version (system app)._

### Added
- Session cookie carries `fac` so sibling apps can show the factory-password Settings banner without calling Auth `/api/me`
- Password change re-issues the cookie so the banner clears without signing out

### Changed
- Status page shows the Auth version.
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- systemd unit: MemoryHigh=150M + OOMScoreAdjust=-200 (prefer keep-alive under Pi memory pressure)
- Disabled-apps lock list includes Notifications and Recovery (SYSTEM services stay available)
- Catalog service groups: SYSTEM / USER (replaces System / Apps)

## 0.1.6 — 2026-09-22

### Changed
- Login / status: `--tap` targets, safe-area padding, phone card gutters aligned with portal phone tier
- Version aligned to platform **0.1.6**

## 0.1.5 — 2026-09-21

### Changed
- Quieter Auth hub / status chrome with install guidance for household setup
- Login and status pages denser for phone and desktop
- Version aligned to platform **0.1.5**

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
