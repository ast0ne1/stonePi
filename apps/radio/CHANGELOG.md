# Radio changelog

## 0.0.11 — 2026-11-01 (first release)

Radio went on the Pi with the 0.5.0-era release smoke on 2026-10-28. Everything below is still
unreleased: first what's changed since then, then the app as it will ship.

### Security
- **Stream relay links are signed** (`/relay/<token>`): one stream each, six hours, at most four
  at once. Stations can't point at the Pi's own loopback services; HLS segment fetches (and each
  redirect) pass the same address check, sizes are capped, and encrypted HLS is refused.
- **Station logos** are fetched and served by Radio (signed `/art`; public addresses and raster
  images only), so pages never load third-party images.
- **Car Thing control** uses signed internal calls only; Odio's unsigned API stays on loopback
  (firewall drops 6600/8018/8080 everywhere else — see the platform Security notes).
- Speakers are only addressed on private literal LAN addresses (never loopback or the internet).

### What's new since the first Pi install

#### Added
- **Bitrate and sample rate for the Car Thing's skins:** `GET /api/internal/now?info=1` adds `kbps` and `khz` for the station playing, from its stream's headers (`icy-br`, `icy-sr`, `ice-audio-info`) or, when they don't say, its first audio frames (MP3 or AAC/ADTS; HE-AAC shown at the rate it plays at). HLS uses the playlist `BANDWIDTH` / `audio=96000` path and a segment's frames (BBC 6 Music: 96 / 48). Read in the background and kept 10 minutes per station, a failed read a minute; `info=1` holds up to 2 s for the first read so the skin's first paint can show them. Checked against SomaFM, KEXP, FIP, Radio Paradise and BBC 6 Music.
- **The player bar opens Now Playing:** tap the station's logo or name (the buttons beside them still play/pause, stop and pick the speaker).
- **Play on (backlog F22):** choose where Radio plays from the output chip on Now Playing or the player bar: **This device** (the browser, with lock-screen controls and AirPlay from an iPhone or Mac), a **speaker** found on the home network over DLNA (e.g. iEAST AudioCast M5), or **StonePi** (Odio). Each person's last choice is remembered (first time: This device); what's playing moves with you when you switch. Each Play on row has an icon: laptop and phone for This device, a speaker, the StonePi mark.
- **Speakers** (Settings, admins): Radio looks at start, every 10 minutes and on **Rescan**. A card per speaker: rename, **Pass https stations through the Pi** (on by default; off only for a speaker that plays https itself), and **Use LinkPlay commands** for speakers that answer them.
- **Relay** (`/relay/<token>`, signed, one stream, 6 hours, at most 4 at once): https stations for speakers that can't do TLS, http stations for a browser on an https page.
- **HLS on speakers:** for HLS stations (BBC Radio 6 Music, RTL) the relay follows the live playlist and joins its segments into one AAC stream (taken out of MPEG-TS, in Python, no ffmpeg), about 10–20 s behind live. Checked against BBC 6 Music's live stream (whether the M5 accepts it is the Pi check); encrypted and fMP4 HLS stay off speakers.
- **The song on air**, when the station sends it (Icecast/Shoutcast ICY metadata: SomaFM, Radio Paradise, NTS…), under the station on Now Playing and in the player bar (`GET /api/song`, read from the first metadata block, kept 20 s). KEXP sends an empty title; FIP, triple j and HLS stations send none.
- **For the Car Thing's artwork screensaver:** `GET /api/internal/now?song=1` adds the song on air and `station_logo`; `GET /api/internal/art?u=` serves a station logo from the same cache and checks as `/art`, behind the internal signature (the panel can't sign in).
- **Station logos on Now Playing and the player bar** for speakers and StonePi too (kept per output when a station starts, moved with it between speakers); This device gets one from presets and output switches as well.
- **No pause between pages:** Radio changes page without reloading (links, search, filters, stars, forms, Back), so a station on This device keeps playing. Other apps, Log out and anything that isn't a Radio page load as usual.
- **Player bar setting:** Off, When playing on This device (as before), or **Everywhere**, which also shows what's on your speaker or StonePi with play/pause and stop (`GET /api/now`). Each person also picks its **position**: Bottom left (as before), Bottom right, or Top right under the header (a compact player in that corner from 1024px; full width on phones). It no longer shows on Settings.
- **Now Playing presets** are tiles with the station's logo and short name, the one on air marked. Each person picks **Latest favourites** (their four newest stars, as before) or **Choose** (four from their favourites and the Home stations; a chosen station that goes is skipped).
- **Admins can hide the Add station +** for themselves, and add Home stations from Settings → General instead.
- Each person picks which of My Radio's lists comes first (Home stations or My favourites).

#### Changed
- **My Radio** (was Listen): **Home stations** and **My favourites** as two sections on one page instead of a switch. Old `?show=favorites` and `/favorites` links land on My favourites.
- **Home stations are the admins' to manage:** only admins add and remove them, enforced by the server; everyone else stars stations. The + and the bin aren't shown to members. Discover marks a household station "✓ In Home stations" instead of offering the bin; removing is on My Radio only.
- **Settings → General** is one panel for everyone in the Settings hub's style: *Display · just for you* (My Radio's order, the Add station switch for admins, the player bar, presets) as segmented buttons and switches, then for admins *For the whole household* (default volume, Discover's country), one Save, and an **Add a Home station** row that opens. Settings → Speakers matches it.
- **Cards:** long Radio Browser names ("Radio X - The best rock 24/7 …") show the name in bold and the rest as one grey line. On phones My Radio and Discover's grids are a compact list (logo, name, country; ▶, bin and star on the right). On wider screens cards are ~170px with a 4:3 cover, the logo a rounded tile on the card's tint, two tags on one line and the bin beside them; Discover's rows match.
- **Phones:** Now Playing fits one screen without scrolling (iPhone 13 in Safari and from the home screen; iPhone SE with the artwork beside the station); the presets are one row of four.
- **Laptops and tablets** (the repo's 14" sizes: 1280×720, 1536×864 = 1920×1200 at 125%, 1920×1200): Now Playing is a landscape card; from 1024px the card and the presets sit side by side and fill the column. Settings sits in a 768px readable column. No horizontal scroll at any reference size (deploy/responsive-audit.md).
- Play on's refresh is **Rescan** (was Look again), the icon alone. The player bar keeps play/pause beside stop, and the done message sits above the bar instead of over it.

#### Fixed
- **Phone bottom nav:** the bar no longer stacks the home-indicator inset twice; the player bar, sheets, toasts and + clear the shorter bar.
- **Car Thing skins got empty kbps/kHz on the first look:** `info=1` now waits briefly for the background stream read (up to 2 s) instead of always answering 0,0 before the headers arrive.
- **kbps/kHz stayed 0 for Icecast stations (e.g. 90s90s):** httpx refuses `Transfer-Encoding: identity`, so the stream probe never saw `icy-br` / `ice-audio-info`. The read now uses urllib, which accepts those streams (192 / 44 on 90s90s HipHop).
- **HLS stations (BBC Radio 6 Music, RTL) left kbps/kHz blank:** the probe skipped HLS entirely. It now reads the master playlist's `BANDWIDTH` (or a USP/BBC `audio=96000` path) and the sample rate from a recent segment's AAC frames, so the Winamp skin fills both boxes.
- **No broken-image "?" on Now Playing or the player bar:** the speaker/StonePi card no longer falls back to the player's raw artwork address (only the Pi can reach it), and artwork that fails to load is hidden with the radio icon shown instead; a failed address isn't retried on every update.
- Picking a speaker for a station it can't reach no longer half-switches: the picker closes, the station keeps playing where it was, and the message says why.
- A done message after a star or add on a filtered page (Discover's `?genre=`) keeps the filter (the redirect added a second `?`).
- The Car Thing's Radio widget said Unavailable / nothing playing on the Pi: its signed "what's playing" call was refused (Car Thing 403 fix; see apps/carthing/CHANGELOG.md).
- **Discover's genres are that genre:** a hip-hop pick could play club or chart radio, because some stations list dozens of tags (hiphop among them) and sorted by plays they topped every genre. Genres and More like your favourites now leave out stations with more than 12 tags unless their name says the genre, and put stations named for the genre first, then those whose tags lead with it.
- **iPhone 13 mini:** Discover's genre pills no longer clip their text; on Now Playing the station name (up to two lines) and the volume row stay inside the card and the artwork grows into the spare height; Discover and Settings no longer keep a blank band at the bottom while the player bar shows (the room for the + is kept only on pages that have it).

### The app as it ships

- **My Radio · Discover · Now Playing · Settings**, in the same chrome as the other Home apps (Home is in the header's app switcher). Done messages are a toast that fades; errors stay as a banner.
- **My Radio:** Home stations (the household's list: eight to start, admins add more from Discover or by stream address with an optional logo) and My favourites (each person's own stars).
- **Discover:** Popular in your country, Trending now, More like your favourites, and genre chips (Pop, Rock, Jazz, Electronic, Classical, News, Talk…), from Radio Browser (cached for 30 minutes). Header search lands here and adds myMPD's WebradioDB. The country is an admin setting; until set, it's the Pi's time zone's country.
- **Now Playing:** what's on, with its logo and song, play/pause and stop, a volume slider that applies as it moves (− / + step 5%), and four presets.
- **Station logos** are fetched and kept by Radio (signed `/art` links; public addresses and raster images only; a week's cache, left out of backups), so pages never load third-party images. A logo that can't be had shows the station's letter.
- Playlists (`.pls`, `.m3u`) are followed to the stream; HLS plays on This device (Safari, or the bundled hls.js 1.5.17) and on speakers through the relay.
- Plays through **Odio** on the Pi (installed by the StonePi installer), which also makes the Pi an AirPlay and Spotify Connect speaker on the home network.
- **Car Thing:** a Radio widget and full-screen player with Classic Winamp, Nintendo AMP BOY, RealPlayer, TouchAmp and Plain looks, play/pause, stop, presets and dial volume, playing on its own Play on output (Notify → Car Thing → Look → Radio; older panels keep StonePi). It controls Radio through signed internal calls (`/api/internal/now|play|transport|volume|outputs`), never Odio directly.
- Streams must be http(s) and can't point at the Pi's own loopback services. Favourites are per person; displays nobody signs in to (Home, the Car Thing) show the household's most-saved stations. Members without Radio get the standard "No access" page.
- Radio's database is in StonePi's backups; its logo cache isn't.
- `RADIO_MOCK=1` (set by run-dev) simulates playback on Windows.
