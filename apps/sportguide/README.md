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

- **Now** — on now + next ~24h, filter by sport (image tiles) and Football league chips
- **Sources** — AusSportGuide (AU) + WheresTheMatch (Euro Football); daily auto-refresh or manual Refresh
- **Settings** — city + IANA timezone; About

## Data sources

| Source | Owns |
|--------|------|
| **[AusSportGuide](https://ausportguide.com/)** | AFL, Cricket, Rugby (Kayo / Fox / FTA cues) |
| **[WheresTheMatch](https://www.wheresthematch.com/?sportid=1)** | Football leagues + UK/Euro channels |
| **timezone.football** (fallback) | Euro Football TV week if WheresTheMatch HTML is sparse |

Playwright + Chromium scrapes only — no paid APIs, mocks, or ICS. Labels use **Football** (never “soccer”).

Household personal use on your LAN. Do not redistribute scraped schedules.

## Platform notes

- nginx path `/sports/` → `127.0.0.1:8007`; unit `stonepi-sportguide`; user `stonepi-sport`
- Install installs Playwright browsers (`install_app … playwright`)
- Display scrape: `GET /api/display` (`N on now` in the look-ahead window)

## Pi update

```bat
cmd /c "scripts\push-sportguide-fixes.cmd -Apply"
```

Uploads a posix-path zip + bootstrap, then `sudo bash` applies under `/opt/stonepi` (venv, Playwright Chromium, unit, nginx).
