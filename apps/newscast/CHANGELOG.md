# Changelog

## 0.0.7 — 2026-10-04

### Added
- `/api/display?items=N` (loopback) adds `card` + `items` for the Car Thing panel: the latest headlines with their summaries. The plain call is unchanged.

### Security
- Household-wide settings (keyword filters, translate target, Briefing size, importance, category mix, OPDS, X3/EPUB layout) are admin-only and hidden for members; saving one settings tab no longer resets values on other tabs.
- Sources API: members list, edit and remove only their own sources; pointing a source at a new URL needs "Add custom sources"; catalog package import/export, categories and Ollama model lookup are admin-only.
- Admin-only routes now really require the household admin (`require_admin` only checked sign-in); a NewsCast admin role follows StonePi, so someone demoted there becomes a member here.
- Remote SVG favicons are no longer cached (previously cached ones are purged) and `/favicons` is served with a locked-down CSP and nosniff.
- Feed, article and favicon fetches refuse loopback and link-local addresses, and private ones unless `NEWSCAST_ALLOW_PRIVATE_FEEDS=1`, on every redirect hop.
- Listens on `127.0.0.1` by default.

### Changed
- Members' Refresh only updates their own sources and they can stop only a refresh they started; status shows only their sources unless they can view status. Scheduled refreshes skip custom sources of people StonePi removed from NewsCast or who lost "Add custom sources".

### Fixed
- Settings warns before you leave a tab with unsaved edits (Save, Discard or Stay), marks those tabs with a dot and asks before you close the page. Fixes a script error (`hostInput`) that stopped Settings tab switching from working.
- Unsubscribing from a catalog source no longer fails when two rows match.

## 0.0.6 — 2026-09-29

_Moves from 0.0.4 to the shared user-app version, 0.0.6 (numbers in between were skipped)._

- Under StonePi the in-app updater is fully hidden (Updates panels, Roll back last app, the Status "Update available" line, update check); updates are managed from StonePi → Settings → Updates. Running on its own is unchanged.
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- systemd unit: Nice/CPU/IO weight + MemoryMax=400M (Pi resource contention).
- Login `next` includes the app path prefix under SSO (SportGuide pattern).
- Performance: activity poll every 4s only while ingest/push is active (60s when idle; pauses when the tab is hidden); SSO session resolved once per request; settings writes skip unchanged values; scheduler tick 5 minutes.
- Under SSO, Settings shows the Auth factory-password banner (links to StonePi → Settings → General) from the shared session flag.
- Publish/push alerts emit through Notify; Settings → Notifications points at Destinations / Event prefs (no local ntfy form).
- Appliance hostname is no longer editable in NewsCast Settings; it is set under **Dashboard → Settings → Network**. Share/status/TLS SANs and `{hostname}` paper titles read the platform hostname.
- **Frame matches Dashboard,** so switching apps no longer jumps:
  - The top bar is 61px, with 40px buttons and 18px icons.
  - On laptop, the side menu is 220px wide, with 0.92rem labels and 20px icons.
  - On phones, the bottom-menu labels shrink slightly below 375px, so all seven fit.
  - The theme switch and your name stay Dashboard-only.
- **Sources → Text cleanup** (per source, opt-in; default **Standard**):
  - **Strict** uses the extractor's precision mode and removes whole lines of page furniture: "Advertisement", "Loading", "Latest in Videos", and empty "Updated ,first published" stamps.
  - If Strict finds no article text on a page, it uses Standard for that page. The source's status line says so ("strict: 1 page used standard").
  - Strict sources show **Strict cleanup** on their card. It applies to stories fetched from then on.
- Saved articles always use Strict cleanup, with the same fallback.
- One-off tidy-up on first start after this update: stored stories are cleared of those page-furniture lines, and a repeated headline at the top of the text is removed.
- **Reader → Send: one job per button.**
  - **Send to reader** on today's paper or a Library file now queues it *and* tries to upload straight away. The message says whether it was sent, is already on the reader, couldn't be sent (with the reason), or is waiting for the reader to wake.
  - "Also send to reader" when adding a file does the same.
  - Once the paper is on the reader, its button becomes a quieter **Send again**.
  - The Reader and Today's paper cards line up on laptop/desktop: headings at the top, text, then buttons along the bottom edge.
  - Library files show their state: **Send to reader**, **Waiting to send**, or "On reader · sent 29 Sep" with **Send again**. A line under Your files explains what sending does.
  - **Push now** is for sending everything waiting in the queue.
  - **Automatic sending:** queued papers and files now go out on their own within about a minute of the reader waking up — no need for Check reader or Push now. A dedicated 60-second check (it used to ride on the 5-minute feed refresh, so it usually missed the reader's short awake window) backs off while the reader stays asleep, never runs two pushes at once, and needs **Push when the reader is on Wi-Fi** turned on.
  - **Queue status:** each item shows its state — "Waiting for reader · last tried 14:05 (asleep) · next try 14:06", "Sending…", "Failed: … · will retry 14:07", and a **Recently** list with "Sent 14:07". The Reader card shows when it was last checked, and rows update in place while a push runs.
  - Failed uploads are retried (up to 5 attempts) instead of dropping out of the queue; a reader falling asleep mid-push doesn't use up an attempt. **Check reader** sends the queue when it finds the reader online.
- Reader → Status → **Your catalog URL** is condensed:
  - The address and QR code button share one line, followed by a one-line how-to.
  - Downloads are one compact row: epub, txt, yesterday's epub, json.
  - Longer setup notes sit under **More setup help**. Nothing was removed.
- Sources: the gap between Feeds/Catalog and the category chips now matches Briefing. The **+ Add feed** button now reads **+ Add**.
- **Search: pick an outlet.**
  - An Outlet dropdown lists the sources you have stories from, with counts. Choosing one searches straight away.
  - The Keyword box is optional; outlet and keyword narrow together.
  - A results line says what was searched ("8 stories from Hackaday matching …"), and Clear resets the search.
  - Auto-search falls back to a plain submit on older iOS Safari.
- Reader → Status is more compact:
  - Stories stored, Delivery and Health sit side by side on wide screens, and wrap to two columns, then one.
  - Their details are compact label/value rows.
  - The separate Last push card is gone, since Delivery already shows the last push.
- Briefing and Sources, laptop/desktop: the category filter button sits right after the last chip instead of at the far edge of the row. Phones are unchanged.
- **Security: per-item actions check ownership.**
  - Covered actions:
    - Stories: favourite, save as long-read, delete saved.
    - Sources: enable/disable, save settings, mute, unmute, refresh, remove.
    - Library files: send, delete.
    - API: single-source refresh and debug.
  - Each one now acts only on the signed-in account's own item. Another member's item is treated like a missing one (404), so ids aren't confirmed.
  - `/api/stories` now returns only your own briefing.
  - A guard test fails if a new id-based route skips the check.
- **Fixed:** "Save as long-read" and the Saved tab's "Save article" now use your own account.
  - New saves used to land under the first account.
  - An existing story was matched across all accounts, so it could update someone else's copy, or error when two accounts had the same story.
- **Reader (was Device):** the bottom-nav section is now **Reader**, with three tabs, each with one job:
  - **Status** is read-only: stories stored, delivery, health, and catalog URL / QR / downloads. Its queue line links to Send. The Push panel and Generate moved to Send.
  - **Library** (new; `/library` opens it): Add a file (with "Also send to reader") and Your files. Each file has **Send to reader** or shows "Waiting to send", plus Delete.
  - **Send**:
    - **Reader**: online or asleep, Check reader, Push now.
    - **Today's paper**: Generate, and Send to reader, which queues the paper alone.
    - **Queue**: always shown, with an empty state.
  - "Queue library" (queue every file) and "Queue for later" (paper plus every file) are gone; the per-file and per-paper Send buttons replace them.
- Device → Status: removed the **Open on this network** panel (with its iPhone home-screen steps). **Your catalog URL** has a **Show QR code** button that opens a QR of the catalog address.
- **Device → Status → Stories stored** now shows where the total sits: on Briefing (incl. starred), saved long-reads, below the importance filter, filtered out by keywords, and older than 7 days awaiting cleanup. The rows add up to the total; empty rows are hidden.
- **Briefing day picker:**
  - A calendar button beside Today / Yesterday / All opens a list of every day in the 7-day story window. Empty days are marked "No stories".
  - A picked day shows as a fourth highlighted segment (e.g. "25 Sep") and survives starring or saving a story.
  - Fixed: older days now get their own stories, not just whatever happened to be among the newest ~80.
- Briefing's "Updated HH:MM" line is smaller and lighter.
- **Stop refresh:**
  - A Stop button appears next to Refresh only while a refresh you started from that button is running. Scheduled and single-source refreshes can't be stopped.
  - It halts at the next safe point (between sources, articles, summaries or translations), after any download or summary already in progress.
  - Stories already fully processed are kept. If none were, nothing is saved and sources aren't marked as fetched.
- Top bar **Refresh** button is icon-only at every screen size. The label moves to its tooltip and screen-reader name ("Refresh sources", translated).
- Sources: an open source card's settings form no longer spills past the card's right edge.
  - It happened on laptops, where Sources shows 2–3 cards side by side (visible around 1024–1440px wide).
  - The form now uses two columns only when the card itself is wide enough, and fields and long URLs shrink or wrap inside it.
  - The per-source refresh button is a compact 32px icon aligned with the expand chevron, so the open card's settings get about 24px more width.
- **X3 paper layout (CrossPoint + CrossInk):**
  - Opens on the front cover, then the briefing and Contents. The outline page is out of the reading order but still drives the reader's chapter menu. The **Open on cover** setting is gone.
  - Chapter menu stops at source level and carries the numbers in its titles (`1. World News`, `1.1 BBC World`).
  - Section pages list their sources and story count instead of "Turn the page for stories".
  - Each story shows its place in the source (`3 of 8`), with a rule between stories.
  - Fixed the blank source title page on CrossInk. CrossInk ranks descendant CSS rules below plain classes, so the per-story page break won and pushed the first story off the source heading.
  - The first page (front image, briefing and Contents) is titled **Front page** in the reader's footer and chapter menu instead of "Unnamed". The firmware only takes page titles from the chapter menu, so it gets one unnumbered entry at the top; the numbered categories and sources are unchanged.
- **Shared Xteink stylesheet:** only `tag`, `.class` and `tag.class` selectors, one element per class. Page breaks come from new chapter files only, never CSS. Properties neither firmware applies (font size, colour, borders, line height) are dropped, and the global `img { display: none }` rule is gone.
- **Settings → Reader → Reader firmware** (Auto-detect / CrossPoint / CrossInk): the choice only adds optional CrossInk extras (inverted section-title band, small-caps source names). Auto-detect checks for CrossInk's web assets on each push and shows "Detected: …"; the manual choice wins.
- **Settings → Publication → Contents page:** titles and summaries, titles only, titles only on busy days (more than N stories), or **categories and sources only** — just the numbered sections and their sources (`1. World News`, `1.1 BBC World`), no story titles or summaries.
- **Settings → Reader → Keep papers on reader** (off / 2 / 3 / 7 / 14 days): each push deletes older papers NewsCast sent to your folder. Send files and other books are never touched; today's and yesterday's papers always stay.
- **Re-sending a paper:** an unchanged paper is skipped. A changed one deletes NewsCast's older copy first (the firmware refuses to overwrite, and deleting also clears its saved layout). A same-name file NewsCast didn't send now gets a clear message instead of "check the upload folder exists".
- **Section icons:**
  - Built-in categories now use Material Symbols (Fill, weight 600): newspaper, memory, shield_lock, science, business_center, trophy, theater_comedy, menu_book. They are stored as 150px pure black-and-white PNGs in `app/assets/category_icons/` with the Apache 2.0 licence.
  - Nordic and Australia get a drawn Nordic cross flag and Southern Cross; they used to share a fallback glyph.
  - Custom categories use the newspaper.

## 0.0.4 — 2026-09-24

### Added
- **Settings hub (phone/tablet):** grouped hub → section drill-in; L2 publication panels show matching icons and short subtexts
- Feed URL helpers for dual-mode / catalog seed paths

### Changed
- Briefing, Sources, Feeds, and catalog panels: denser mobile chrome, filter/help sheets that scroll correctly, icon-only expand controls
- Catalog seed only creates default feeds; existing stubs are not treated as “added” until enabled
- Cover image and category helpers tightened for briefing packages
- Settings → Notifications: **Connection** links to StonePi Notifications (ntfy server/topic are household-wide); **Alerts** lets each person choose paper published / reached reader (default on)
- Reader activity banner: amber **“x items queued”** while files wait for the reader, green **“Sending…”** only when it is online; tap opens Device → Send (what’s waiting to reach the reader)

### Fixed
- Mobile sheets (category filter, help/info) no longer trapped under nav or unscrollable inside `main`
- Activity banner no longer clipped on long mobile pages (Briefing, Device, Sources)
- Banner strings (Sending, queued) now translated in the browser
- Settings no longer error after the ntfy move to StonePi Notifications
- Briefing status line shows only “Updated HH:MM”; refresh progress stays in the top activity bar and results in the toast (no duplicate messages)
- “Updated HH:MM” shows the household clock (Pi timezone, Europe/Copenhagen) instead of UTC, which read two hours behind in summer

## 0.0.3 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px** (aligned with portal); OPDS / catalog URLs wrap on narrow viewports
- Laptop/desktop: main column fills width beside the rail; Settings cards lay out in 2–3 columns; Briefing / Saved / Search / Feeds story grids 2–3 columns
- Device Status/Send: clearer tab icons; section headings (Delivery, Health, Catalog, Push, QR, files) include stroke icons; Status panels use 2–3 column layout on laptop

## 0.0.2 — 2026-09-21

### Added
- **Device** and **Sources** nav shells (Status/Send; Feeds/Catalog) with side-by-side tabs
- PaywallSkip (instance + per-feed) and configurable “read article” label
- Sync / ingest activity strip on the briefing chrome
- Briefing day chips: Today / Yesterday / **All** (publish date only)
- Catalog `rss_url` + per-source Scrape | RSS switch for dual-mode feeds (Hackaday, Krebs, iTnews)

### Changed
- Webpage scrape ranks dated article links and skips featured/nav noise; explicit Scrape no longer probes RSS first
- Dead catalog feeds removed (AP, Reuters, New Scientist, Copenhagen Post, Space.com, HBR, Quartz); repaired Politico / SciAm / Smithsonian / Vulture URLs
- Mobile bottom nav uses seven columns; story kickers keep save/star on one row with truncated source names

### Fixed
- Seed preserves user-chosen type/URL for dual-mode sources; known-dead RSS URLs still get rewritten

## 0.0.1 — 2026-09-17

### Added
- Admin permission `can_view_status` (off by default) gates the Status nav and `/status` for non-admins; admins always have access
- Per-account Reader device settings (host, upload folder, push-when-online, Kobo SSH): each household member can target their own Xteink/Kobo
- Optional **Copy admin reader setup** when creating a user (off by default), with a one-time non-blocking banner to confirm their own host
- Upload folders auto-namespace to `…/{username}` (e.g. `/News/pat`) so shared devices do not mix files
- Background “push when online” flushes each account to that account’s reader host only

### Fixed
- Transfer queue is scoped per account: Send/Status only show that user’s pending files; Push/Queue/Cancel no longer touch another household member’s queue

### Changed
- Status is hidden from household users until an admin enables it under Settings → Users
- Queue cancel control is icon-only (trash), with title/aria-label for accessibility
- Settings → Reader is each signed-in user’s personal device (admin included), not a single household reader
- Version scheme jumps from `0.0.0.9` to `0.0.1`; further patches are `0.0.1.x`

## 0.0.0.9 — 2026-09-16

### Added
- In-app LAN HTTPS: Settings → General → Use HTTPS on the LAN creates a household local CA and server certificate (`app/services/tls.py`, `cryptography`) and serves TLS on the existing app port via `python -m app.serve`
- Admin **Download root CA** (`/settings/tls/root-ca.pem`) plus certificate status (expiry, SANs) on General
- **Restart to enable HTTPS** on General after the CA is ready (HTTPS stays on HTTP until you restart, so the CA can be downloaded first)
- Backup/restore includes `data/tls/` so the same CA survives restores

### Fixed
- Enabling HTTPS no longer auto-restarts before you can download the root CA (avoids “site can’t be reached” when the browser is still on `http://`)

### Changed
- Share/OPDS https URLs include the app port (e.g. `https://newscast.local:8080`) instead of implying port 443
- `deploy/run.sh` and `run-local.bat` start through `app.serve` (HTTP or HTTPS, never both)
- Turning HTTPS **off** still schedules a restart back to plain HTTP; turning it **on** waits for Download root CA + Restart to enable HTTPS
- Hostname changes while HTTPS is on renew the certificate and prompt a restart when needed
- README / INSTALL document in-app HTTPS and CA trust steps; `deploy/Caddyfile` demoted to optional/advanced
- Settings action buttons gain small icons (Download root CA, Restart HTTPS, Catalog Import/Export, Select/Unselect all, Save approvals, Load from Ollama, Save user, Create account, Add category, Download/Restore backup, Roll back, Check for updates)
- Users card button label is **Save user** (username removed from the label)
- Transfer queue **display** order: today’s paper first, then older papers (newest date first), then Send files (push still runs oldest-first)

## 0.0.0.8 — 2026-09-15

### Added
- Opt-in LAN HTTPS under Settings → General (reverse proxy); Secure cookies and https share/OPDS URLs when enabled
- Always-on argon2id password hashing and login rate limiting
- Household multi-user accounts with per-user feeds, papers, library, OPDS, ntfy, and shared URL fetch cache
- Interface language under Settings → General (`app/services/i18n.py`, `app/locales/*.json`); nav, login, ingest pill, and JS busy/confirm strings via `t()`; Spanish scaffold for fallbacks
- Per-user ntfy topic/token/events (blank server uses household default); Settings → Notifications writes user settings
- Admin permission `can_use_ntfy` (off by default) gates the Notifications tab and ntfy sends for non-admins
- Per-user OPDS/X3 mounts at `/opds/u/{username}` and `/api/x3/u/{username}`; legacy `/opds` and `/api/x3` keep serving the admin catalog
- Catalog approvals (Settings → Catalog) so non-admins only Add household-approved sources; custom URL add gated by `can_add_custom_sources`
- One-time login QR / `/login/token/{token}` from Settings → Users

### Changed
- Backup/restore covers nested library paths, briefings, and feed cache used by multi-user installs
- UI `ui_lang` prefers the signed-in user’s setting over the instance General default
- Backup download/restore stays admin-only
- Non-admins only see their Settings tabs (no LLM, Schedule, Catalog packages, Users, Backup, Update)
- Status emphasizes each user’s `/opds/u/{username}` catalog URL
- Catalog approvals titled “Approved for user viewing”, with Select all / Unselect all
- Startup migrates `users.can_use_ntfy` before loading the admin user (fixes local DB upgrade crash)
- Settings → Users is a polished Users list: per-user cards with permission chips, editable custom feeds / ntfy / active, password reset, login QR, and remove account
- Status catalog/share URLs wrap on narrow phones so long `/opds/u/…` links no longer overflow the gutter
- README phone screenshots refreshed for 0.0.0.8; Web UI table and OPDS paths updated for multi-user / Settings tabs
- README and INSTALL brought in line with current capability: household accounts, ntfy, HTTPS/Caddy, Publication/Translation, per-user OPDS, and argon2id login notes
- Admin can remove a household user (and that user’s feeds, stories, papers, and Send files); admin accounts stay protected
- Show login QR / create-user forms submit as normal page posts so the QR card actually appears (fetch+reload was dropping it)
- Login QR card has a Hide button to dismiss it after scanning
- About shows the GitHub profile link instead of the author name
- Status / Send queue rows stack on phones so paper titles are not crushed by “Remove from transfer”
- Action buttons gain small icons (Save article, Search, Check reader, Push now, Queue for later, Remove, Add file, Save settings)
- Send “Your files” rows also stack on narrow screens
- Mobile buttons are more compact (shorter labels, denser action rows, slightly smaller tap height)

## 0.0.0.7 — 2026-09-15

### Added
- Settings Notifications tab for optional ntfy phone alerts when today’s paper is published and/or reaches the reader (off by default; configure server, topic, and token first)
- Settings Translation tab sets a global target language (default English) for Translate feeds; Google and LLM follow that language, and stories store `content_lang` for backfill when the target changes

### Changed
- Favicon startup reconciles icons already on disk and only network-fetches for enabled feeds (and Saved) that are truly missing, with a cooldown after failures
- Send tab splits waiting transfers from Your files, labels today’s paper by briefing path (not custom save name), and aligns Status push copy

## 0.0.0.6 — 2026-09-15

### Added
- Settings Translation tab sets the global Translate to English engine: Google Translate (Chrome fallback) or the configured LLM
- Each feed can choose As published, follow the global setting, force Google, or force LLM
- Category mix can allocate Briefing and daily-paper slots by percentage (blank categories share the leftover; 0 excludes)
- Optional per-category OPDS papers: tick topics to publish separate EPUBs listed under OPDS → Categories
- Settings Publication tab (before Schedule) for paper composition settings
- Category papers use their own title pattern (with `{category}`) instead of suffixing the full-paper name

### Changed
- Briefing reloads automatically when a full refresh finishes, so new and translated stories appear without a manual reload
- Settings Translation tab stays visible (it was incorrectly hidden by the client tab switcher)
- Success toasts appear at the top-right on desktop so they stay clear of settings tabs and Save settings
- Catalog cards keep category/type chips under the title beside Add/Remove, so the button no longer pushes them down
- Paper naming moves from Reader to Publication; Briefing size and importance move from Schedule to Publication
- Schedule keeps refresh timing and Publish at only
- OPDS acquisition links put day, category, and the paper filename in the path so readers save category papers under the displayed name (not `news.epub`)
- Category EPUB metadata (`dc:title`) uses the category title pattern; changing naming patterns needs a republish for already-written papers
- Settings Device tab is labelled General (URL `?tab=device` unchanged)

## 0.0.0.5 — 2026-09-14

### Added
- Stories get an importance score from 1–5 (LLM when configured, heuristics otherwise); Settings Schedule can keep only stories at or above a threshold (default 3+)
- Status Health card shows disk free space, database size, failing feeds with error details, and the last AI error
- Status Delivery card shows last published time, last successful push, queue age, and a trust line such as "Morning paper is on the reader"

### Changed
- Daily EPUB/TXT papers open with a masthead of story counts by category and denser cover digest blurbs
- Settings Schedule and Reader Sync show a short delivery snapshot (published / last push / queue)

## 0.0.0.4 — 2026-09-14

### Added
- Settings Reader can set the paper title/filename pattern and date style used by OPDS, EPUB metadata, and downloads (`{product}`, `{hostname}`, `{instance}`, `{label}`, `{date}`)
- Settings Reader can set a custom paper label for `{label}`; Device instance name is `{instance}`
- Settings Schedule can limit Global refresh to local active hours (for example every hour from 09:00 until 12:00)

### Changed
- Settings Reader splits connection, Sync, and Paper naming into separate cards
- Settings Reader Paper naming offers clickable `{token}` chips and a live filename preview while editing
- Settings Catalog holds package import and category export; the Catalog page is browse and add/remove only
- Saved marks long-reads as From Briefing or Added URL; Briefing notes that bookmarked stories live on the Saved tab

## 0.0.0.3 — 2026-09-14

### Added
- Settings Reader can target Xteink (CrossPoint HTTP push) or Kobo (KOReader OPDS plus SSH/SFTP push)
- Kobo Check reader probes the KOReader SSH port; Push now and Queue for later copy queued EPUBs and PDFs over SFTP into `/mnt/onboard/News`
- Status and README document KOReader OPDS setup and SSH server steps beside the existing CrossPoint path
- Daily briefing EPUB includes a generated newspaper-style cover image for library previews

### Changed
- Reader host, upload folder, and push hints follow the chosen device (CrossPoint File Transfer vs KOReader SSH)
- OPDS navigation uses **Daily Briefings** (today and yesterday, newest first) beside Library; only published papers appear
- Reader EPUB contents and TOC nest Category → Source → stories, with more space between entries for e-ink reading
- Feeds cards keep the source header visible and collapse schedule and filters behind Schedule & filters by default
- Catalog shows Remove on added sources (including custom ones), so feeds can be taken off without opening Feeds
- Feeds Schedule & filters uses a compact toggle with a fixed-size chevron; Catalog Remove no longer 404s after confirm
- Feeds Schedule & filters expands with native details (no JS), and static CSS/JS cache-bust when assets change
- Feeds, Catalog, and Briefing keep the active chip filter after enable, add, remove, and other reloads
- Feeds and Catalog tighten for narrow phones: stacked headers, single-column catalog, and package forms that stay inside the page margins

### Fixed
- Status OPDS and catalog links use the LAN IP when `PUBLIC_BASE_URL` is still localhost, so readers are not pointed at 127.0.0.1
- Daily briefing downloads use dated filenames, and new papers stamp the local paper date in the EPUB so the cover matches the calendar day
- Story summaries drop AI preambles such as “Here is a concise news briefing based on the provided text”

## 0.0.0.2 — 2026-09-13

### Added
- Reader newspaper publishes at a set local time (default 06:30) as a dated EPUB with category TOC, e-ink CSS, and a cover; Status can publish now
- Status and Send list queued CrossPoint files with briefing vs Send labels and a cancel control
- Show-password icons on the new and confirm password fields on Settings Device
- Colour palettes Default, Ocean, Forest, and Slate, each with the existing light / dark / Auto switch; picker is on Settings Device
- README shows phone-width screenshots: sign-in, then Briefing, Saved, Search, Send, Feeds, Catalog, Status, and Settings
- Briefing chips switch Today and Yesterday; OPDS and `/api/x3/news.epub` can serve yesterday as well
- Briefing bookmark saves a story as a Saved long-read beside the star
- Feeds can mute a source for 24 hours, add include/exclude words, and show a Healthy / Empty / Error badge
- Settings Filters tab holds global include/exclude words; each feed can add more
- Search tab looks through briefing stories, favourites, and Saved long-reads with SQL LIKE
- Status and Send can push the briefing and library to CrossPoint when it is on Wi-Fi, or queue files while the reader is asleep

### Changed
- OPDS and `/api/x3/news.epub` serve the frozen dated paper instead of rebuilding from live stories
- Refresh no longer queues a new briefing file; the publish job or Publish now does that, and the same reader path is not queued twice
- Settings Access tab is now Device; the hostname card is labelled Network
- Search uses the same panel head, form layout, and text-field styling as Saved and Send
- Send and Status no longer ping the reader on load; use Check reader to see if it is online or asleep
- Sign-in follows the saved palette and light / dark mode but no longer has switches to change them

### Fixed
- Installing a GitHub update on Windows no longer deletes the running `app` folder, which caused an internal server error after the backup
- Status and Settings treat a GitHub release that matches this copy as up to date, so they no longer offer to install the version already running

## 0.0.0.1 — 2026-09-13

### Added
- FastAPI + SQLite news aggregator with env-based config for Windows and Raspberry Pi OS
- Mobile-first UI: Briefing, Feeds, Catalog, Status
- Recommended RSS catalog with first-run seeding
- Custom source form for RSS or website URLs
- Website scraping when a source has no usable RSS (discover feed links, then scrape article pages)
- Preconfigured additional sources: Hackaday, Krebs on Security, iTnews
- Deduped story ingest and concise summaries (OpenAI when a key is set)
- Xteink X3 JSON, TXT, EPUB, and sync task endpoints
- Admin login (`admin` / `admin`) and Status-panel settings for credentials, OpenAI, and X3 tokens
- Tab icons next to Briefing, Feeds, Catalog, and Status labels
- This changelog and app version `0.0.0.1`
- OpenAI model picker on Status (gpt-4o-mini, gpt-4o, gpt-4.1-mini, gpt-4.1, o4-mini, or a custom id)
- Global refresh interval on Status, plus per-source Global vs Custom schedule on Feeds
- Background ingest ticks every minute and only fetches sources that are due; Refresh still fetches all enabled sources
- Light / Dark / Auto theme switch in the header, persisted locally
- UI polish: paper wash, story accent rule, status chip, focus rings, and tighter header/nav chrome
- Default local port changed to 8080 so Windows installs are not blocked by another app on 8000
- In-page sign-in (styled login page) instead of the browser HTTP Basic popup
- Category chips on Feeds and Briefing, matching Catalog
- Refresh button icon in the header, plus a sign-out control
- Status upload for EPUB or PDF files that skip summarising and queue a direct X3 sync task
- `run-local.bat` for Windows: stop any process on the NewsCast port, then start the server
- Catalog Add / Added controls use matching full-width buttons with plus and check icons
- Status settings headings (Admin access, Refresh schedule, API access) sit inside their cards
- Line-drawn satellite mark to the left of the NewsCast name in the header and on sign-in
- Brand mark changed to a small display with a newspaper inside
- Status Send a file panel and stat cards: tighter heading, even padding, and a cleaner file picker
- Publication date on each briefing story (UI, TXT, and EPUB)
- Briefing date now reads published_at/created_at in the template so it always shows beside the source
- Stories expire after 7 days unless favourited; star on each briefing card keeps them
- Per-source Summarise / Full article setting on Feeds; full mode stores the extracted article instead of a short summary
- Settings tab for admin, schedule, and API forms; Status keeps health, file send, and X3 links
- Feeds cards: header with On/Off and trash icon, inset schedule tray, plus-icon Add custom button
- Catalog cards: compact Add/Added pills in the header, accent rule, tighter type
- Catalog titles sit in a fixed two-line slot so every card lines up
- Sticky category chips sit below the header instead of on its border when you scroll
- Sticky filter chips sit just under the header line, without an extra floating gap
- `deploy/install.sh` for a fresh Raspberry Pi OS copy: packages, venv, systemd, LAN `.env`
- Install `--hostname` and `deploy/set-hostname.sh` so the Pi is reachable as `http://NAME.local:8080`
- `DEVICE_HOSTNAME` in `.env` and a Device hostname field on Settings; X3 and Status URLs use `http://NAME.local`
- `deploy/export-pi.bat` builds a slim `dist/NewsCast-pi` folder to copy onto the Pi
- `INSTALL.md` how-to for a fresh Raspberry Pi OS install, written for a non-technical user
- Install guide covers SSH, Raspberry Pi Connect, and copying the folder from Windows without a USB stick
- Saved tab: paste a URL, scrape the full article, keep 7 days or a custom date, remove by hand; included in the X3 briefing
- Ollama as an optional summary provider next to OpenAI, with URL, model, and Load from Ollama
- Instance name so a home Pi and a work laptop each label their X3 briefing; Status shows this copy’s URL
- Catalog cards show RSS vs Scrape, with type filters and Add custom for your own feed or website
- TechCrunch in the recommended catalog as RSS (`https://techcrunch.com/feed/`)
- OPDS catalog at `/opds` for CrossPoint: today's briefing EPUB and Status library files; HTTP Basic uses the X3 token
- Settings option to require a CrossPoint username and password; off by default because that login can crash the X3
- Catalog username and password fields on Settings when you do want CrossPoint to log in
- Reader-facing labels (Status, Settings, Saved) no longer name a specific Xteink model
- Favicons are fetched once when a feed is added, cached on disk, and shown on Briefing, Saved, Feeds, and Catalog cards
- Favicon capture tries the feed first, then the publication website (BBC RSS falls back to bbc.com)
- Filter chips on Briefing, Feeds, and Catalog show a small icon next to News, Tech, Sport, and the other categories

### Fixed
- Custom-feed sheet overlay blocked taps because `display: grid` overrode the `hidden` attribute
- Catalog filters left a tall blank gap: hidden groups could still take layout, and scroll anchoring kept the old position
- Header and Status stayed on Refreshing during long first fetches; ingest now skips full-article downloads when RSS already has a usable excerpt, and Refresh returns immediately while status polls in the background
- Website scrape sources such as TechCrunch hung on the homepage; ingest now tries `/feed/` and other common RSS paths first, and scraped stories keep a date so they stay in the briefing
- Briefing no longer drops undated scrapes or buries a new source under a single busy feed
- Delete feed and saved-article confirmations use the same in-app sheet as Add custom, instead of the browser popup
- Saved articles now fetch the page as HTML (not RSS), accept URLs without https://, and show an on-page error when the scrape fails
- Save and error toasts are a compact centred chip instead of a full-width black bar, with green or red colour by outcome
- Successful save toasts survive the page reload and stay up longer so they can be read
- TechCrunch and AP favicons were blank: skip empty default .ico files, read the site icon from the homepage, and use a public icon helper when the site blocks us
- Filtering to a single item (Favourites, Security, and similar) left a blank gap above the page heading; the list now jumps back to the top
- Briefing (and Saved) favicons sit beside the source chip instead of inside it, so they line up with the date and star
- Saved articles use the same favicon capture as feeds (page, then helper) and show a cached icon even when the host is not a subscribed source
- Cached source favicons are stored in the app and copied onto a fresh Pi so Catalog and Feeds show icons without re-fetching
- Version number moved from the header and Status onto an About card on Settings, with author and a short description
- Feed On/Off control is a quieter chip labelled Enabled or Disabled
- Schedule lengths of an hour or more show as hours (24 hrs) instead of minutes (1440 min)
- Send tab for EPUB and PDF uploads; Status keeps health and reader links
- README lists the current tabs, CrossPoint OPDS paths, Send library, and Settings overrides
- Status shows a QR code for this copy’s hostname or LAN IP so a phone can open the app
- Status Open on this network sits below Reader, with iOS home-screen steps named NewsCast plus the instance (NewsCast Home)
- Phone layout keeps the tab bar on the bottom and filter chips under the header without a pinch-zoom to correct them
- Catalog two-column cards shrink on a phone so titles and Add fit without overflowing
- Catalog is a browsable library of news, culture, tech, science, and other feeds; Add captures the publication favicon
- Desktop filter chips wrap so every category stays visible and the horizontal scrollbar does not appear
- Sticky filter chips span the full column so briefing cards do not show through on the right when you scroll
- Scrollbars use a thin ink-on-paper thumb so they match the UI when they appear
- Catalog category and type chips keep an even 8px gap when the category row shows a scrollbar
- Desktop page scrollbar uses the same ink-on-paper track and thumb as the rest of the app
- Nordic catalog sources (DR, Politiken, SVT, NRK, and others) translate headline and excerpt to English with the Google Translate web API before they are stored
- Feeds can set Language to Translate to English for any custom source
- Catalog adds Copenhagen sources (DR Copenhagen, TV 2 Kosmopol, The Copenhagen Post, The Local Denmark) and key Australian news, business, and sport feeds
- Catalog filter News is now World News; Australian sources sit under their own Australia chip, like Nordic
- iTnews moves from Tech into the Australia catalog group
- Translation uses a Google POST request and a Chrome fallback when the free endpoint rate-limits, and existing Danish briefing stories are backfilled on refresh
- Chrome translate replies no longer append the language code to the English headline or excerpt
- Feeds cards have an update control that fetches that source only, without refreshing every feed
- Settings can set how many stories Briefing shows: 10, 20, 30, 40, or 50
- Settings can check GitHub Releases, validate a zip, back up data, then install and restart without touching data or .env
- Settings can download or restore a backup of the database, Send library, and .env
- Settings can add custom categories; Catalog can import and export a JSON package of providers
- Settings Backup card lines up Download with the other pills, centres its label, and leaves space above Restore
- Settings uses its own tabs: Access, Schedule, LLM, Reader, Categories, Backup, and About
- Settings tabs are links with icons so you can move between them even if the script is cached
- Settings splits Backup and Update: restore stays on Backup; GitHub check and install sit on Update
- Settings tab is labelled Backup/Restore, and Roll back last app sits there with restore, not on Update
- Git ignore covers SQLite WAL files, Cursor settings, and leftover cookie dumps so they stay off GitHub
