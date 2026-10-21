# Changelog — Notify

## 0.4.0 — 2026-10-21

### Added
- Car Thing editor, **Look → Music → Options**: under *The screensaver shows the artwork while music plays*, **Show the time on it**, **Time position**, **Time size** and **Dark backing**, for that screensaver only.
- Car Thing editor: a **Default** button beside the panel accent (Look), the clock colour and accent (Look → Clock) and the screensaver clock's own colour and accent, putting back StonePi's sand accent and stone clock colour.
- **Simpler Car Thing page** (same features, laid out around the editor). The editor is the page: under the title, pills show the panel's status, **Showing: <config> ▾** (switch, Use on Car Thing, Export, Delete, Save a copy, Rename, Import, earlier versions) and Spotify (amber in the last 14 days, red with Reconnect once it has run out), with **⚙ Setup** beside the title. **Setup** is its own page (`/displays/carthing/setup`): Device, Spotify sign-in, Saved configs and Earlier versions, and **one Save** for the PIN, client ID and expiry date. Device and Spotify actions come back to Setup; config actions to the page they came from.
- Car Thing editor: **four tabs**, Pages, Buttons, Idle and Look, with **Undo** and **Save to panel** in the tab row (**Save** plus **Use on panel** when the config isn't on the Car Thing). The open tab is remembered, and `#pages`, `#buttons`, `#idle` or `#look` opens one.
- Car Thing editor, **Pages:** page pills (drag, or Alt + arrow keys, to reorder; **+** adds a page, which now starts **empty**), Delete page beside the name, the header as one line with **Edit**, a **6-space bar**, and only the page's widgets as rows: drag ⠿ (mouse, touch or Alt + arrows), **Small / Half / Full**, a one-line summary, ✕, and ⚙ on Clock and Music to open their settings in the row. **+ Add widget** lists the rest (greyed when they don't fit, "Page is full" when nothing does) and **Web page…**. Turning pages is a summary line linking to Idle.
- Car Thing editor, **Buttons** (was Controls + Actions): grouped Buttons, Dial, Holds (folded) and Swipes, with *What this config may do* folded at the bottom. **Idle** gains Turning pages and the screensaver clock. **Look** gains the header default and folded **Clock** and **Music** sections (their home; an open widget row borrows them).
- Car Thing editor: **Page header** settings (Show: Always, Auto-hide or Off; Hide after 3, 5 or 10 s; Time & date, Page title, Weather, Page dots), as a default for every page plus **This page's own** header on any page.
- Car Thing editor: **Screensaver time** (position, size and dark backing) for the animated styles, and Time position and size in Matrix's options.
- Car Thing editor: **Screensaver style is a dropdown** now there are nine, grouped **Clock & info** (Clock, Web page), **Classic** (Bouncing logo, Matrix) and **Windows 98** (3D Pipes, Flying Through Space, Mystify Your Mind, 3D Flower Box, 3D Maze), with each new style's options under it.
- **Web pages on the displays** (backlog F14; spec `ideation/web-capture.md`). Displays → **Web pages**: add any page the Pi can reach (first use: a self-hosted Homepage dashboard) with its address, a clean-up preset (**Flat** removes background images, blur and shadows), parts to hide (CSS selectors), extra CSS, zoom and a wait for late widgets. **Preview** captures it with the shared headless Chromium and shows where it's used.
- **TRMNL Web page Displays:** Add Display → kind **Web page**. The page is captured at the panel's size (OG 800×480, X 1872×1404), turned greyscale, contrast-stretched, inverted when it's dark (automatic, always or never) and reduced to black and white (default, with a cut-off slider), 4 or 16 greys, thresholded or dithered, then POSTed as a PNG to its own TRMNL **Webhook Image** plugin. It can sit on the same device as the Dashboard panel. The webhook is a new Vault key per Display (`DISPLAY_WEBHOOK_URL_<ID>`), 12 images an hour (StonePi sends at most 10), every 5–120 minutes, and an unchanged image is never re-sent. **Preview** shows the exact image a push sends; **Push now** runs off the event loop.
- **Clearer TRMNL setup for the two kinds of Display.** Add a Display is now two cards, **Widgets** (needs a TRMNL Private Plugin + the StonePi template) and **Web page** (needs a TRMNL Webhook Image plugin), and a Web page Display asks for its address there, so the Display and its page are made in one step (or it reuses a page you already have). Each Display page opens with a **setup checklist** (plugin to add on trmnl.com, webhook, playlist, first push) that ticks itself off, and each Display card shows its plugin. A push TRMNL refuses (4xx) now says which plugin this Display needs, in case the webhook came from the other kind.
- **The web page is set up on its Display:** address, clean-up, parts to hide, extra CSS, zoom and wait sit on the Web page Display beside the e-ink settings, and **Preview** uses them before they're saved. If the page is shown elsewhere too, the Display says where. **Use this page instead** switches to another page. Deleting a Web page Display also deletes its page when nothing else shows it.
- Car Thing editor: **+ New web page** under the page's widgets (it goes on the page if it's empty, otherwise on a new page) and under the Web page screensaver. Displays → **Web pages (shared)** moves below Car Thing as the place to see where each page is used, preview it in colour, add one ahead of time or delete one.
- Car Thing editor: each web page is a **full-page widget** ("Web: <name>") and the screensaver has a **Web page** style (page, refresh every 1–5 minutes, show time).
- **Spotify sign-in expiry** (backlog U21). Spotify refresh tokens end 6 months after sign-in. Displays → Car Thing → Music now says when (6 months from connecting, or a date you set under **Change the expiry date**), turns amber in the last 14 days, and red with Reconnect once it has run out or the Car Thing service reports the sign-in is gone. A daily check sends two new admin events, **Spotify sign-in expiring** (14 days before) and **Spotify sign-in expired**, once per expiry date. Reconnecting starts a fresh 6 months and clears the set date.
- Signed `GET /api/internal/web/<id>.png?w=&h=&max_age=` for the Car Thing service (colour snapshot); `/api/internal/carthing/state` lists the web pages.

### Changed
- **The bell in Notify opens the "Your alerts" panel**, the same one as on Dashboard home (backlog U29): what's on for you, recent notifications from every app, Clear, and **Preferences** to Notifications. It was a plain link. The panel is built from Notify's own data, without Notify calling itself over HTTP, and the dot now means a new alert since you last opened the panel, as on the Dashboard (the "Set up phone alerts" nudge moves into the panel).

### Removed
- The display-count pill in Notify's header (backlog U29).

### Security
- `/api/events` and `/api/push-trmnl` only accept `application/json` (a web page can't send it without a preflight), and nginx no longer routes either one. Captured web pages can't reach this Pi's loopback services.

### Fixed
- One TRMNL push per Display at a time. Captures give up after 60 s waiting for Chromium. Images over 1 MB aren't sent, and a 413 says "too big". The capture cache no longer evicts locks that are still in use. Page ids are race-free. The preview fails closed without a session secret. The unit has CPU, IO and memory weights for Chromium.

### Changed
- The Car Thing's system feed gives each service its `running` flag, detail `line` and `note` on their own, and Notify's health-check `alerts`, for the panel's new System screen.
- Car Thing editor, **Look → Music** (and ⚙ on a Music widget): **Now Playing look**: Background (Glow, Artwork colour, Blurred artwork, Theme), Controls on screen (Always, When touched, Never), Accent (from the artwork or the panel's) and an edge line. Changing one opens Now Playing in the preview. The Liked Songs option now describes the tick (save or remove).
- Spotify sign-in asks for `user-library-read` too, so Now Playing can show whether a song is liked: press **Reconnect Spotify** once under Setup to get it.
- Car Thing editor: changing the clock face or colour under **Look → Clock** now shows the result on the screensaver in the preview when the screensaver uses that clock (and the page you're editing has no Clock widget); it used to reload on the page, so the change looked ignored. With the screensaver clock set apart, Look says the screensaver has its own face.
- Car Thing configs keep their **last 5 versions** (was 10); older ones go at the next save.
- Notify installs Playwright and uses the shared Chromium in `/opt/stonepi/.cache/ms-playwright` (unit sets `HOME` and `PLAYWRIGHT_BROWSERS_PATH`), behind the cross-app Chromium lock. Captures are shared: a preview, a push and the Car Thing asking for the same page and size within a minute use one Chromium run.

## 0.3.0 — 2026-10-17

### Changed
- **Shared StonePi header** (backlog U16): the old sign-out icon is replaced by the same app switcher and avatar menu as every app (appearance and Log out in the avatar menu). The bell stays as it was, a link to your notifications. **Home** is gone from the nav (the switcher's Home replaces it), so the bottom bar has three tabs. The "Notify is for administrators" page gets the header too.
- New event **AI unavailable** (`system.ai_unavailable`, admin): every AI model for a job failed and the app is on its non-AI fallback. Sent once until a model works again. Approved by default on fresh installs; existing installs approve it once in Notify → Events.
- `GET /api/admin/summary`: `displays.carthing` says whether the Car Thing is switched on and showing the panel now, for the Dashboard's Displays line (backlog B10).

### Added
- **Car Thing → Music (Spotify): connect your Spotify account** (backlog F7, step S1). A new Music card in Displays → Car Thing walks you through creating your own Spotify developer app, takes its client ID (saved in the Vault; no client secret, since sign-in uses PKCE) and connects one Spotify account for the house. On HTTPS (the Tailscale `ts.net` address) Spotify sends you straight back to Notify; otherwise you approve, land on a `127.0.0.1` error page, and paste its address back into Notify. The card then shows the connected account, with **Test connection** (asks the Car Thing service for a Spotify token) and **Disconnect**. Admins only. The refresh token goes to the Vault and the account name to `carthing/music.json`.
- Car Thing editor → Pages: a **Clock** widget (the clock on its own, no weather). The Clock tab's face hint now names both clock widgets.
- **Car Thing editor → Music tab** (backlog F7): presets 1–4, each picked from your playlists or pasted as a Spotify link (playlist, album, artist or track), with an optional speaker; the **default speaker** for when nothing is playing; and options for dial volume on Now Playing (with the step per click), the artwork screensaver and Save to Liked Songs. Speakers and playlists come from the Car Thing service, which holds the Spotify token. **Show Now Playing in the preview** opens it in the drawing. Actions gain a **Music** group, the live line can be **Music**, and the preview can send Music commands to the real speakers.
- Car Thing editor → Idle: **Screensaver style** (Clock, Bouncing logo, Matrix, 3D Pipes) with each style's options; changing one shows the screensaver in the preview. 3D Pipes: corners, speed, show time.
- Car Thing editor → Clock: **Give the screensaver its own face and colours** (backlog U13). Off by default; switched on, it starts from the widget clock's face and colours. **Tapping the Clock & weather widget**: nothing (default) or open the weather screen (U14).
- Car Thing editor → Pages: the **Weather** widget (small, half, full); Controls and Actions: **Open Weather** (allowed in saved configs without re-ticking).

### Fixed
- **Car Thing preview was slow to show changes**, so screensaver options looked like they did nothing. The preview proxy built a new HTTP client for every file the panel loads (about 350 ms each, mostly loading the certificate store), so each reload took several seconds, and the screensaver only came up after every font and icon had loaded. It now reuses one client (each file takes milliseconds), and after a screensaver change the preview opens straight on the screensaver. The Clock tab also says when the screensaver clock settings can't show because the screensaver style isn't Clock.
- Leaving the Car Thing editor or a TRMNL display's builder with unsaved changes (Home, the nav, sign out) asks in Notify's own sheet instead of the browser's "Leave site?" dialog. Reload, closing the tab and the browser's Back button still use the browser's prompt; pages can't restyle that one.
- Car Thing editor: the device drawing matches the real Car Thing (round dial right of the screen, Back below it, presets and Settings/Mute on the top edge). Dial and Back labels sit under the drawing's right side.

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
