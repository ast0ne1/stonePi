# StonePi release checklist

Use this after Display/Home work is verified on a clean Pi and docs match the tree.

## 1. Docs (done when this list matches the repo)

- [ ] Root [README.md](../README.md) — Display designs, Home drag order, Glance diagram
- [ ] [apps/dashboard/README.md](../apps/dashboard/README.md) + [CHANGELOG.md](../apps/dashboard/CHANGELOG.md)
- [ ] Root [CHANGELOG.md](../CHANGELOG.md) + [VERSION](../VERSION)
- [ ] [deploy/SECURITY.md](SECURITY.md) + [nginx/stonepi-public-deny-display.conf](nginx/stonepi-public-deny-display.conf)
- [ ] [deploy/walkthrough.html](walkthrough.html) first-boot Display / Home checks
- [ ] [`.gitignore`](../.gitignore) — no runtime `data/`, vault, or `.env` in the tree you commit

## 2. Pi smoke (after overlay)

Hard-refresh the browser after syncing code. Confirm units with `stonepi status` and that Display/Home behave as expected. Spot-check Cockpit (`https://…:9090`) and EventTrakr favourites.

## 3. Smoke test

1. Home — Edit order drag → Done; refresh keeps order  
2. Settings → Display — Status wall + Household previews are landscape and full-width  
3. Copy markup → paste into TRMNL → Push now → panel updates  
4. Health (`/overview`) shows app health (Healthy/Down) plus disk and backup; Status Services metadata not all `—` when apps respond  
5. Health → Cockpit opens with HTTPS  
6. EventTrakr — star an event → “Added to Favourites” (not a false “Removed”)  
7. Open via router DNS (e.g. `http://stonepi.home/`) — login and **Back to apps** stay on that host (not bounced to `.local`); Health URLs show that host  
8. From an app (NewsCast/EventTrakr/…) nav **Home** returns to the same host’s launcher
9. Services — enable toggle; Route + Port visible; Health stays monitor-only

## 4. Commit (when you ask)

Do **not** commit until you explicitly request it. Suggested message focus: Status wall + Home drag + StonePi-labelled zips + install hardening for 0.1.1.

Before the first commit, dry-run staged paths and confirm no `vault.key`, `secrets.enc`, `*.db`, or `apps/*/data/` runtime files.

## 5. Build release zips

From repo root (versions already in each `app/__init__.py`):

```bash
python scripts/build_release_zips.py --all
```

Assets are **StonePi portal variants** (not standalone app releases):

- `stonepi-dashboard-0.0.2.zip`, `stonepi-auth-0.0.2.zip`, `stonepi-eventtrakr-0.0.2.zip`, `stonepi-studio-0.0.2.zip`, …
- `stonepi-platform-0.1.1.zip`

Each zip includes `STONEPI.txt`. The updater prefers `stonepi-{app}-*.zip` and still accepts legacy `{app}-*.zip` for one cycle.

Do **not** ship ad-hoc Pi overlay helpers (`scripts/push-*-fixes.*`, `scripts/apply-*-on-pi.sh`) — those stay local-only (gitignored).

## 6. GitHub Release

Tag platform **v0.1.4** (or your chosen tag), attach the `stonepi-*.zip` assets, summarize from root `CHANGELOG.md`.
