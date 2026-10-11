# Changelog — Pinboard

## 0.0.8 — 2026-10-11

### Changed
- **New look** (StonePi design system): Pinboard icon in the header and as favicon and home-screen icon; new colours, fonts, buttons and cards in light and dark; the old palettes are gone.
- New shared StonePi header: notifications bell with its alerts panel, an app switcher (replaces the Home tab) and your avatar menu with appearance and Log out (replaces the sign-out button).
- Bottom bar is now Board · Notices · Reminders · Settings.

### Added
- Floating **+** button: on Board it opens **Add to board** (reminder, or notice if you may post notices) and replaces the red Add button at the top of the page; on Notices it starts a new notice, ready to type.

## 0.0.7 — 2026-10-04

### Added
- "Post notices" and "Assign reminders to others" permissions (both on by default). Without the second, reminders are your own (the picker shows "Me"), including PriceScout shopping-list sends.
- `/api/display?items=N` (loopback) adds `card` + `items` for the Car Thing panel: notices and reminders, overdue ones badged. The plain call is unchanged.

### Security
- On a platform install with no session secret every page returns 503 instead of opening up (solo runs unchanged).

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
