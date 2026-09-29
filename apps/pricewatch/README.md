# PriceWatch

Watch specific consumer products and get notified when your target price is met.

Distinct from **PriceScout** (Danish supermarket leaflet specials): PriceWatch monitors named SKUs via PriceRunner Denmark (or a mock source for local/dev).

## Features (V1)

- Create watches with target price, condition, stock requirement, and scan schedule
- Scheduled background checks (APScheduler tick every 5 minutes)
- Strike detection with snapshot + re-arm / pause / resume
- Price history observations
- Strike alerts via **StonePi Notify** (ntfy destination + PriceWatch event prefs)

## Local run

```bat
set PRICEWATCH_MOCK=1
python -m app.serve
```

Or use the monorepo `scripts/run_dev.py` (port **8008**).

Mock catalogue: `fixtures/mock_products.json`.

## Deploy

- Path: `/watch/`
- Port: `8008`
- Unit: `stonepi-pricewatch`
- Data: `/var/lib/stonepi/pricewatch`

Portal tile colour `#b45309` (catalog + App colours). Shared theme `--accent` is not overridden.
