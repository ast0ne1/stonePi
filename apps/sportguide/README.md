# SportGuide

**Part of [StonePi](../../README.md)** — what’s on sports TV and streams for your household LAN.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/sports/ |
| Windows `run-dev.bat` | http://127.0.0.1:8007/ |

Shared StonePi sign-in. Non-admins need the **SportGuide** app grant. Optional cap: **Refresh schedules** (`can_refresh`).

## Why it’s in StonePi

See AFL, Cricket, Rugby, and Football kick-offs and channels without juggling guide sites. Times follow your city timezone.

## What it does

- **Now** — on now + next ~24h, filter by sport (image tiles) and Football league chips; Football cards show each team's badge above its name (initials when there is no badge)
- **Sources** — AusSportGuide (AU) + timezone.football and WheresTheMatch (Football); daily auto-refresh or manual Refresh (the popup asks "Refresh now?" with the detail below)
- **Settings** — city + IANA timezone; About

## Data sources

| Source | Owns |
|--------|------|
| **[AusSportGuide](https://ausportguide.com/)** | AFL, Cricket, Rugby (Kayo / Fox / FTA cues) |
| **timezone.football** | Football fixtures, competitions and channels in the Pi's own country; team badges |
| **[WheresTheMatch](https://www.wheresthematch.com/?sportid=1)** | UK Football channels, merged into the same match (marked "(UK)" outside the UK) |

AusSportGuide is scraped with Playwright + Chromium. The two Football guides are read over plain HTTP, with Chromium only when a site refuses a plain request. No paid APIs, mocks, or ICS. Labels use **Football** (never “soccer”).

### Team badges

- Badges come from timezone.football (crests.football-data.org and TheSportsDB images). For matches only WheresTheMatch lists, its club crest is guessed from the team page and is often missing.
- After a refresh, missing badges are downloaded to the Pi once: from those hosts only, PNG, GIF, JPEG or WebP up to 300 KB each (no SVG). They are served from `/badges/`, so cards never load images from other sites.
- A badge that couldn't be fetched is asked for again after a week. Until then the card shows the team's initials.
- Listings carry `home`, `away`, `home_badge` and `away_badge` columns (added on start).

Household personal use on your LAN. Do not redistribute scraped schedules.

## Platform notes

- nginx path `/sports/` → `127.0.0.1:8007`; unit `stonepi-sportguide`; user `stonepi-sport`
- Install installs Playwright browsers (`install_app … playwright`)
- Display scrape: `GET /api/display` (`N on now` in the look-ahead window)

## Updating on the Pi

SportGuide updates with StonePi: install a new release from Dashboard → Settings → Updates. The installer sets up its venv, Playwright's Chromium, the service and nginx.
