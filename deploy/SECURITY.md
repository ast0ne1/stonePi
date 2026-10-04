# StonePi security notes

StonePi is a private hub on your home network. Default install is **trusted LAN**. Use these controls before exposing StonePi beyond your network.

## First boot

1. Sign in as `admin` with the random password the installer prints at the end (generated only when no accounts exist yet; kept for root in `/etc/stonepi/initial-admin.txt`, mode `0600` — `sudo cat` it if you missed it). The installer passes it to Auth through `/etc/stonepi/auth.env` only, not the shared `stonepi.env` every app loads.
2. Change the admin password under **Settings → General → Your password** (a banner stays until you do — browsing is not blocked). `initial-admin.txt` is not updated afterwards; delete it once changed if you like.
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

`install.sh` installs Tailscale from its signed apt repository (`pkgs.tailscale.com`, key in `/usr/share/keyrings/tailscale-archive-keyring.gpg`; no `curl | sh`) and enables `tailscaled` so the appliance is ready out of the box. If that fails the install carries on with a warning and the ready check lists it; re-run the installer to retry. Admins only **Enable → Connect → approve the auth link** under **Settings → Network**. That does **not** change nginx routes or LAN URLs (`stonepi.local`, LAN IP) — Tailscale is an extra path onto the same edge.

Prefer Tailscale with exposure left on **Home network**. Use **Internet-facing** only for true public HTTPS / raw port exposure.

After Tailscale is connected, StonePi enables **Tailscale Serve** so the MagicDNS name works as **`https://<name>.ts.net/`** (TLS terminated by Tailscale, proxied to local nginx on port 80). Plain **`http://`** to the MagicDNS name or Tailscale IP also works when your client is on the same tailnet. LAN URLs (`stonepi.local`) are unchanged.

## Network

- App processes bind **`HOST=127.0.0.1`**. Do not publish ports 8001–8011 / 8004–8006 publicly; only nginx (or a tunnel) should be reachable.
- Dashboard, Library and Notify run with **`NoNewPrivileges=no`** so they can use NOPASSWD sudo — each only for fixed root helpers in `/usr/local/sbin`: Dashboard for `stonepi-service-helper` (start/stop/restart/is-active/logs of one validated `stonepi-*` unit; no sudoers wildcards on `systemctl`/`journalctl`), `stonepi-tailscale`, `stonepi-hostname`, `stonepi-backup-helper`, `stonepi-tailscale-acl`, `stonepi-update-helper`; Library for `stonepi-library-helper`; Notify for `stonepi-carthing-helper`. Other app units keep `NoNewPrivileges=yes`. The installer's ready check verifies Notify's unit setting.
- App virtualenvs (`/opt/stonepi/apps/*/.venv`) are `root:root`, read-only to service users: root runs their `pip`/`python` during install and updates.
- Cockpit on **`https://…:9090`** is separate — firewall it off the internet. Overview links use HTTPS; the installer upgrades old `http://` `COCKPIT_URL` values.
- Access via **`stonepi.local`**, Pi LAN IP, or router LAN DNS (e.g. **`stonepi.home`**). Portal login and Home links follow the host you typed.
- `stonepi.local` needs Avahi (`avahi-daemon` + `avahi-utils`). If `.local` fails, use the Pi LAN IP or router DNS; `sudo systemctl restart avahi-daemon` often restores IPv4 mDNS.
- Rate limits trust **`X-Real-IP`** from nginx (not client-supplied leftmost `X-Forwarded-For`).

## Host firewall (nftables)

Installer applies [`nftables/stonepi.nft`](nftables/stonepi.nft) via [`nftables/install-firewall.sh`](nftables/install-firewall.sh):

| Allow | Notes |
|-------|--------|
| TCP 22 | SSH |
| TCP 80, 443 | nginx |
| TCP 8099 | Recover (Recovery Console) escape hatch — private source ranges only (RFC 1918, link-local, Tailscale `100.64.0.0/10`, IPv6 ULA/link-local) |
| UDP 41641 + `tailscale0` | Tailscale |
| UDP 5353 | mDNS |
| TCP 9090 | Cockpit — **not** via `tailscale0` (dropped before the `tailscale0` accept; LAN / local only) |

Default input policy is **drop**. The rules live in their own `table inet stonepi`, which the installer replaces on its own (`delete table` + re-add) — no `flush ruleset`, so tailscaled's tables survive a re-install.

Recover listens on `0.0.0.0:8099` over **plain HTTP** on purpose: it is the break-glass console for when nginx or the apps are down, so it cannot sit behind them. It is a root console (HTTP Basic, user `stonepi`), hence the source-range restriction above; LAN clients reaching the Pi only by a global IPv6 address fall back to IPv4. Do not port-forward 8099. Prefer `http://stonepi.local/recover/` (through nginx) when nginx is up. Recover's login lockout (5 failures per client IP, 30 overall, per 15 minutes) needs the real client address, but every nginx request arrives from loopback, as would one from any compromised local app. So nginx proves itself with a shared secret: the installer generates `/etc/stonepi/recover-proxy.token` once (`root:root 0600`) and rebuilds `/etc/nginx/snippets/stonepi-recover-proxy.conf` (`root 0600`, read by nginx's root master process) from it on every run; `location /recover/` includes the snippet, which sends `X-StonePi-Proxy`. Recover trusts `X-Real-IP` / `X-Forwarded-Host` only from loopback **with** a matching token (constant-time compare, file re-read when it changes). Any other local caller is keyed on its socket address, so all of them share one `127.0.0.1` bucket and can't rotate a forged `X-Real-IP` past the per-IP limit. If the token file is missing, Recover trusts no proxy headers (nginx traffic then shares that bucket too) and logs a warning; re-run the installer. The Recover password itself must be at least 12 characters when set from Dashboard → Settings → Vault. App processes remain on **`127.0.0.1`** (8001–8012). Health → Listening compares `ss` against this expected set (plus Avahi/Tailscale process names and Tailscale companion / `100.x` binds).

Re-apply: `sudo bash /opt/stonepi/deploy/nftables/install-firewall.sh`

## X3 / CrossPoint on the internet

1. Prefer **Tailscale** (Settings → Network) or Cloudflare Tunnel so the reader stays private.
2. If public: set **Internet-facing** in Settings → Network, set a strong **catalog / sync token** in NewsCast Settings → Reader, and configure the reader with Basic/Bearer/`?token=`.
3. Do not force catalog login on LAN if CrossPoint crashes — use public mode only when exposed.

## Vault

- Directory: `STONEPI_VAULT_DIR` (Pi: `/var/lib/stonepi/vault`), owner `stonepi-dash`, group `stonepi-vault`, dir mode `2770` (setgid).
- Files (`vault.key`, `secrets.enc`): mode `660` — every app service user is in `stonepi-vault` and can read **and** save secrets (apps store their own ntfy / Bright Data / OAuth keys).
- **Trust boundary:** the Vault is shared by design, so a compromise of any one app exposes every Vault secret. Keep app API keys there, but never a secret that grants root or more than an app already has. Root-only secrets live outside it, `root:root` mode `0600` under `/etc/stonepi`: the Recover console password (`recover.passwd`, set from Dashboard through `stonepi-backup-helper recover-passwd-set`) and the first admin password (`initial-admin.txt`). The installer moves a Recover password left in the Vault by older releases into `recover.passwd` and deletes the Vault key.
- Migrator: `scripts/migrate_secrets_to_vault.py` (run by `install.sh`); installer re-`chown`s after migrate.
- Apps prefer Vault over env/DB for session and API secrets.

## Studio

LLM keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, optional `STUDIO_LLM_BASE_URL`) live in Vault only. Generated sites must satisfy FileServe hosting rules (see Studio prompt / `.cursor/skills/studio-fileserve`).

## Backup

USB labelled `STONEPI-BACKUP` holds application data — treat the stick as sensitive.
