# StonePi Radio

Digital radio for the household. Browse stations, keep your own favourites, and play them in your browser, on a speaker on the home network, or through **[Odio](https://odio.love)** on the Pi's audio output. Radio never exposes Odio itself: its API and myMPD stay on loopback, behind StonePi's firewall. Odio also makes the Pi an **AirPlay** and **Spotify Connect** speaker (named after the Pi) for phones on the home network.

| | |
|---|---|
| Path / port | `/radio/` · **8016** |
| Unit / user | `stonepi-radio` |
| Data | `/var/lib/stonepi/radio/radio.sqlite`: Home stations, favourites and settings (per person and household), speakers; in StonePi's backups. Logo cache `art/` is left out |
| Odio | `ODIO_API_URL` (`http://127.0.0.1:8018`), `MYMPD_URL` (`http://127.0.0.1:8080`) |

## Pages

- **My Radio**: two lists on one page. **Home stations** is the household's list (eight to start, the same for everyone); admins add stations (from Discover, by stream address with the +, or in Settings) and remove them, and the server enforces it. **My favourites** is each person's own stars. Which list comes first is each person's choice.
- **Discover**: Popular in your country, Trending now, More like your favourites, genre chips, and search (myMPD's WebradioDB, else [Radio Browser](https://www.radio-browser.info), kept 30 minutes). A station already in Home stations says "✓ In Home stations"; admins can "+ Add to Home" the rest.
- **Now Playing**: what's on and where, with the station's logo and, when the station sends it, the song (see below); play/pause, stop and volume; and four **presets**: each person's four latest favourites, or four they choose from their favourites and the Home stations.
- **Settings**: General (everyone), then Speakers and Odio (admins), phone alerts and About.

Station logos are fetched and kept by Radio (`/art/…`, signed links, public addresses and raster images only, a week's cache, left out of backups), so pages never load third-party images; a logo that can't be had shows the station's letter. Long Radio Browser names ("Radio X - The best rock 24/7 …") show the name in bold and the rest as one grey line.

The player bar shows on My Radio and Discover only (Now Playing has the full controls; Settings has none).

Radio changes page without reloading: links, search, filters and forms fetch the next page and swap in its main area, bottom nav and +, so a station playing on This device carries on. Other apps, Log out and anything that isn't a Radio page load as usual.

Streams must be `http://` or `https://`, and can't point at the Pi's own loopback services.

## Settings

**General** is one panel, saved with one Save:

| Setting | Who | Choices |
|---|---|---|
| My Radio shows first | each person | Home stations · My favourites |
| Add station button (the + on My Radio) | each admin | on · off (Settings still has **Add a Home station**) |
| Player bar | each person | **This device** (shows while the browser plays; the default) · **Everywhere** (also a speaker or StonePi, with play/pause and stop) · **Off** |
| Player bar position | each person | **Bottom left** (the default) · **Bottom right** · **Top right** (under the header). From 1024px a compact player in that corner; on phones and tablets full width, at the bottom or under the header |
| Now Playing presets | each person | **Latest favourites** · **Choose** (four pickers) |
| Default volume | household (admins) | what speakers and StonePi start at |
| Country for Discover | household (admins) | the Pi's time zone's country, a country, or none |

**Speakers** (admins): **Rescan**, then a card per speaker with **Name in Radio**, **Pass https stations through the Pi** (on by default; turn off only for a speaker that plays https itself) and, for speakers that answer LinkPlay, **Use LinkPlay commands**.

## Where it plays

The output chip (on Now Playing, and on the player bar) picks where Radio plays. Each person's last choice is remembered; the first time it's **This device**.

- **This device:** the browser plays the stream itself, with the player bar, lock-screen controls, and AirPlay from an iPhone or Mac. HLS stations play natively in Safari, elsewhere through the bundled hls.js (1.5.17, `app/static/vendor/hls`). An http station on an https page, or one the browser won't load directly, goes through the relay.
- **Speakers:** DLNA renderers on the home network (e.g. iEAST AudioCast "Soundcast" M5). Radio searches at start, every 10 minutes and on **Rescan** (SSDP). The speaker fetches the stream; Radio sends play, pause, stop and volume over UPnP (or LinkPlay). Playlists are followed to the stream. A speaker's description and control addresses must be on a private home-network address, never this Pi or the internet.
- **StonePi:** Odio on the Pi's own audio out.

Switching output moves what's playing: it stops on the old one and starts on the new. A station the new output can't reach doesn't switch; it keeps playing where it was.

**Relay** (`/radio/relay/<token>`): passes a stream on for listeners that can't fetch it themselves: https stations for speakers (unless "Pass https stations through the Pi" is off), and http stations for a browser on an https page. A token names one stream, lasts 6 hours and is signed with a per-install key; at most 4 relays run at once. nginx serves it unbuffered, on port 80 too (cheap speakers can't do TLS).

**HLS on speakers:** speakers can't follow an HLS playlist, so for HLS stations (BBC, RTL) the relay does: it fetches the live playlist (an audio variant of a master playlist), takes each new segment's AAC out of MPEG-TS (or strips packed audio's ID3), and sends one ADTS stream, `audio/aac`, starting two segments behind live (about 10–20 s). Pure Python, no ffmpeg; encrypted and fMP4 (`#EXT-X-MAP`) HLS can't be joined.

**The song on air:** `GET /api/song` reads Icecast/Shoutcast ICY metadata (`StreamTitle='Artist - Title'`) from the first metadata block and lets go; answers are kept 20 s. SomaFM, Radio Paradise and NTS send it; KEXP sends an empty title; FIP, triple j and HLS stations send none. The page asks every 20 s while something plays.

## Screen sizes

Checked at the repo's reference viewports (deploy/responsive-audit.md): phones (iPhone 13 in Safari and from the home screen, iPhone SE), tablets, and the 14" laptop at 1280×720, 1536×864 (1920×1200 at 125%) and 1920×1200. No page scrolls sideways. Now Playing fits one screen at every size: on phones a compact card and one row of presets; from 720px a landscape card; from 1024px the card and the presets side by side. Settings sits in a 768px readable column.

## Odio on the Pi

The installer runs `stonepi-odio-setup` (`deploy/stonepi-odio-setup.sh`) after the firewall is up. It installs a pinned Odio release (both files checked against SHA-256) as the user `odio`: PulseAudio, MPD, myMPD and the Odio API for Radio, plus AirPlay (shairport-sync) and Spotify Connect (spotifyd). DLNA, Snapcast, Bluetooth, the disc player, the kiosk display and Odio's login banner are left out. The firewall lets AirPlay and Spotify Connect in from LAN and tailnet addresses only, and drops Odio's API, myMPD and MPD ports from everywhere but loopback (deploy/SECURITY.md). Expect 10-20 minutes on a Pi the first time; after that it's skipped while the pinned version answers.

- Skip it: `sudo bash deploy/install.sh --no-odio`. Radio then shows Odio as unreachable.
- Install or retry later: `sudo stonepi-odio-setup` (`--force` re-runs it; `--status` prints one line).
- Odio adds its own apt source (`apt.odio.love`) and user services under `odio`. To move to a newer Odio, change `ODIOS_VERSION` and the two hashes in the script.

How playback works: Radio asks Odio for its MPD player, adds the stream with `tracklist/add` and plays it. Cover art while on air comes from Odio's MPRIS metadata (`mpris:artUrl`).

## Car Thing

Add the **Radio** widget in Notify → Displays → Car Thing → Pages. Under Look → **Radio**, pick the player look, **Play on** (StonePi or a speaker Radio found), dial volume and up to four presets. Empty presets fall back to the household's most-saved stations.

| Theme id | Look |
|----------|------|
| `winamp` (default) | Classic Winamp 2.x: dark metal, green LCD, bevelled transport |
| `ampboy` | Nintendo AMP BOY: grey plastic, olive LCD, maroon A/B buttons |
| `realplayer` | RealPlayer skin from skins.webamp.org |
| `touchamp` | TouchAmp skin from skins.webamp.org |
| `plain` | Panel theme: station logo, icon controls, favourite cards when idle |

With the Car Thing's **Artwork while music plays** on, its screensaver shows Radio when Spotify isn't playing: the station's logo, name, the song (when the station sends it) and the speaker, read again every 30 s (`/api/internal/now?song=1`, logos through `/api/internal/art`).

The panel reaches Radio on its catalog port over loopback, never Odio. Playback goes through signed internal calls (`/api/internal/now`, `play`, `transport`, `volume`; `stonepi_auth.internal`), which nginx also blocks at the edge. The presets come from the loopback `/api/display`, which the public edge blocks too.

## Local Windows

`scripts\run-dev.bat` starts Radio on http://127.0.0.1:8016/ with `RADIO_MOCK=1`, so playback is simulated and no Odio is needed. On its own:

```bat
cd apps\radio
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -e ..\..\packages\stonepi_auth -e ..\..\packages\stonepi_vault -e ..\..\packages\stonepi_contracts
set RADIO_MOCK=1
.venv\Scripts\python -m app.serve
```

Tests: `.venv\Scripts\python -m pytest -q tests`.
