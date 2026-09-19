# StonePi SD-card recovery

StonePi backups are consistent snapshots, not a live `dd` of the running card.

For a brand-new OS install of the apps themselves, follow [../INSTALL.md](../INSTALL.md) first.

## Granular restore (preferred)

1. Flash Raspberry Pi OS onto a replacement SD card and boot it.
2. Copy this repository onto the Pi (see [INSTALL.md](../INSTALL.md)).
3. Run `sudo bash deploy/install.sh --hostname stonepi`.
4. Plug in the USB drive labelled `STONEPI-BACKUP`.
5. Mount it and run:
   `sudo stonepi-restore /mnt/.../RaspberryPi-Backup/TIMESTAMP`
6. Reboot.

This restores `/var/lib/stonepi`, `/etc/stonepi`, and the Nginx site. Application code comes from the installer.

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

1. Format a USB disk with label `STONEPI-BACKUP` (see below).
2. Plug it into the Pi — a udev rule starts `stonepi-backup` automatically.
3. Or run manually: `sudo systemctl start stonepi-backup`.

The dashboard only reports the last backup stamp; it does not replace Cockpit / journalctl.
