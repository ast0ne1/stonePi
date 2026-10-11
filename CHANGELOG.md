# Changelog — StonePi platform

Platform version lives in [`VERSION`](VERSION). App zips use each app’s `__version__` (see `scripts/build_release_zips.py`).

## 0.2.0 — 2026-10-11

**A new look.** StonePi gets its own design system across every app, with one shared header, app icons, light and dark themes, and a Home that greets you by name and fits on a phone screen. Also: the NewsCast Briefing fills in during a refresh, a Clear button in the bell, TRMNL plans per Display, steadier Car Thing links, and a fix for admins locking themselves out.

### Added
- **New look: the StonePi design system** (backlog F6; spec `ideation/ui-refresh.md`). StonePi's own identity replaces the warm-paper palettes and the red accent across every app, both themes (Dark, Light, System).
  - **`packages/stonepi_brand` is the single source of the look:** self-hosted Sora (titles), IBM Plex Sans (text) and IBM Plex Mono (ports, versions, numbers); `tokens.css` generated from the design system's `tokens.json` by `build_tokens.py` (never hand-edited); `components.css` and `app-base.css` (shared buttons, inputs, cards, banners, status pills); the StonePi mark in five variants; fixed app and system icons with favicon, home-screen and PWA sizes (`render_icons.py`). Served at `/assets/brand/` by nginx and by `stonepi_auth.brand` in run-dev; every page links it through `stonepi/head.html`.
  - **Matte, calm, colour only for meaning:** no wallpaper, glass, blur or gradients, no coloured side stripes; Sand primary buttons on dark, Basalt on light; Rust only for errors and destructive actions; status is always dot + word.
  - **Icons:** each app has a fixed icon (its category colour, one Sand piece) in its header, the app switcher, Home, Services, Users, Updates and as its favicon and home-screen icon. Dashboard uses the StonePi mark.
  - **Shared header restyled:** matte top bar, design-system icon buttons, Rust bell dot, the switcher with the app icons, pill theme switch in the avatar menu.
  - **Dashboard Home:** the date, a welcome message and **Your apps** as cards with the app icons (Cards, List and Icons layouts all kept). The welcome message is per person, in Settings → General → Appearance: Time of day (default, by your browser's clock), Hello, your own text (up to 60 characters, `{name}` for your name) or none. Stored in Auth (`PUT /api/me/welcome`).
  - **Health** stats are widgets with meters that turn amber above 80 % and red above 95 % and say so in words.
  - **Removed:** the colour palettes (Default, Ocean, Forest, Slate) and **App colours** (`/settings/app-colours`); saved choices are ignored, icons have fixed colours now.
  - **Car Thing screen:** design-system palette and fonts (served by the panel service, cached by the device), Sand accent, Basalt background and Sora clock for **new** configs; saved configs keep their colours. App icons on page widgets; the StonePi mark on the pairing and restart screens.
  - **TRMNL:** the title bar shows the StonePi mark instead of TRMNL's icon. **Re-paste the universal template into TRMNL for every Display** (Notify → Displays → the Display → template) to get it; the screen keeps working with the old template until you do.
  - **Install site:** same lockup, tokens and fonts; small-cut mark as favicon.
  - The X3 / NewsCast reader paper is unchanged.
  - **Docs:** new phone-sized screenshots in `docs/screenshots/` (Home, Health, Services, sign-in, Users, Settings), shown three to a row in the README. READMEs brought up to date: Notify's Phone alerts (was Destinations), the platform Phone alerts permission (was NewsCast's ntfy flag), NewsCast's AI tab and bottom bar, EventTrakr's settings tabs, TRMNL plans, palettes and App colours removed, and two app READMEs' broken sign-in images.
- **Shared app header and bottom bar in every app** (backlog U2, U6, U10; spec `ideation/stonepi-navigation-plan.md`). One component in `stonepi_auth` (`stonepi/header.html`, `stonepi_auth.header`) gives every app the same header, left to right: app logo, search, the app's primary action (Refresh in NewsCast, EventTrakr, PriceScout, SportGuide and PriceWatch), the bell, the app switcher and your avatar. Each app only supplies its logo, search page, primary action and tabs, so the coming restyle (F6) changes one header instead of nine.
  - **App switcher** (grid icon) replaces the Home tab: **Home** (the Dashboard, with the house icon), then the nine apps in Home order, the current one highlighted.
  - **Avatar menu:** who's signed in, light/dark/system, Manage account (Dashboard → Settings → Your password) and Log out in red below a divider. The sign-in page has its own light/dark switch.
  - **Bell panel, per app:** which of that app's alerts you have on, the last alert it sent you (what, when, whether it reached your phone) and **Preferences** (`/me/notifications`). A dot marks a new alert from that app since you last opened its panel. With nothing set up, or none of that app's alerts on, the panel says so first, with a button to set them up. The bell itself still only appears once an admin has phone alerts on. On the **Dashboard** the bell's panel is an overview across every app: the alerts you have on, grouped by app, and your last five notifications with whether each reached your phone. Notify gains `/api/me/apps/{app}`; `stonepi_contracts.app_alerts_status` caches it per person and app for a minute.
  - **Bottom bars** carry 2–4 destinations with Settings last; Home leaves every bar, Search moves to the header (NewsCast, PriceScout, EventTrakr), Sources moves into Settings (NewsCast, PriceScout, SportGuide, EventTrakr), and Add/Create/New notice become a floating **+**. EventTrakr goes from 8 tabs to 4. Panels open as dropdowns, near full-width over a dimmed backdrop on phones, and close on a tap outside, Esc or Back; under ~420 px the header shows only the app logo.
- **Car Thing Sleep action** (backlog U8): a mappable action that goes straight to the dimmed screensaver, or turns the backlight off where the panel controls it; any input wakes it. New configs map it to swipe up. Car Thing configs now record the actions they knew about (`actions_known`, `stonepi_display`), so an action added later is allowed in older allow-lists instead of silently blocked.
- **Car Thing:** swipes now register on the device (the panel claimed touch gestures from the browser, which had been cancelling them as scrolls; lists still scroll). Swipe left/right turn pages by default. Notify's editor groups Touch gestures in Controls, shows swipe callouts under the drawing, and draws arrow zones on its screen while mapping.
- **Car Thing drop-outs fixed at the cause:** the panel service ran `adb reverse --list` every 30 s, and on the Car Thing's firmware that takes ADB down along with the whole USB link, so the panel froze ~30 s after every takeover (12 times in a row across three ports). It no longer lists: the panel's 15 s check-ins prove the link, and the reverse is only re-created (plainly) after 45 s without one.
- **Car Thing link:** when the USB link to the Pi drops, the panel shows a "Lost connection to StonePi · reconnecting…" bar instead of looking frozen, retries every 3 s and reloads its view when the link is back. The udev rule keeps the Car Thing out of USB autosuspend. The panel service now logs connects, takeover steps and disconnects (quiet on routine HTTP polling).
- **TRMNL push limits** (`stonepi_display`): per-Display Standard / TRMNL+ plan with the documented payload and hourly limits, unchanged-screen skips and 429 back-off.
- **Alerts bell** opens the personal alerts page at its new address, `/me/notifications` (`stonepi_auth` / `stonepi_contracts`), fixing browsers stuck on a cached pre-rename redirect to Notify.
- **Dashboard:** Home → Reorder has **Default order**, which resets the tiles to the platform order.
- **Notify event** `newscast.push_missed` ("Not delivered to reader", personal) in `stonepi_contracts`; pre-approved on fresh installs, existing installs approve it once in Notify → Events.

### Changed
- **Install site:** the nine apps as cards with their icons, and the Home screen beside the title (light or dark to match the visitor's setting), and a "Made for your phone" row of NewsCast, EventTrakr and SportGuide (light, captured from a real StonePi). Displays are named as TRMNL and Car Thing. Install page: no emoji or exclamation marks; one Callout style (warning for the invisible-password note, neutral for the rest); the two passwords explained once in step 1; a "read the script first" link beside the install command (`/install.sh` now served as plain text). New **Callout** component (`.sp-callout`, `.sp-callout--warning`) in `stonepi_brand` components.css.
- **Dashboard on phones** keeps the **StonePi** wordmark in its header (apps still show just their icon on narrow phones).
- **Bell panel: Clear.** Each panel has a **Clear** link that hides what it lists, for you only: nothing is deleted and Notify → History still has every delivery. Clearing on the Dashboard clears every app; inside an app it clears just that app. New alerts show again as they arrive (`stonepi_notify` `bell_cleared.json`, Notify `POST /api/me/apps/{app}/clear`, shared header).
- **Phones (new look):** the app icon in every header is the same size as the header buttons (40 px, 36 px on narrow phones), and Refresh matches them (icon-only on phones, labelled for screen readers). The bell panel's empty state reads "No notifications yet. When one of your apps sends you something, you'll see it here."
- **Home tile order** by default: NewsCast, EventTrakr, SportGuide, PriceScout, PriceWatch, Pinboard, Library, FileServe, Studio (`stonepi_auth.DEFAULT_LAUNCHER_ORDER`). People who reordered keep theirs.
- **NewsCast:** new installs start with BBC World as the only feed. **Daily refresh at** (Settings → Schedule) runs the Once-a-day refresh at a fixed time; a **reader auto-push window** looks for the e-reader for 30 minutes after something is queued; the X3 cover shows the lead story's photo; paper alerts go out once per edition. Details in [apps/newscast/CHANGELOG.md](apps/newscast/CHANGELOG.md).
- **Dashboard → Settings → Vault** is one list: each secret once, with its status, env var name and action on one row.

### Fixed
- **NewsCast: the Briefing fills in during a refresh.** Each source's new stories are saved as soon as that source is done, and the Briefing swaps its list in place, keeping open stories and your filter, instead of waiting for every source and reloading. When two sources carry the same story, the first keeps it. Also: the morning paper no longer publishes late, and sources are no longer reseeded on every start.
- **Health said "ntfy off"** while phone alerts worked; it now follows Notify's per-person setup.
- **Bell panel said "Couldn't reach Notify" when Notify was fine but slow** (e.g. just after a restart). The bell now keeps showing its last good answer for up to 15 minutes, waits a little longer on its first look, and if it truly can't check it says "Couldn't load your alerts just now" instead of implying Notify is down (`stonepi_contracts`, `stonepi_auth`).
- **Admins could lock themselves out** by switching their own role to User on Dashboard → Users (it autosaves), even as the only admin. Auth now keeps at least one enabled admin and refuses self-demotion, self-disable and self-delete; the Dashboard locks your own role switch.

### App versions in this cut

Platform: **0.2.0**

| System (ships with platform) | Version |
|-----|---------|
| dashboard | 0.2.0 |
| auth | 0.2.0 |
| notify | 0.2.0 |
| recover | 0.2.0 |
| carthing | 0.2.0 |

| Apps | Version |
|-----|---------|
| newscast | 0.0.8 |
| fileserve | 0.0.8 |
| eventtrakr | 0.0.8 |
| pinboard | 0.0.8 |
| studio | 0.0.8 |
| pricescout | 0.0.8 |
| sportguide | 0.0.8 |
| pricewatch | 0.0.8 |
| library | 0.0.8 |

| Packages | Version | Why |
|----------|---------|-----|
| stonepi_auth | 0.2.0 | design system serving (`brand`), shared head and restyled header, bell Clear, alias and welcome, `DEFAULT_LAUNCHER_ORDER` |
| stonepi_contracts | 0.1.2 | bell keeps its last good answer when Notify is slow; `newscast.push_missed` event |
| stonepi_display | 0.5.0 | TRMNL plans per Display, push skips and back-off; Car Thing Sleep action, `actions_known`, Sand/Basalt defaults; StonePi mark in TRMNL's title bar |
| stonepi_notify | 0.1.1 | bell Clear (`bell_cleared.json`); personal test notifications recorded |
| stonepi_brand | (static files) | the design system: tokens, fonts, logos, app and system icons, components |

## 0.1.9 — 2026-10-04

Fresh-install fixes, the new **Library** app, **PriceWatch** retailer trust scores, a **Jungle** source for EventTrakr, football channels from two guides in **SportGuide**, and a security review of all of it. 

### Added
- **EventTrakr permissions** (Dashboard → Users): Manage sources, Instagram & Facebook, Share agenda publicly, Google Calendar sync, enforced on pages, APIs and background jobs. Existing members keep everything except Instagram & Facebook (paid Bright Data) with no admin action; app permissions can now declare a catalog `default` that Auth applies to older grants and new people.
- **EventTrakr search** matches every word in any order across title, place, source, category and description, ignoring case and accents (æ/ø/å), ranks title matches first, and no longer silently filters by your default location.
- **Car Thing panel (new platform service, `apps/carthing`, versioned with the platform):** a Spotify Car Thing plugged into the Pi by USB becomes an 800×480 touch + dial control panel. Plan: `ideation/carthing-plan.md`.
  - **Firmware untouched:** the device keeps its own (ADB-enabled Thing Labs) firmware. `stonepi-carthing` borrows its screen the way [pything](https://github.com/trwy7/pything) does (MIT): `adb reverse tcp:8013`, plus a redirect page pushed to the device's `/tmp` and bind-mounted over its web app. Switching the panel off, unplugging or rebooting the Car Thing gives it its own app back. Pairs by itself with a fresh device token on each connect.
  - **Panel:** Home cards and lightweight list → detail mini-apps for **System, SportGuide, EventTrakr, NewsCast, Pinboard**; screensaver with **seven clock faces** (bold, thin, segment LCD, stacked, split-flap, analogue, word clock), clock/accent/quiet-hours colours, date, weather (Open-Meteo) and a live line; dimming and quiet hours on the real backlight over adb where available, otherwise by palette. Every button, the dial, long presses and swipes map to actions, limited by a per-config list of allowed actions. Server-rendered fragments, ~15 KB of plain JS for the firmware's older Chromium; checks in every 15 s.
  - **Restart StonePi services** from the System mini-app, behind a 4-digit admin PIN (salted; lockout after wrong tries) that the admin can switch off (then a one-tap confirm). Dashboard does the restart through a new signed endpoint, `/api/internal/units/restart`, limited to catalog units.
  - **Notify → Displays → Car Thing:** switch the panel on/off, re-pair, PIN; **saved configs** (one active; copy, rename, delete, export/import JSON, last 10 versions restorable); editor tabs Home · Controls · Actions · Idle · Clock · Look beside a drawing of the Car Thing with the live panel (unsaved draft) in its screen: click its buttons/dial to map them or to try them. Configs live in Notify's data folder (`carthing/`), so backups include them.
  - **Feeds:** each app's loopback `/api/display` gains opt-in `?items=N` (`card` + `items`); the plain call is unchanged. NewsCast now exposes headlines.
  - **Pages:** up to 6 pages of widgets instead of one Home, turned by hand (Next/Previous/Go to page N on any button, dial or swipe, plus page dots) or on a timer that pauses while someone uses the panel; screensaver and quiet hours still come first, and "Screensaver after 0" keeps a dashboard up by day. Existing configs and exports load as page 1.
  - **Widget sizes:** small, half and full page; a large System widget (CPU, memory, temperature, disk with bars, services, uptime) and a new Clock & weather widget (screensaver faces, 3-hourly and 3-day forecast when large).
  - **Install:** apt `adb` (best effort), user `stonepi-carthing`, udev rule, unit installed but not enabled, root helper `stonepi-carthing-helper` (sudo for `stonepi-notify` only), ready-check lines. Not a catalog app (no tile, no Watch row): ships in the platform zip via `PLATFORM_SERVICE_APP_IDS`.
- **Library (new user app, 0.0.7):** offline Wikipedia and references through Kiwix at `/library/` (port 8009, unit `stonepi-library`, capability *Manage content*). Featured Wikipedia, Wikivoyage, Wiktionary and Gutenberg in each size with "Fits" / "Doesn't fit", plus **keyword search across the whole Kiwix catalogue** and links pasted from browse.library.kiwix.org. Spec: `ideation/offline-library.md`; details in `apps/library/CHANGELOG.md`.
  - **Reader:** `stonepi-kiwix` (loopback 8014, not a catalog app) at `/library/read/` behind nginx `auth_request` against the Library's `/_auth`, so StonePi sign-in applies on the LAN and Tailscale alike. It runs only while content is installed.
  - **Root helper** `stonepi-library-helper` (sudo for `stonepi-library` only): installs `kiwix-tools` from apt, mounts a USB stick or SSD by UUID (ext4 or exFAT; FAT32 refused; the backup drive only if ext4) and sets the content folder.
  - **Downloads:** background, resumable, verified against Kiwix's SHA-256, kept across restarts; updates swap safely, or replace in place when both copies won't fit; storage moves between microSD, USB and a custom folder.
  - **Backup:** ZIMs are never in the per-run copy; Library settings, the content list, the Kiwix drop-in, the fstab line and the Kiwix `.deb`s always are. Optionally (Library → Settings → Backup, with a capacity check) ZIMs go to USB backups in one shared, incremental, verified store (`stonepi-backup-library`); a copy that doesn't fit marks the run `STATUS=partial` and alerts admins. Restore reinstalls Kiwix offline, restores the drive mount and copies content back (to the microSD if the drive is gone).
  - **Dashboard:** Library tile with live status ("Installing Wikipedia · 71%") via a new catalog flag, `live_status`. **Recover** lists `stonepi-library`.
  - **Notify events:** `library.content_installed` (household), `library.download_failed`, `library.update_available`, `library.storage_missing`, `library.backup_capacity` (admin).
- **PriceWatch 0.0.7:** **Compare** (every retailer's offer with price, delivery, total, stock and trust score, and **Watch this**); **retailer trust scores** from Trustpilot via Bright Data (background lookups within the shared Bright Data limit) with PriceRunner's own rating as an opt-in fallback; per-watch minimum score, "Require a rating", Ignore / warn for cheaper low-rated shops, and "Target includes delivery". New Notify event `pricewatch.low_rated_offer` (personal, warning), pre-approved on fresh installs. Spec `ideation/pricewatch-trust-score.md`.
- **EventTrakr 0.0.7:** **Jungle Copenhagen** (`jungle.am/events`, Food & Drink) in the recommended sources, read from the site's own event data over plain HTTP (no headless browser).
- **Bright Data monthly limit, shared by all apps** (`stonepi_vault.quota`, default **5,000 records**: the free tier): EventTrakr (Facebook, Instagram) and PriceWatch (Trustpilot) count against one allowance kept in the Vault folder (`quota-brightdata.json`, group-writable like the Vault; no installer change). Each call books its maximum first and settles the real record count after, so the limit can't be passed; scheduled jobs also spread it evenly across the period, while manual buttons can use what's left. The period resets on a chosen **day of the month** (default 1; matches the Bright Data billing date; a day past a short month's end uses its last day). When the limit is reached, calls are skipped and sources keep what they have until the reset. Set the limit and reset day in EventTrakr → Settings → Data Providers or PriceWatch → Settings → Trust scores; both show this period's usage per app. A feature can have its own **allowance inside the limit** (EventTrakr's Instagram polling: default 2,000). Replaces PriceWatch's own 500-record cap.
- **Dashboard → Settings → Vault:** a **Data providers** group: `BRIGHTDATA_API_KEY` (EventTrakr, moved from NewsCast) and `PRICEWATCH_BRIGHTDATA_API_KEY`. `scripts/migrate_secrets_to_vault.py` also moves a PriceWatch key out of its database.
- **Installer ready check:** the install ends by checking, as each service user, that data folders are writable and owned, the Vault reads and saves, Notify's status is readable, the exposure flag is writable, sudo works for services/logs/backups/updates, and tesseract and Chromium are present. Saved to `/var/lib/stonepi/ready-check`; the install site reports it.
- **Dashboard:** QR code for the Tailscale login link (Settings → Network).

### Changed
- **SportGuide 0.0.7:** football reads **timezone.football** (channels in the Pi's own country) and **WheresTheMatch** (UK channels, marked "(UK)" outside the UK) and merges them per match, from each site's HTML structure over plain HTTP (browser as fallback). Matches show the channel, "Club's official stream", "Channel TBC" or "Not on TV in <country>" instead of "Check guide", and open the match's own page. Adds `beautifulsoup4`.
- **PriceWatch:** watches are private to whoever created them; admins still see and manage every watch.
- **Installer:** one pip run per app (requirements plus shared packages together), skipped when nothing changed; pip upgrades itself only for a new virtualenv. Re-running the installer no longer uninstalls and reinstalls every package.
- **Installer:** one shared, root-owned Chromium for EventTrakr and SportGuide, installed once (headless shell only, ~150 MB less); Playwright's `install-deps` (its own apt update + install) runs only if Chromium can't start. Installs `tesseract-ocr` for EventTrakr's poster reading.
- **Installer, faster:** one apt run with the right package names for Bookworm or Trixie (no failing first attempt), `unattended-upgrades` included; shared StonePi packages install without a throwaway build environment per package per app, and pip no longer upgrades itself in every new venv. About half the Python install time per app.
- **Dashboard:** live tile status is fetched in the background, so a slow or stopped app can't hold up Home.
- **Faster pages and a quieter Pi** (it shares its CPU with Tailscale's encryption, so this matters most remotely):
  - Auth remembers whether the factory password is still set instead of re-running argon2 (~0.2-0.4 s of CPU) on every admin page, and sign-in no longer blocks Auth's other requests while it checks a password.
  - Dashboard's background monitor re-checks nginx with `systemctl` every 20 s instead of every 2 s, and only rebuilds its summary when something was refreshed; automations reuse its results instead of probing every service again each minute; the USB backup check no longer walks every file on mounted drives.
  - Dashboard Services pages use the monitor's results instead of probing every service per visit; Settings → Network reads the Tailscale API settings from the Vault directly (no root helper starting Python twice); Settings caches Notify's summary for 30 s; Home skips an admin-only Auth call for members.
  - The Tailscale sign-in poll runs every 4 s and stops after 5 minutes instead of calling the Tailscale CLI every 2 s indefinitely.
  - NewsCast remembers which icon belongs to which feed until the icon folder changes, instead of searching it and reading files for every feed and story on each page.
  - Apps no longer write an access-log line per request to the journal (nginx already logs requests); Library downloads use idle disk priority; the reader's per-asset access check runs without a thread hop.
- **Release builds** come from an LF export of the tag (`git -c core.autocrlf=false archive …`); see `deploy/RELEASE.md`.
- **Install summary** shows the Recover console login (user `stonepi` and its password) next to the first admin password, and says it is a separate login. The web bootstrap's final box shows it too.
- **Installer:** pip runs with `PYTHONWARNINGS=ignore:DEPRECATION`, hiding the wall of piwheels `pytz` "Wheel filename is not correctly normalised" warnings on fresh installs.

### Fixed
- **Upgrades restart Recover and the failover monitor.** The installer only ran `enable --now`, which leaves a running unit on its old code, so after an update Recover kept showing the old version and its new Dashboard button and Sign out didn't work until a reboot.
- **CPU readings** (Car Thing, Dashboard, TRMNL) are the use since the last reading instead of a fresh 0.12 s sample that mostly read 0–3%.
- **Car Thing editor:** button and dial labels sat on the device drawing and overran at desktop widths; they now sit around it.
- **Upgrades from early setups:** apps whose env file lacked `STONEPI_DATA_DIR` kept files under `/opt/stonepi/apps/<app>/data` (FileServe hosted pages, NewsCast favicons); once the installer added the key they read `/var/lib/stonepi/<app>` and existing FileServe pages returned Not Found. The installer now copies those files across once (never overwriting, no databases/secrets/keys; the `/opt` copy is kept).
- **Library reader:** PDFs in a ZIM opened as "This page has been blocked by Chrome" (Kiwix's CSP `sandbox` stops Chrome's PDF viewer); nginx now drops that header for PDFs only.
- **Sign-in to a Library link:** Auth's redirect back to `/library/...` was rewritten to `/auth/library/...` (a 404) because `/library` was missing from the platform path list in `stonepi_auth.prefix`; a test now checks every catalog path.
- **Dashboard Library tile:** shows its standard description; the live line appears only while a download runs (it showed the installed title, e.g. "Knots").
- **Fresh installs — Health** returned an internal error: the installer locked Notify's data folder (`chmod 700`) and Dashboard reads Notify's status from it. `/var/lib/stonepi/notify` is now group-readable by `stonepi-dash`, and an unreadable status file shows as not configured.
- **Fresh installs — Settings → Network → exposure** couldn't be saved (`/var/lib/stonepi/exposure` was created as root). It's owned by `stonepi-dash`, and a failed save shows a message.
- **Fresh installs — Vault:** only Dashboard could write it, so Notify's ntfy token was silently not saved and NewsCast, EventTrakr and PriceWatch kept keys in their own databases. The Vault is group-writable for every service user (`2770`, files `660`); saves re-read the store under a file lock and swap it in atomically, and a store that can't be read is never overwritten. Notify reports a failed token save.
- **Fresh installs — data ownership:** migrations run as root after services start, and SQLite could leave root-owned `-wal`/`-shm` files. The installer restores every owner afterwards (`fix_data_ownership`).
- **0.1.8 release tarball** had CRLF scripts (built on Windows), so the installer stopped at line 2.
- **Restore** reset the Vault to `750`/`640`, undoing the fix above; it now matches the installer.
- **Restore** never restored Library content (wrong arguments to the copier).
- **SportGuide on phones:** the football league filters were squeezed to an invisible strip and long lists could clip. League filters are one swipeable row on phones and wrap from tablet width up.
- **SportGuide:** football leagues come from the competition, not club names (an FA Cup tie with Man United was filed under Premier League; "International Friendlies" under Serie A).
- **PriceWatch:** the low-rated warning repeated when a shop's price went up and back down; "Current lowest" went blank when no offer passed the stock/condition filters.
- **Library — custom folder** storage always failed on a Pi ("Can't write there"): the app checked the folder before the root helper had created it. The helper now prepares it first (`prepare-folder`), opens `/media/<user>` desktop mounts to the Library and its reader (ACL, traverse only; installs `acl`), and a failed reader switch is reported instead of ignored.
- **Library — Kiwix install** waits for the apt lock (busy on a freshly flashed Pi) and runs outside the Library's memory limit, so it can't be killed half-way.
- **Restore** rewrites the Library drive's exFAT `uid`/`gid` for this Pi's `stonepi-library`, and folders it creates for restored content belong to the Library.
- **Installer:** restarts the Kiwix reader if it's running so a changed unit applies, and the ready check covers it (group membership, can read Library data, running when enabled).
- `backup.conf` written by the local-schedule save pinned `APPS` without newer apps and dropped other keys; it keeps `INCLUDE_LIBRARY_CONTENT`, and backups add `stonepi-library` to an older pinned list.
- **Auth:** after signing in you land on the Dashboard page you asked for (e.g. Notifications) instead of a 404 at `/auth/notifications`.

### Security
- **Recover login lockout** can no longer be dodged by a local process forging X-Real-IP: the installer writes a root-only token (`/etc/stonepi/recover-proxy.token`) into a root-only nginx snippet that nginx sends on `/recover/`, and Recover trusts proxy headers only alongside it. The Recover password set from Settings → Vault must be at least 12 characters.
- **Per-app permissions audit (2026-10-03):** every user app checked for member actions with household-wide or public impact.
  - **NewsCast:** `require_admin` only checked sign-in, so members could change other members' sources, import catalog packages into the admin's paper and overwrite household settings; admin routes, the sources API and household settings are now properly scoped, and a demoted admin loses NewsCast admin.
  - **FileServe:** new "Publish pages" (on) and "Publish without a password" (off) permissions; protected and switched-off pages only skip the password for their owner or an admin; Studio Publish also needs Studio's permission.
  - **Library:** custom storage folders, drives, backup inclusion, setup and the speed limit are admin-only (the custom folder was only hidden in the page).
  - **PriceWatch / PriceScout:** "Manage watches" and "Strike alerts" enforced; household retention and Bright Data lookup controls admin-only; PriceScout refresh needs "Manage sources" and uses one household postcode.
  - **Pinboard:** "Post notices" and "Assign reminders to others" permissions (on by default).
  - **Background jobs** (NewsCast, PriceWatch, SportGuide, EventTrakr) honour revokes within 5 minutes through a shared Auth roster helper (`stonepi_auth.roster`) instead of waiting for the 14-day sign-in cookie; if Auth is unreachable they carry on as before.
  - **Permissions missing from an older sign-in cookie** use the catalog default, so members keep default-on permissions without signing in again.
- **Restore** no longer runs `chown -R` as root on a folder named in the Library-written manifest; the copier checks `content_dir` and sets ownership file by file.
- **Library root helpers** accept only the backup drive's `StonePi-Library/` folder, and hand an existing folder under `/mnt`, `/media` or `/srv` to the Library only if it's empty, already the Library's, or holds nothing but ZIMs (restore falls back to the microSD otherwise). The backup copier accepts only plain `*.zim` names from allowed folders and never follows links.
- **Library:** a Kiwix metalink file name that isn't a plain `*.zim` name is refused; sign-in return links with tabs, newlines or backslashes no longer leave StonePi.
- **PriceWatch:** members could edit, pause or delete each other's watches (now owner-scoped).
- **Pre-release review (2026-10-03):**
  - **FileServe:** hosted pages, ZIP sites and their assets load in a CSP sandbox (no same-origin) with nosniff, so an uploaded page can't act as whoever opens it; the URL fetch proxy blocks loopback/link-local and re-checks every redirect.
  - **Recover:** CSRF tokens on every form, sign-in rate-limited per IP and overall, restore paths resolved against the backup roots. The password lives only in root-only `/etc/stonepi/recover.passwd` (Vault copies move there and are deleted; Dashboard sets it through the backup helper on stdin, never argv or the Vault). Port 8099 only accepts LAN and tailnet addresses.
  - **NewsCast:** remote SVG favicons are no longer cached (old ones purged) and `/favicons` is served with a locked-down CSP; feed, article and favicon fetches refuse loopback/link-local (and private addresses unless `NEWSCAST_ALLOW_PRIVATE_FEEDS=1`) on every redirect hop.
  - **EventTrakr:** the same for source favicons: no SVG/HTML cached, and `/events/favicon-cache/` is served with a sandbox CSP.
  - **Dashboard sudo:** wildcard `systemctl`/`journalctl` sudo rules replaced by `stonepi-service-helper`, which takes one validated `stonepi-*` unit (logs with `--no-pager`). Automations' backup start and app self-restarts after an update use it too; other apps exit and let systemd restart them.
  - **Venvs are root-owned** (service users could previously plant code that root ran on the next install or update).
  - **First admin password is random** (16 characters, saved root-only to `/etc/stonepi/initial-admin.txt`, printed at the end of install); no more `admin`/`admin`. Login banners no longer quote it.
  - **Fail closed without a session secret:** Library (and the Kiwix reader's `/_auth`), PriceWatch and the Car Thing editor outside dev; Pinboard, Notify, PriceScout, SportGuide and Studio return 503 on a platform install (`/etc/stonepi`) with no secret instead of opening up (solo runs unchanged).
  - **PriceWatch:** the shared Bright Data limit, reset day and key are admin-only.
  - **Car Thing:** udev rule matches only the Car Thing (`18d1:4e40`), not every Google/Android USB device.
  - **Firewall:** Cockpit really is blocked over Tailscale (rule order matched the comments' intent); re-applying the firewall replaces only StonePi's table instead of `flush ruleset`, so Tailscale's rules survive.
  - **Install:** the bootstrap requires the release checksum (`--insecure-skip-checksum` to override) and resumes an interrupted install instead of reporting "already installed"; Tailscale comes from its signed apt repository and a failure no longer aborts the install; `adb` and the Car Thing files are optional; Notify's unit allows sudo so the Car Thing switch works; the ready check covers every enabled app's port.
  - **Upgrades:** `ADMIN_PASSWORD` moves from the shared `stonepi.env` (read by every app) to `auth.env`, which is now root-only 600; the firewall restarts tailscaled once if an earlier `flush ruleset` had wiped its rules (skipped, with a hint, when SSH comes in over Tailscale).
  - **Defaults:** NewsCast, EventTrakr and FileServe bind `127.0.0.1` unless told otherwise; Library reports a missing root helper instead of simulating success on a Pi.
  - **Release build:** `.gitattributes` keeps shipped files LF; `build_release_zips.py` has an explicit deny list (DBs, keys, secrets, `.env*`, caches), converts text to LF, and refuses to build while files `deploy/install.sh` needs are untracked (`--allow-dirty` to override; `--dry-run` to list). Requirements pinned exactly (EventTrakr drops `pytest`).

### App versions in this cut

Platform: **0.1.9**

| System (ships with platform) | Version |
|-----|---------|
| dashboard | 0.1.9 |
| auth | 0.1.9 |
| notify | 0.1.9 |
| recover | 0.1.9 |
| carthing | 0.1.9 |

| Apps | Version |
|-----|---------|
| newscast | 0.0.7 |
| fileserve | 0.0.7 |
| eventtrakr | 0.0.7 |
| pinboard | 0.0.7 |
| studio | 0.0.7 |
| pricescout | 0.0.7 |
| sportguide | 0.0.7 |
| pricewatch | 0.0.7 |
| library | 0.0.7 |

| Packages | Version | Why |
|-----|---------|-----|
| stonepi_auth | 0.1.1 | Library and new app permissions in the catalog, permission defaults, shared Auth roster (`roster`), platform lock (`platform_lock`) |
| stonepi_contracts | 0.1.1 | `library.*` and `pricewatch.low_rated_offer` events |
| stonepi_vault | 0.1.1 | group-writable store, locked atomic saves, shared Bright Data quota (`quota`) |
| stonepi_display | 0.4.0 | Car Thing panel feeds and config (pages, widget sizes; `carthing` module), CPU reading between calls |
| stonepi_automations | 0.1.1 | backup start through `stonepi-service-helper` |
| stonepi_update | 0.1.1 | restarts through `stonepi-service-helper`; a failed restart of another app no longer exits the caller |

## 0.1.8 — 2026-09-29

### Versions (single source)
- App lists come from `APP_CATALOG`: Dashboard Settings → Updates (every app, grouped System / Apps), service icons, Health journal units, and `scripts/build_release_zips.py` (adds the missing `notify` zip).
- Catalog `ships_with: platform` on the system apps (Dashboard, Auth, Notify, Recover): they take the platform version, have no zip of their own, ship inside `stonepi-platform-*.zip`, and Settings → Updates shows them as "Updates with the StonePi platform". `stonepi_auth.UPDATABLE_APP_IDS` = the user apps.
- `build_release_zips.py --versions-table` prints the System / Apps table for each cut from `VERSION` + `__version__`.
- `stonepi_auth.brand.asset_rev()` / `fonts_rev()`: automatic `?v=` cache-busting in every app (Recover has a local copy); no hand-typed tokens left in templates.
- FileServe / NewsCast hide their in-app updater under StonePi (EventTrakr already did).
- Guard tests (`packages/stonepi_auth/tests/test_platform_consistency.py`): no literal `?v=` tokens, zip ids and Recover's unit list match the catalog, `stonepi_contracts.APP_LABELS` matches catalog names.

### Displays / TRMNL
- **Multiple TRMNL Displays** in Notify, each with its own Private Plugin webhook (Vault `DISPLAY_WEBHOOK_URL` / `DISPLAY_WEBHOOK_URL_<ID>`), interval and device.
- **Drag-and-drop screen builder** with a real TRMNL Framework render (sample or live data).
- **Universal Liquid template** pasted once per plugin; layout is part of each push. Payloads carry only the variables a Display's widgets read (≤ 2 KB).
- `stonepi_display` 0.3: `grid`, `templates`, `render`, `push`, `variables` modules replace `layout` / `service` (fixed Household / Status wall designs and the block catalogue are gone). `stonepi_contracts`: `WIDGET_SIZES` (`small` 1×1, `medium` 2×1, `large` 2×2, `wide` full width), widget `code`, `widget_by_code`; `BLOCK_TO_WIDGET` removed.
- Fixed Notify pushes sending variable names the Liquid never read.

### Platform
- **Easy install:** README and `deploy/INSTALL.md` point to [stonepi-install.vercel.app](https://stonepi-install.vercel.app) — flash Raspberry Pi OS, SSH in, `curl -fsSL https://stonepi-install.vercel.app/install.sh | sudo bash`. Releases attach `stonepi-source-<version>.tar.gz` for that bootstrap. `scripts/*-bootstrap-on-pi.sh` overlay helpers are now local-only (gitignored, excluded from the platform zip).
- **Renamed platform services:** Notifications → **Notify** (`apps/notify`, `stonepi-notify`, `/notify/`, `/var/lib/stonepi/notify`, `/etc/stonepi/notify.env`, `STONEPI_NOTIFY_URL`) and Recovery → **Recover** (`apps/recover`, `stonepi-recover`, `/recover/`, `/etc/stonepi/recover.passwd`, Vault `STONEPI_RECOVER_PASSWORD`). Ports unchanged (8012 / 8099). `deploy/stonepi-migrate-renames.sh` (run by `install.sh`, `stonepi-restore` and the full push overlays) retires the old units and carries venv, data, env, passwd, Vault key and failover snippet forward; nginx 308-redirects `/notifications/` and `/recovery/`; old session cookies, grants and `STONEPI_NOTIFICATIONS_*` env names are still honoured.
- **Remote latency (Phases 1–4):** failover hysteresis (3 misses / 2 passes, curl max-time 8); Dashboard overview collector + loopback health probes; background jobs for restore/drill/update; self-hosted fonts at `/assets/fonts/`; nginx static aliases (HTTP + optional TLS), higher keepalive, `absolute_redirect off`; uvicorn `timeout_keep_alive=15`; Tailscale helper socket timeout + Serve-only when proxying loopback:80; CLI `/sports/` + `/watch/`
- **Latency follow-ups:** Home restores saved launcher order; password change forwards Auth session cookie (`fac` clears); Overview overlays disabled apps into Watch; Studio/PriceScout form POSTs sync; collector skips immediate re-probe after prime
- **Pi resource contention (Phase 3):** scraper units Nice/CPU/IO weight + MemoryMax=400M; auth/dashboard MemoryHigh + OOMScoreAdjust; shared `stonepi_browser` Chromium fcntl lock; EventTrakr deep search backgrounded; SportGuide daily refresh scheduler-only; PriceScout boot refresh delayed 10 min; PriceScout/SportGuide SQLite WAL
- **Request-path performance (Phase 2):** vault + hostname 30s TTL caches; Dashboard drops `/api/me` on render (`fac` claim), shared Auth httpx client, Backup/ACL caches; login `next` prefix for NewsCast/EventTrakr/FileServe; EventTrakr/FileServe skip static/favicon work
- **Appliance hostname** is admin-controlled under Dashboard → Settings → Network (`stonepi-hostname` helper + `/var/lib/stonepi/hostname` hot-read via `stonepi_auth.platform_hostname()`). NewsCast/FileServe no longer own a Hostname settings field.
- **App catalog:** Recover is a system service (with Dashboard, Auth, Notify); Dashboard Services groups System / Apps
- **Local backup:** scheduled single copy at `/var/backups/stonepi/current`; dual local/USB stamps; Recover HTML login + restore picker
- Local backup rotate keeps `current.prev` until new `current` is in place; timer uses `Unit=` (not `Requires=` oneshot)
- Vault `STONEPI_RECOVER_PASSWORD` preferred for Recover login; Vault save syncs `recover.passwd`
- **README** opens with the StonePi pitch and tagline ("The bedrock of your digital home."); the use-case table follows the pitch order and lists Notify; Recover (Recovery Console) is in the Portal table.
- `stonepi_watch.summarize()` / `summary_text()`: one Watch headline for Dashboard Health, the status API and TRMNL displays. Several services down read "Multiple services are not running (NewsCast, Studio)" on plain-text surfaces.

### Updates
- **App updates from Settings → Updates work on the Pi.** Dashboard downloads and checks the release; the new root helper `stonepi-update-helper` (installed by `install.sh`, one sudoers line) installs it into the app's own venv, restarts it, waits for its health check and rolls back automatically if it fails. Releases attach `SHA256SUMS` (written by `build_release_zips.py`) and downloads must match it. Platform updates still use the installer (`--reinstall`) until in-place platform updates land.

### Apps
- **Studio projects are per person:** only the owner can open, chat on, rename, delete or publish a project; admins see everything under **All projects** with a filter by person. Pre-existing projects move to the first admin who opens Studio.

### Platform / nginx
- **Tailscale cellular path:** gzip for proxied text, conservative cache for versioned static (`7d` with `?v=`, `1h` without), upstream keepalive, WebSocket-safe Connection map
- Trust Tailscale Serve only from loopback (`real_ip` + forwarded proto map) so rate limits see `100.x` and HTTPS redirects stay correct
- **stonepi-tailscale:** read-only `status`, honest `ServeHTTP` from `serve status --json`, Serve setup only on `up` with timeout + `ServeEnableURL`

### App versions in this cut

Platform: **0.1.8**

| System (ships with platform) | Version |
|-----|---------|
| dashboard | 0.1.8 |
| auth | 0.1.8 |
| notify | 0.1.8 |
| recover | 0.1.8 |

| Apps | Version |
|-----|---------|
| newscast | 0.0.6 |
| fileserve | 0.0.6 |
| eventtrakr | 0.0.6 |
| pinboard | 0.0.6 |
| studio | 0.0.6 |
| pricescout | 0.0.6 |
| sportguide | 0.0.6 |
| pricewatch | 0.0.6 |

## 0.1.7 — 2026-09-24

### UX
- **Settings hub (phone/tablet):** Dashboard and NewsCast use a grouped hub → section drill-in (with L2 panel icons + short subtexts); laptop+ keeps chips/stacked cards
- NewsCast: briefing/sources/filter sheets, catalog seed vs added feeds, mobile sheet scroll fixes
- PriceScout: safer offer ingest when Tjek pages repeat or omit ids

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.7 |
| auth | 0.1.6 |
| newscast | 0.0.4 |
| eventtrakr | 0.0.3 |
| fileserve | 0.0.0.9 |
| pinboard | 0.0.3 |
| pricescout | 0.0.3 |
| sportguide | 0.0.2 |
| studio | 0.0.5 |

## 0.1.6 — 2026-09-22

### UX
- **Global responsive contract**: shared viewport tiers **720 / 1024 / 1440** (side rail at ≥1024 for portal and all apps — removes the 800–899 cliff)
- Auth login/status: phone safe-area, `--tap` targets, denser card padding
- Portal + all apps: main column fills width beside the rail on laptop/desktop; Settings panels 2–3 columns; list/card pages (Saved, Search, Feeds, Pages, events, offers, projects, listings) use multi-column grids
- NewsCast OPDS/URL wrap; Studio split panes fit short laptop heights; Pinboard notice/reminder grids on tablet+
- Docs: [deploy/responsive-audit.md](deploy/responsive-audit.md), ux-stonepi + new-stonepi-app chrome; optional [scripts/responsive](scripts/responsive/) overflow smoke

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.6 |
| auth | 0.1.6 |
| newscast | 0.0.3 |
| eventtrakr | 0.0.3 |
| fileserve | 0.0.0.9 |
| pinboard | 0.0.3 |
| pricescout | 0.0.2 |
| sportguide | 0.0.2 |
| studio | 0.0.5 |

## 0.1.5 — 2026-09-21

### UX
- Portal: browser layout prefs, denser Home / Health / Services cards, Users expand control aligned with Health/Services
- Auth hub with quieter chrome and install guidance
- Mobile: keep bottom nav visible when Settings and other tall pages overscroll
- NewsCast: Device / Sources shells, PaywallSkip, sync activity, scrape↔RSS for dual-mode sources, Today / Yesterday / All by publish date, catalog cleanup, mobile nav and story kickers

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.5 |
| auth | 0.1.5 |
| newscast | 0.0.2 |

## 0.1.4 — 2026-09-20

### UX
- Admin nav: **Health** (monitor) vs **Services** (manage enable/disable); Overview route kept at `/overview`
- Health cards: Version + URL labels; URLs follow the hostname you opened; long URLs wrap in-card
- Services cards: Route + Port only (no Healthy / Status lines); enable toggles unchanged
- Shared Settings chrome: General sliders icon; About cards flush; Studio Settings pills match NewsCast
- Studio: Settings → About; admins edit LLM prompt markdown under Settings → Prompts
- Docs: [docs/screenshots/](docs/screenshots/) gallery linked from the root README

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.4 |
| auth | 0.1.4 |
| studio | 0.0.4 |
| pinboard | 0.0.2 |

## 0.1.3 — 2026-09-20

### UX
- Overview absorbs Watch: conditional alert banner, disk + backup stats, one services grid; Settings Watch tab redirects to Overview
- Services: per-card Enabled toggle (autosave); Dashboard and Auth stay always on
- Users: Accounts list first (rows collapsed); Add account expands the create form; new users default to Dashboard only
- Studio: Create landing (`/`) vs Projects (`/projects`); three-up bottom nav
- Windows split-port: app **Home** nav uses dashboard `PUBLIC_ORIGIN` (no longer loops to the app’s own port)

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.3 |
| auth | 0.1.3 |
| studio | 0.0.3 |

## 0.1.2 — 2026-09-19

### UX
- Overview backup card honest about failed/skipped stamps
- Path-install Sign out stays on `/auth` (hostname you opened), not loopback `AUTH_URL`
- Settings → General: Appearance + own password for everyone; admins keep **Users** nav for household accounts
- Theme/palette cookies + auth status page theming
- Launcher tiles stay on the hostname you opened (relative paths; no `.home` → `.local` flip)
- FileServe `/browse` scoped: own pages for members; admin/root only for anonymous visitors
- App Settings General no longer offers local password change when SSO is on (use portal Settings → General)

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.1.2 |
| auth | 0.1.2 |

## 0.1.1 — 2026-09-19

### Display / TRMNL
- Design presets: **Status wall** (ops board), **Household focus**, **Custom**
- Landscape preview scaled for laptop Settings; Copy markup works on LAN HTTP
- Service metadata on Status wall from app `/api/display` (Auth sessions, EventTrakr favourites, Studio projects, FileServe pages, Pinboard notices, …)
- nginx edge deny list includes `/auth/api/display` and `/studio/api/display`

### Home
- Launcher reorder is drag-to-place (not arrow buttons)

### Install / ops
- Avahi: install `avahi-utils`, force IPv4 address publish for `stonepi.local`
- Cockpit URL defaults / upgrades to `https://…:9090`
- Installer refuses to proceed without `stonepi-public-deny-display.conf`
- Release assets named `stonepi-{app}-{version}.zip` (+ `STONEPI.txt`); updater prefers that prefix
- Hostname-agnostic portal: works via `stonepi.local`, Pi IP, or router LAN DNS (e.g. `stonepi.home`)

### Fixes
- EventTrakr favourite toast no longer reports “Removed” on API errors
- Login / “Back to apps” / app **Home** stay on the hostname you opened (e.g. router `stonepi.home` or LAN IP), not forced to `stonepi.local`
- `safe_next` allows `.home` / `.lan` / private IPs; `portal_home_url()` builds absolute portal links so PrefixRewriter does not turn `/` into `/auth/`

### App versions in this cut
| App | Version |
|-----|---------|
| dashboard | 0.0.2 |
| auth | 0.0.2 |
| eventtrakr | 0.0.2 |
| studio | 0.0.2 |
| (unchanged) newscast, fileserve, pinboard | see each app |

### Packages
- `stonepi_display` 0.1.1 — Status wall Liquid + fixed layouts
- `stonepi_update` — matches `stonepi-{app}-*.zip` (legacy `{app}-*.zip` still accepted)

## 0.1.0 — prior

- Initial packaged platform baseline
