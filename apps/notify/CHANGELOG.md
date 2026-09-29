# Changelog — Notify

## Unreleased

## 0.1.8 — 2026-09-29

_First release as Notify (was Notifications); the local 0.1.0 is folded in. Carries the platform version (system app)._

- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- **Multiple TRMNL Displays:** each Display has its own webhook (Vault), push interval, device (OG / TRMNL X) and push status; the scheduler pushes each on its own interval. Destinations keeps ntfy only.
- **Screen builder:** drag, drop and resize widgets on the device grid (mouse, touch, keyboard: arrows move, +/− resize, Delete removes) over a real render of the TRMNL screen with sample or live data, e-ink look, and push-size readout.
- **Title bar per Display:** top, bottom or off, titled with the Display's name; date style per Display (e.g. Sat 27 Sep, 14:30 / 27/09/2026 / 09/27/2026 2:30 PM / ISO / time only), in the Pi's local time (was UTC ISO).
- **Universal template:** paste once per plugin; the layout is sent in each push, so layout changes need no re-paste. "Copy template" on each Display.
- **Fixed:** pushes from Notify sent variable names the Liquid never read (`cpu`, `newscast_feeds`, …) so fields showed blank on the device. Notify now uses the shared `stonepi_display.build_merge_variables` contract, checked by tests.
- Upgrade moves the old TRMNL destination onto its Display (legacy template mode until the new template is pasted).
- New dependency: `python-liquid` (preview rendering); Notify now also installs `stonepi_watch`.

- Renamed from Notifications: `apps/notify`, unit `stonepi-notify`, path `/notify/`, data `/var/lib/stonepi/notify`, env `notify.env` / `STONEPI_NOTIFY_URL`. Old `/notifications/` URLs redirect.
- Settings hub (phone) and laptop chips; compact event prefs grouped by app with smaller checks.
- Topbar pill shows configured Display count (was the word “Displays”).
- Mobile form layout for Displays create and Destinations actions.
- Destinations: TRMNL/ntfy cards use full content width (nested settings grid no longer squeezes columns).
- Settings shows the Auth factory-password banner (links to StonePi → Settings → General).
- Lightweight Pi overlay: `push-notify-ui-fixes` (UI files + restart, no pip).
- Initial Notifications platform service: Displays, Destinations (TRMNL + ntfy), event ingest, prefs, history.
- Capability-aware widget presentation (link vs QR).
- Services card (always-on); Dashboard Display settings redirected here.
