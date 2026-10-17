# StonePi release checklist

Use this after Display/Home work is verified on a clean Pi and docs match the tree.

## 1. Docs (done when this list matches the repo)

- [ ] Root [README.md](../README.md) and every `apps/*/README.md`: menu names, permissions table, optional extras
- [ ] [docs/screenshots/](../docs/screenshots/): phone-sized, light, no household names or locations ([docs/README.md](../docs/README.md))
- [ ] Root [CHANGELOG.md](../CHANGELOG.md) + [VERSION](../VERSION); each app's `CHANGELOG.md` heading matches its `__version__`
- [ ] [deploy/SECURITY.md](SECURITY.md) + [nginx/stonepi-public-deny-display.conf](nginx/stonepi-public-deny-display.conf)
- [ ] [`.gitignore`](../.gitignore) — no runtime `data/`, vault, or `.env` in the tree you commit

## 2. Pi smoke (one overlay)

Run the release smoke overlay from Windows: `cmd /c "scripts\push-release-smoke-fixes.cmd -Apply"`. It ships the tree a tag would hold (tracked plus untracked-but-not-ignored files, LF), runs the real `deploy/install.sh` over the existing install and prints PASS/FAIL checks. Hard-refresh the browser afterwards (Ctrl+F5).

## 3. Smoke test (browser)

1. Sign-in page and Home in the new look; Home greets you by your alias and every app fits on a phone screen
2. Home → Reorder: drag, Done, refresh keeps the order; **Default order** resets it
3. Each app: header (bell, app switcher, avatar menu with light/dark/system), bottom bar, app icon as favicon
4. Bell: panel lists recent notifications; **Clear** hides them (Notify → History still has them)
5. Health (`/overview`) shows every app Healthy with its version, plus disk, memory and backup; Health → Cockpit opens
6. Notify → Displays: TRMNL Display with its plan, Push now updates the panel (re-paste the universal template once for the StonePi mark); Car Thing on/off
7. NewsCast: Refresh fills the Briefing source by source without reloading
8. Users: your own Role switch is locked; another account can be changed
9. Open via router DNS (e.g. `http://stonepi.home/`): sign-in and the app switcher stay on that host
10. Dashboard → Settings → AI: providers show as set up, **Test** on a model answers, and NewsCast → Settings → AI can switch AI on
11. Car Thing (if plugged in): Music → Now Playing shows the current track; Test connection in Notify → Displays → Car Thing → Music passes

## 4. Commit (when you ask)

Do **not** commit until you explicitly request it. Summarize the cut from the root `CHANGELOG.md` entry for the version in [`VERSION`](../VERSION).

Before the first commit, dry-run staged paths and confirm no `vault.key`, `secrets.enc`, `*.db`, or `apps/*/data/` runtime files.

## 5. Build release zips

From repo root (versions already in each `app/__init__.py`; the app list comes from `APP_CATALOG`):

```bash
python scripts/build_release_zips.py --versions-table
```

Paste the output under `### App versions in this cut` in the root [CHANGELOG.md](../CHANGELOG.md) — never type the table by hand. Then check, and build:

```bash
python scripts/build_release_zips.py --all --dry-run [-v]   # pre-flight + file list, writes nothing
python scripts/build_release_zips.py --all
```

Every build starts with a **pre-flight**. In a git checkout it stops when `apps/`, `packages/` or `deploy/` hold untracked, non-ignored files: they would be in the zips but missing from the tag tarball the web installer downloads. In any tree it checks that every `$DEST/...` path `deploy/install.sh` uses exists (and, in a checkout, is tracked and in tag `v<VERSION>` once that tag exists). `--allow-dirty` turns these into warnings (local test builds only). The zips never carry caches, venvs, `.env` files (`.env.example` excepted), databases, keys/certificates, `session.secret` / `vault.key` / `secrets.enc`, runtime `data/` folders (bundled `app/data/` ships), logs or tarballs, and text files are written with LF endings.

Assets are **StonePi portal variants** (not standalone app releases):

- `stonepi-<app>-<version>.zip` for every user app (catalog apps without `ships_with: platform`)
- `stonepi-platform-<version>.zip`, which also carries the system apps (`apps/dashboard`, `apps/auth`, `apps/notify`, `apps/recover`)

Each zip includes `STONEPI.txt`. The updater prefers `stonepi-{app}-*.zip` and still accepts legacy `{app}-*.zip` for one cycle.

Do **not** ship ad-hoc Pi overlay helpers (`scripts/push-*-fixes.*`, `scripts/apply-*-on-pi.sh`, `scripts/*-bootstrap-on-pi.sh`) — those stay local-only (gitignored).

## 6. GitHub Release

Tag the platform `v<VERSION>` (the value in [`VERSION`](../VERSION)), attach the `stonepi-*.zip` assets, the source tarball and `SHA256SUMS`, summarize from root `CHANGELOG.md`. Tag every cut you publish.

**Build from an LF export, never the Windows working tree.** With `core.autocrlf=true` and no `.gitattributes`, both the working tree and a plain `git archive` had CRLF line endings, and a CRLF `install.sh` fails on the Pi at line 2 (`set: pipefail: invalid option`). The repo's [`.gitattributes`](../.gitattributes) now pins text files to LF (`.bat`/`.cmd`/`.ps1` CRLF), but keep exporting with conversion off as a second guard. Export the tag with conversion off, then build the zips from that export (this also keeps `.pytest_cache` / `*.egg-info` out):

```bash
git -c core.autocrlf=false -c core.eol=lf archive --format=tar --prefix=stonePi-<VERSION>/ v<VERSION> | gzip -n -9 > <folder>/stonepi-source-<VERSION>.tar.gz
tar xzf <folder>/stonepi-source-<VERSION>.tar.gz -C <tmp> && (cd <tmp>/stonePi-<VERSION> && python scripts/build_release_zips.py --all --out <folder>)
python scripts/build_release_zips.py --out <folder> --checksums
```

`SHA256SUMS` is required: Pis refuse app updates from a release without it, or whose zip doesn't match it. `build_release_zips.py` writes it; after adding the source tarball to the folder, refresh it with `python scripts/build_release_zips.py --out <folder> --checksums`.
