# Install StonePi on a fresh Raspberry Pi OS

This is the path from a blank SD card to apps that start on every boot.

**Prefer a guided checklist?** Open [`walkthrough.html`](walkthrough.html) in your browser (double-click or drag into Chrome/Edge). Progress is saved locally in that browser.

## What you need

- Raspberry Pi 4/5 recommended (3B+ works, EventTrakr Playwright is heavier)
- MicroSD card (16 GB+)
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
- This `stonePi` folder on your PC
- Network (Ethernet or Wi‑Fi set in Imager)

## 1. Flash the OS (Imager)

1. Choose **Raspberry Pi OS** (Bookworm, 64-bit Lite is fine).
2. Open the gear / OS customisation:
   - Set hostname to `stonepi` (optional; the installer can set it too)
   - Enable **SSH** (password or public key)
   - Set username + password (remember these for SSH)
   - Configure Wi‑Fi if you are not using Ethernet
3. Write the image to the SD card.

## 2. Copy StonePi onto the SD card

After Imager finishes, Windows usually remounts the small **boot** partition (often `bootfs`).

**Important:** the card has two partitions. Windows can only read the FAT **boot** one. A large volume that Windows says must be formatted is the Linux root filesystem — **do not format it**. Close that dialog. If `bootfs` does not appear, eject and reinsert the card, or assign a drive letter to the FAT partition in Disk Management.

Copy the whole `stonePi` project folder onto that boot partition so it looks like:

```text
bootfs/
  stonePi/
    apps/
    deploy/
    packages/
    scripts/
    README.md
    …
```

Tips:

- Do **not** copy `.venv` folders (they are Windows-specific). Use `scripts\copy-to-sd.bat E:\` if the boot drive is `E:`.
- Leave out `dist/`, `.git/`, and local `data/` if present.

Eject the card, insert it in the Pi, and power on.

### Alternative: copy over the network (no boot-partition copy)

If Windows never shows a usable boot drive:

1. Flash and boot the Pi with SSH enabled (empty of StonePi is fine).
2. On the Pi: `mkdir -p ~/stonePi`
3. From your PC: `scp -r C:\path\to\stonePi\* USER@stonepi.local:~/stonePi/`
4. SSH in and run `sudo bash ~/stonePi/deploy/install.sh --hostname stonepi`

A guided checklist with the same notes lives in [`walkthrough.html`](walkthrough.html).

## 3. SSH in

From your PC (replace `adam` with the Imager username):

```bash
ssh adam@stonepi.local
```

If `.local` does not resolve yet, use the Pi’s IP from your router.

Confirm the copy is on the boot partition:

```bash
ls /boot/firmware/stonePi/deploy/install.sh
```

On older images the path may be `/boot/stonePi/...`.

## 4. Run the installer

Boot partitions are FAT, so run with `bash` (execute bits are not reliable):

```bash
sudo bash /boot/firmware/stonePi/deploy/install.sh --hostname stonepi
```

Or copy to the home directory first (slightly cleaner for re-runs):

```bash
cp -a /boot/firmware/stonePi ~/stonePi
cd ~/stonePi
sudo bash deploy/install.sh --hostname stonepi
```

The installer will:

- Install system packages (Python, Nginx, Avahi + `avahi-utils`, Cockpit, Playwright libs)
- Configure Avahi for IPv4 mDNS publish (`stonepi.local`)
- Copy the tree to `/opt/stonepi`
- Create `/etc/stonepi/*.env` (with `STONEPI_DATA_DIR` under `/var/lib/stonepi`) and data dirs
- Upgrade legacy `COCKPIT_URL=http://…:9090` to `https://…`
- Create Python venvs and install dependencies
- Install and **enable** systemd units so apps start on boot
- Configure Nginx path routing (remove stock welcome site; works via `stonepi.local` **and** Pi IP)
- Enable USB-plug backup udev rule; leave weekly backup timer **disabled**
- Enable Cockpit
- Run health checks (units active, loopback apps, edge HTTP)

First run can take several minutes (pip + Chromium for EventTrakr).

## 5. Sign in

On another device on the same LAN:

| URL | What |
|-----|------|
| http://stonepi.local/ or http://PI_IP/ or http://stonepi.home/ | Home / app launcher (Dashboard) |
| http://stonepi.local/auth/login | Sign in |
| http://stonepi.local/news/ | NewsCast |
| http://stonepi.local/files/ | FileServe |
| http://stonepi.local/events/ | EventTrakr |
| http://stonepi.local/pinboard/ | Pinboard |
| http://stonepi.local/studio/ | Studio |
| https://stonepi.local:9090 or https://PI_LAN_IP:9090 | Cockpit (Linux admin; HTTPS) |

Prefer a **router DHCP reservation + local DNS** name (e.g. `stonepi.home` on a F@ST / ISP gateway) when `stonepi.local` (Avahi) is flaky. Login and app **Home** links follow whichever host you open — they no longer force `.local`.

`stonepi urls` prints the same list using the Pi hostname.

Default account: **admin** / **admin** — change it under People immediately.

## After install

### Status / restart

```bash
stonepi status
stonepi restart
sudo journalctl -u stonepi-auth -u stonepi-dashboard -f
```

### Re-run the installer

Safe to run again (keeps databases and existing env files):

```bash
cd ~/stonePi   # or /opt/stonepi after first install
sudo bash deploy/install.sh --hostname stonepi
```

When the tree already lives in `/opt/stonepi`, run from there; the installer skips the rsync copy.

### Backup USB

Backup starts automatically when you plug in a USB disk labelled `STONEPI-BACKUP` (udev). There is no weekly timer by default.

1. Format a USB disk with label `STONEPI-BACKUP` (see [backup/RESTORE.md](backup/RESTORE.md)).
2. Plug it into the Pi — apps pause briefly, then resume.
3. Optional manual run: `sudo systemctl start stonepi-backup`

Check the last run:

```bash
sudo journalctl -u stonepi-backup -n 40 --no-pager
cat /var/lib/stonepi/last-backup.txt
```

## Fresh OS checklist

- [ ] SSH enabled in Imager
- [ ] `stonePi` folder on boot partition (without `.venv`)
- [ ] `sudo bash …/deploy/install.sh --hostname stonepi` completed
- [ ] http://stonepi.local/ loads
- [ ] Password changed from `admin` / `admin`
- [ ] Backup USB labelled and tested once

## Public internet exposure (optional)

Default is **home network only**. Install leaves `STONEPI_EXPOSURE=lan` and `/var/lib/stonepi/exposure` as `lan`. When you put StonePi behind a tunnel or public HTTPS, open **Dashboard → Settings → General → Network exposure** and choose **Internet-facing**. Apps tighten reader APIs, SSRF guards, and calendar ICS feeds on the next request — no restart and no nginx edit for display scrapes (those are already denied at the edge).

Optional HTTPS: include [`nginx/stonepi-tls.conf`](nginx/stonepi-tls.conf) (Let’s Encrypt paths). Full checklist: [`SECURITY.md`](SECURITY.md).


## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| `stonepi.local` not found | Wait for Avahi; try the Pi IP or router DNS (`stonepi.home`); `sudo systemctl restart avahi-daemon`; `avahi-resolve -n -4 stonepi.local` (needs `avahi-utils`) |
| `stonepi.local` only on IPv6 / browsers fail | Installer sets `use-ipv4=yes` + `publish-addresses=yes`; restart Avahi; use `http://PI_IP/` or router DNS until mDNS recovers |
| Login / Back to apps jumps to `.local` and fails | Upgrade auth + `stonepi_auth` (portal stays on request host); hard-refresh |
| Cockpit won’t open from Overview | Use `https://…:9090` (not http); installer upgrades legacy `COCKPIT_URL` |
| “Welcome to nginx” on IP or hostname | Re-run installer; or `sudo rm -f /etc/nginx/sites-enabled/default && sudo systemctl reload nginx` |
| 502 Bad Gateway | `stonepi status` then `sudo journalctl -u stonepi-auth -e` — often a crash-loop |
| EventTrakr sync: Playwright executable missing | `sudo -u stonepi-events HOME=/opt/stonepi /opt/stonepi/apps/eventtrakr/.venv/bin/python -m playwright install chromium` then restart `stonepi-eventtrakr` |
| `PermissionError` on `…/apps/*/data` | Re-run installer (sets `STONEPI_DATA_DIR` + ownership). Or: `sudo chown` the service user on that `data` dir |
| Install fails on apt | `sudo apt-get update` and re-run; check network/DNS |
| Scripts fail with `$'\r'` | Installer strips CR; re-run with `bash`, not a broken `./` from FAT |
| Permission errors under `/opt/stonepi` | Re-run installer as root (`sudo bash …`) |

Application units (enabled on boot):

- `stonepi-auth`
- `stonepi-dashboard`
- `stonepi-newscast`
- `stonepi-fileserve`
- `stonepi-eventtrakr`
- `stonepi-pinboard`
- `stonepi-studio`
