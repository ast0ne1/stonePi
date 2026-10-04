# Car Thing panel

Turns a Spotify Car Thing, plugged into the StonePi by USB, into an 800×480 touch + dial control panel:
pages of widgets (small cards, half- or full-page System, mini-app lists and Clock & weather; turned by hand
or on a timer), small list → detail views of System, SportGuide, EventTrakr, NewsCast and Pinboard, a clock
screensaver, and restarting StonePi services behind an admin PIN.

Set it up in **Notify → Displays → Car Thing**. Plan and decisions: [`ideation/carthing-plan.md`](../../ideation/carthing-plan.md).

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
| `app/state.py` | config + PIN material from Notify (signed), feeds from each app's `/api/display?items=N`, weather, restart via Dashboard |
| `app/routes.py` | shell page, fragments (`v/home`, `v/app/<id>`, `v/item/<id>`, `v/saver`, `v/restart`), `tick`, `restart`, `screen` |
| `app/static/js/panel.js` | input map, focus, idle/dimming, clock faces. Plain ES2017 for the firmware's Chromium (~70) |
| `packages/stonepi_display/stonepi_display/carthing.py` | config schema + store (owned by Notify) |

Listens on **127.0.0.1:8013** only. Unit `stonepi-carthing` (installed, enabled from Notify through
`stonepi-carthing-helper`). Data: `/var/lib/stonepi/carthing` (adb keys, background cache); configs live in
Notify's data folder.

## Development

`scripts/run_dev.py` starts it on 127.0.0.1:8013. Preview at
`http://127.0.0.1:8012/notify/displays/carthing`. Keys stand in for the hardware: `1`–`4` presets, `m`,
`Backspace`/`Escape` back, `Enter` dial press, arrows or mouse wheel for the dial. With `adb` on PATH and a
Car Thing plugged into the PC, the connector takes over its screen too (`CARTHING_ADB=0` turns that off).

Tests: `cd apps/carthing && .venv/Scripts/python -m pytest -q tests`.
