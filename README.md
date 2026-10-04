# StonePi

**The bedrock of your digital home.**

StonePi is a private hub for your home that turns information into something useful. It can bring you the news you want on your e-reader, show what’s happening locally, tell you what’s on in sport and entertainment, find supermarket deals, watch prices for things you want, and push useful updates to your phone or screens around the house — while keeping your files and other household tools in one place.

It runs on one Raspberry Pi on your own network: a portal with one shared sign-in and phone-first apps, set up once and left running. Open it at `stonepi.local` (or your router name, e.g. `stonepi.home`). E-ink readers, a wall display and AI are optional extras you plug in when you want them.

**Set up a Pi: [stonepi-install.vercel.app](https://stonepi-install.vercel.app)** — a guided install from stock Raspberry Pi OS with one command.

| When you want… | StonePi covers it with… |
|----------------|-------------------------|
| The news you want, on your e-reader, without opening five news apps | **NewsCast** — feeds you choose, a daily paper for phone or e-reader |
| To know what’s happening locally this week | **EventTrakr** — agenda, favourites, calendar sync |
| What’s on in sport and entertainment | **SportGuide** — TV / stream schedules and the teams you follow |
| This week’s supermarket deals without five retailer apps | **PriceScout** — cross-store compare |
| To know when something you want hits your price | **PriceWatch** — strike alerts for named products |
| Wikipedia and other references without the internet | **Library** — offline reference content (Kiwix) on the Pi |
| Useful updates on your phone or screens around the house | **Notify** — ntfy phone alerts and TRMNL / e-ink displays |
| Your files and household tools in one place | **FileServe** (share files and small sites on the LAN), **Pinboard** (notices and reminders), **Studio** (build a simple site and publish it to FileServe) |

The apps are useful on a phone. **Notify** takes the same jobs further, so you don’t have to open the portal for every check:

- **TRMNL** (or another glance display) — compose a board from household widgets (Pinboard notices, status, and other blocks). The panel shows what matters at a fridge or desk without unlocking a phone.
- **ntfy** — phone buzz when something finishes or crosses a threshold: paper ready to push, a PriceWatch strike, an event you care about, and similar emits from the apps.

Wire Destinations once; apps keep owning their data and only emit when something useful happened.

StonePi is built to behave like an appliance rather than a hobby stack: install with one command, come back after reboot, watch host and apps under **Health**, back up to a labelled USB stick, and use **Recover** if the portal itself fails. Admins manage people, which apps are on, updates, backup, and platform settings from the **Dashboard**. **Cockpit** (`:9090`) is for the Linux host itself.

## Screenshots

| | |
|:---:|:---:|
| ![Sign in](docs/screenshots/login.jpg) | ![Home launcher](docs/screenshots/home.jpg) |
| *Shared sign-in* | *Home — drag-to-order launcher* |
| ![Health](docs/screenshots/health.jpg) | ![Services](docs/screenshots/services.jpg) |
| *Health — monitor host and apps* | *Services — enable / disable apps* |
| ![Users](docs/screenshots/users.jpg) | ![Settings](docs/screenshots/settings.jpg) |
| *Users — household accounts* | *Settings → General* |

More images: [docs/screenshots/](docs/screenshots/).

## What’s included

### Launcher apps

| App | Purpose | Why it’s in the stack | Key usage |
|-----|---------|------------------------|-----------|
| **[NewsCast](apps/newscast/README.md)** | Daily briefings from feeds you choose, ready for an e-reader | “What’s worth reading” without five news apps | Sources / Device; RSS or scrape; OPDS / CrossPoint / KOReader; optional LLM, translation, ntfy |
| **[FileServe](apps/fileserve/README.md)** | Host HTML, PDF, Word, or zip sites on the LAN | Household “put it on a URL” without the cloud | Keep-until + optional password; per-user `/u/…`; Studio publish target |
| **[EventTrakr](apps/eventtrakr/README.md)** | Local events, favourites, and calendar sync | Complements news with “what’s on near us” | 7-day agenda; ICS/webcal; Google Calendar; Bright Data for Facebook events |
| **[Pinboard](apps/pinboard/README.md)** | Household notices and short reminders | Shared fridge-door; feeds the wall display | Grant-gated; Display / TRMNL Pinboard block |
| **[Studio](apps/studio/README.md)** | Chat-build static sites (SPA, Guide, Game) | Family authoring without a laptop toolchain | Vault LLM keys; publishes to FileServe with the same expiry/password options |
| **[PriceScout](apps/pricescout/README.md)** | Weekly supermarket offers and cross-store compare | Household shopping without five retailer apps | eTilbudsavis JSON; optional Salling madspild |
| **[PriceWatch](apps/pricewatch/README.md)** | Watch specific products until a target price is met | Strike alerts for named SKUs | PriceRunner Denmark (+ mock for local) |
| **[SportGuide](apps/sportguide/README.md)** | Sports TV / stream schedules (Now + Sources) | What’s on without juggling guide sites | Playwright scrapes (AusSportGuide + WheresTheMatch) |
| **[Library](apps/library/README.md)** | Wikipedia and other references, offline on your Pi | Household reference that works without the internet | Kiwix ZIMs on microSD, USB or SSD; reader at `/library/read/` behind sign-in |

### Portal (not launcher tiles)

| App | Role |
|-----|------|
| **[Dashboard](apps/dashboard/README.md)** | Home launcher, **Health**, **Services**, Users/grants, Settings |
| **[Auth](apps/auth/README.md)** | Shared household sign-in — one account, one cookie across apps |
| **[Notify](apps/notify/README.md)** | Displays, TRMNL, and ntfy destinations (platform service) |
| **[Recover](apps/recover/README.md)** | **Recovery Console** — escape hatch on `:8099` when Dashboard or nginx is down |

Admin nav split:

| Nav | Job |
|-----|-----|
| **Health** (`/overview`) | Monitor the host — alerts, disk, backup, whether each app is responding |
| **Services** (`/applications`) | Manage apps — enable/disable for the household; open a service to start/stop/restart |

## How it fits together

```text
Portal     Dashboard + Auth (people, grants, exposure, secrets, Health / Services)
Consume    NewsCast (briefings / e-readers) · EventTrakr (events / calendars)
Publish    Studio ──publish──▶ FileServe hosted pages
Glance     Apps emit widgets ──▶ Notify Displays ──▶ TRMNL; events ──▶ ntfy Destinations
```

- **Studio → FileServe** — generated zips land under the user’s Hosted Pages.
- **Notify → Displays** — one Display per TRMNL screen, laid out in a drag-and-drop builder over a real render of the device. Paste the universal template into each Private Plugin once; layout changes travel with each push. ntfy lives under **Notify → Destinations**. Dashboard Settings → Display redirects here.
- **NewsCast → readers** — OPDS catalogs and optional X3 Sync for CrossPoint / KOReader.
- **Vault** — encrypted home for integration keys (LLM, Bright Data, Google, webhook, ntfy, session).

## Home launcher

On **Home**, tiles follow each user’s saved order. **Edit order** → drag tiles to place → **Done** (order autosaves while dragging).

## What you’ll need

StonePi itself only needs a **Raspberry Pi** on your home network and a phone or laptop. Everything below is **optional** — add the pieces that match how your household wants to use it. Keys go in **Dashboard → Settings → Vault** (not scattered in chat apps).

### Hardware (optional)

| Want… | Get… | Notes |
|-------|------|--------|
| Daily paper on an e-reader | **[Xteink X3 / X4](https://www.xteink.com/products/xteink-x3)** + **[CrossPoint](https://crosspointreader.com/)** firmware | Prefer buying from [xteink.com](https://www.xteink.com/) (or official channels) so the unit can flash CrossPoint. NewsCast talks OPDS / push over your LAN. Setup: [NewsCast README](apps/newscast/README.md#e-reader). |
| Same idea on a Kobo | Any Kobo that runs **[KOReader](https://koreader.rocks/)** | Add StonePi’s OPDS catalog in KOReader; push uses KOReader’s SSH when you want Send-tab files on the device. |
| Fridge / desk glance board | **[TRMNL](https://trmnl.com/)** ([shop](https://shop.trmnl.com/)) | One Private Plugin per screen; wire its webhook on the Display in **Notify → Displays**. Pinboard notices and other widgets appear on the panel. |
| Comfortable EventTrakr scraping | **Pi 4 or Pi 5** | Some event sites need a real browser (Playwright / Chromium). A Pi 3 will struggle. |

### Cloud & AI keys (optional)

| Want… | Get… | Put the key in Vault as… |
|-------|------|---------------------------|
| Shorter NewsCast briefings | **[OpenAI](https://platform.openai.com/api-keys)** API key, or a local **[Ollama](https://ollama.com/)** model on the LAN | `OPENAI_API_KEY` — or point NewsCast → LLM at Ollama (no OpenAI key). Refresh still works with plain extracted text if you skip AI. |
| Translate feeds into another language | Same OpenAI / Ollama path, or NewsCast’s Google translate option | NewsCast → **Settings → Translation** (provider + target language). |
| Studio chat-build sites | **[Anthropic](https://console.anthropic.com/)** and/or **OpenAI** key; optional OpenAI-compatible base URL | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, optional `STUDIO_LLM_BASE_URL`. Without a key, Studio cannot call a model. |
| Facebook Events in EventTrakr | **[Bright Data](https://brightdata.com/)** API access ([Facebook Events scraper docs](https://docs.brightdata.com/api-reference/scrapers/social-media-apis/facebook-events-discover-by-url)) | `BRIGHTDATA_API_KEY`. Catalog, ICS, and normal web sources still work without it. |
| Favourites → Google Calendar | Google Cloud OAuth client for your household | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — EventTrakr → Integrations. |
| Phone buzz when the paper is ready | **[ntfy](https://ntfy.sh/)** topic (hosted or self-hosted) | **Notify → Destinations** (token in Vault as `STONEPI_NTFY_TOKEN`; apps emit via the shared outputs service). |

**Studio in one line:** add an Anthropic and/or OpenAI key to Vault, open **Studio**, pick SPA / Guide / Game, chat, then **Publish** to FileServe (same keep-until and password options as Hosted Pages).

**Readers in one line:** flash CrossPoint on an X3/X4 (or use KOReader on Kobo), open NewsCast → Status, copy your OPDS URL onto the device.

## Platform services

Admin **Settings** tabs (not Home tiles):

| Tab | Role |
|-----|------|
| **Network** | Hostname, LAN vs internet-facing, Tailscale (optional ACL) |
| **Vault** | Encrypted secrets for apps and services (`stonepi_vault`) |
| **Automations** | Thin when→then (USB→backup, Health/backup→Notify Display / TRMNL push) |
| **Backup** | USB status, restore drill, failover, snapshot restore |

**Displays / TRMNL / ntfy** live under **[Notify](apps/notify/README.md)** (Destinations + Displays), not Dashboard Settings.

## Integrations (where to turn them on)

Optional bolt-ons — core apps work without them. Prefer **Settings → Vault** for API keys; details above under **What you’ll need**.

| Integration | Where in StonePi | What it enables |
|-------------|------------------|-----------------|
| **TRMNL** | Notify → Displays (webhooks in Vault: `DISPLAY_WEBHOOK_URL` for the built-in Dashboard Display, `DISPLAY_WEBHOOK_URL_<ID>` for others) | One Private Plugin per Display; paste the **universal template** into its Full markup once; arrange widgets in the screen builder; Save / Push now (StonePi only pushes `merge_variables`). Outbound POSTs are https-only. On the public edge, nginx denies app `/api/display` routes ([`stonepi-public-deny-display.conf`](deploy/nginx/stonepi-public-deny-display.conf)); collection still runs via loopback. |
| **AI / LLM** | NewsCast → LLM; Studio via Vault | OpenAI, Anthropic, or Ollama / local base URL. NewsCast writes short briefings (extracted text if no model). Studio generates sites. |
| **Translation** | NewsCast → Translation | Per-feed or global; Google or LLM; target language for Translate feeds. |
| **Bright Data** | EventTrakr → Integrations | Facebook Events discover/scrape when keyed; otherwise catalog/ICS/browser paths still work. |
| **E-readers** | NewsCast → Reader | CrossPoint (X3/X4), KOReader OPDS; Sync-style API. LAN vs internet-facing token rules: [deploy/SECURITY.md](deploy/SECURITY.md). |
| **Calendars** | EventTrakr | `.ics` download, webcal feed, Google OAuth forward. |
| **ntfy** | Notify → Destinations + Event prefs | Phone alerts when apps emit (NewsCast publish/push, PriceWatch strikes, Watch, …). Capability-gated for non-admins where applicable. |

## Security posture

Default install is **trusted home network (LAN)**. Flip to **Internet-facing** under Dashboard → Settings → Network when you expose the portal beyond the LAN (no service restart). Tailscale remote access is also under Network (preinstalled on the Pi). Checklist: **[deploy/SECURITY.md](deploy/SECURITY.md)**.

## Quick start on a fresh Pi

The guided version, step by step, is at **[stonepi-install.vercel.app](https://stonepi-install.vercel.app)**.

1. Flash **Raspberry Pi OS Lite (64-bit)** with Imager on a Raspberry Pi 4 or 5 — hostname `stonepi`, enable **SSH**, set user/password (and Wi‑Fi if needed).
2. Boot the Pi and SSH in:
   ```bash
   ssh YOURUSER@stonepi.local
   ```
3. Install the latest release:
   ```bash
   curl -fsSL https://stonepi-install.vercel.app/install.sh | sudo bash
   ```
4. Open http://stonepi.local/ and sign in with **admin** / **admin**, then change the password.

Something broken on an existing Pi? **[stonepi-install.vercel.app/recover](https://stonepi-install.vercel.app/recover)**.

### From a local copy of this repo

For development builds or offline installs: copy this folder onto the SD **boot** partition after imaging (e.g. `bootfs/stonePi/`; helper `scripts\copy-to-sd.bat E:\` skips `.venv` / `data` / `.git`), then on the Pi:

```bash
sudo bash /boot/firmware/stonePi/deploy/install.sh --hostname stonepi
```

Use `bash` — boot is FAT, so `./` execute bits are unreliable.

Full walkthrough, boot units, backup USB, and troubleshooting: **[deploy/INSTALL.md](deploy/INSTALL.md)**.  
Interactive checklist (open in a browser): **[deploy/walkthrough.html](deploy/walkthrough.html)**.

The installer enables systemd services so everything comes back after a reboot. Re-running it is safe: it keeps `/var/lib/stonepi` and existing `/etc/stonepi/*.env` files.

## URLs (on the Pi)

| Address | Service |
|---------|---------|
| http://stonepi.local/ | Home (app launcher) |
| http://stonepi.local/overview | Health (admin monitor) |
| http://stonepi.local/applications | Services (enable / disable) |
| http://stonepi.local/auth/login | Sign in |
| http://stonepi.local/news/ | NewsCast |
| http://stonepi.local/files/ | FileServe |
| http://stonepi.local/events/ | EventTrakr |
| http://stonepi.local/pinboard/ | Pinboard |
| http://stonepi.local/studio/ | Studio |
| http://stonepi.local/prices/ | PriceScout |
| http://stonepi.local/watch/ | PriceWatch |
| http://stonepi.local/library/ | Library |
| http://stonepi.local/sports/ | SportGuide |
| http://stonepi.local/notify/ | Notify |
| http://stonepi.local/recover/ | Recovery Console (Recover; also direct on `:8099` when nginx or Dashboard is down) |
| https://stonepi.local:9090 or https://PI_LAN_IP:9090 | Cockpit |

Same paths work via the Pi LAN IP or a router DNS name (e.g. `http://stonepi.home/`). Prefer router DNS when Avahi `.local` is unreliable.

## Windows development

```bat
scripts\run-dev.bat
```

Creates per-app virtualenvs and starts:

| URL | App |
|-----|-----|
| http://127.0.0.1:8010/ | Dashboard |
| http://127.0.0.1:8011/login | Auth |
| http://127.0.0.1:8012/ | Notify |
| http://127.0.0.1:8001/ | NewsCast |
| http://127.0.0.1:8002/ | FileServe |
| http://127.0.0.1:8003/ | EventTrakr |
| http://127.0.0.1:8004/ | Pinboard |
| http://127.0.0.1:8005/ | Studio |
| http://127.0.0.1:8006/ | PriceScout |
| http://127.0.0.1:8007/ | SportGuide |
| http://127.0.0.1:8008/ | PriceWatch |
| http://127.0.0.1:8009/ | Library (no Kiwix on Windows) |

SSO is shared on `127.0.0.1`. Solo `run-local.bat` still works if `STONEPI_SESSION_SECRET` is unset. On the Pi (with session secret set), **Users**, **Updates**, and **Backup** live under dashboard Settings; in-app Users/Update tabs are for solo runs only.

## Pi helpers after install

```bash
stonepi status
stonepi restart
stonepi logs
stonepi urls
```

## Backup

Label a USB stick **STONEPI-BACKUP** and plug it into the Pi — backup starts automatically (or run `sudo systemctl start stonepi-backup`). Details: [deploy/backup/RESTORE.md](deploy/backup/RESTORE.md).

## Updates

GitHub Releases should attach **StonePi-labelled** per-app zips (`stonepi-<app>-<version>.zip`) plus `stonepi-platform-<version>.zip`, which also carries the system apps (Dashboard, Auth, Notify, Recover). These are portal variants — not standalone upstream apps. Current versions are in [CHANGELOG.md](CHANGELOG.md) (`python scripts/build_release_zips.py --versions-table`). Build them with:

```bash
python scripts/build_release_zips.py --all
```

Admins install from Dashboard → Settings → Updates, which lists the platform and every app (or each app’s own Settings → Update when it runs solo, outside StonePi). On the Pi, Dashboard downloads the zip and checks it against the release’s `SHA256SUMS`; the root helper `stonepi-update-helper` installs it, restarts the app, waits for its health check and rolls back if it fails. Platform updates use the installer for now: `curl -fsSL https://stonepi-install.vercel.app/install.sh | sudo bash -s -- --reinstall`.

Platform notes: [CHANGELOG.md](CHANGELOG.md). Release steps: [deploy/RELEASE.md](deploy/RELEASE.md).

## Layout

```text
apps/auth            shared login
apps/dashboard       home launcher + admin (Health / Services)
apps/notify          Displays, TRMNL, ntfy destinations, alerts (platform service)
apps/recover         Recovery Console escape hatch (:8099, ships in the platform zip)
apps/newscast
apps/fileserve
apps/eventtrakr
apps/pinboard
apps/studio
apps/pricescout
apps/sportguide
apps/pricewatch
apps/library
packages/stonepi_auth         shared login, APP_CATALOG, brand fonts + asset_rev
packages/stonepi_update
packages/stonepi_display
packages/stonepi_watch
packages/stonepi_vault
packages/stonepi_automations
packages/stonepi_browser      shared Chromium lock
packages/stonepi_contracts    event / widget contracts
packages/stonepi_notify       Notify routing + delivery
packages/stonepi_brand        shared fonts (/assets/fonts/)
docs/screenshots     README gallery
deploy/install.sh    Pi installer
deploy/stonepi-migrate-renames.sh   Notifications→Notify / Recovery→Recover migration
deploy/INSTALL.md    fresh OS guide
deploy/RELEASE.md    release checklist
deploy/walkthrough.html
deploy/responsive-audit.md   viewport tiers + audit notes
scripts/run-dev.bat  Windows
scripts/copy-to-sd.bat
scripts/build_release_zips.py
scripts/responsive/          optional viewport overflow smoke
```

Responsive contract (720 / 1024 / 1440) and QA viewports: [deploy/responsive-audit.md](deploy/responsive-audit.md). UX skill: [.cursor/skills/ux-stonepi/SKILL.md](.cursor/skills/ux-stonepi/SKILL.md).
