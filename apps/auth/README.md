# Auth

**Part of [StonePi](../../README.md)** — shared household sign-in.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/auth/login |
| Windows `run-dev.bat` | http://127.0.0.1:8011/login |

Not a launcher tile. Issues the shared `stonepi` session cookie so NewsCast, FileServe, EventTrakr, Pinboard, Studio, and the Dashboard share one account.

## Why it’s in StonePi

One login for the portal — no duplicate per-app password databases when `STONEPI_SESSION_SECRET` is set.

## What it does

- Sign-in / sign-out with rate limiting and CSRF protection
- Household users and password hashes (argon2id)
- **App grants** — which launcher apps each person may open
- **Capabilities** — per-app flags (e.g. NewsCast ntfy, Studio LLM / publish)
- Disabled-app list for the portal
- Catalog contract used by Dashboard People UI (`stonepi_auth.catalog`)
- `GET /api/display` — active session count for Status wall (loopback; nginx denies at the edge)

## Factory login

Default **admin** / **admin**. Change the password on Dashboard → Users as soon as you install (banner until you do).

## Platform notes

- Session secret and related material prefer **Vault** / `/etc/stonepi` env bootstrap.
- Exposure mode (`lan` / `public`) is read by apps via `stonepi_auth` helpers; toggle under Dashboard → Settings → General.
- Solo `run-local.bat` apps can run without this service when the session secret is unset.

## Related

- Dashboard (grants UI): [apps/dashboard/README.md](../dashboard/README.md)
- Security: [deploy/SECURITY.md](../../deploy/SECURITY.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
