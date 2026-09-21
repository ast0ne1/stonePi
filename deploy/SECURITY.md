# StonePi security notes

Household LAN portal. Default install is **trusted LAN**. Use these controls before exposing StonePi beyond your network.

## First boot

1. Sign in with factory `admin` / `admin`.
2. Change the admin password on **Dashboard → Users** (a banner stays until you do — browsing is not blocked).
3. Prefer Vault for API keys (Dashboard → Settings → Vault).

## Exposure modes

Default is **home network**. Flip anytime under **Dashboard → Settings → Network → Network exposure** (writes `/var/lib/stonepi/exposure`). Apps read that file on each request — **no service restart**.

Installer also seeds `STONEPI_EXPOSURE=lan` in `/etc/stonepi/stonepi.env` as a fallback when the file is missing.

| Mode | Behaviour |
|------|-----------|
| Home network (`lan`) | OPDS / X3 / sync file APIs open on LAN when catalog login is off (CrossPoint-friendly). |
| Internet-facing (`public`) | Catalog token required for OPDS, `/api/x3`, and `/api/v1/files` + device tasks. Firmware that cannot send a token must use Tailscale/VPN. ICS feeds require sign-in. Stricter FileServe URL-fetch SSRF. |

Nginx already denies `/news/api/display`, `/events/api/display`, `/pinboard/api/display`, `/auth/api/display`, `/studio/api/display`, and `/prices/api/display` at the edge (Dashboard scrapes loopback). Optional TLS: [`nginx/stonepi-tls.conf`](nginx/stonepi-tls.conf).

## Tailscale remote access

`install.sh` installs Tailscale and enables `tailscaled` so the appliance is ready out of the box. Admins only **Enable → Connect → approve the auth link** under **Settings → Network**. That does **not** change nginx routes or LAN URLs (`stonepi.local`, LAN IP) — Tailscale is an extra path onto the same edge.

Prefer Tailscale with exposure left on **Home network**. Use **Internet-facing** only for true public HTTPS / raw port exposure.

After Tailscale is connected, StonePi enables **Tailscale Serve** so the MagicDNS name works as **`https://<name>.ts.net/`** (TLS terminated by Tailscale, proxied to local nginx on port 80). Plain **`http://`** to the MagicDNS name or Tailscale IP also works when your client is on the same tailnet. LAN URLs (`stonepi.local`) are unchanged.

## Network

- App processes bind **`HOST=127.0.0.1`**. Do not publish ports 8001–8011 / 8004–8006 publicly; only nginx (or a tunnel) should be reachable.
- Dashboard runs with **`NoNewPrivileges=no`** so admin actions can use NOPASSWD sudo (`systemctl` for app units, `stonepi-tailscale` for remote access). Other app units keep `NoNewPrivileges=yes`.
- Cockpit on **`https://…:9090`** is separate — firewall it off the internet. Overview links use HTTPS; the installer upgrades old `http://` `COCKPIT_URL` values.
- Access via **`stonepi.local`**, Pi LAN IP, or router LAN DNS (e.g. **`stonepi.home`**). Portal login and Home links follow the host you typed.
- `stonepi.local` needs Avahi (`avahi-daemon` + `avahi-utils`). If `.local` fails, use the Pi LAN IP or router DNS; `sudo systemctl restart avahi-daemon` often restores IPv4 mDNS.
- Rate limits trust **`X-Real-IP`** from nginx (not client-supplied leftmost `X-Forwarded-For`).

## X3 / CrossPoint on the internet

1. Prefer **Tailscale** (Settings → Network) or Cloudflare Tunnel so the reader stays private.
2. If public: set **Internet-facing** in Settings → Network, set a strong **catalog / sync token** in NewsCast Settings → Reader, and configure the reader with Basic/Bearer/`?token=`.
3. Do not force catalog login on LAN if CrossPoint crashes — use public mode only when exposed.

## Vault

- Directory: `STONEPI_VAULT_DIR` (Pi: `/var/lib/stonepi/vault`), owner `stonepi-dash`, group `stonepi-vault`, dir mode `2750` (setgid).
- Files (`vault.key`, `secrets.enc`): mode `640` so every app user in `stonepi-vault` can read; only the dashboard owner writes.
- Migrator: `scripts/migrate_secrets_to_vault.py` (run by `install.sh`); installer re-`chown`s after migrate.
- Apps prefer Vault over env/DB for session and API secrets.

## Studio

LLM keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, optional `STUDIO_LLM_BASE_URL`) live in Vault only. Generated sites must satisfy FileServe hosting rules (see Studio prompt / `.cursor/skills/studio-fileserve`).

## Backup

USB labelled `STONEPI-BACKUP` holds application data — treat the stick as sensitive.
