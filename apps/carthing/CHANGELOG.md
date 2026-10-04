# Changelog — Car Thing panel

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
