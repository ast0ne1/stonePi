# Car Thing panel

Turns a Spotify Car Thing, plugged into the StonePi by USB, into an 800×480 touch + dial control panel:
pages of widgets (small, half or full page: System, mini-app lists, Clock, Clock & weather, Weather, Music, Radio, an **Audio** launcher for Spotify and Radio on one page, and full-page Web pages;
turned by hand or on a timer), small list → detail views of System, SportGuide, EventTrakr, NewsCast and
Pinboard, a weather screen, a **Spotify remote** (Now Playing, Speakers, presets 1–4, dial volume; no audio
goes through the Pi), a screensaver (clock faces, album artwork while music plays, Bouncing logo, Matrix,
3D Pipes, Flying Through Space, Mystify Your Mind, 3D Flower Box, 3D Maze or a Web page), and restarting StonePi services behind an admin PIN.

Set it up in **Notify → Displays → Car Thing**.

## On the panel

- **Page header.** The strip along the top (time & date, page title, weather, page dots) is set per config, and a page can have its own. **Always**, **Auto-hide** (shows when you arrive, touch, turn or press anything, then hides after 3, 5 or 10 s so the page grows into the space) or **Off**. With Auto-hide or Off the page gets the whole 800×480. On Now Playing and full-page web pages only arriving brings an auto-hidden header back.
- **Web page widget.** A page from Notify → Displays → Web pages, full page, edge to edge under the header. The panel shows the Pi's snapshot until it's older than the page's own refresh time (Notify → Displays → Web pages, 15 minutes at least), with **Updated 14:05** and a **↻** button in the corner to capture it again now; an edited page shows at once, and the last good snapshot stays up if a capture fails.
- **System.** A health strip (CPU, memory, temperature and disk with meters that turn amber and red; uptime, last backup, host), a **Check** line when something needs attention, then every service as a card, stopped ones first and in red. A service's page shows **Running** or **Not running**, its systemd unit, how long it has been running (or stopped), memory, restarts by itself, Notify's note and its last log lines, with **Restart** and **Open on the Car Thing**. Restart asks for the admin PIN when one is needed and confirms, then shows **Stopped → Starting → Running** and says when the service is back.
- **Now Playing.** The artwork beside the title, artist, album and speaker, over one of four backgrounds: **Glow**, **Artwork colour**, **Blurred artwork** or **Theme** (colours worked out on the device once per track). A progress bar you can tap or drag to seek, then liked, shuffle, previous, play/pause, next, repeat and **⋯** (Speakers and Presets). Controls show always, for 4 s after a touch, or never. The accent comes from the artwork or the panel, with an optional line round the screen. Turning the dial shows a large volume bar. **Nothing playing** shows the presets as tiles. The **liked tick** shows whether the song is in Liked Songs and saves or removes it; sign-ins from before it need **Reconnect Spotify** once (Notify → Car Thing → Setup), and until then the tick only saves.
- **Audio.** A full-page launcher with StonePi Radio then Spotify cards. Opens the Radio or Music screen; Back returns to the launcher, so one page covers both players.
- **Radio.** A widget, the Radio screen and a full-page widget, in one of five looks (Notify → Car Thing → Look → Radio): **Classic Winamp** (the Winamp 2.91 base skin), **Nintendo AMP BOY**, **RealPlayer** and **TouchAmp** are the real skins, drawn from their own bitmaps at Winamp's 275×116 and scaled to fill the widget (both axes, so the panel never letterboxes); **Plain** follows the panel theme (station logo, icon play/pause and stop, favourite cards when idle). The main window shows on its own; on the full page, a full-page widget and the screensaver the skin's **PL** button opens its playlist window below, whose rows are the presets (the panel remembers it). Play, pause, stop, previous / next preset, the volume slider (left half quieter, right half louder) and the playlist rows all work; the time display shows the clock and the analyser dances while a station plays (no audio reaches the panel). Radio plays on the panel's own **Play on** output (StonePi or a speaker Radio found), and the dial can turn its volume.
- **Each player in the other's look.** Look → Music → **Spotify plays in** its own Now Playing or Radio's theme; Look → Radio → **Radio plays in** its theme or Now Playing's look (the station's logo as the artwork, the song under it, play/pause and stop). It applies to the screen, a full-page widget and the artwork screensaver; a skin with its own clock doesn't get the screensaver's time on top.
- **Screensavers.** Clock, **Web page** (the snapshot over the whole screen, kept for the page's refresh time), Bouncing logo, Matrix and the Windows 98 styles 3D Pipes, Flying Through Space, Mystify Your Mind, 3D Flower Box and 3D Maze (drawn on a canvas, each with Speed and its own options), plus album artwork while music plays (Spotify, or else the station on Radio, in Radio's theme unless Look → Radio says Now Playing). **Show time** sits in any corner, top or bottom centre, or the centre, at Small, Medium, Large or Extra large, in the screensaver clock's face, with an optional dark backing; Matrix and the artwork screensaver have their own time settings.

## Radio skins: where they come from

The bitmap looks are classic Winamp 2 skins (`.wsz`) from the [Winamp Skin Museum](https://skins.webamp.org/),
cut to PNGs in `app/static/skins/<id>/` (only the bitmaps the main and playlist windows use, plus each skin's
`VISCOLOR.TXT` / `PLEDIT.TXT` colours and AMP BOY's `REGION.TXT` shape, which live in `panel.css`).

| Look | Skin file (museum md5) | Author |
|---|---|---|
| Classic Winamp | `base-2.91.wsz` (`5e4f10275dcb1fb211d4a8b4f1bda236`) | Nullsoft (Winamp's default skin) |
| Nintendo AMP BOY | `AMPBOY.wsz`, "Amp Boy 2.0" (`0eaba96cdab9ebac4d72f852aef6c69b`) | Ahmed Arebi, 1999 |
| RealPlayer | `realplayer.zip`, "Realplayer Sucks!" (`0b081a6ee753c44a8f6786898aa62021`) | Mario Portuguez |
| TouchAmp | `TouchAmp.wsz`, "TouchScreen" (`0bcb2b88f8ab624b091366725eb59369`) | James Cook |

**Can they be bundled?** None of the four comes with a licence: they were released as freeware for anyone to
download and use with Winamp, and the readmes ask for nothing beyond credit. Nobody granted redistribution in
writing, and the art leans on other people's marks (Winamp's base skin is Nullsoft's, now owned by Llama Group;
Nintendo / Game Boy; RealPlayer is RealNetworks', as its author notes). The museum (Internet Archive backed) and
the MIT-licensed Webamp project redistribute them the same way, as fan art kept for preservation. So: fine to
ship with StonePi on your own Pi, credited as above; take any of them out if its author or owner asks. Before
selling StonePi or listing it in a store, get permission or fetch the skins from the museum at install instead
of keeping them in the repo.

## How it reaches the device

The Car Thing keeps its own firmware (Thing Labs custom firmware with ADB). The connector borrows the screen
using the method from [pything](https://github.com/trwy7/pything) (MIT, by trwy7):

1. A device is a Car Thing when `/usr/share/qt-superbird-app/webapp/` exists on it.
2. `adb reverse tcp:8013 tcp:8013`: the device's `localhost:8013` is this service.
3. A redirect page (with a fresh device token from Notify) goes to `/tmp/stonepi-panel` on the device and is
   bind-mounted over the web app folder; the firmware's Chromium is restarted.
4. Switching the panel off, stopping the service, unplugging or rebooting the device restores its own web app.
   The firmware is never written to.

## Pieces

| | |
|---|---|
| `app/connector.py` | adb: detect, reverse, inject, restore, backlight |
| `app/state.py` | config + PIN material from Notify (signed), feeds from each app's `/api/display?items=N`, weather, restart and service details (`unit_details`, Dashboard's signed `/api/internal/units/details`) via Dashboard |
| Web pages (F14) | snapshots captured by Notify (`/api/internal/web/<id>.png`, signed), cached in memory and served at `web/<id>.png`; the panel refreshes one only while it's on screen |
| `app/music.py` | Spotify Web API: access tokens (memory only), Now Playing state cache, commands, artwork cache |
| `app/routes.py` | shell page, fragments (`v/home`, `v/app/<id>`, `v/item/<id>`, `v/weather`, `v/music`, `v/saver`, `v/restart`, `v/restarting`), `tick`, `np`, `music/cmd` (incl. `seek`), `art/<key>`, `web/<id>.png`, `unit-state`, `restart`, `screen`; signed `/internal/status`, `/internal/repair`, `/internal/music/{check,reset,catalog}` for Notify |
| `app/static/js/panel.js` | input map, focus, idle/dimming, page header, clock faces, screensaver styles, Now Playing updates, seek and dial volume, restart progress. Plain ES2017 for the firmware's Chromium (~70) |
| `packages/stonepi_display/stonepi_display/carthing.py` | config schema + store (owned by Notify) |

Listens on **127.0.0.1:8013** only. Unit `stonepi-carthing` (installed, enabled from Notify through
`stonepi-carthing-helper`). Data: `/var/lib/stonepi/carthing` (adb keys, background cache, `music-art/` with the
newest 150 album covers); configs live in Notify's data folder.

**Music (Spotify).** Notify runs the sign-in (one Spotify account for the house, PKCE with your own developer app)
and keeps `SPOTIFY_CLIENT_ID` and `SPOTIFY_REFRESH_TOKEN` in the Vault; this service turns them into access tokens
and saves a rotated refresh token back. Spotify is only read while music is on screen (every 3 s on Now Playing,
10–30 s for a widget or the artwork screensaver). Spec: `ideation/carthing-spotify.md`.

## Development

`scripts/run_dev.py` starts it on 127.0.0.1:8013. Preview at
`http://127.0.0.1:8012/notify/displays/carthing`. Keys stand in for the hardware: `1`–`4` presets, `m`,
`Backspace`/`Escape` back, `Enter` dial press, arrows or mouse wheel for the dial. With `adb` on PATH and a
Car Thing plugged into the PC, the connector takes over its screen too (`CARTHING_ADB=0` turns that off).

Tests: `cd apps/carthing && .venv/Scripts/python -m pytest -q tests`.
