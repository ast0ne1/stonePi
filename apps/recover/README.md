# Recover

**Part of [StonePi](../../README.md)** — the **Recovery Console**: an escape hatch for when Dashboard, Auth or nginx is unhealthy.

| Mode | URL |
|------|-----|
| On the Pi (through nginx) | http://stonepi.local/recover/ |
| On the Pi (direct, if nginx is down) | http://stonepi.local:8099/ |

The app id is `recover` (unit `stonepi-recover`); people see it as **Recovery Console**. It is a system service, not a Home tile (`launcher: False`).

## Why it’s in StonePi

When the portal itself is broken there is no Dashboard to fix it from. Recover is a small, root-owned FastAPI app that stays up on its own port so an admin can see what is wrong, restart things, or restore a backup from a phone — without SSH.

## Signing in

- **Username** `stonepi`, **password** from Vault key **`STONEPI_RECOVER_PASSWORD`** (Dashboard → Settings → Vault). The fallback is `/etc/stonepi/recover.passwd`. This is **not** your portal or OS password.
- First start creates a random password if none exists; the installer prints where to find it.
- Saving the Vault key syncs `recover.passwd`, so the two stay in step.
- A StonePi **admin** session cookie is also accepted when Auth is healthy. The Recover login is for when it isn't.
- Sign-in sets a signed `stonepi_recover` session cookie (12 hours).

## What it does

- **Services** — status of every StonePi unit plus nginx, with **Restart** and **Stop**. Stop is refused for Auth, nginx and Recover itself.
- **Restore** — pick the local backup (`/var/backups/stonepi/current`) or a USB snapshot (`/mnt/stonepi-backup/RaspberryPi-Backup/…`). Runs `stonepi-backup-helper restore`.
- **Clear failover** — turns off the nginx failover that sends `/` to Recover once Dashboard is healthy again.
- **Reboot** the Pi.
- **Logs** — recent journal for Dashboard, Auth, Notify and nginx.
- **Network** — `hostname -I`, `ip -br addr`, and Tailscale status.
- Every destructive action asks for confirmation first.

## How it's wired

| Piece | Where |
|-------|-------|
| Unit | `deploy/systemd/stonepi-recover.service` — runs as root on `0.0.0.0:8099` |
| Firewall | `deploy/nftables/stonepi.nft` allows TCP 8099 (see [SECURITY.md](../../deploy/SECURITY.md)) |
| Failover | the failover monitor + nginx `@dashboard_down` send `/` to `/recover/` while Dashboard is down |
| Releases | no zip of its own — ships inside `stonepi-platform-<version>.zip` (catalog `ships_with: platform`). A Dashboard platform install overlays `apps/recover`. |
| Version | `__version__` in `app/__init__.py`; tracks the platform version (system app) |

Recover must start even when the shared packages are broken. It therefore keeps its own unit list and cache-bust helper, and only *optionally* uses `stonepi_vault` / `stonepi_auth`. A guard test (`packages/stonepi_auth/tests/test_platform_consistency.py`) checks its unit list against `APP_CATALOG`.

## Renamed from Recovery

Before 0.1.8 this was `apps/recovery` (`stonepi-recovery`, `/recovery/`, `/etc/stonepi/recovery.passwd`, Vault `STONEPI_RECOVERY_PASSWORD`). `deploy/stonepi-migrate-renames.sh` and Recover's own startup migrate the old names. nginx 308-redirects `/recovery/`.

More: [deploy/backup/RESTORE.md](../../deploy/backup/RESTORE.md).
