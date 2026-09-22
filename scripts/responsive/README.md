# Responsive viewport smoke (optional)

Asserts `document.documentElement.scrollWidth <= window.innerWidth` on one
route per surface at the QA widths from [deploy/responsive-audit.md](../../deploy/responsive-audit.md).

## Setup

```bash
cd scripts/responsive
npm init -y
npm i -D @playwright/test
npx playwright install chromium
```

Set a base URL that already has a session cookie if routes require auth
(or point at public login only):

```bash
set STONEPI_BASE_URL=http://127.0.0.1:8000
npx playwright test
```

Default routes hit the dashboard origin; app paths assume nginx prefixes
(`/news/`, `/files/`, …). Override with `STONEPI_ROUTES` JSON if needed.

This is **optional** Wave 4 verification — manual DevTools resize remains the
primary sign-off.
