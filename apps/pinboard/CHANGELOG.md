# Changelog — Pinboard

## 0.0.10 — 2026-11-01

### Fixed
- **Phone bottom nav:** the bar no longer stacks the home-indicator inset twice.

### Added
- **Add links from anywhere** (Settings → Board): a **bookmarklet** for computers (drag "Add to Pinboard" to the bookmarks bar, click it on any page), step-by-step set-up for an **iPhone/iPad Shortcut** that puts "Add to Pinboard" in the share sheet, and on **Android** Pinboard appears in the share sheet once added to the home screen from its https:// address. Each opens the Board with Add link already filled in (`/add?url=…&title=…`); signing in first brings you back to it. A shared page's title stays on the card if the preview can't find one.
- Pinboard can be added to the home screen as its own app (web app manifest).
- **Canvas view:** the Board's cards placed freely on one big board. Drag the background (or two fingers) to move around, ctrl or cmd + scroll or a pinch to zoom, plain scroll to move around, and the − / + / fit buttons. Drag a card anywhere and drag its corner to resize it; drag an area by its name to move it with its cards, its corner to resize it. A card belongs to whichever area you drop it in. Double-click empty space to add a card right there. **List / Canvas** switch at the top; the canvas is the default on wide screens and each person's view (and zoom) is remembered on their device.
- **The open board follows other people's changes:** cards they move move on your screen within a few seconds; new, edited or removed cards reload the board when you're not in the middle of something.
- **The Board is now a board for collecting things:** notes (post-its in four colours), **links** (Pinboard fetches the page's title, site name, description, picture and icon), reminders and **checklists**, grouped by area. The + button adds any of the four; you can also paste a web address anywhere on the Board to add it as a link.
- **Pins:** four colours (red goes first on the displays and is marked Important on the Car Thing) and four styles (push pin, round tack, map pin, tape). A pinned card shows on the TRMNL and Car Thing; your last pin style is remembered. Pinning needs **Add and pin cards**; without it your cards stay on the board only.
- **Card options** from the ⋯ button or a long press: pin or unpin, move to another area, edit, fetch the link preview again, remove. Whoever added a card, or an admin, edits and removes it; anyone can move it.
- **Done:** tick a reminder or checklist items on the card. A household reminder is anyone's to tick; an assigned one is its person's, its author's or an admin's.
- **Settings → Board:** your own background (cork board, plain, dotted grid or whiteboard), areas (admins add, rename and remove them; Household stays) and **Remove done items** after 1–30 days (admins; off by default, so done items stay until removed).
- Link previews never fetch addresses on your own network, the Pi itself or the tailnet, and only use ports 80 and 443; the pictures are saved on the Pi and served by Pinboard, so the board loads nothing from other sites.

### Changed
- **The canvas fills the window on desktop** (beside the side rail, 1024px and wider): the whole column's width (it was held to the list's 1120px) and the window's height down to the help line, so the page doesn't scroll (checked at 14" and 125%, 1536×730). The list view keeps its reading width.
- **Pinboard keeps its board in a database** (`pinboard.sqlite`), the first step towards the canvas board (`ideation/pinboard-canvas.md`, F20). Every notice and reminder is now a card with a place on the board, an area ("Household") and a pin. On first start the old `pinboard.json` is imported once and kept as `pinboard.json.migrated`. The pages, PriceScout and Pebble see no difference yet.
- **The displays show pinned cards plus reminders that are due.** Imported and new notices and reminders are pinned, so the TRMNL and Car Thing look the same as before; a reminder that is due today or overdue shows even without a pin.
- The nightly backup copies `pinboard.sqlite` with the same integrity check as the other apps' databases.

- A done reminder or finished checklist leaves the displays, the due alerts and Pebble's list.
- The Notices page marks notes that aren't pinned as "Board only".
- The card count is gone from the header, and the areas no longer show a count next to their name.

## 0.0.9 — 2026-10-25

### Added
- **Pebble can read the board** (when switched on in Notify → Sidekick): your reminders plus unassigned household ones (never someone else's), and the notices. A signed internal endpoint (`POST /api/internal/agent`) used only by AgentLink; read-only.

## 0.0.8 — 2026-10-11

### Changed
- **New look** (StonePi design system): Pinboard icon in the header and as favicon and home-screen icon; new colours, fonts, buttons and cards in light and dark; the old palettes are gone.
- New shared StonePi header: notifications bell with its alerts panel, an app switcher (replaces the Home tab) and your avatar menu with appearance and Log out (replaces the sign-out button).
- Bottom bar is now Board · Notices · Reminders · Settings.

### Added
- Floating **+** button: on Board it opens **Add to board** (reminder, or notice if you may post notices) and replaces the red Add button at the top of the page; on Notices it starts a new notice, ready to type.

## 0.0.7 — 2026-10-04

### Added
- **Add and pin cards** and **Assign reminders to others** permissions (both on by default; the capability id is still `can_post_notices`). Without the second, reminders are your own (the picker shows "Me"), including PriceScout shopping-list sends.
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
