# FileServe

**Part of [StonePi](../../README.md)** — host and share pages and files on your home network.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/files/ |
| Windows `run-dev.bat` | http://127.0.0.1:8002/ |

Shared StonePi sign-in. On the Pi, **Users**, **Updates**, and **Backup** live under Dashboard Settings. Hosted page URLs stay public unless you require a password on that page.

## Why it’s in StonePi

Household “put it on a URL” without cloud hosting — HTML, PDF, Word, or multi-file zip sites (games, tools, guides). **Studio** publishes here with the same keep-until and password options as Hosted Pages.

## Platform notes

- Paths are under the FileServe prefix: admin pages at `/files/<slug>`, household users at `/files/u/<username>/<slug>` (exact prefix depends on nginx; Status/QR copy the live URL).
- **Studio** posts zips to `POST /api/studio/publish` (same session + CSRF).
- Under StonePi, bind is loopback; nginx serves `/files/`. Per-app HTTPS is for **solo** runs only.

<p align="center">
  <img src="docs/screenshots/login.png?v=0.0.0.5" alt="FileServe sign-in screen on a phone" width="280" />
</p>

## What it does

- Hosts one HTML, PDF, Word, or zip site per public path
- Zip uploads unpack as one **Site** card (`index.html` required); remove deletes the whole folder
- Optional **username + password** per page (off by default)
- Optional **Keep until** (1 week, 1 month, 3 months, 6 months, or custom date; default = until you delete)
- Disable a page to hide the URL without deleting files
- Replace the file later without changing the path
- Label, description, and path on Add / Edit; path defaults from the label
- Public `/browse` listing of enabled, non-expired titles
- Themes / palettes shared with other StonePi apps

## Web UI

| Tab | What it is for |
| --- | --- |
| **Pages** | Labels, paths, QR, search/sort, Enable/Disable, Open, Edit, download/print, Remove |
| **Add** | Upload `.html` / `.pdf` / `.docx` / `.zip`, or **Add from URL** (pack into a Site zip) |
| **Settings** | Appearance and password; Users/Backup/Update when solo |

## Paths

| Path | Who it belongs to |
| --- | --- |
| `/slug` (under `/files` on StonePi) | Admin / household-root pages |
| `/u/<username>/slug` | That user’s pages |
| `/browse` | Public list of live titles |

## Zip sites

- `index.html` at the zip root, or inside one top-level folder (unwrapped).
- One Hosted Pages card per Site — not one card per file.
- Relative asset paths only; opening `/slug` redirects to `/slug/`.
- Upload limits and unsafe-path rejection apply (see app Settings / errors).
- Sample HTML games: `games/` + `games/README.md`; pack with `python games/pack.py <name>`.

## Studio publish

Studio builds phone-first SPA / Guide / Game projects and publishes a zip with:

- Label, description, path
- Keep until (including custom date)
- Require a password (username + password)

Republish updates the same FileServe page id when linked.

## Household accounts

On StonePi, create people on **Dashboard → Users** with a FileServe grant. Each person gets storage under their user tree; admins can filter by owner on Pages.

## Solo / standalone run (optional)

Without platform session secret, `run-local.bat` serves FileServe alone (default port **8081**). Solo Pi export/install remains available; prefer StonePi for the full portal.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
.\run-local.bat
```
