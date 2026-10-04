# StonePi SD-card recovery

StonePi backups are consistent snapshots, not a live `dd` of the running card.

For a brand-new OS install of the apps themselves, follow [../INSTALL.md](../INSTALL.md) first.

## Backup kinds

| Kind | Where | Retention | Purpose |
|------|--------|-----------|---------|
| **Local** | `/var/backups/stonepi/current` | Single copy (replaced each run) | Convenience restore / Recover when the SD still boots |
| **USB (offline)** | `/mnt/stonepi-backup/RaspberryPi-Backup/<stamp>/` | Accumulates stamps on the stick | Survives SD failure |

Local does **not** survive SD-card death — keep USB offline backups for disasters.

Enable and schedule local backups under **Dashboard → Settings → Backup**. USB backups still start automatically when you plug in a stick labelled `STONEPI-BACKUP`.

## Granular restore (preferred)

1. Flash Raspberry Pi OS onto a replacement SD card and boot it.
2. Copy this repository onto the Pi (see [INSTALL.md](../INSTALL.md)).
3. Run `sudo bash deploy/install.sh --hostname stonepi`.
4. Plug in the USB drive labelled `STONEPI-BACKUP`.
5. Mount it and run:
   `sudo stonepi-restore /mnt/.../RaspberryPi-Backup/TIMESTAMP`
6. Reboot.

This restores `/var/lib/stonepi` (including `notify/` Displays & Destinations JSON and the vault tree), `/etc/stonepi`, and the Nginx site. Application code comes from the installer. Restore chowns app data for all catalog users including `stonepi-notify`.

## Restore drill checklist

Automated: `sudo stonepi-restore-drill` (or Dashboard → Settings → Backup → **Run restore drill**). Picks the newest local or USB snapshot when no path is given.

After restore (or periodically on a spare card), verify:

1. `stonepi status` — all catalog units active (auth, dashboard, notify, apps).
2. Open Health (`/overview`) — apps responding, disk/backup look sane.
3. **Destinations** — `/notify/settings?tab=destinations`:
   - ntfy topic / server still present; vault key `STONEPI_NTFY_TOKEN` configured if you used auth.
   - TRMNL webhook still present; vault key `DISPLAY_WEBHOOK_URL` configured if you used TRMNL.
4. Optional: send a test ntfy from Destinations (or trigger a known app event) and confirm a TRMNL push if configured.
5. Confirm Displays list still has the built-in Dashboard display.

Vault secrets live under `/var/lib/stonepi/vault` and are included in the applications rsync; do not copy credentials into git or logs.

## In-Dashboard restore

Settings → Backup lists **local + USB** snapshots. **Restore** runs `stonepi-restore` via the privileged helper (apps stop → rsync → restart). Prefer a successful restore drill first.

## Recovery Console (Recover)

When Dashboard is unhealthy, nginx redirects `/` to `/recover/` (also `http://stonepi.local:8099/` if nginx is down).

- Sign-in matches the StonePi login look; subtitle **Recovery Console**. More: [apps/recover/README.md](../../apps/recover/README.md).
- Username **`stonepi`**; password lives only in root-only **`/etc/stonepi/recover.passwd`** (set it from Dashboard → Settings → Vault) — **not** your portal or OS password.
- Admins already signed in on the portal may skip the form via SSO cookie.
- Home lists local and USB snapshots for restore.

## Full image restore (optional)

If you also keep an `rpi-clone` or RonR `image-backup` image on the same USB disk, restore that image to the new card first, then boot. Only take those images while `stonepi-backup` has stopped the application units.

Do not `dd` a mounted live SD card.

## USB disk identity

The backup script looks for filesystem **label** `STONEPI-BACKUP` (override in `/etc/stonepi/backup.conf`). It never uses `/dev/sda1` by name.

Format a USB disk once:

```bash
sudo mkfs.ext4 -L STONEPI-BACKUP /dev/sdX1
```

## Run a backup

**USB**

1. Format a USB disk with label `STONEPI-BACKUP`.
2. Plug it into the Pi — a udev rule starts `stonepi-backup` automatically.
3. Or run manually: `sudo systemctl start stonepi-backup`.

**Local**

1. Dashboard → Settings → Backup → enable schedule (daily/weekly) and save.
2. Or **Run local backup now** / `sudo systemctl start stonepi-local-backup`.

Stamps: `/var/lib/stonepi/last-local-backup.txt` and `last-usb-backup.txt`.

## Library (offline content)

**Always in every backup:** Library settings (`library.sqlite`), the content list (`backup-manifest.json`), `library.xml`, the Kiwix unit drop-in, the Library drive's fstab line (`manifests/fstab-library.txt`) and the Kiwix `.deb` packages (`packages/kiwix/`), so a restore can reinstall the reader without internet.

**Never in the per-run copy:** the ZIM files themselves (`/var/lib/stonepi/library/zim/` is excluded).

**Opt-in content backup** (Library → Settings → Backup, `INCLUDE_LIBRARY_CONTENT=1` in `backup.conf`): USB backups also copy ZIMs into one shared store, `RaspberryPi-Backup/library-content/`, beside the timestamped runs. Only new or updated files are copied (checked against the recorded size + SHA-256). Removed titles are pruned after a successful copy. The store can be on the same drive as the content; that's a full second copy. A copy that doesn't fit or fails marks the run `STATUS=partial`, and the platform data is still good.

**On restore** (`stonepi-restore DIR`):

1. Kiwix is reinstalled from `packages/kiwix/*.deb` (falls back to apt).
2. The Library drive's fstab line is restored if that drive is plugged in.
3. ZIMs are copied back from `library-content/` into the recorded content folder. If that drive isn't connected, they go to the microSD (`/var/lib/stonepi/library/zim`).
4. On start, the Library app reconciles: it finds files that moved and restarts the reader. Anything still missing shows as **Missing**, with Remove, or install again from Browse.

Skip the content copy with `stonepi-restore DIR --no-library-content`. The restore drill checks the content store against its manifest (sizes only).
