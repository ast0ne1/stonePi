# Notify

**Part of [StonePi](../../README.md)** — central Displays and Outputs (platform service).

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/notify/ |
| Local (port) | http://127.0.0.1:8012/ |

Shared StonePi sign-in. **Not a launcher tile** (`launcher: False`) — like Auth, it appears on **Services** as an always-on platform card.

## What it does

- **Displays** — one per TRMNL screen (built-in Dashboard Display + as many as you add), each with its own TRMNL webhook, interval and device (TRMNL OG or TRMNL X). **Add a Display** offers **Widgets** (a drag-and-drop **screen builder** over a real render of the device; needs a TRMNL Private Plugin), **Web page** (a captured page turned into e-ink; needs a TRMNL Webhook Image plugin), and one kind per app with a display page, such as **Ripple family board** (name, model and webhook; see below). Each Display card shows its plugin, and each Display page opens with a **setup checklist** that ticks itself off.
- **Web pages (shared)** — pages the displays can show as a captured image (e.g. a self-hosted Homepage dashboard): address, clean-up preset (**Flat** removes background images, blur and shadows), parts to hide (CSS selectors), extra CSS, zoom and a wait for late widgets. **Preview** shows the page in colour and where it's used. Captured by headless Chromium (Playwright, the shared copy in `/opt/stonepi/.cache/ms-playwright`, behind the cross-app Chromium lock; a capture gives up after 60 s waiting for it); one capture serves a preview, a push and the Car Thing within a minute. A captured page can't reach this Pi's loopback services. Stored in `web_pages.json`.
- **Car Thing** — the touch-and-dial panel's saved configs, edited beside a live preview (Displays → Car Thing; see [Car Thing editor](#car-thing-editor)). The device, Spotify sign-in, saved configs and earlier versions are on **Setup** (`/displays/carthing/setup`). **Music:** connect Spotify under Setup (admins; your own developer app, PKCE, one account for the house), then presets 1–4, a default speaker, the Now Playing look, and dial-volume, artwork-screensaver and Liked Songs options under Look → Music or on a Music widget. Spotify ends a sign-in 6 months after connecting, so Setup's Music card shows the expiry date (worked out from `connected_at`; an admin can change it under **Change the expiry date**), turns amber in the last 14 days and red with Reconnect once it has run out or the Car Thing service gets `invalid_grant`. A once-a-day check sends the admin events `carthing.spotify_expiring` (14 days before) and `carthing.spotify_expired`, once per expiry date. Reconnecting starts a fresh 6 months.
- **Phone alerts** — **ntfy**: the household token and topic, and which events may notify. People with the Phone alerts permission set up their own alerts from the bell.
- **Bell** — opens the **Your alerts** panel, as on Dashboard home: what's on for you, recent notifications from every app, Clear, and **Preferences** to Notifications. The dot means a new alert since you last opened it.
- **History** — the delivery log.
- **Ingest** — `POST /api/events` (loopback, `application/json` only; nginx returns 404 for it) via `stonepi_contracts.emit_event()`.

Apps own data; Notify owns delivery. External failures never break apps.

## Platform notes

| Field | Value |
|-------|--------|
| Path | `/notify/` |
| Port | `8012` |
| Unit | `stonepi-notify` |
| User | `stonepi-notify` |
| Data | `/var/lib/stonepi/notify` |
| Packages | `stonepi_contracts`, `stonepi_display`, `stonepi_notify`, `stonepi_browser` |

Vault keys: `STONEPI_NTFY_TOKEN`; TRMNL webhooks `DISPLAY_WEBHOOK_URL` (built-in Dashboard Display) and `DISPLAY_WEBHOOK_URL_<ID>` (other Displays); `SPOTIFY_CLIENT_ID` and `SPOTIFY_REFRESH_TOKEN` (Car Thing Music; the account name, `connected_at`, any expiry date set by the admin and the sent alerts are kept in `carthing/music.json`).

Dashboard Settings → Display redirects here. The push scheduler runs in this process and pushes each enabled Display on its own interval; Dashboard automations call `POST /api/push-trmnl` with a JSON body (pushes every Display with TRMNL on; loopback, `application/json` only, and nginx returns 404 for it).

## TRMNL Displays

1. On trmnl.com, create one **Private Plugin** per screen and copy its webhook URL.
2. In Notify → Displays, open (or add) a **Widgets** Display, paste the webhook under **TRMNL**, and switch **Push to TRMNL** on.
3. Paste the **universal template** (Display → TRMNL → *Universal template for the plugin*) into the plugin's **Full** markup once. The layout travels in each push (`L` = `[[code, x, y, w, h], …]`, `G` = `[cols, rows]`), so rearranging widgets never needs a re-paste.
4. Lay out widgets in the builder. The preview renders the same template with the same payload a push sends, styled by the TRMNL Framework (3.2.0, loaded from trmnl.com), with sample or live data.

Pushes send only the variables the Display's widgets read, trimmed to the plan's payload limit. Each Display picks its TRMNL plan: **Standard** allows 12 pushes an hour and 5 KB (intervals 10–120 minutes), **TRMNL+** 30 pushes an hour and 10 KB (intervals 5–120 minutes). Unchanged data isn't re-sent.

### Web page Displays (F14)

Add a Display → **Web page**, and give the page's address there (or reuse a page you already have), so the Display and its page are made in one step. On trmnl.com add a **Webhook Image** plugin (it can share the device and playlist with your other plugins, including the Dashboard panel) and paste its webhook under **TRMNL**; it's kept as a Vault key per Display (`DISPLAY_WEBHOOK_URL_<ID>`).

The page's address, clean-up, parts to hide, extra CSS, zoom and wait sit on the Display beside the e-ink settings, and **Preview** uses them before they're saved. If the page is shown elsewhere too, the Display says where. **Use this page instead** switches to another page. Deleting a Web page Display also deletes its page when nothing else shows it.

Each push captures the page at the panel's size (OG 800×480; X 1872×1404, laid out at 1040×780 × 1.8), converts it with Pillow (greyscale, contrast stretch, invert dark pages automatically, always or never, then black and white with a cut-off slider, 4 or 16 greys, thresholded or dithered; the default is black and white on the OG and 16 greys on the X) into a true greyscale PNG, and POSTs it raw (`Content-Type: image/png`). Webhook Image takes 12 images an hour on any plan; StonePi sends at most 10, every 5–120 minutes, and never re-sends an unchanged image. Images over TRMNL's 1 MB aren't sent (the Display suggests fewer greys or no dithering). One push runs per Display at a time. **Preview** on the Display shows the exact image a push sends; **Push now** pushes it straight away. A push TRMNL refuses (4xx) says which plugin the Display needs, in case the webhook came from the other kind.

Upgrading: the old single TRMNL destination moves onto the Display it pushed, in **legacy** mode (its plugin still holds the older pasted markup, so every flat variable is still sent). Paste the universal template and switch the Display to it to use the builder layout.

### App display pages (Ripple)

An app can offer its own e-ink page: the catalog marks it with `capture_key` (a signed endpoint) and
`display_page` (with a label and blurb). `app/app_pages.py`:

- **Add a Display → Ripple family board** asks only for a name, the TRMNL model and the Webhook Image
  webhook. It creates the shared page (`http://127.0.0.1:<port>/display`, no clean-up, no wait), a web
  Display with the model's greys, never inverted and no dithering (the app prepares its photos), push on
  every 60 min, the webhook in the Vault, and pushes once. Ripple → Settings → Family display links
  here with the card picked (`/displays?add=ripple`).
- **Capture keys:** for a page at that app's port on this Pi, every capture first fetches a one-off key
  (signed v2) and adds `k`, `w`, `h`, `scale` and `bits` to the address (`stonepi_display.webcapture`
  key provider). The app accepts it once, within two minutes, only from loopback without forwarding
  headers.
- **Change nudge:** the app posts `POST /api/internal/web/changed` (signed v2, `{"app": "ripple"}`).
  Notify waits 20 s to batch quick edits, forgets that page's cached captures, and pushes its Displays
  (skip-unchanged, hourly budget). Pushes held back (one already running, the hourly limit, a TRMNL
  back-off) are retried every 2 minutes for up to an hour.
- **Status for the app:** `GET /api/internal/web/app-displays/{app}` (signed) lists the app's Displays
  and their last push, for its own settings page.

## Car Thing editor

Displays → Car Thing is the editor. Under the title, pills show whether the panel is on, **Showing: <config> ▾** (switch config, Use on Car Thing, Export, Delete, Save a copy, Rename, Import, earlier versions) and Spotify (amber in the last 14 days, red with Reconnect once the sign-in has run out). **⚙ Setup** beside the title opens `/displays/carthing/setup`: Device, Spotify sign-in, Saved configs and Earlier versions, with one Save for the PIN, client ID and expiry date. Configs keep their last 5 versions.

Four tabs, with **Undo** and **Save to panel** in the tab row (**Save** plus **Use on panel** when the config isn't on the Car Thing). The open tab is remembered, and `#pages`, `#buttons`, `#idle` or `#look` opens one.

| Tab | What's there |
|-----|--------------|
| **Pages** | Page pills (drag or Alt + arrow keys to reorder; **+** adds an empty page), the page's own **header**, a 6-space bar, and the page's widgets as rows: drag ⠿, **Small / Half / Full**, a summary, ✕, and ⚙ on Clock and Music for their settings. **+ Add widget** lists the rest; **+ New web page** adds a page from Web pages as a full-page widget ("Web: <name>"), on this page if it's empty, otherwise on a new one |
| **Buttons** | Buttons, Dial, Holds and Swipes, with *What this config may do* folded at the bottom |
| **Idle** | When the screensaver starts and the screen dims, quiet hours, turning pages, and the screensaver: its clock, background and what it shows. **Style** is a dropdown: **Clock & info** (Clock, Web page), **Classic** (Bouncing logo, Matrix) and **Windows 98** (3D Pipes, Flying Through Space, Mystify Your Mind, 3D Flower Box, 3D Maze), each with its options. The Web page style picks a page, refreshes every 1–5 minutes and can show the time. **Screensaver time** sets where the time sits, its size and a dark backing for the animated styles (Matrix has its own) |
| **Look** | Theme, accent, weather location, the **Page header** default (Always, Auto-hide or Off; hide after 3, 5 or 10 s; time & date, page title, weather, page dots), and folded **Clock** and **Music** sections. Music holds the Now Playing look (background Glow, Artwork colour, Blurred artwork or Theme; controls always, when touched or never; accent from the artwork or the panel; an edge line) and, for the artwork screensaver, **Show the time on it**, its position, size and dark backing |

**Default** buttons beside the panel accent, the clock colour and accent, and the screensaver clock's colour and accent put back StonePi's sand accent and stone clock colour.
