# StonePi app platform wiring

New apps must be first-class citizens: launcher tile, nginx path, systemd,
install, local `run_dev`, health. Reference: PriceScout (`apps/pricescout`,
port `8006`, prefix `/prices/`).

## Identity sheet (fill before coding)

| Field | Example |
|-------|---------|
| `app_id` | `pricescout` |
| Display name | `PriceScout` |
| Path prefix | `/prices/` |
| Port | `8006` |
| Unit | `stonepi-pricescout` |
| Service user | `stonepi-prices` |
| Health | `/healthz` |
| Data dir | `/var/lib/stonepi/pricescout` |
| Env file | `/etc/stonepi/pricescout.env` |

Pick a free port; keep `packages/stonepi_auth/.../session.py` `allowed_ports`
in sync (dev + cookie trust). Taken: 8001–8007, 8010–8011 (SportGuide = 8007 `/sports/`).

## Files to touch

1. **`packages/stonepi_auth/stonepi_auth/catalog.py`**
   - Entry: `id`, `name`, `path`, `port`, `unit`, `health`, `color`,
     `capabilities[]`, `launcher`, `icon`, `description`.
2. **`deploy/nginx/stonepi.conf`**
   - `upstream stonepi_{id} { server 127.0.0.1:PORT; }`
   - `location /prefix/ { proxy_pass http://stonepi_{id}/; … }` (match sibling
     proxy headers).
3. **`deploy/systemd/stonepi-{id}.service`**
   - `User=` / `WorkingDirectory=/opt/stonepi/apps/{id}`
   - `EnvironmentFile=` stonepi.env + app.env
   - `ExecStart=.../.venv/bin/python -m app.serve`
4. **`deploy/install.sh`**
   - `ensure_user`, data mkdir, `write_env` / `ensure_env_key`,
     `install_app {id} {user}`, `ensure_writable_data`, vault group membership,
     enable/restart unit list, backup `APPS=` string.
5. **`scripts/run_dev.py`**
   - Tuple `(id, path, port, prefix, serve cmd)` + vault allowlist + banner URL.
6. **Dashboard**
   - `apps/dashboard/app/templates/_icons.html` — app glyph.
   - Optional badge map in `static/js/app.js` if product badges exist.
7. **`apps/{id}/`**
   - `requirements.txt`, `app/serve.py` / `main.py`, `README.md`, `CHANGELOG.md`,
     `__version__`.
8. **Docs**
   - Root `README.md` app table row when the app is a public citizen.

## Env keys (typical)

```
PORT=
STONEPI_PREFIX=/prefix
STONEPI_APP_ID={id}
STONEPI_DATA_DIR=/var/lib/stonepi/{id}
PUBLIC_BASE_URL=http://{host}.local/prefix
# plus auth/session from stonepi.env
```

Honor prefix in redirects and TemplateResponse context (`app_prefix`,
`public_origin`).

## Health & Display

- Implement `GET /healthz` → `{ ok, service }`.
- If the app should appear on TRMNL/Display scrapes: `GET /api/display` JSON
  shape consistent with siblings; ensure public nginx deny still applies to
  `/api/display` at the edge.

## Auth capabilities

Declare only real gates in catalog `capabilities`. Enforce with
`user.has_capability(app_id, cap)` / `can_access(app_id)`. Default new household
users: Dashboard only until an admin grants the app.

## Local data

- Runtime under `STONEPI_DATA_DIR` (Pi) or `apps/{id}/data` (dev).
- Gitignore `data/`, `.venv/`, `__pycache__/`, `.env`.
- Never ship SQLite, caches, or vault material in release zips.

## Pi update helpers

Ad-hoc overlays are gitignored (`scripts/push-*-fixes.*`). For a new app:

1. `apply-{id}-on-pi.sh` — rsync app tree into `/opt/stonepi`, install unit,
   nginx, catalog/icon/related routes, venv `pip install`, enable/restart.
2. `push-{id}-fixes.ps1` + `.cmd` — stage tree, **Python zip with posix paths**
   (Windows `ZipFile::CreateFromDirectory` backslashes break on the Pi), scp
   zip + bootstrap, `ssh -t` → `sudo bash bootstrap`.
3. Bootstrap: `python3 -m zipfile -e`, strip CR, run apply.

Interactive CMD is required for scp/ssh/sudo passwords on Windows.

## Release

- Prefer full `deploy/install.sh` path for first install on a Pi.
- Overlay push for iterate-on-device.
- Official releases: `scripts/build_release_zips.py` + GitHub assets — do not
  attach push helpers to releases.

## Smoke after wire

1. `run-dev.bat` — app URL + Home launcher tile.
2. SSO login → app → Home returns to same host.
3. Theme/palette match dashboard without Save in the app.
4. Phone width: bottom nav usable; Settings chips show icons.
5. On Pi: `systemctl is-active stonepi-{id}`; `curl -s localhost:PORT/healthz`.
