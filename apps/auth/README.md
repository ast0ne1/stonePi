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
- **Capabilities** — per-app flags (e.g. Studio LLM / publish), plus the platform **Phone alerts** permission
- Disabled-app list for the portal
- Catalog contract used by Dashboard Users UI (`stonepi_auth.catalog`)
- `GET /api/display` — active session count for Display widgets (loopback; nginx denies at the edge)
- The signed-in status page uses the shared StonePi header (app switcher, avatar menu with Log out)

## Factory login

The installer creates **admin** with a random password, printed at the end and saved in `/etc/stonepi/initial-admin.txt` (dev and solo runs fall back to **admin** / **admin** when `ADMIN_PASSWORD` is unset). Change it under Dashboard → Settings → General → Your password; a banner shows until you do.

## Platform notes

- Session secret and related material prefer **Vault** / `/etc/stonepi` env bootstrap.
- Exposure mode (`lan` / `public`) is read by apps via `stonepi_auth` helpers; toggle under Dashboard → Settings → Network.
- Solo `run-local.bat` apps can run without this service when the session secret is unset.

## Related

- Dashboard (grants UI): [apps/dashboard/README.md](../dashboard/README.md)
- Security: [deploy/SECURITY.md](../../deploy/SECURITY.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
