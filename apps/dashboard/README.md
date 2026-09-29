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
| **Health** | Admin monitor: Watch/alerts, host metrics, disk thresholds, backup + restore drill, remote access, listening ports, recent journal errors, whether each app is responding (and its version), grouped **SYSTEM** / **USER** |
| **Services** | Manage apps: Route + Port, enable/disable for the household; open a service to start/stop/restart. Grouped **SYSTEM** (Dashboard, Auth, Notify, Recover — always available) / **USER** (launcher apps) |
| **Users** | Household accounts, app grants, capability flags (Studio Publish also needs FileServe); denser expandable cards |
| **Settings** | General, **Network** (hostname, exposure, Tailscale, optional ACL), Vault, Automations, Updates, Backup/Restore, About |

## Platform Settings tabs

| Tab | Role |
|-----|------|
| **General** | Appearance, password, view options |
| **Network** | Appliance hostname (`NAME.local`); LAN vs internet-facing exposure; Tailscale remote access (Enable → Connect → auth link); optional Tailscale ACL apply from Vault API keys |
| **Vault** | Encrypted secrets (session, LLM, Bright Data, Google, webhook, ntfy, …). TRMNL webhooks are also editable on each Display in **Notify → Displays**; ntfy under **Notify → Destinations** |
| **Automations** | USB→backup; Health/backup→Display push (Notify `POST /api/push-trmnl`) |
| **Updates** | StonePi GitHub Release zips for the platform and every app in `APP_CATALOG`, grouped System / Apps; the system apps (Dashboard, Auth, Notify, Recover) update with the platform zip, user apps one by one. Apps' own updaters are hidden under StonePi. OS security is separate (unattended-upgrades) |
| **Backup** | Local schedule (one on-disk copy) + USB `STONEPI-BACKUP`; restore drill; failover; restore local or USB snapshots. When Dashboard is down, the **Recovery Console** ([Recover](../recover/README.md)) takes over `/` |
| **About** | What StonePi is |

Display / TRMNL setup (one Display per screen, screen builder, webhooks) and Destinations live in **[Notify](../notify/README.md)**. Old Dashboard URLs (`/settings?tab=display`, `/settings/display`) redirect there.

## Related

- Auth: [apps/auth/README.md](../auth/README.md)
- Notify: [apps/notify/README.md](../notify/README.md)
- Recover (Recovery Console): [apps/recover/README.md](../recover/README.md)
- Security: [deploy/SECURITY.md](../../deploy/SECURITY.md)
- Install: [deploy/INSTALL.md](../../deploy/INSTALL.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
