# EventTrakr

**Part of [StonePi](../../README.md)** — local events, favourites, and calendar sync in one place.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/events/ |
| Windows `run-dev.bat` | http://127.0.0.1:8003/ |

Shared StonePi sign-in. On the Pi, **Users**, **Updates**, and **Backup** live under Dashboard Settings. Playwright Chromium is used for some web sources (Eventbrite and similar) — a **Pi 4/5** is recommended.

## Why it’s in StonePi

Complements NewsCast (“what’s worth reading”) with “what’s on near us” — a 7-day agenda, favourites, and calendar hand-off for the household.

## Platform notes

- Themes/palettes match other StonePi apps.
- **Display / TRMNL:** `GET …/api/display` returns next favourite label and `favourites` count for Status wall / Household.
- **Vault keys:** `BRIGHTDATA_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
- Under StonePi, bind is loopback; nginx serves `/events/`. Per-app HTTPS is for **solo** runs only.
- Public ICS / overview behaviour tightens when exposure is **internet-facing** — see [deploy/SECURITY.md](../../deploy/SECURITY.md).

## What it does

- **7-day agenda** — Today through +6d, category chips, free/price badges
- **Source catalog + custom feeds** — Eventbrite, Meetup, ICS/webcal, community sites; keyword include/exclude; global or per-source schedule
- **Favourites** — star events; optional Google Calendar forward; one-click Google Calendar link; `.ics` download; live **webcal** feed
- **Public overview** — optional shareable `/u/<username>` agenda without login
- **Search** — date + location across configured sources
- **Bright Data** (optional) — Facebook Events discover/scrape when an API key is set; otherwise catalog/ICS/browser paths still work

## Integrations (this app)

| Integration | Role |
|-------------|------|
| **Bright Data** | Facebook Events API scrape when keyed |
| **Google Calendar** | OAuth forward of favourites |
| **ICS / webcal** | Apple, Outlook, native calendars |
| **TRMNL** | Display block for upcoming favourites |
| **Playwright** | Headless Chromium for sites that need a real browser |

## Settings highlights

- Appearance (theme + palette)
- Privacy (public vs private overview)
- Schedule / location defaults
- Integrations: Bright Data key, Google OAuth client
- Network / TLS tabs matter mainly for **solo** runs

## Solo / standalone run (optional)

```cmd
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m app.serve
```

Open http://127.0.0.1:8085 — default **admin** / **admin**. Prefer `scripts\run-dev.bat` from the StonePi root for SSO with the rest of the portal.

## Tests

```cmd
.venv\Scripts\pytest.exe -v
```
