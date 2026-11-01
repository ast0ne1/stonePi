# Changelog — EventTrakr

## 0.0.11 — 2026-11-01

### Security
- **Every signed-in POST checks the CSRF token**, not just sign-in (backlog: CSRF coverage). Every page sets the `stonepi_csrf` cookie; the 37 POST forms carry `csrf_token` and background saves (app.js, the map) send `X-StonePi-CSRF`, all checked in one `before_request`. A refused form goes back to its page with "That page expired. Refresh and try again."; a refused background save answers 403 `{ok: false}`. Exempt: the signed Pebble read (`/api/internal/agent`) and Log out (the shared header's form). Every page renews the cookie, so a tab left open doesn't go stale, and a refused cross-site post goes to EventTrakr's home rather than back to the other site. CSRF also requires Origin/Referer to match Host (cross-port).
- **Solo login `?next=` is path-only** (no off-site redirect after sign-in). Platform SSO was already safe.
- **Google OAuth tokens at rest** no longer fall back to a well-known string when Vault is
  down; they derive from the session secret or a random local key file.
- `cryptography` pin raised to **50.0.1** (aligned with Dashboard).

### Fixed
- **Same source, two titles for one event:** a source that lists the same start twice under
  different titles (e.g. Jungle's "Søndagsbrød" and "Søndagsbrød: Floor" at NABO 08:00) is
  folded into one card. Two real sessions the same day (starts a minute or more apart) stay
  separate. A refresh also collapses twins left over from before.

**What's new:** EventTrakr now opens on a **map** of what's on this week, shows an event **once** when several sources list it ("Also on: BrugByen"), keeps **exhibitions and other runs** on until their last day, and adds **Copenhagen's 12 local committee calendars** with two new categories, **Committee meetings** and **Flea markets**.

### Added
- **Map** (backlog F18, `ideation/eventtrakr-map.md`), now the home page. A map icon in the header opens it from anywhere.
  - **Pins** sit on venues with the category's icon (a count when there's more than one). Tapping one shows that venue's events as cards.
  - **The week card** shows the calendar week (**Wk 44 · 26 Oct – 1 Nov**, ‹ › for up to 8 weeks ahead) with a bar per day, M–S, sized by the events in view (lighter for exhibitions and other runs). Tap a day to show only that day.
  - **Filters** on the week card: category toggles (Committee meetings start hidden), Free, and "N not on the map" (events with no place yet, listed as cards).
  - **Event cards** swipe past beside the week card, which stays put. A mouse wheel steps one card at a time.
  - **A small || grip** on the week card's left edge collapses it to the left into a slim tab, as tall as the cards, with a **Wk 44** pill at the top, a mini bar per day M–S, and the event count at the bottom. The cards then get the whole row. Open or collapsed is remembered on that device.
  - **Days past the 7-day sync** show hatched. **Look for more in wk 46** runs a deep search for that week, only when you ask.
  - **Find a place** jumps the map to a place ("Refshaleøen"). On the map, the header's search button goes straight to it.
  - **Show where I am** centres the map on you.
  - **"Wrong place?"** on a card lets you drag a pin to where the venue really is, for every event there.
  - OpenStreetMap tiles via Leaflet 1.9.4, served from EventTrakr (`static/vendor/leaflet`). The tiles send the site's origin as their referrer, because OpenStreetMap refuses tile requests without one and StonePi's nginx strips it everywhere else.
- **Venues get coordinates in the background** after each sync (and when the map opens). Each new location is looked up once with OpenStreetMap's Nominatim and stored in a new `places` table:
  - One lookup a second, at most 150 per run, kept to your city and about 15 km around it, so "Union" is the one on Nørrebro and Hellerup venues are found too.
  - Nothing is looked up until the city itself has been found.
  - "Online", "Unspecified" and a bare city name are skipped.
  - Places not found are tried again after 30 days.
- **Same event from several sources, shown once** (backlog U33, `ideation/eventtrakr-dedup.md`).
  - **Matching:** a listing that's the same event as another source's (same day, start within 90 minutes, similar title, same venue, or the same ticket link) is merged into it instead of becoming a duplicate. Generic titles ("Lokaludvalgsmøde") only merge when the venue agrees, and a source's own two listings are never merged.
  - **On the card:** **Also on: BrugByen**, with a link to that source's page, on the Agenda, Search and public agendas. **×** next to a source splits it back off ("Not the same event"), and that pair is never merged again.
  - **Merged details:** the first source's title, link and category stay; a real price, an address, a longer description, an image or an end time fill in from the others. A location only ever gets better (an address for the same venue), never flips back and forth between sources.
  - **Tables:** new `event_sightings` and `dedupe_exclusions` tables. Every existing event gets a sighting for its own source on start.
- **Exhibitions and other runs stay on until their last day.** A listing that lasts a day or more ("18.09.2026 - 26.09.2026") is kept and shown on every day it covers: Agenda, the day filter, Search and public agendas. The card says "Wed 14 Oct – until Wed 25 Nov". A one-off that ends after midnight (a club night 22:00–04:00) still belongs to the day it starts.
- **A show's other dates are kept**, up to 120 days ahead, so later weeks fill in without another scan.
  - Where a BrugByen or district card says "2 andre datoer" / "3 other dates", the dates come from the event's own page (at most 40 pages a sync, each trusted for 12 hours).
  - A date isn't cancelled just because its page wasn't read on a sync.
  - Repeating schedules ("every Sunday until 20 Dec") become one date each.
- **Copenhagen's 12 local committee calendars** (lokaludvalg) in the Sources catalog: Amager Øst, Amager Vest, Bispebjerg / Nordvest, Brønshøj-Husum, Christianshavn, Indre By, Kongens Enghave / Sydhavn, Nørrebro, Østerbro, Valby, Vanløse and Vesterbro. They run on the same platform as BrugByen, so the BrugByen scraper reads them too, including their Danish dates ("Søn. 08. nov. 2026 Kl. 10 - 12").
- **Committee meetings** category (clipboard, blue). The committees' own meetings, working groups and public meetings (lokaludvalgsmøde, borgermøde, bydelsmøde, høring) from a district calendar are filed here instead of under Community, so neighbourhood events (Repair Café, swap markets, lantern parades) stay in Community.
- **Flea markets** category (bag, amber), from the title wherever it's listed: loppemarked, loppetorv and other "loppe…" titles (not Loppen, Christiania's venue), kræmmermarked, genbrugsmarked, flea market. Byttemarked (swap markets) stay where their source puts them.
- A scraper can now set one event's category (`ScrapedEvent.category_key`), which wins over the source's, and give its further dates (`ScrapedEvent.more_dates`).

### Changed
- **EventTrakr opens on the map:** the home page (`/`) and signing in land there. The Agenda is unchanged at `/agenda` and keeps its tab.
- **Filtering the Agenda by a source**, the source chips, and searching by source name include events merged with another source's listing.

### Fixed
- **Dedupe missed a short title inside a longer one at the same venue** (e.g. "Søndagsbrød" and "Søndagsbrød: Floor" both at NABO, one with a street and one with the district). A distinctive one-word title now counts as contained, and Copenhagen district names no longer dilute the venue score.
- **An event is no longer marked cancelled while another source still lists it.** Before, when the source that first found an event stopped listing it, the event was cancelled even if another source still had it.
- **Running exhibitions no longer drop off the Agenda** after their first day (see Added).

### Upgrading
- New tables (`event_sightings`, `dedupe_exclusions`, `places`) are created on start. No new Python packages.
- Events already in the database aren't merged with each other; new listings merge from the next sync. For a clean start, clear the events after the first start on the new version (sources, categories, settings and users stay) and run a full Refresh. Map pins fill in over a few minutes as venues are looked up.

## 0.0.10 — 2026-10-25

### Added
- **Pebble can read your events** (when you switch it on in Notify → Sidekick): upcoming events like your Agenda, and search. A signed internal endpoint (`POST /api/internal/agent`) used only by AgentLink, answering for one person from their own EventTrakr; read-only.
- **Ask Pebble for a time or a kind of event:** "what's on tonight?", "what's on tomorrow?", "what community events are on next week?". Pebble's events read takes a window (today, tonight, tomorrow, this weekend, this week, next week, in the Pi's local time) and an optional category matched loosely ("communities", "Music"), and lists the window's categories with counts so a busy evening can be narrowed down. Nothing in the window is left out unless you ask for one kind.

## 0.0.9 — 2026-10-21

### Added
- **Settings → General → Agenda view** (backlog U25): tick which filter chips show above your Agenda: **Category chips**, **Free chip** and **Source chips** (all on by default, per person). Category and Free share a row, which goes only when both are off; Day chips always show. A hidden filter also stops filtering, so an old link can't leave you with a filter you can't see.

### Changed
- **Price pill with a tag icon** (backlog U26) on Agenda, Search and public agendas: **green with the amount** for paid events ("DKK 150"; long price text is cut to the amount, e.g. "120-180 kr"), **blue, icon only** for free, **grey, icon only** when no price is listed. The full price, "Free" or "Price not listed" shows on hover and to screen readers. It was a "Price: …" text badge.
- **Category pills get an icon and a colour** (backlog U31) on Agenda, Search, public agendas and the Sources catalog: Music (note, purple), Culture (columns, amber), Community (people, sand), Food & Drink (cup, red), Business (briefcase, grey), Arts (palette, green), Sports (ball, blue), Family (heart, sand), General (tag, grey). Categories you add get a tag and a colour picked from their name, so each keeps the same colour everywhere.
- **Choose each category's colour** in Settings → Categories (admins): every category shows its pill and a row of swatches (Purple, Blue, Green, Sand, Amber, Red, Grey); tapping one saves it, and **Auto** goes back to the default. Icons stay fixed. New `categories.color` column, added on start.

## 0.0.8 — 2026-10-11

### Changed
- **New look** (StonePi design system): EventTrakr icon in the header and as favicon and home-screen icon; new colours, fonts, buttons and cards in light and dark; the old palettes are gone.
- **Navigation (shared StonePi header):** the bottom bar is down from 8 tabs to 4: **Agenda · Faves · Review · Settings**. Search, Refresh, the notifications bell, the app switcher (replaces Home) and your avatar sit in the header; **Add** is a floating **+** on Agenda and Faves.
- **Search** opens from the header as an overlay ("Search all events and articles", with your recent searches), then shows results on the search page as before.
- **Review** (was Find): the queue of events found through Instagram tracking, with a badge counting open items, one-tap **Approve** / **Reject**, "You're all caught up" when it's empty, and **Manage tracked accounts**.
- **Settings** opens on **Sources** and **Instagram tracking** (no longer bottom-bar tabs), followed by the existing sections. Light/dark lives in the avatar menu.
- **Log out** moved into the avatar menu, below a divider, so it can't be hit by accident.
- On phones and tablets, each event card's **Open** and red **Add to calendar** buttons sit together on the left, clear of the floating **+**. **Track account** (Settings → Instagram tracking) and **Manage tracked accounts** (Review) are smaller.

### Fixed
- Review's "View original post" icon had no size and filled the card as a large black shape.

## 0.0.7 — 2026-10-04

### Added
- Per-person permissions (Dashboard → Users): Manage sources, Instagram & Facebook, Share agenda publicly, Google Calendar sync. Members need the matching one to add/edit/remove sources, track Instagram or use Facebook sources and "Check now", make their agenda or favourites public, or connect Google Calendar (and the webcal feed); refused actions say who to ask and the controls are hidden. Existing members keep everything except Instagram & Facebook (paid Bright Data).
- `/api/display?items=N` (loopback) adds `card` + `items` for the Car Thing panel: upcoming favourites with venue, cost and description. The plain call is unchanged.
- **Bright Data monthly limit** (default 5,000 records, shared with PriceWatch on the same account): every Facebook and Instagram call books its maximum against the platform quota first and is skipped, with the reason on the source or account, once the limit is reached. Scheduled syncs and polls also keep to the month's share so far; **Fetch Details** and **Check now** can use the rest. **Settings → Data Providers** shows this period's usage per app and sets the limit and the day of the month it resets (default 1, to match the Bright Data billing date).
- **Instagram polling controls** (Settings → Discovery), all counted within the Bright Data limit:
  - **Instagram allowance**: Instagram's own share of the limit (default 2,000 records a period; blank shares the whole limit; 0 stops Instagram checks), so polling can't use up what Facebook sources and PriceWatch need. Scheduled checks spread it across the period; **Check now** can use what's left.
  - **Polling hours** (e.g. 08:00 to 23:00): scheduled interval checks only run in the window, which may cross midnight. Accounts on fixed weekly times keep them.
  - A **worst-case estimate** for the period from the tracked accounts, their schedules, the polling hours and posts per check, with a warning when it's above the allowance.
- **Jungle Copenhagen** (`https://jungle.am/events`, Food & Drink) in the recommended sources. Jungle is a SvelteKit site, so events are read from the page's `__data.json` over plain HTTP (no headless browser): title, date and end time (per occurrence for multi-date events), host and address, price, poster and the event's own page.

### Changed
- Capability roster refresh uses the shared stonepi_auth roster helper (behaviour unchanged).
- Paid social polling and Facebook source syncs skip people without Instagram & Facebook; public agenda and favourites go private when the owner loses Share agenda publicly. Revokes reach background jobs within 5 minutes via Auth's roster.
- **Facebook search via Bright Data:** at most 30 events per run (was unlimited), and a result is reused for 12 hours, by every user with the same search and by **Sync all**, instead of being bought again every hour. **Fetch Details** reuses a lookup of the same event for 24 hours.
- A Bright Data request that is refused outright (bad key, network) no longer counts against the limit.

### Security
- Source favicons: SVG/HTML icons are no longer cached (raster only, whatever type the site claims), and `/favicon-cache/` is served with a sandbox CSP and nosniff, so an icon can't run script on the StonePi origin.
- Listens on `127.0.0.1` by default (the platform already set this).
- Requirements pinned exactly; `pytest` is no longer a runtime dependency.

### Fixed
- Search matches every word in any order across title, place, source, category and description, ignoring case and accents (æ/ø/å); results rank title matches first, upcoming first. Your default location is no longer applied silently: it's a one-tap chip, and a location filter shows as a removable chip.
- On phones, running a search no longer squashes the filter card (date, location, category) under the results; the page scrolls with the filters intact.
- Source icons heal themselves: a cached favicon whose file is missing (or an old SVG) is refetched in the background at startup or when Sources opens, and the placeholder shows meanwhile instead of a broken "?"; cached icons are served with an explicit image type.

## 0.0.6 — 2026-09-29

_Moves from 0.0.3 to the shared user-app version, 0.0.6 (numbers in between were skipped)._

### Added
- **Stop** button beside Refresh (header) and on Sources while a manual refresh runs; the sync finishes the source it is on, then stops and reports “Stopped after x/y sources”
- Per-account Instagram poll schedules (Global Discovery interval, or custom interval / weekly days & times — same pattern as URL sources)
- Instagram social discovery & enrichment (full v1): track public accounts, Bright Data poll (capped posts/check), local OCR, match-then-discover pipeline, Discoveries UI, Sources → Instagram admin, Settings → Discovery, event updates/cancellations, and optional ntfy envelopes
- Under SSO, Settings shows the Auth factory-password banner (links to StonePi → Settings → General) from the shared session flag
- Approaching alerts for favourited events via Notify (schedule tick every 5 min; Settings → Notifications for lead time + Destinations pointer)

### Changed
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- Deep search past the Agenda horizon runs in a background thread (pending “searching…” state) so Chromium scrapes no longer block `/search`; ICS-only deep search still finishes in-request
- Headless Chromium launches hold a shared appliance lock (`stonepi_browser`) so EventTrakr and SportGuide never run two browsers at once
- systemd unit: Nice/CPU/IO weight + MemoryMax=400M (Pi resource contention)
- Login `next` includes the app path prefix under SSO; skip session load for `/static` and `/favicon*`
- Settings uses hub → section on laptop/desktop as well as phone (no horizontal chip scroller)
- Topbar Sync renamed to Refresh (status pill Refreshing); icon-only Refresh at ≤420px to match NewsCast
- Public/TLS URLs and Network tab copy use the platform appliance hostname from Dashboard → Settings → Network (no per-app hostname field)

### Fixed
- Refresh no longer ends in Error once an Instagram-linked event goes past: the daily past-event purge now clears its social links/updates and detaches discoveries first (FK constraint)
- Refresh no longer fails instantly on the Pi when SportGuide created the shared Chromium lock first (`stonepi_browser` opens an existing lock without `O_CREAT`; tmpfiles pre-creates it root-owned)
- Header Refresh, favourites, per-source refresh, and Facebook import work under the `/events` prefix now that nginx serves `app.js` directly (requests apply the app prefix in the browser)
- Laptop/desktop: main column no longer shrink-wraps to page content (auto margins in the grid), so page headers stay put between Agenda, Faves, Search, Add, Sources and Settings
- Laptop/desktop: sticky Settings chips sit below the sticky topbar instead of under it; scrollbar gutter reserved so short vs long pages don't shift sideways; toast moves top-right like NewsCast
- Phone/tablet: buttons and form controls inherit the chrome font (button chips no longer render in the browser's default font) and inputs are 16px so iOS doesn't zoom on focus
- Bottom nav labels 0.7rem (was 0.58–0.62rem) with safe-area side padding for landscape notches; global h1–h3 styles; size/padding no longer animate on load (`transition: all` removed)
- Topbar height re-measured on `pageshow` (iOS back/forward cache)

## 0.0.3 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px**; wide main / events grid density at **≥1440px** (was 860 / 1100)
- Laptop/desktop: main column fills width beside the rail; Settings cards lay out in 2–3 columns; agenda/search event cards 2+ columns from 1024

## 0.0.2 — 2026-09-19

### Added
- `GET /api/display` includes `favourites` count alongside `next` for Status wall Services metadata

### Fixed
- Favourite star toggle: only toast “Removed” when the API returns `favourited: false`; errors no longer look like a successful unfavourite

## 0.0.1 — prior

- See app history / README
