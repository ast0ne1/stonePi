# PriceScout

**Part of [StonePi](../../README.md)** — weekly supermarket offers for your household LAN.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/prices/ |
| Windows `run-dev.bat` | http://127.0.0.1:8006/ |

Shared StonePi sign-in. Non-admins need the **PriceScout** app grant. Optional caps: **Manage sources** (`can_manage_sources`), **Offer alerts** (`can_use_alerts`, reserved).

## Why it’s in StonePi

Compare this week’s tilbuds across Netto, Lidl, 365discount, føtex, and Kvickly without five retailer apps. Shopping list + best-price hints stay on your Pi.

## What it does

- **Offers** — latest deals with store and category filters
- **Search** — cross-store comparison
- **Shopping List** — checklist plus best-price summary from current offers; optional **Send to Pinboard** reminder
- **Sources** — enable/disable supermarket dealers on the shared **eTilbudsavis** feed; Refresh pulls JSON offers
- **Settings / About** — postcode (for optional madspild), data clear, version

## Data sources

| Source | Role |
|--------|------|
| **eTilbudsavis / Tjek** | Primary weekly leaflet offers (JSON). One feed; each “source” is a dealer filter. |
| **Salling food-waste** (optional) | Netto / føtex clearance (“madspild”). Needs free API token + postcode. |
| **Pinboard** (optional) | Shopping list → household reminder via `POST /api/reminder` (needs Pinboard grant). |

Set `SALLING_API_TOKEN` in Dashboard → Settings → Vault (or env). Optional `TJEK_API_KEY` if you have a developer key. `PRICESCOUT_MOCK=1` forces seed data.

Household personal use on your LAN. Campaign prices only — not a full shelf catalogue. Do not redistribute retailer data.

## Platform notes

- nginx path `/prices/` → `127.0.0.1:8006`; unit `stonepi-pricescout`
- Display scrape: `GET /api/display` (denied at nginx edge when wired)
