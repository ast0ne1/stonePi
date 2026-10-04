# Changelog

## 0.0.7 — 2026-10-04

### Fixed
- Football kick-offs honour a time-zone offset in timezone.football's data, and a week spanning New Year keeps the right year.
- **Now on phones:** the football league filters were squeezed to an invisible strip (and long lists could clip) because the page grid let a scrolling row shrink to nothing. Rows now keep their height; league filters are one swipeable row on phones and wrap from tablet width up.
- Football matches were filed by club names ("Man United" → Premier League even in the FA Cup, "International Friendlies" → Serie A). The competition from the listing decides the league now.

### Changed
- Kick-off alerts stop for people who no longer have SportGuide in StonePi; manual refresh is refused without a session secret outside dev.
- **Football channels from both guides, merged per match.** timezone.football (channels in the Pi's own country, picked by the site from its IP) is now a full source, not a fallback, and WheresTheMatch adds UK channels (marked "(UK)" outside the UK). Both are read from their HTML structure instead of page text, over plain HTTP with the browser as a fallback. Matches show the channel, "Club's official stream", "Channel TBC" or "Not on TV in <country>" instead of "Check guide", and open the match's own page. Adds `beautifulsoup4`.

### Added
- `/api/display?items=N` (loopback) adds `card` + `items` for the Car Thing panel: watched teams first, then by start time, with channels in the detail. The plain call is unchanged.

### Security
- On a platform install with no session secret every page returns 503 instead of opening up (solo runs unchanged).

## 0.0.6 — 2026-09-29

_Moves from 0.0.2 to the shared user-app version, 0.0.6 (numbers in between were skipped)._

### Added
- Settings shows the Auth factory-password banner (links to StonePi → Settings → General)
- Teams to Watch (Settings) with approaching alerts via Notify (5‑minute tick + post-refresh check); Settings → Notifications points at Destinations / Event prefs
- `/api/display` includes `watched_teams` and `next_watched` for Displays

### Changed
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- Daily listing refresh is scheduler-only (removed from GET `/`); Chromium collectors hold the shared `stonepi_browser` lock
- SQLite uses WAL + busy_timeout=5000
- systemd unit: Nice/CPU/IO weight + MemoryMax=400M (Pi resource contention)
- Icon-only Refresh at ≤420px (was ≤559px) to match NewsCast
- **Teams to Watch:** search and add from a built-in list of standard teams — AFL, NRL, Super Rugby and rugby nations, cricket nations plus BBL and IPL, and football (Premier League, A-League, big European and Danish clubs, national sides) — plus any team in the current listings, instead of typing names separated by commas. Short names work ("Man Utd", "Spurs"); a name in more than one sport asks which ("Australia · Cricket"). A team only matches its own sport, and each side of a fixture is compared ("Melbourne" no longer matches North Melbourne games). Existing teams carry over; ones with no games in the guide right now are marked.

## 0.0.2 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px** (aligned with portal)
- Laptop/desktop: main column fills width beside the rail; Settings uses full column; Now listings 2–3 columns

## 0.0.1

- Initial SportGuide citizen: Now feed, Sources (AusSportGuide + WheresTheMatch), Settings city/timezone.
