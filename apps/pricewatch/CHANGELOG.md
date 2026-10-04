# Changelog

## 0.0.7 — 2026-10-04

### Added
- **Compare:** picking a search result opens a page listing every retailer's offer (price, delivery, total, stock, trust score), sortable by total, price or trust, with **Watch this** to set up a watch prefilled. Picking a result no longer re-runs the search.
- **Retailer trust scores:** Trustpilot scores via Bright Data's Trustpilot scraper (`gd_lm5zmhwd2sni130p`), looked up in the background (new shops on the next scheduler tick, all shops weekly), never during a check or page load. PriceWatch never contacts Trustpilot directly.
- **PriceRunner rating fallback:** one global switch (off by default) uses PriceRunner's own shop rating when Trustpilot has none, shown in a muted, outlined badge.
- **Minimum reviews** (default 50): a score with fewer reviews is shown but doesn't count toward a watch's minimum.
- Badges on Compare, the watch page (strike card and current offers) and dashboard rows ("at Elgiganten ★ 3.5 · Trustpilot"); strike alerts include the badge.
- **Per-watch minimum retailer score** (Any / 3.5+ / 4.0+ / 4.5+), **Require a rating**, and **If a low-rated shop is under target: Ignore / Alert me with a warning**. Low-rated offers are never strikes; the watch page shows a "Cheaper, low-rated" card. Warnings are a new Notify event, `pricewatch.low_rated_offer`, sent once per shop (again only if it gets cheaper or another low-rated shop leads). If a trusted strike fires in the same check, the cheaper low-rated shop is one extra line in the strike alert instead.
- **Target includes delivery:** per-watch switch; strikes, "current lowest" and the offer highlight all follow it. New watches default on.
- **Edit a watch** (target, condition, stock, delivery, trust options, schedule) from its page; saving re-checks it.
- **Settings → Trust scores:** Bright Data key status, **Copy from EventTrakr**, paste/remove key, the two source switches, minimum reviews, refresh interval, the **Bright Data monthly limit shared with EventTrakr** (default 5,000 records, the free tier) and the day of the month it resets (default 1), with this period's usage per app, **Refresh scores**, and a **Retailers** list (both scores, status, last update, a link to the Trustpilot page) where a shop's web address can be corrected.
- Shops are recorded from PriceRunner's offer data (merchant id, web address from the merchant's Klarna link, PriceRunner rating) in a new `merchants` table.

### Changed
- Scheduled checks, admin "check all" and alerts skip owners StonePi removed from PriceWatch or who lost the permission (watches are kept). New-watch defaults, history retention and Bright Data lookup controls (enable, interval, refresh, re-queue) are admin-only.
- Trust lookups book 3 records per shop against the shared Bright Data limit before calling (Bright Data doesn't strictly keep to one review per shop) and settle the real count after; at most 50 shops per run. Scheduled lookups keep to the period's share so far, **Refresh scores** can use the rest, and a refused request (bad key, network) doesn't count.
- Watches are private to whoever created them; admins still see and manage every watch. Before, any signed-in household member could edit, pause or delete anyone's watch.
- Existing watches keep comparing product price only (delivery off) and have no minimum score, so nothing changes under them until edited.
- Strike price comparisons use the watch's price basis; stored strikes from 0.0.6 still compare correctly.
- A button's own `data-confirm` is honoured and the form re-submits with that button (fixes Settings → Sources "Remove" skipping its confirm).

### Robustness
- A failed Bright Data call (network, key, service down) leaves shops queued with their last known scores and pauses scheduled lookups for an hour; **Refresh scores** ignores the pause. A single shop's lookup error is retried after 6 hours instead of on the weekly cycle.
- Lookups run in their own thread, so a slow Bright Data job never delays watch checks.
- Product links and images passed to Compare / the watch form are accepted only as `http(s)` URLs.

### Needs on the Pi
- Push the platform-side pieces too (the overlay only copies `apps/pricewatch`): the `stonepi_contracts` catalog entry, Notify's default approval and Dashboard's vault list. Without them PriceWatch works, but nobody can subscribe to low-rated warnings.
- Approve **Cheaper offer from low-rated shop** once in Notify → Events (only fresh installs get it pre-approved), **and** each person who wants warnings ticks it in Dashboard → Notifications — personal alerts only reach people who ticked that event.
- The key lives in the vault as `PRICEWATCH_BRIGHTDATA_API_KEY` (Dashboard → Settings → Vault, "Data providers"); set it there, paste it in PriceWatch, or use **Copy from EventTrakr** (copies the value into PriceWatch's own slot; there is no automatic fallback to EventTrakr's key).

### Fixed
- A watch's check interval is limited to the offered options (a crafted 0 would have checked it every tick).
- The low-rated warning repeated when a shop's price went up and back down; it now compares with the price it last warned at.
- "Current lowest" went blank when no offer passed the stock/condition filters; it shows the cheapest listing again, as in 0.0.6.

### Security
- "Manage watches" and "Strike alerts" are enforced (both on by default): without them, people can view but not add, search, compare, edit, pause, check or delete watches, and alerts aren't sent.
- The shared Bright Data limit, reset day and key (set, copy, remove) are admin-only; source managers keep the trust-score options.
- Locks itself if the session secret is missing outside dev.

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
