---
name: push-to-pi
description: >-
  Prepare StonePi LAN overlay push scripts and give the user a cmd to run
  (they enter scp/ssh passwords). Use when pushing/updating/deploying to the Pi,
  applying overlays, or syncing apps to stonepi. Never execute the push yourself.
---

# Push updates to the Pi

StonePi uses **local-only LAN overlay scripts** (gitignored: `scripts/push-*-fixes.*`,
`scripts/apply-*-on-pi.sh`). Official GitHub Release zips are a separate path —
this skill is for **dev → Pi** overlays after local testing.

## Hard rule (passwords)

**Never execute** pack / upload / apply yourself:

- Do **not** run `push-*.ps1`, `push-*.cmd`, `scp`, or `ssh`
- Do **not** pass `-Apply` in the agent terminal
- Password prompts fail or hang when the agent runs them

The agent only **prepares** scripts (create/update if needed), then **hands the
user** this prompt (real script name instead of `xxxxxxxx`):

```bat
cmd /c "scripts\xxxxxxxx.cmd -Apply"
```

Prefer the `.cmd` form. Do not only offer the `.ps1` unless they ask.

## Existing overlays

| Script | Scope |
|--------|--------|
| `scripts/push-briefing-settings-fixes.cmd` | NewsCast + Dashboard + PriceScout app trees |
| `scripts/push-settings-stack-fixes.cmd` | Settings hub / stacked panels (NewsCast, Dashboard, FileServe, EventTrakr, Pinboard, Studio, PriceScout, SportGuide, PriceWatch, Notify) |
| `scripts/push-newscast-fixes.cmd` | NewsCast only |
| `scripts/push-newscast-paper-fixes.cmd` | NewsCast app tree + Dashboard `app/` tree + `stonepi_auth` and `stonepi_contracts` `catalog.py` (Home tile order, `newscast.push_missed` event) + Notify `boot.py`; tar backup to `/var/backups`, restarts NewsCast, Notify, Dashboard, Auth, checks the event and tile order are visible |
| `scripts/push-pricescout-fixes.cmd` | PriceScout (+ some platform wiring) |
| `scripts/push-ux-fixes.cmd` | Multi-app flat file list (older UX overlay) |
| `scripts/push-display-fixes.cmd` | Display-related |
| `scripts/push-sportguide-fixes.cmd` | SportGuide |
| `scripts/push-brand-chrome-fixes.cmd` | EventTrakr + PriceScout + SportGuide |
| `scripts/push-fileserve-fixes.cmd` | FileServe only |
| `scripts/push-pinboard-fixes.cmd` | Pinboard only |
| `scripts/push-studio-fixes.cmd` | Studio only |
| `scripts/push-notify-fixes.cmd` | Full Notify platform (apps + packages + pip) |
| `scripts/push-renames-fixes.cmd` | One-off Notifications → Notify / Recovery → Recover rename: all touched apps + packages + deploy, runs `deploy/stonepi-migrate-renames.sh` (idempotent) |
| `scripts/push-notify-ui-fixes.cmd` | Notify UI only (templates/static/routes + Dashboard Settings link; restart, no pip) |
| `scripts/push-health-layout-fixes.cmd` | Full Dashboard + Recover `app/` trees (Health tab layout, Recovery Console wording; restart both, no pip) |
| `scripts/push-services-groups-fixes.cmd` | Dashboard Services System/Apps groups + Recover catalog (Dashboard + Auth + stonepi_auth) |
| `scripts/push-release-smoke-fixes.cmd` | **Pre-release smoke.** Packs the tree a release tag would ship (tracked + untracked-not-ignored, release deny list, LF), then runs the real `deploy/install.sh` on the Pi and checks the hardening (sudo helper, root venvs, Recover passwd, firewall, CSP). Snapshot in `/var/backups`, log `/var/log/stonepi-release-smoke.log`; exits non-zero on any FAIL |
| `scripts/push-design-system-fixes.cmd` | **F6 design system** (2026-10-11): every app's `app/` tree + CHANGELOG, whole `packages/stonepi_brand`, `stonepi_auth` + `stonepi_display` modules, nginx confs (`/assets/brand/`, live site TLS-aware, `nginx -t` with restore). Keeps each app tree's owner; tar backup to `/var/backups`; restarts every running app; checks `/assets/brand/` + fonts over nginx and that an enabled admin exists |
| `scripts/push-carthing-screensavers-fixes.cmd` | **U12 Car Thing screensavers + device drawing** (2026-10-12): Car Thing + Notify `app/` trees + CHANGELOGs, `stonepi_display` module. Tar backup to `/var/backups`; checks both venvs see `SAVER_STYLES`; restarts Notify, and Car Thing only if it was running; health checks |
| `scripts/push-carthing-weather-pipes-fixes.cmd` | **U13 / U14 / U19** (2026-10-17): Car Thing + Notify `app/` trees + CHANGELOGs, `stonepi_display` module (screensaver clock, Weather widget + screen, 3D Pipes). Same shape as the screensavers overlay; checks both venvs see Pipes / Weather / `clock.saver`; restarts Notify, and Car Thing only if it was running |
| `scripts/push-headers-recover-fixes.cmd` | **U16 / U17 / U11 / B10** (2026-10-17): Notify, Recover, Dashboard and Auth `app/` trees + CHANGELOGs (Recover's `templates_fallback/` rides inside its `app/` tree). Tar backup to `/var/backups`; checks Recover's venv imports `stonepi_auth.header`; restarts Auth, Notify, Dashboard, Recover; health checks + Recover login render. No pip |
| `scripts/push-newscast-summaries-fixes.cmd` | **U18a on-device summaries** (2026-10-17): NewsCast `app/` tree (incl. bundled `app/nltk_data` punkt + Danish stop words), `requirements.txt` (+ sumy, nltk, numpy) and CHANGELOG. Tar backup to `/var/backups`; **pip installs into NewsCast's venv** (restores files if pip fails) and re-locks it root-owned; as `stonepi-news` checks sumy imports, punkt loads from the bundle (download blocked) and times sample summaries; restarts NewsCast, health on :8001, prints memory. `-PackOnly` builds the zip without uploading |
| `scripts/push-ai-platform-fixes.cmd` | **F13 + U18a/U18b/U1 AI platform** (2026-10-17; supersedes `push-newscast-summaries-fixes`): new `packages/stonepi_ai`, `stonepi_vault` `quota.py`, `stonepi_contracts` `catalog.py`, Dashboard + Studio + NewsCast `app/` trees + CHANGELOGs, NewsCast `requirements.txt`, Notify `boot.py`, `deploy/install.sh`. Tar backup to `/var/backups`; **pip installs `stonepi_ai` (-e) into the Dashboard, NewsCast and Studio venvs** plus NewsCast's requirements (restores files if pip fails), re-locks venvs root-owned; checks as each service user that `stonepi_ai` imports and the Vault folder is writable, NewsCast's bundled punkt loads, `stories.ai_record` exists, and Studio's old model choice moved to `ai-config.json`; removes Studio's `model_settings.py`; restarts Notify, Dashboard, NewsCast, Studio. `-PackOnly` builds the zip without uploading |
| `scripts/push-carthing-music-fixes.cmd` | **F7 Car Thing Music (Spotify)** (2026-10-17): Car Thing + Notify `app/` trees + CHANGELOGs, `stonepi_display` module (new `spotify.py`), and `stonepi_vault/store.py` alone (`Vault.reload`). Dashboard's Vault labels for the Spotify keys are left out (its tree carries F13). Tar backup to `/var/backups`; checks both venvs see the Music config, `stonepi_display.spotify` and `Vault.reload`, and that `stonepi-carthing` can write the Vault and its data folder; restarts Notify, and Car Thing only if it was running; checks the Spotify callback route and `/np`. No pip |
| `scripts/push-purge-favicon-fixes.cmd` | Three files only: FileServe `main.py` + `services/pages.py` (favicon 500, expired pages), EventTrakr `services/ingest.py` (purge FK). Hash-checks Pi copies against the pre-fix versions and aborts if they differ (`-Force` overrides); restarts FileServe + EventTrakr |

Prefer a **tree-based** combined script when several apps changed in one session
(pattern: `push-briefing-settings-fixes`).

## When asked to update the Pi

1. **Identify apps** touched since the last Pi sync (or what the user names).
2. **Prepare** — confirm/update the matching `push-*.ps1` + `.cmd` +
   `apply-*-on-pi.sh` + bootstrap so staged trees include current work
   (exclude `.venv`, `data`, `__pycache__`, `.env`). Do not run them.
3. **No cache-bust bump needed** — `?v=` tokens are hashed from each app's
   `static/` at startup, so the restart after the overlay busts caches.
4. **Reply with the run prompt** (plus a one-line hard-refresh hint), e.g.:

```bat
cmd /c "scripts\push-briefing-settings-fixes.cmd -Apply"
```

The user runs that themselves (pack + scp + apply + passwords). Then they
hard-refresh the affected apps on the Pi.
