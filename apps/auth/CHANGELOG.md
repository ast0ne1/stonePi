# Changelog — Auth

## 0.0.2 — 2026-09-19

### Added
- `GET /api/display` — active session count for Dashboard Status wall (loopback scrape; nginx denies at the edge)

### Fixed
- Post-login redirect no longer rewrites to `PUBLIC_ORIGIN` (`stonepi.local`) on path installs — stays on `.home` / IP / `.local`
- **Back to apps** uses `portal_home_url()` so PrefixRewriter does not loop to `/auth/`

## 0.0.1 — initial

- Shared household sign-in and session cookie
