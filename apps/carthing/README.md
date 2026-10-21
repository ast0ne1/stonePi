# Car Thing panel

Turns a Spotify Car Thing, plugged into the StonePi by USB, into an 800×480 touch + dial control panel:
pages of widgets (small, half or full page: System, mini-app lists, Clock, Clock & weather, Weather, Music, and full-page Web pages;
turned by hand or on a timer), small list → detail views of System, SportGuide, EventTrakr, NewsCast and
Pinboard, a weather screen, a **Spotify remote** (Now Playing, Speakers, presets 1–4, dial volume; no audio
goes through the Pi), a screensaver (clock faces, album artwork while music plays, Bouncing logo, Matrix,
3D Pipes, Flying Through Space, Mystify Your Mind, 3D Flower Box, 3D Maze or a Web page), and restarting StonePi services behind an admin PIN.

Set it up in **Notify → Displays → Car Thing**.

## On the panel

- **Page header.** The strip along the top (time & date, page title, weather, page dots) is set per config, and a page can have its own. **Always**, **Auto-hide** (shows when you arrive, touch, turn or press anything, then hides after 3, 5 or 10 s so the page grows into the space) or **Off**. With Auto-hide or Off the page gets the whole 800×480. On Now Playing and full-page web pages only arriving brings an auto-hidden header back.
- **Web page widget.** A page from Notify → Displays → Web pages, full page, edge to edge under the header. The panel swaps in a fresh snapshot every minute only while it's on screen; the last good one stays up if a capture fails.
- **System.** A health strip (CPU, memory, temperature and disk with meters that turn amber and red; uptime, last backup, host), a **Check** line when something needs attention, then every service as a card, stopped ones first and in red. A service's page shows **Running** or **Not running**, its systemd unit, how long it has been running (or stopped), memory, restarts by itself, Notify's note and its last log lines, with **Restart** and **Open on the Car Thing**. Restart asks for the admin PIN when one is needed and confirms, then shows **Stopped → Starting → Running** and says when the service is back.
- **Now Playing.** The artwork beside the title, artist, album and speaker, over one of four backgrounds: **Glow**, **Artwork colour**, **Blurred artwork** or **Theme** (colours worked out on the device once per track). A progress bar you can tap or drag to seek, then liked, shuffle, previous, play/pause, next, repeat and **⋯** (Speakers and Presets). Controls show always, for 4 s after a touch, or never. The accent comes from the artwork or the panel, with an optional line round the screen. Turning the dial shows a large volume bar. **Nothing playing** shows the presets as tiles. The **liked tick** shows whether the song is in Liked Songs and saves or removes it; sign-ins from before it need **Reconnect Spotify** once (Notify → Car Thing → Setup), and until then the tick only saves.
- **Screensavers.** Clock, **Web page** (the snapshot over the whole screen, refreshed every 1–5 minutes), Bouncing logo, Matrix and the Windows 98 styles 3D Pipes, Flying Through Space, Mystify Your Mind, 3D Flower Box and 3D Maze (drawn on a canvas, each with Speed and its own options), plus album artwork while music plays. **Show time** sits in any corner, top or bottom centre, or the centre, at Small, Medium, Large or Extra large, in the screensaver clock's face, with an optional dark backing; Matrix and the artwork screensaver have their own time settings.

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
