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
