# Changelog — Notify

## 0.2.0 — 2026-10-11

### Changed
- `POST /api/me/apps/{app}/clear`: clears that app's bell panel for the caller (`dashboard` = every app); hides, never deletes.
- **TRMNL icon** on each TRMNL display's card on Displays and beside its page heading, like Car Thing's: TRMNL's own seven-block glyph (from trmnl.com/brand) drawn in the StonePi icon style.
- **New look** (StonePi design system): Notify icon in the header and as favicon; the Car Thing card and page carry the Car Thing icon. TRMNL's title bar shows the StonePi mark: re-paste the template into TRMNL for each Display.

### Added
- **Per-app bell panel API** for the shared app header: `GET /api/me/apps/{app}` (which of that app's alerts you have on, the last alert it sent to your phone and whether it got there, and whether that's new since you last looked) and `POST /api/me/apps/{app}/seen` (opening the panel clears the bell's dot). "Last looked" is stored per person and app in `bell_seen.json` in Notify's data folder. Answers include your five most recent alerts from that app; `dashboard` covers every app, for the Dashboard's overview.
- **Car Thing editor:** Touch gestures get their own group in Controls (with arrows), swipe callouts under the drawing, and clickable swipe arrows on the drawing's screen in the Controls tab. New configs map swipe left/right to next/previous page.
- **TRMNL plan per Display** (Standard / TRMNL+) on the Display's TRMNL settings. It sets the payload cap to TRMNL's real limit (5 KB / 10 KB; StonePi assumed 2 KB, trimming content for nothing), an hourly budget just under TRMNL's (10 of 12 / 25 of 30, counting manual and automatic pushes) and the shortest interval (10 / 5 min). Pushes are skipped when nothing on the screen changed (still refreshed every 2 hours), and a TRMNL `429` pauses pushes until its window passes. History no longer logs the "nothing changed" checks.
- Phone alerts setup: "Who can use alerts" counts everyone, admins included ("2 of 3 household members"), and is shown as an optional step with a neutral **?** instead of done/to-do; Setup is now out of 3. Links to the personal page point at `/me/notifications`.
- `newscast.push_missed` ("Not delivered to reader") is pre-approved on fresh installs. Existing installs approve it once in Events; each person then ticks it in Dashboard → Notifications.

### Fixed
- **Test notifications now show as yours:** a test sent from your notification settings is recorded as delivered to you, so the Dashboard bell lists it under Recent notifications (`stonepi_notify`).

## 0.1.9 — 2026-10-04

_Platform 0.1.9._

### Added
- Car Thing editor: Pages tab (add, remove, reorder, rename, per-page widgets and sizes with a space count, rotation and "Try turning"); the preview follows the selected page; the System feed sends raw stats for the large widget.
- **Displays → Car Thing:** device on/off (root helper `stonepi-carthing-helper`), re-pair, admin PIN; saved configs (activate, copy, rename, delete, export/import, last 10 versions); editor (Home, Controls, Actions, Idle, Clock, Look) with a drawing of the Car Thing and the live panel showing the unsaved draft through an admin-only proxy (`/carthing/panel/*`). Background upload (cropped to 800×480; adds Pillow) and weather location search (Open-Meteo).
- Signed internal API for the panel service: `/api/internal/carthing/state|pair|system|asset/<id>`.
- `pricewatch.low_rated_offer` ("Cheaper offer from low-rated shop", personal, warning) is pre-approved on fresh installs. Existing installs approve it once in Events; as with every personal event, each person also ticks it in Dashboard → Notifications to receive it. Catalog entry in `stonepi_contracts` (version bump deferred until the staging smoke test passes).
- Library's new events (`library.content_installed`, `library.download_failed`, `library.update_available`, `library.storage_missing`, `library.backup_capacity`) come from the same catalog with no Notify code change; they start unapproved, so approve them in Events alongside the PriceWatch one.

### Fixed
- Car Thing editor: button and dial labels no longer sit on the device drawing or overrun at desktop widths; they're clickable labels around it.
- Saving the ntfy access token no longer fails silently: the Vault is now writable by Notify, and a failed save shows an error on Phone alerts.

### Security
- Car Thing editor and APIs fail closed when the session secret is missing outside dev; the unit now allows sudo so the Car Thing switch works from the running service.
- On a platform install with no session secret every page returns 503 instead of opening up.

### Changed
- Displays page lists TRMNL first and the Car Thing panel below it.
- Displays page intro tells the two kinds of screen apart: TRMNL e-ink displays (read-only, refresh on a timer, for the wall) and the Car Thing touch panel (for a desk or dash), with a line per section on what it's for and where to set it up.

## 0.1.8 — 2026-09-29

_First release as Notify (was Notifications); the local 0.1.0 is folded in. Carries the platform version (system app)._

- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- **Multiple TRMNL Displays:** each Display has its own webhook (Vault), push interval, device (OG / TRMNL X) and push status; the scheduler pushes each on its own interval. Destinations keeps ntfy only.
- **Screen builder:** drag, drop and resize widgets on the device grid (mouse, touch, keyboard: arrows move, +/− resize, Delete removes) over a real render of the TRMNL screen with sample or live data, e-ink look, and push-size readout.
- **Title bar per Display:** top, bottom or off, titled with the Display's name; date style per Display (e.g. Sat 27 Sep, 14:30 / 27/09/2026 / 09/27/2026 2:30 PM / ISO / time only), in the Pi's local time (was UTC ISO).
- **Universal template:** paste once per plugin; the layout is sent in each push, so layout changes need no re-paste. "Copy template" on each Display.
- **Fixed:** pushes from Notify sent variable names the Liquid never read (`cpu`, `newscast_feeds`, …) so fields showed blank on the device. Notify now uses the shared `stonepi_display.build_merge_variables` contract, checked by tests.
- Upgrade moves the old TRMNL destination onto its Display (legacy template mode until the new template is pasted).
- New dependency: `python-liquid` (preview rendering); Notify now also installs `stonepi_watch`.

- Renamed from Notifications: `apps/notify`, unit `stonepi-notify`, path `/notify/`, data `/var/lib/stonepi/notify`, env `notify.env` / `STONEPI_NOTIFY_URL`. Old `/notifications/` URLs redirect.
- Settings hub (phone) and laptop chips; compact event prefs grouped by app with smaller checks.
- Topbar pill shows configured Display count (was the word “Displays”).
- Mobile form layout for Displays create and Destinations actions.
- Destinations: TRMNL/ntfy cards use full content width (nested settings grid no longer squeezes columns).
- Settings shows the Auth factory-password banner (links to StonePi → Settings → General).
- Lightweight Pi overlay: `push-notify-ui-fixes` (UI files + restart, no pip).
- Initial Notifications platform service: Displays, Destinations (TRMNL + ntfy), event ingest, prefs, history.
- Capability-aware widget presentation (link vs QR).
- Services card (always-on); Dashboard Display settings redirected here.
