# EventTrakr

**Part of [StonePi](../../README.md)** — local events, favourites, and calendar sync in one place.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/events/ |
| Windows `run-dev.bat` | http://127.0.0.1:8003/ |

Shared StonePi sign-in. On the Pi, **Users** and **Updates** live in the Dashboard. Playwright Chromium is used for some web sources (Eventbrite and similar) — a **Pi 4/5** is recommended.

## Why it’s in StonePi

Complements NewsCast (“what’s worth reading”) with “what’s on near us” — a 7-day agenda, favourites, and calendar hand-off for the household.

## Platform notes

- Shared StonePi design system; theme (light, dark or system) is in the avatar menu.
- **Display / TRMNL:** `GET …/api/display` returns next favourite label and `favourites` count for the EventTrakr widget on TRMNL and Car Thing Displays.
- **Vault keys:** `BRIGHTDATA_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
- Under StonePi, bind is loopback; nginx serves `/events/`. Per-app HTTPS is for **solo** runs only.
- Public ICS / overview behaviour tightens when exposure is **internet-facing** — see [deploy/SECURITY.md](../../deploy/SECURITY.md).

## What it does

- **7-day agenda** — Today through +6d; Day, Category, Free and Source filter chips; category and price pills on each event
- **Source catalog + custom feeds** — Eventbrite, Meetup, ICS/webcal, community sites; keyword include/exclude; global or per-source schedule
- **Instagram discovery** — track public accounts (Sources → Instagram); Bright Data poll + local OCR; **Review** queue; high-confidence auto-create (Settings → Discovery)
- **Favourites** — star events; optional Google Calendar forward; one-click Google Calendar link; `.ics` download; live **webcal** feed
- **Public overview** — optional shareable `/u/<username>` agenda without login
- **Search** — date + location across configured sources
- **Bright Data** (optional) — Facebook Events discover/scrape and Instagram post discovery when an API key is set; otherwise catalog/ICS/browser paths still work

## Pills

On Agenda, Search and public agendas:

- **Price pill** with a tag icon: green with the amount for paid events (parsed from the price text, e.g. "DKK 150" or "120-180 kr"), blue and icon only for free, grey and icon only when no price is listed. The full price, "Free" or "Price not listed" shows on hover and to screen readers.
- **Category pill** with an icon and a colour (also on the Sources catalog). Built-in categories have their own icon and default colour: Music (note, purple), Culture (columns, amber), Community (people, sand), Food & Drink (cup, red), Business (briefcase, grey), Arts (palette, green), Sports (ball, blue), Family (heart, sand), General (tag, grey). Categories you add get a tag and a colour picked from their name, so each keeps the same colour everywhere.

## Integrations (this app)

| Integration | Role |
|-------------|------|
| **Bright Data** | Facebook Events + Instagram posts (Web Scraper API; shared free monthly credits) |
| **Tesseract OCR** | Local poster text extraction (optional system package `tesseract-ocr`) |
| **Google Calendar** | OAuth forward of favourites |
| **ICS / webcal** | Apple, Outlook, native calendars |
| **TRMNL** | Display block for upcoming favourites |
| **Playwright** | Headless Chromium for sites that need a real browser |

## Settings highlights

- **General → Agenda view** (per person): tick which filter chips show above your Agenda — **Category chips**, **Free chip**, **Source chips** (all on by default). Category and Free share a row, which goes only when both are off; Day chips always show. A hidden filter also stops filtering, so an old link can't leave you with a filter you can't see.
- **Categories** (admins): pick each category's pill colour from Purple, Blue, Green, Sand, Amber, Red or Grey; **Auto** keeps the default. Icons stay fixed. Stored in the `categories.color` column, added on start.
- Privacy (public vs private overview)
- Schedule / location defaults
- Discovery (Instagram tracking, poll interval, OCR, auto-create, social ntfy)
- Calendar Integrations (Google OAuth client) and Data Providers (Bright Data key and the shared monthly limit)
- Network / TLS tabs matter mainly for **solo** runs

## Solo / standalone run (optional)

```cmd
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m app.serve
```

For Instagram OCR on the Pi, also install the system package (Debian/Ubuntu): `sudo apt install tesseract-ocr`.

Open http://127.0.0.1:8085 — default **admin** / **admin**. Prefer `scripts\run-dev.bat` from the StonePi root for SSO with the rest of the portal.

## Tests

```cmd
.venv\Scripts\pytest.exe -v
```
