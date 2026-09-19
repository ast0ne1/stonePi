# Dashboard

**Part of [StonePi](../../README.md)** — home launcher and administration for the household portal.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/ |
| Windows `run-dev.bat` | http://127.0.0.1:8010/ |

Not a launcher tile itself (`launcher: false` in the app catalog). After Auth sign-in, this is the front door.

## Why it’s in StonePi

One place for people and apps — separate from **Cockpit** (Linux host admin on `:9090`).

## What it does

| Area | Role |
|------|------|
| **Home** | Launcher tiles for apps the signed-in user may access; **Edit order** = drag-to-place (autosave) |
| **Overview** | Services health, users, backup snapshot (admin) |
| **Services** | Catalog apps, enable/disable, systemd-oriented controls |
| **Users** | Household accounts, app grants, capability flags |
| **Settings** | General (including **network exposure**), Display, Watch, Vault, Automations, Updates, Backup, About |

## Platform Settings tabs

| Tab | Role |
|-----|------|
| **General** | Instance basics; LAN vs internet-facing exposure (writes `/var/lib/stonepi/exposure`) |
| **Display** | TRMNL webhook (or Vault `DISPLAY_WEBHOOK_URL`), panel size (OG 800×480 / V2 1040×780), design presets, landscape preview scaled to the Settings column, **Copy markup** + Push now / schedule |
| **Watch** | healthy / attention / critical rollup for apps + disk + backup age |
| **Vault** | Encrypted secrets (session, LLM, Bright Data, Google, webhook, ntfy, …) |
| **Automations** | USB→backup; Watch/backup→Display push |
| **Updates** | Install GitHub Release zips |
| **Backup** | USB `STONEPI-BACKUP` status / trigger |
| **About** | What StonePi is |

### Display designs

| Design | Behaviour |
|--------|-----------|
| **Status wall** | Fixed ops board: hostname + SYSTEM, five-metric strip, wide Services list (name · detail · count · UP), Alerts, Storage & Backup. B/W-first Framework tokens. |
| **Household focus** | Fixed: Watch + Apps, then product cards (2×2 on OG), thin stats strip, title bar. |
| **Custom** | Freeform drag blocks from the palette; half / full width; × to remove. |

StonePi pushes **`merge_variables` only**. After changing design or markup, use **Copy markup** and paste into the TRMNL Private Plugin Markup editor, then Force Refresh on the device.

Preview matches the panel landscape frame and scales to fit the desktop Settings column (≤1×). Fixed designs use the full column width (palette hidden).

## Related

- Auth: [apps/auth/README.md](../auth/README.md)
- Security: [deploy/SECURITY.md](../../deploy/SECURITY.md)
- Install: [deploy/INSTALL.md](../../deploy/INSTALL.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
