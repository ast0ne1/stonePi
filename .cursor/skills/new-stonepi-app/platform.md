# StonePi app platform wiring

A new app is a first-class citizen: catalog entry (Home tile, switcher, People
grants, Updates, release zip), nginx path, systemd unit, installer, backup,
Recover, CLI, run-dev, README. Python skeleton reference: **Ripple**
(`apps/ripple`, port 8017, prefix `/ripple/`). The list of registration points
below is what Radio (2026-10-28) and Ripple (0.5.0) needed; check each against
the code when you add an app, since the files change.

## Identity sheet (fill before coding)

| Field | Example (Ripple) |
|-------|------------------|
| `app_id` | `ripple` |
| Display name | `Ripple` |
| Path prefix | `/ripple/` |
| Port | `8017` |
| Unit | `stonepi-ripple` |
| Service user | `stonepi-ripple` (older apps use short names: `stonepi-news`, `stonepi-pin`) |
| Health | `/healthz` |
| Data dir | `/var/lib/stonepi/ripple` |
| Env file | `/etc/stonepi/ripple.env` (plus the shared `/etc/stonepi/stonepi.env`) |
| Icon | `packages/stonepi_brand/static/icons/apps/ripple.svg` + PNGs |

**Ports taken:** 8001–8009 (NewsCast, FileServe, EventTrakr, Pinboard, Studio,
PriceScout, SportGuide, PriceWatch, Library), 8010 Dashboard, 8011 Auth, 8012
Notify, 8013 Car Thing, 8014 Kiwix (loopback, `/library/read/`), 8015 AgentLink,
8016 Radio, 8017 Ripple, 8018 Odio API and 8080 myMPD (loopback, Radio's
player), 8099 Recover. Next free: **8019**. Apps listen on `127.0.0.1` only.

## App layout

```
apps/{id}/
  app/
    __init__.py        # __version__, __asset_rev__ (computed), __github_user__, __github__
    config.py          # pydantic-settings EnvSettings, ROOT_DIR, DATA_DIR
    main.py            # FastAPI app, PrefixMiddleware, /static, mount_brand_fonts, router, add_platform_lock
    serve.py           # uvicorn.run(app, host=env.host, port=env.port, timeout_keep_alive=15, access_log=False)
    routes.py          # router, templates, _session_secret/_settings/_user/_gate/_render, pages, POSTs
    db.py              # SQLite under DATA_DIR, versioned migrations (Ripple) — if the app stores data
    templates/
      base.html  _theme_boot.html  _brand_mark.html  _icons.html  denied.html  settings.html  …
    static/css/app.css
    static/js/app.js
  tests/               # pytest, TestClient on routes.router
  requirements.txt     # pinned: fastapi, uvicorn[standard], pydantic-settings, python-multipart, jinja2, httpx …
  README.md
  CHANGELOG.md
```

Gitignore `data/`, `.venv/`, `__pycache__/`, `.env`, `.pytest_cache/`. Shared
packages (`stonepi_auth`, `stonepi_contracts`, `stonepi_vault`, `stonepi_update`,
plus any extras) are installed by the installer and run-dev, not listed in
`requirements.txt`.

### `config.py` (Ripple)

```python
class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")
    host: str = "127.0.0.1"
    port: int = 8017
    auth_url: str = "http://127.0.0.1:8011"
    public_origin: str = Field(default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_PUBLIC_ORIGIN", "PUBLIC_ORIGIN"))
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    notify_url: str = Field(default="http://127.0.0.1:8012", validation_alias=AliasChoices("STONEPI_NOTIFY_URL", "NOTIFY_URL"))
    routing: str = "path"
    stonepi_prefix: str = Field(default="", validation_alias=AliasChoices("STONEPI_PREFIX", "PREFIX"))
    data_dir: Path = Field(default=ROOT_DIR / "data", validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
```

### `main.py` (Ripple)

```python
app = FastAPI(title="StonePi MyApp")
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    app.add_middleware(PrefixMiddleware, prefix=_prefix)
_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
mount_brand_fonts(app)   # /assets/fonts in run-dev; nginx serves it on the Pi
if _prefix:
    # The prefixed path too, as nine apps do (kept, decided 2026-10-31).
    app.mount(f"{_prefix}/static", StaticFiles(directory=_static), name="static_prefixed")
app.include_router(router)
# A platform install without a session secret locks (503) instead of running open.
platform_lock = add_platform_lock(app, lambda: _app_routes._session_secret())
```

## Routes: auth, rendering, forms

Copy Ripple's `routes.py` helpers:

| Helper | Does |
|--------|------|
| `_session_secret()` | Vault first (`stonepi_vault.get_secret("STONEPI_SESSION_SECRET", …)`), then env |
| `_settings()` | `PlatformSettings(enabled, session_secret, app_id, prefix, auth_url=browser_auth_url(...), public_origin, hostname)` |
| `_user(request)` | `decode_session(request.cookies.get(COOKIE_NAME), secret)` |
| `_allowed(user)` | Standalone: yes. Else `user.is_admin or user.can_access(APP_ID)`: **admins open every app** without a grant (decided 2026-10-31, matching the README's "Admins can do everything"; `PlatformUser.can_access` itself is being changed to say so, after which `user.can_access(APP_ID)` alone is enough) |
| `_is_admin(user)` | Standalone: yes. Else `user.is_admin` |
| `_gate(request, back)` | `(user, stop)`: signed out → redirect to `login_url(_settings(), f"{prefix}{back}")`; no grant → `denied.html` with 403 |
| `_render(...)` | Context: `user`, `active`, `csrf_token`, `stonepi_home_url` (`portal_home_url`), `error`/`message` from `?err=`/`?msg=`, `is_admin`, `alerts_bell_state`; sets the CSRF cookie |
| `_form_error(back, msg)` | 303 back with `?err=` |
| `/logout` | `RedirectResponse(logout_url(_settings(), "/"), 303)` (GET and POST) |

Every page and every POST starts with `user, stop = _gate(request, back)`.
Access is decided from the session cookie on every request, so removing a grant
takes effect at once. Capabilities (catalog `capabilities`):
`user.has_capability(APP_ID, "can_x")` (Pinboard `_can`).

`denied.html`: a `.card` saying "No access to MyApp", "Ask an admin to add
MyApp for you in StonePi → People.", and a "Back to StonePi" link to
`{{ stonepi_home_url }}/`. Rendered with `active=""` so no nav shows.

### CSRF

Double-submit cookie from `stonepi_auth.csrf`:

- GET: `csrf = csrf_from_request(request.cookies)`; put `csrf_token` in the
  context; `set_csrf_cookie(response, csrf, secure=request_is_https(request))`.
- Every form: `<input type="hidden" name="csrf_token" value="{{ csrf_token }}" />`;
  pass `csrf_token=csrf_token` to `header_tools` so Log out carries it.
- Every POST:

  ```python
  if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):        # cookie VALUE
      return _form_error(back, "Form expired. Try again.")
  # or: csrf_ok_request(request.cookies, form_token=…, header_token=request.headers.get("X-StonePi-CSRF"))
  ```

  `csrf_ok(cookie_value, form_token)` compares two strings. Passing
  `request.cookies` (the dict) as the first argument makes every form fail.
  JSON calls send the token in `X-StonePi-CSRF`.

### Redirects

- `next=` / `back` inside the app: only paths starting with `/` and not `//`
  (Pinboard `_safe_next`, NewsCast `auth.safe_next`); anything else → `/`.
- Cross-app or absolute: `stonepi_auth.session.safe_next(value)` (LAN hosts and
  the StonePi ports only; add a new app's port there, see below).
- Links to other apps: `stonepi_auth.header.app_page_url(home, "notify", "displays")`
  (works behind nginx and in run-dev).

### Prefix

Behind nginx the app is served under `/myapp/` with the prefix stripped; in
run-dev it runs at its own port with no prefix. `PrefixMiddleware` rewrites
`href`/`src`/`action`/`Location` and `fetch("/…")` in HTML and JS. Write root
paths everywhere. JSON bodies aren't rewritten: wrap URLs you return in JSON
with `prefix_path(path, clean_prefix(env.stonepi_prefix))` (Ripple `_app_url`).

### Per-person data

Store the owner as the Auth user id (`user.user_id`, a UUID), with the display
name copied for showing. Standalone (no secret) uses a fixed local owner
(Ripple `("local", "You")`). Never key data by username.

## Service-to-service

### Signed internal calls (`stonepi_auth.internal`, v2)

- Endpoints under `/api/internal/…`, reached only on loopback (nginx returns 404
  for `^/(?:[^/]+/)?api/internal` at the edge, every app).
- Receiver:

  ```python
  body = await request.body()
  if not verify_internal_v2(_session_secret(), "POST", request.url.path, request.headers, body):
      return JSONResponse({"ok": False}, status_code=404)
  ```

- Caller: `headers = sign_internal_v2(secret, "POST", path, body)` and post to
  `http://127.0.0.1:{port}{path}` (Ripple `notify.py`). Bodyless GETs:
  `get_internal_json(base_url, secret, path)`.
- Signed with the platform session secret; 60s skew. No unsigned write
  endpoints for other services.

### `/api/display` (loopback feed)

Only if the app feeds the Dashboard tile, TRMNL or the Car Thing panel:
`GET /api/display` → compact JSON (`{"ok": true, …}`); `?items=N` adds the Car
Thing list (`card`, `items[{id,title,sub,badge,detail}]`, `refresh_s`), the plain
call unchanged (NewsCast, Pinboard). It has no sign-in, so add
`location = /myapp/api/display { return 404; }` to
`deploy/nginx/stonepi-public-deny-display.conf`.

A display **page** captured as an image (Ripple `/display`) instead opens for
Notify only with a one-off key from a signed `/api/internal/capture-key`
(catalog `capture_key`, `display_page`, `display_label`, `display_blurb`).

### Outbound fetches

Anything fetched from a URL a person typed (link previews, feeds, images):
follow Pinboard `app/links.py`:

- http(s) only; port 80 or 443 only;
- every hop (page, each redirect, each image) resolves to **public**
  addresses only (`ipaddress.is_global`, not multicast; IPv4-mapped unwrapped),
  so a link can't reach the LAN, the Pi or the tailnet;
- redirects followed by hand (max 4), short timeouts, size caps;
- HTML only for pages, **raster images only** (png, jpeg, gif, webp, ico; no SVG);
- fetched in the background, never on page load; files saved under the data
  dir and served by the app (no third-party images in the browser).

## Notifications (phone alerts)

Every app has the bell through `header_tools`. Apps that emit events also:

1. **Register events** in `packages/stonepi_contracts/stonepi_contracts/catalog.py`:
   `EventType("{app_id}.{event}", app_id, label, audience, blurb)`, audience
   `personal` / `household` / `admin`. New events start not approved in Notify.
   `APP_LABELS` needs the app's name for **every** user app, emitting or not
   (consistency test).
2. **Emit** with `stonepi_contracts.emit_event` / `EventEnvelope`: `audience=`
   always; personal events `user=` the owner's Auth id
   (`stonepi_auth.alerts.auth_user_id(...)`). Never local ids or `"local"`; no
   owner → don't send. Only under the platform (secret set).
3. **Templates**: `add_shared_templates(templates.env)` (needed for
   `stonepi/head.html` and `stonepi/header.html` in every app).
4. **Bell**: `alerts_bell_state = bell_context(user, session_cookie=request.cookies.get(COOKIE_NAME), home_url=home, enabled=bool(secret), app_id=APP_ID)`
   on every page.
5. **Settings → Notifications** (emitting apps only):
   `{% from "stonepi/alerts.html" import notifications_card %}{{ notifications_card(notifications_card_state) }}`
   with `notifications_card_context(APP_ID, user if secret else None, home_url=home)`;
   app timing settings below it, nothing else.
6. Context keys are exactly `alerts_bell_state` / `notifications_card_state`
   (a macro import shadows a same-named variable).
7. **Tests** (`tests/test_personal_alerts.py`, Pinboard / PriceWatch): emitter
   audience and Auth `user`; card on the tab; standalone shows neither.
   Monkeypatch `bell_context` so tests don't call Notify.

## Tests

Ripple's set is the minimum (`apps/ripple/tests/test_posts.py`): a `_client`
fixture that monkeypatches `routes._session_secret`, stubs `bell_context`, sets
`CSRF_COOKIE` and an `encode_session(...)` cookie, and mounts `routes.router`
on a bare FastAPI. Cover:

- signed out → redirect to sign-in; no grant → 403 and nothing saved;
- the main create / edit / delete paths; others can't change what isn't theirs;
- expired / missing CSRF refused;
- admin-only settings refused for members;
- output escaped; empty state; standalone (no secret) works;
- schema migrations; `/healthz`; `/api/display` shape if present.

Also run `packages/stonepi_auth/tests` (platform consistency, prefix roots,
icons, contract labels) after registering.

## Registration checklist

| # | File | What |
|---|------|------|
| 1 | `packages/stonepi_auth/stonepi_auth/catalog.py` | `APP_CATALOG` entry: `id`, `name`, `path`, `port`, `unit`, `health`, `color`, `capabilities` (only real gates, each `{id, label, default}`), `launcher: True`, `group: "user"`, `icon`, `description` (+ display keys if any). Add the id to `DEFAULT_LAUNCHER_ORDER`. Updates, release zips, the versions table and the switcher follow the catalog. |
| 2 | `packages/stonepi_auth/stonepi_auth/session.py` | `safe_next` default allowed ports: add the port |
| 3 | `packages/stonepi_auth/stonepi_auth/prefix.py` | `_is_other_platform_path` roots: add `/myapp` |
| 4 | `packages/stonepi_contracts/stonepi_contracts/catalog.py` | `APP_LABELS["myapp"] = "MyApp"` (+ events) |
| 5 | `packages/stonepi_brand/static/icons/apps/myapp.svg` | Icon artwork (design system); then `render_icons.py` for `png/myapp-{32,180,192,512}.png` |
| 6 | `packages/stonepi_auth/stonepi_auth/templates/stonepi/header.html` | Outline glyph in `_icon()` for the app id |
| 7 | `apps/dashboard/app/templates/_icons.html` | Outline glyph in `svg()` for the app id |
| 8 | `deploy/nginx/stonepi.conf` **and** `deploy/nginx/stonepi-tls.conf` | `upstream stonepi_myapp { server 127.0.0.1:PORT; keepalive 8; keepalive_timeout 10s; }`; `location ^~ /myapp/static/ { alias /opt/stonepi/apps/myapp/app/static/; access_log off; expires $stonepi_expires; }`; `location /myapp/ { proxy_pass http://stonepi_myapp/; }` |
| 9 | `deploy/nginx/stonepi-public-deny-display.conf` | `location = /myapp/api/display { return 404; }` if the app has `/api/display` |
| 10 | `deploy/systemd/stonepi-myapp.service` | Copy Ripple's: `User`/`Group`, `EnvironmentFile=/etc/stonepi/stonepi.env` + `-/etc/stonepi/myapp.env`, `WorkingDirectory=/opt/stonepi/apps/myapp`, `ExecStart=…/.venv/bin/python -m app.serve`, `Restart=always`, `NoNewPrivileges=yes`, `MemoryMax=` if heavy |
| 11 | `deploy/systemd/stonepi.target` | `Wants=stonepi-myapp.service` |
| 12 | `deploy/install.sh` | `ensure_user`; `$DATA/myapp` in the `mkdir -p` line; `write_env "$CONF/myapp.env"` (PORT, PUBLIC_BASE_URL, STONEPI_PREFIX, STONEPI_APP_ID, STONEPI_DATA_DIR) **and** `ensure_env_key` for `STONEPI_DATA_DIR` and `STONEPI_PREFIX` (existing Pis); extra packages in `install_app`'s `case` if needed; `install_app myapp stonepi-myapp` (`playwright` third arg for browsers); `APP_DATA_OWNERS` pair; `ensure_writable_data`; the vault group `for u in …` loop; `backup.conf` default `APPS=` **and** the append-if-missing block; the unit copy loop; `systemctl enable --now`, `systemctl restart` and `UNITS=(…)` lists; the final URL echo |
| 13 | `deploy/backup/stonepi-backup.sh` | `APPS=` list; `backup_sqlite /var/lib/stonepi/myapp/myapp.sqlite …` if it has SQLite |
| 14 | `deploy/backup/stonepi-backup-helper.sh` | Default `APPS=` list |
| 15 | `deploy/backup/stonepi-restore.sh` | `APPS=` list; the `chown` line for the data dir; the vault group loop |
| 16 | `apps/recover/app/main.py` | `SERVICE_GROUPS` → Apps: `("stonepi-myapp", "MyApp")` (consistency test checks) |
| 17 | `deploy/stonepi-cli.sh` | `UNITS`, the `logs` `-u` list, the `urls` echo |
| 18 | `scripts/run_dev.py` | `APPS` tuple `(id, path, port, "", "python -m app.serve")`; `APP_ENV` if it needs extras; the package-install sets in `ensure_venv` (vault, contracts, display/ai/browser as needed); banner line |
| 19 | `README.md` (root) | Apps table row; People and permissions row; Development ports table; Optional extras if it needs hardware |
| 20 | `CHANGELOG.md` (root) | Under **Unreleased**: "**MyApp 0.0.N (new app)**: one line. See apps/myapp/CHANGELOG.md", then a **Platform** bullet listing the registration (catalog, nginx, systemd, target, backup/restore, Recover, CLI, switcher, run-dev) |

Only if the app listens on something other than loopback (rare: Radio's Odio):
`deploy/nftables/stonepi.nft` (drop or LAN-only accept) and Dashboard Health's
expected listeners (`apps/dashboard/app/system_health.py`: `EXPECTED_LISTENERS`
/ allowed processes), or the listener shows as unexpected.

Nothing to add in `scripts/build_release_zips.py`: zips and the versions table
come from the catalog. Its pre-flight refuses a build while files `install.sh`
needs are untracked, so the app must be committed before a release.

## Env keys

```
PORT=8019
PUBLIC_BASE_URL=http://{host}.local/myapp
STONEPI_PREFIX=/myapp
STONEPI_APP_ID=myapp
STONEPI_DATA_DIR=/var/lib/stonepi/myapp
# shared from /etc/stonepi/stonepi.env: session secret (Vault), STONEPI_PUBLIC_ORIGIN, STONEPI_AUTH_URL, hostname …
```

run-dev sets `HOST`, `PORT`, `STONEPI_SESSION_SECRET`, `STONEPI_VAULT_DIR`,
`STONEPI_AUTH_URL`, `STONEPI_PUBLIC_ORIGIN`, `STONEPI_NOTIFY_URL`,
`STONEPI_PREFIX=""` for every app.

## Local data

- Runtime under `STONEPI_DATA_DIR` (Pi: `/var/lib/stonepi/myapp`, mode 700,
  owned by the service user) or `apps/myapp/data` (dev).
- SQLite with versioned migrations; uploads re-encoded and swept for orphans on
  startup (Ripple `photos.py`).
- Secrets in the Vault (`stonepi_vault`), never in the data dir or env files in git.
- Never ship databases, caches or vault material in zips (`build_release_zips`
  denies `data/`).

## Versioning and changelogs

- `__version__` in `app/__init__.py` is the only place: Dashboard Health /
  Services / Updates, the app's About, release zips and the versions table read
  it. Three parts (`0.0.11`); a test requires it.
- **Two tracks.** System apps (Dashboard, Auth, Notify, Recover, AgentLink)
  carry the platform version (`VERSION`, e.g. `0.5.0`), `0.5.0.N` between cuts.
  User apps share one `0.0.x` line, set together at alignment cuts; between
  cuts each app takes its own patch when it changes, others stay put. A new user
  app starts **one above the highest Home app version** at the time (Ripple
  0.0.10, Radio 0.0.11); confirm the number with the owner.
- **Staging:** while a feature is staged on the Pi, only the feature's own app
  gets its new number. Packages, system apps and `VERSION` keep theirs; notes go
  under "Unreleased"; the other bumps come after the Pi smoke test.
- App `CHANGELOG.md`: `# Changelog — MyApp`, then `## Unreleased` with
  `### Added` / `### Changed` / `### Fixed`; at the cut it becomes
  `## 0.0.N — YYYY-MM-DD`. Plain, user-facing first lines.
- Root `CHANGELOG.md`: a short "What's new" per app above the platform detail
  at release time.
- Ideation specs never carry target version numbers.

## Pi update

Dev → Pi is a LAN overlay, prepared with [push-to-pi](../push-to-pi/SKILL.md):
gitignored `scripts/push-*-fixes.ps1` + `.cmd` and `scripts/apply-*-on-pi.sh`
(zip with posix paths, scp, `ssh -t` → `sudo bash`). A new app's apply script
creates the user, data dir, env file, venv, unit, nginx blocks and catalog
changes, or simply runs `deploy/install.sh`. The user runs
`cmd /c "scripts\….cmd -Apply"` (passwords); never run scp/ssh yourself.

Official releases: `scripts/build_release_zips.py` + GitHub release assets;
never attach push helpers.

## Smoke after wiring

1. `run-dev.bat`: the app at `http://127.0.0.1:PORT/`, its Home tile, the
   switcher entry and icon.
2. Sign in as admin and as a member without the grant (denied page), then with it.
3. Header: bell, switcher (Home on top returns to the same host), avatar →
   Appearance switches the theme in this app too.
4. Phone width: bottom nav without Home, "+" opens the sheet, sheets clear the
   bar; laptop: side rail, centred sheets.
5. `pytest` in the app and in `packages/stonepi_auth`.
6. On the Pi: `systemctl is-active stonepi-myapp`;
   `curl -s 127.0.0.1:PORT/healthz`; `http://{host}.local/myapp/`;
   `curl -s http://{host}.local/myapp/api/display` → 404.
