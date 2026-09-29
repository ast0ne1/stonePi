# Changelog — Pinboard

## Unreleased

## 0.0.6 — 2026-09-29

_Moves from 0.0.3 to the shared user-app version, 0.0.6 (numbers in between were skipped)._

### Changed
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- **Board** is an all-items feed: notice/reminder pills + icons, collapsed cards with due-date meta, expand for details
- **Add** on Board opens a sheet (Add reminder / Add notice) and routes to the matching tab compose form
- **Notices** and **Reminders** are separate pages (`/notices`, `/reminders`), not hash sections of one page

### Added
- Settings shows the Auth factory-password banner (links to StonePi → Settings → General)

## 0.0.3 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px**
- Notices / reminders: 2-column grid from 720px, 3-column from 1440px
- Laptop/desktop: main column fills width beside the rail; Settings uses full column

## 0.0.2 — 2026-09-20

### Changed
- Version cut with platform 0.1.4 UX chrome parity
