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
| **Home** | A greeting with the person's alias ("Good afternoon, Sam") and an optional welcome message, then tiles for the apps they may open; **Reorder** = drag-to-place (autosave), **Default order** resets it. On a phone every app fits on one screen |
| **Health** | Admin monitor: Watch/alerts, host metrics, disk thresholds, backup + restore drill, remote access, listening ports, recent journal errors, whether each app is responding (and its version), grouped **SYSTEM** / **USER** |
| **Services** | Manage apps: Route + Port, enable/disable for the household; open a service to start/stop/restart. Grouped **SYSTEM** (Dashboard, Auth, Notify, Recover — always available) / **USER** (launcher apps) |
| **Users** | Household accounts with an editable alias, app grants, capability flags (Studio Publish also needs FileServe) and the Phone alerts permission. Admins can't demote, disable or delete themselves |
| **Notifications** | Your own phone alerts (ntfy), opened from **Preferences** in the bell's alerts panel. **← Back** returns to the app you came from, or Home when there's nothing to go back to |
| **Settings** | General, **Network** (hostname, exposure, Tailscale, HTTPS, optional ACL), Vault, **AI**, Automations, Updates, Backup/Restore, About |

## Platform Settings tabs

| Tab | Role |
|-----|------|
| **General** | Appearance (light, dark or system), your name on Home and the welcome message, password, view options |
| **Network** | Appliance hostname (`NAME.local`); LAN vs internet-facing exposure; Tailscale remote access (Enable → Connect → auth link); **Turn on HTTPS**, shown while Tailscale is connected but its MagicDNS name has no HTTPS yet: turn on **HTTPS Certificates** in the Tailscale admin console (DNS page) first, then it sets up Tailscale Serve and fetches the certificate (`stonepi-tailscale serve`), so the first visit isn't the slow one; optional Tailscale ACL apply from Vault API keys |
| **Vault** | Encrypted secrets (session, AI keys for Gemini / Anthropic / OpenAI, Bright Data, Google, webhook, ntfy, Spotify, …). TRMNL webhooks are also editable on each Display in **Notify → Displays**; ntfy under **Notify → Phone alerts**; the Spotify keys are set by **Notify → Displays → Car Thing → Setup** (Music) |
| **AI** | AI providers, model order per tier (Cheap / Standard / Best), per-job overrides, Free/Paid flags, model status and the monthly AI request limit, for every app |
| **Automations** | USB→backup; Health/backup→Display push (Notify `POST /api/push-trmnl`, JSON body) |
| **Updates** | StonePi GitHub Release zips for the platform and every app in `APP_CATALOG`, grouped System / Apps; the system apps (Dashboard, Auth, Notify, Recover) update with the platform zip, user apps one by one. Apps' own updaters are hidden under StonePi. OS security is separate (unattended-upgrades) |
| **Backup** | Local schedule (one on-disk copy) + USB `STONEPI-BACKUP`; restore drill; failover; restore local or USB snapshots. When Dashboard is down, the **Recovery Console** ([Recover](../recover/README.md)) takes over `/` |
| **About** | What StonePi is |

Display / TRMNL setup (one Display per screen, screen builder, webhooks) and phone alerts live in **[Notify](../notify/README.md)**. Old Dashboard URLs (`/settings?tab=display`, `/settings/display`) redirect there.

For the Car Thing's service page, Dashboard answers a signed `POST /api/internal/units/details` (catalog services only): state, since when, memory, restarts by itself and the last few log lines.

## Related

- Auth: [apps/auth/README.md](../auth/README.md)
- Notify: [apps/notify/README.md](../notify/README.md)
- Recover (Recovery Console): [apps/recover/README.md](../recover/README.md)
- Security: [deploy/SECURITY.md](../../deploy/SECURITY.md)
- Install: [deploy/INSTALL.md](../../deploy/INSTALL.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
