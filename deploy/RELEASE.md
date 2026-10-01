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
2. Notify → Displays — Status wall + Household designs; Destinations for webhook / ntfy  
3. Copy markup → paste into TRMNL → Push now → panel updates  
4. Health (`/overview`) shows app health (Healthy/Down) plus disk and backup; Status Services metadata not all `—` when apps respond  
5. Health → Cockpit opens with HTTPS  
6. EventTrakr — star an event → “Added to Favourites” (not a false “Removed”)  
7. Open via router DNS (e.g. `http://stonepi.home/`) — login and **Back to apps** stay on that host (not bounced to `.local`); Health URLs show that host  
8. From an app (NewsCast/EventTrakr/…) nav **Home** returns to the same host’s launcher
9. Services — enable toggle; Route + Port visible; Health stays monitor-only

## 4. Commit (when you ask)

Do **not** commit until you explicitly request it. Summarize the cut from the root `CHANGELOG.md` entry for the version in [`VERSION`](../VERSION).

Before the first commit, dry-run staged paths and confirm no `vault.key`, `secrets.enc`, `*.db`, or `apps/*/data/` runtime files.

## 5. Build release zips

From repo root (versions already in each `app/__init__.py`; the app list comes from `APP_CATALOG`):

```bash
python scripts/build_release_zips.py --versions-table
```

Paste the output under `### App versions in this cut` in the root [CHANGELOG.md](../CHANGELOG.md) — never type the table by hand. Then build:

```bash
python scripts/build_release_zips.py --all
```

Assets are **StonePi portal variants** (not standalone app releases):

- `stonepi-<app>-<version>.zip` for every user app (catalog apps without `ships_with: platform`)
- `stonepi-platform-<version>.zip`, which also carries the system apps (`apps/dashboard`, `apps/auth`, `apps/notify`, `apps/recover`)

Each zip includes `STONEPI.txt`. The updater prefers `stonepi-{app}-*.zip` and still accepts legacy `{app}-*.zip` for one cycle.

Do **not** ship ad-hoc Pi overlay helpers (`scripts/push-*-fixes.*`, `scripts/apply-*-on-pi.sh`, `scripts/*-bootstrap-on-pi.sh`) — those stay local-only (gitignored).

## 6. GitHub Release

Tag the platform `v<VERSION>` (the value in [`VERSION`](../VERSION)), attach the `stonepi-*.zip` assets, the source tarball and `SHA256SUMS`, summarize from root `CHANGELOG.md`. Tag every cut you publish.

**Build from an LF export, never the Windows working tree.** With `core.autocrlf=true`, both the working tree and a plain `git archive` have CRLF line endings, and a CRLF `install.sh` fails on the Pi at line 2 (`set: pipefail: invalid option`). Export the tag with conversion off, then build the zips from that export (this also keeps `.pytest_cache` / `*.egg-info` out):

```bash
git -c core.autocrlf=false -c core.eol=lf archive --format=tar --prefix=stonePi-<VERSION>/ v<VERSION> | gzip -n -9 > <folder>/stonepi-source-<VERSION>.tar.gz
tar xzf <folder>/stonepi-source-<VERSION>.tar.gz -C <tmp> && (cd <tmp>/stonePi-<VERSION> && python scripts/build_release_zips.py --all --out <folder>)
python scripts/build_release_zips.py --out <folder> --checksums
```

`SHA256SUMS` is required: Pis refuse app updates from a release without it, or whose zip doesn't match it. `build_release_zips.py` writes it; after adding the source tarball to the folder, refresh it with `python scripts/build_release_zips.py --out <folder> --checksums`.
