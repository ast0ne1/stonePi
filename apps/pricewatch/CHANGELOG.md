# Changelog

## Unreleased

## 0.0.6 — 2026-09-29

_First release in git, on the shared user-app version. Local builds were numbered 0.1.0–0.1.3; none were released, so their notes are folded in below._

### Changed
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- systemd unit: Nice/CPU/IO weight + MemoryMax=400M (Pi resource contention)
- Empty watches state no longer duplicates the hero Add watch button.
- Settings shows the Auth factory-password banner (links to StonePi → Settings → General).
- Strike alerts go through Notify (`emit_event`); Settings → Notifications points at Destinations / Event prefs (no local ntfy form).
- Rebuild chrome from the NewsCast baseline (CSS/JS were cloned from SportGuide/Studio and PriceScout): shared shell, topbar Refresh (checks every active watch), toasts, confirm sheet, sheet hoist.
- Watches grouped by status in NewsCast-style group cards with status dots; page-status strip + help sheet.
- Watch detail: section-heading icons, fact list, offer rows (replaces table), two-column panels on laptop+.
- Settings: phone hub → section drill-in, chip bar on laptop+; nav/settings icons via `_filter_icon.html`.

### Earlier local builds (0.1.0–0.1.2, never released)
- PriceRunner DK: switch to Klarna-era `/dk/api/...` suggest + offers endpoints (old search-edge URLs 404).
- Sources: mock is no longer seeded on normal installs; removable from Settings when present. Auto-drops unused mock on boot unless `PRICEWATCH_MOCK=1`.
- Initial PriceWatch V1: watches, PriceRunner DK source, mock source, scheduler, strikes, ntfy, StonePi chrome.
