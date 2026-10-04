# Changelog

Current version is **0.0.7**.

## 0.0.7 — 2026-10-04

### Security
- Only the page owner, or an admin with FileServe access, skips page passwords or sees switched-off pages; the URL fetch tool needs FileServe access and "Publish pages"; under StonePi only the StonePi sign-in counts, so signing out or a role change applies straight away.
- Members without "Publish without a password" must set a page password when creating, replacing or switching on a page and can't remove one; existing open pages are left as they are.
- Publishing pages, ZIP sites and Studio Publish follow the "Publish pages" permission (Studio Publish also needs Studio's "Publish to FileServe"); members can still switch off or delete their pages.
- Hosted pages, ZIP sites and their assets load in a CSP sandbox (no same-origin) with nosniff, so an uploaded page can't act as the person viewing it; the same applies to the URL fetch preview.
- The URL fetch proxy always blocks loopback/link-local and re-checks every redirect hop.
- Listens on `127.0.0.1` by default; a solo install that should be reachable on the LAN needs `HOST=0.0.0.0` in `.env`.

### Fixed
- The household "new page" alert is sent only for admin pages and links to the page's real address.

## 0.0.6 — 2026-09-29

_Three-part versions from here, on the shared user-app version (0.0.6). 0.0.0.10–0.0.0.15 were never released and are folded in; 0.0.0.9 and earlier stay as history._

### Changed
- Under StonePi the in-app updater is fully hidden (Updates panels, Roll back last app, update check); updates are managed from StonePi → Settings → Updates. Running on its own is unchanged.
- URL pack User-Agent carries the real FileServe version.
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- Login `next` includes the app path prefix under SSO; skip DB session for `/static` and `/favicon*`; move `purge_expired` to a background timer
- Hosted Pages **Add page** button goes to the dedicated Add page (sheet approach dropped)
- Hosted page cards use Share sheet + More menu (QR no longer inline on every card)
- Appliance hostname is no longer editable in FileServe Settings; it is set under **Dashboard → Settings → Network**. Share URLs and TLS SANs use the platform hostname.

### Fixed
- Expired hosted pages 404 and drop off the admin list as soon as they expire, not only after the next purge tick
- `/favicon.ico` serves the app icon instead of 500ing (it was reaching the hosted-page route without a DB session)
- Under Pi mount `/files`, JS no longer posts edit saves, add-from-URL fetch, add-tab history, or session-expiry login to unprefixed `/admin` / `/login` (uses `data-stonepi-prefix` + `withPrefix`, matching NewsCast)

### Added
- Settings → Notifications points at StonePi Destinations / Event prefs (new-publication alerts already emit via `fileserve.publication_created`)

## 0.0.0.9 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px** (aligned with portal)
- Laptop/desktop: main column fills width beside the rail; Settings cards lay out in 2–3 columns; Hosted Pages / browse use full width with 2–3 column cards

## 0.0.0.5 — 2026-09-17

### Fixed
- **`/browse`** no longer lists every household member’s pages to standard users: signed-in non-admins see only their own titles; anonymous visitors see admin/root (household) pages only; admins still see everyone

### Added
- **Add** splits into **Add file** and **Add from URL** (same header; chips switch panels in place)
- Add from URL packs a live page into a FileServe-ready Site zip via a signed-in server fetch (no public CORS proxy)
- `games/` folder convention plus `games/pack.py` to zip each game for Site upload
- Sample hotseat game **Moving Maze** under `games/labyrinth/` (shifting-maze rules; original art)

### Changed
- Add page layout no longer reloads when switching between file and URL modes

## 0.0.0.4 — 2026-09-17

### Added
- Multi-user household accounts: admin pages stay at `/slug`, user pages at `/u/username/slug`
- Settings → Users for creating accounts, resetting passwords, and removing users
- Optional LAN HTTPS with a local root CA, deferred restart, and CA download
- Zip site hosting: upload a folder as `.zip` (needs `index.html`); one **Site** card on Pages; assets under `/slug/…`; remove deletes the whole site as a single entry

### Changed
- Non-admin users see full Pages for their own files only, and Settings limited to General and About
- Settings tab renamed from Device to General
- Default serve path uses uvicorn (TLS-capable); Waitress remains available via `FILESERVE_LEGACY_SERVER=1`

## 0.0.0.3 — 2026-09-14

### Added
- Host HTML, PDF, and Word (`.docx`); PDFs open in the browser, Word is shown as a readable preview
- Replace the hosted file on an existing page without deleting the URL or settings
- Optional short description on add and edit, shown on the card and public listing
- Public `/browse` listing of enabled, non-expired titles
- Copy URL and copy username on each card; copy password once when it is set
- Download the original hosted file from the card
- Download and print QR codes
- Search and sort on Hosted Pages, plus open count and last opened

### Changed
- Hosted Pages cards group URL/file and QR actions as icon buttons on desktop and phone
- Add Page accepts `.html`, `.pdf`, or `.docx`

### Fixed
- Copy URL/username/password works on plain HTTP LAN addresses, not only localhost

## 0.0.0.2 — 2026-09-14

### Added
- QR code on each Hosted Pages card for the public LAN URL
- Optional username and password per page; pages stay public unless you turn that on
- Card label and custom public path, with an edit modal on each card after setup
- Optional expiry of 1 week, 1 month, 3 months, 6 months, or a custom date; default is keep until removed
- Enable/disable a page to hide the public URL without deleting files
- Full-width tap target on Add Page for choosing the HTML file

### Changed
- Default port is 8081
- Phone layout: stacked page actions, QR above the URL, stretched Open button, and safe-area padding
- QR codes are PNG with a quiet zone, sized to stay inside the card frame

### Fixed
- Path field keeps the leading slash inside the same input
- QR codes were clipped by the rounded frame on cards

## 0.0.0.1

- First version: login, hosted HTML pages, add/delete, themes and settings
- GitHub Release check/install/rollback and backup/restore of pages plus settings
