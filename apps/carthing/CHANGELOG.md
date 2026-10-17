# Changelog — Car Thing panel

## 0.3.0 — 2026-10-17

### Added
- **Clock widget:** the clock face and date on their own, without the weather (small, half or full page), alongside Clock & weather and Weather. It uses the Clock tab's face, colours, 12/24 h and seconds, at a size that fits each face (seconds, the word clock and the tall faces get their own). On a page with it, the top strip shows the page name, and still shows the weather unless a Clock & weather or Weather widget is there too.
- **Music: a Spotify remote on the panel** (backlog F7; spec `ideation/carthing-spotify.md`). It shows what's playing on your Spotify Connect speakers and controls it. No audio goes through the Pi: the service only talks to the Spotify Web API, and artwork comes through the Pi because the device can't reach the internet.
  - **Now Playing** (Music mini-app, `open:music`): artwork, track, artist, album, a progress bar that moves every second, the speaker and its volume. Play/pause takes the first focus, then previous, next, shuffle, repeat (off, all, one) and Save to Liked Songs, plus **Speakers** (pick one to move the music there) and **Presets** (presets 1–4 from Notify). With the option on, the **dial turns the volume** here: clicks add up for 300 ms and go out as one change. If the speaker refuses remote volume or the volume actions aren't allowed, the dial keeps its usual job.
  - **Music widget:** small (track · artist) and half (artwork, track, progress) open Now Playing when tapped. **Full page is Now Playing itself:** all the controls, Speakers, Presets and volume, with dial volume and the 3 s refresh, and no header to open it again.
  - **Screensaver:** while music plays it shows the artwork, track and progress, with the time in a corner (option, on by default). **Music** is also a live-line choice ("♪ Track — Artist").
  - **Actions** (group Music, mappable to any button, dial or swipe): play/pause, next, previous, volume up/down, shuffle, repeat, save, and play music preset 1–4. Each goes through the config's allowed actions, and older allow-lists allow them automatically.
  - **Polling only while music is on screen:** Spotify is read every 3 s while Now Playing is open, with one extra read 0.7 s after a command. A music widget or the music screensaver follows the existing 15 s check-in, so Spotify is read at most every 10–30 s. A 429 from Spotify pauses calls for as long as its Retry-After says, and the screen keeps its last state. A 403 shows as "needs Premium or this speaker can't be controlled". A speaker that has dropped off Spotify's list says to open Spotify on it once.
  - **Sign-in tokens:** the service turns the Spotify sign-in saved by Notify into access tokens (memory only) and saves a rotated refresh token back to the Vault. A revoked one isn't retried until you connect again. Notify's **Test connection** and its speaker and playlist lists go through new signed endpoints (`/internal/music/check`, `/reset`, `/catalog`), and `/internal/status` gains `music`. Artwork is cached in `music-art/` in the data folder (the newest 150).
- **Screensaver styles** (Notify → Displays → Car Thing → Idle): **Clock** (the faces, as before; saved configs stay on it), **Bouncing logo** (the StonePi mark drifting and bouncing off the edges; options: change colour on bounce, corner celebration, speed, show time) and **Matrix** (falling code rain; options: colour, show time over the rain, speed, density). Animations run at up to 30 fps, 15 fps while dimmed or in quiet hours, and stop while the screen is off. Date, weather and the live line stay with the Clock style.
- **3D Pipes screensaver**, after the Windows 98 one: pipes grow through a 3D grid seen in perspective, shaded, turning at random, each in a new colour; once the screen is busy it holds for a moment, clears and starts again. Options: corners (elbows, ball joints or mixed), speed, show time. Drawn over the screensaver background, one segment at a time, so it costs the device very little.
- **Weather widget** (backlog U14): weather on its own, without the clock. Small: now, with high and low. Half: plus the next hours and three days. Full: plus ten hours and six days. Tapping it (or pressing the dial on it) opens the weather screen.
- **Weather screen** (U14): the current temperature with feels-like, wind and today's high and low, the next 12 hours with the chance of rain, and the week ahead. Back returns to the page. Also reachable from a button with the new **Open Weather** action.
- **Screensaver clock of its own** (backlog U13): when set apart in Notify, the Clock screensaver uses its own face and colours while the Clock & weather widget keeps the others. Matrix's time uses that face and those colours too (bold digital otherwise), and the small corner time on Bouncing logo and 3D Pipes takes the colours. Saved configs keep one face everywhere.

### Changed
- Tapping the **Clock & weather widget** can open the weather screen (Notify → Car Thing → Clock); it still does nothing by default.
- The weather fetch asks Open-Meteo for the week, hourly rain chance, feels-like temperature and wind in the same single call.

### Fixed
- **Weather could stay blank for 15 minutes.** One failed or slow weather fetch, for example just after the service starts or a new location is set, with nothing older to show, was cached as "no weather" for the whole 15-minute refresh. It's now retried after a minute. Failed fetches (weather and mini-app feeds) are logged once in the journal until they work again; before, nothing said why a card or the weather was blank.

## 0.2.0 — 2026-10-11 (part of the platform; no separate version)

### Changed
- **New look** (StonePi design system): Basalt / Sand palette, Sora and IBM Plex fonts (served by the panel service), app icons on widgets, StonePi mark on the pairing and restart screens. New configs default to the Sand accent and a plain Basalt idle background; saved configs keep their colours.

### Added
- **Sleep action** (Controls → Screen): goes straight to the screensaver at its dimmest, with no wait for "Then dim after"; where the panel controls the real backlight it switches the screen off. Any button, dial turn or touch wakes it, like the screensaver. New configs map it to **swipe up**; existing configs keep their own mappings, and Sleep is allowed in them without re-ticking (configs now remember which actions existed when they were saved, so newer actions are never silently forbidden).

## 0.1.9 — 2026-10-04 (part of the platform; no separate version)

_Ships inside platform 0.1.9. A platform service, not a catalog app: switched on in Notify → Displays → Car Thing._

### Added
- Widget sizes small, half and full page: a large System widget (CPU, memory, temperature, disk with bars, services, uptime), a new Clock & weather widget (screensaver faces; 3-hourly and 3-day forecast when large) and larger mini-app lists.
- Pages: up to 6 pages of widgets with page dots, Next/Previous/Go to page N actions (mappable to any button, dial or swipe) and optional rotation on a timer that pauses while someone uses the panel; Home goes to page 1 and Back returns to the page you came from.
- Panel for a Spotify Car Thing on its own (ADB-enabled) firmware, plugged into the Pi by USB: Home cards, list → detail mini-apps (System, SportGuide, EventTrakr, NewsCast, Pinboard), screensaver with seven clock faces, weather (Open-Meteo), quiet hours, configurable buttons/dial/long-press/swipes.
- ADB connector adapted from [pything](https://github.com/trwy7/pything) (MIT): `adb reverse tcp:8013`, redirect page bind-mounted from the device's `/tmp` over its web app, automatic pairing, restore on switch-off/stop, backlight levels over adb.
- PIN-gated (or confirm-only) restart of StonePi services via Dashboard's signed restart endpoint; wrong-PIN lockout.
- Admin preview through Notify (signed, shows the editor's draft).
### Security
- udev rule matches only the Car Thing (`18d1:4e40`), not every Google/Android USB device; the helper's JSON output escapes backslashes.

### Fixed
- Saving a config twice in the same clock tick no longer overwrites the earlier version snapshot.
