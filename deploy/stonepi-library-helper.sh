#!/bin/bash
# Privileged helper for the Library app (sudo, user stonepi-library only).
# Every action prints one JSON line last; non-zero exit on failure.
# Usage:
#   stonepi-library-helper status
#   stonepi-library-helper install-kiwix | remove-kiwix
#   stonepi-library-helper kiwix start|stop|restart
#   stonepi-library-helper set-storage PATH
#   stonepi-library-helper prepare-folder PATH
#   stonepi-library-helper mount-drive UUID
#   stonepi-library-helper unmount-drive UUID
#   stonepi-library-helper backup-content on|off
set -euo pipefail

UNIT="stonepi-kiwix.service"
DROPIN_DIR="/etc/systemd/system/stonepi-kiwix.service.d"
DROPIN="$DROPIN_DIR/10-storage.conf"
APP_USER="stonepi-library"
LIB_MOUNT="/mnt/stonepi-library"
BACKUP_MOUNT="/mnt/stonepi-backup"
BACKUP_LABEL="STONEPI-BACKUP"
BACKUP_CONF="/etc/stonepi/backup.conf"
FSTAB_TAG="# stonepi-library"
DRIVE_SUBDIR="StonePi-Library/zim"
# set-storage only ever touches folders under these roots (it chowns them).
ALLOWED_ROOTS=("/var/lib/stonepi/library" "/mnt" "/media" "/srv")

# apt runs in its own transient unit: run straight from sudo it would sit in
# the Library's cgroup (MemoryMax) and die mid-install if the app restarts.
apt_run() {
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --quiet --wait --collect --unit="stonepi-library-apt-$$" -p Environment=DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 "$@"
  else
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 "$@"
  fi
}

# Desktop automounts sit under /media/<user> (0750 + an ACL for that user):
# let the Library and its reader pass through (traverse only, no listing).
grant_traverse() {
  local p="$1" dir
  [[ "$p" == /media/* ]] || return 0
  command -v setfacl >/dev/null 2>&1 || return 1
  dir="$(dirname "$p")"
  while [[ "$dir" != "/media" && "$dir" != "/" ]]; do
    setfacl -m u:"$APP_USER":x,u:stonepi-kiwix:x "$dir" || return 1
    dir="$(dirname "$dir")"
  done
}

json_escape() {
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/ }"
  printf '%s' "$s"
}

die() {
  printf '{"ok": false, "error": "%s"}\n' "$(json_escape "$1")"
  exit 1
}

ok() {
  # ok [extra JSON members, already escaped]
  if [[ -n "${1:-}" ]]; then
    printf '{"ok": true, %s}\n' "$1"
  else
    printf '{"ok": true}\n'
  fi
}

include_content() {
  if [[ -f "$BACKUP_CONF" ]] && grep -Eq '^INCLUDE_LIBRARY_CONTENT=(1|true|yes)$' "$BACKUP_CONF"; then
    echo true
  else
    echo false
  fi
}

allowed_path() {
  local p="$1" root
  # Plain characters only: the path goes into a unit file (a newline would add
  # directives) and RequiresMountsFor= is space-separated.
  [[ "$p" =~ ^/[A-Za-z0-9._/-]+$ ]] || return 1
  [[ "$p" != *"/../"* && "$p" != *"/.." && "$p" != *"/./"* && "$p" != *"//"* ]] || return 1
  case "$p" in
    # The backup drive holds every app's data: only its Library folder.
    "$BACKUP_MOUNT"/*) [[ "$p" == "$BACKUP_MOUNT/StonePi-Library" || "$p" == "$BACKUP_MOUNT/StonePi-Library/"* ]] || return 1 ;;
  esac
  for root in "${ALLOWED_ROOTS[@]}"; do
    # Strictly below a root (never the root itself, nor a bare mountpoint).
    if [[ "$p" == "$root/"* ]]; then
      case "$p" in /mnt/*/*|/media/*/*|/srv/*|/var/lib/stonepi/library/*) return 0 ;; esac
    fi
  done
  return 1
}

# Create PATH and hand it to the app without ever following a symlink: each
# component is opened with O_NOFOLLOW relative to its parent, then fchown/fchmod
# act on that descriptor, so swapping a component for a link can't redirect it.
prepare_dir() {
  python3 - "$1" "$APP_USER" <<'PY'
import os, pwd, stat, sys
path, user = sys.argv[1], sys.argv[2]
pw = pwd.getpwnam(user)
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
fd = os.open("/", flags)
parts = [p for p in path.split("/") if p]
try:
    for i, part in enumerate(parts):
        try:
            os.mkdir(part, 0o2750, dir_fd=fd)
            created = True
        except FileExistsError:
            created = False
        nfd = os.open(part, flags, dir_fd=fd)
        os.close(fd)
        fd = nfd
        st = os.fstat(fd)
        last = i == len(parts) - 1
        if last and not created and st.st_uid != pw.pw_uid:
            # An existing folder is only handed over when it holds nothing but
            # library files: never someone else's folder that happens to sit
            # under /mnt, /media or /srv.
            for name in os.listdir(fd):
                est = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not (name.endswith((".zim", ".zim.part")) and stat.S_ISREG(est.st_mode)):
                    sys.exit("folder already holds other files")
        # Hand over the final folder, and folders we just created below an
        # existing mount (StonePi-Library/), never pre-existing system dirs.
        if last or (created and st.st_uid == 0):
            try:
                os.fchown(fd, pw.pw_uid, pw.pw_gid)
                os.fchmod(fd, 0o2750)
            except OSError:
                pass  # exFAT: owners come from the mount options
finally:
    os.close(fd)
PY
}

write_dropin() {
  local path="$1"
  mkdir -p "$DROPIN_DIR"
  cat > "$DROPIN" <<EOF
# Written by stonepi-library-helper: wait for the content folder's mount.
[Unit]
RequiresMountsFor=$path
EOF
  systemctl daemon-reload
}

cmd="${1:-}"
case "$cmd" in
  status)
    installed=false
    version=""
    if command -v kiwix-serve >/dev/null 2>&1; then
      installed=true
      version="$(kiwix-serve --version 2>/dev/null | grep -m1 -Eo '[0-9]+\.[0-9]+(\.[0-9]+)?' || true)"
    fi
    active=false
    systemctl is-active --quiet "$UNIT" 2>/dev/null && active=true
    ok "\"kiwix_installed\": $installed, \"kiwix_version\": \"$(json_escape "$version")\", \"kiwix_active\": $active, \"include_content\": $(include_content)"
    ;;

  install-kiwix)
    export DEBIAN_FRONTEND=noninteractive
    if ! command -v kiwix-serve >/dev/null 2>&1; then
      apt_run update -qq >/dev/null 2>&1 || true
      apt_run install -y -qq kiwix-tools >/dev/null 2>&1 || die "apt couldn't install kiwix-tools — check the Pi's internet connection"
    fi
    command -v kiwix-serve >/dev/null 2>&1 || die "kiwix-serve not found after install"
    systemctl daemon-reload
    ok
    ;;

  remove-kiwix)
    systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
    rm -f "$DROPIN"
    rmdir "$DROPIN_DIR" 2>/dev/null || true
    export DEBIAN_FRONTEND=noninteractive
    apt_run remove -y -qq kiwix-tools >/dev/null 2>&1 || true
    systemctl daemon-reload
    ok
    ;;

  kiwix)
    case "${2:-}" in
      start|restart)
        command -v kiwix-serve >/dev/null 2>&1 || die "Kiwix isn't installed"
        systemctl enable "$UNIT" >/dev/null 2>&1 || true
        systemctl restart "$UNIT" || die "stonepi-kiwix didn't start — see journalctl -u stonepi-kiwix"
        ok
        ;;
      stop)
        systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
        ok
        ;;
      *) die "Usage: kiwix start|stop|restart" ;;
    esac
    ;;

  prepare-folder)
    # Create and hand over a custom folder before the app checks it (its
    # parents are root's, so the app can't create it itself).
    path="${2:-}"
    allowed_path "$path" || die "That folder isn't allowed for library storage"
    prepare_dir "$path" || die "Couldn't prepare that folder (is part of it a link, or does it already hold other files?)"
    grant_traverse "$path" || die "Couldn't open the way to that folder for the Library (install the acl package)"
    ok
    ;;

  set-storage)
    path="${2:-}"
    allowed_path "$path" || die "That folder isn't allowed for library storage"
    prepare_dir "$path" || die "Couldn't prepare that folder (is part of it a link, or does it already hold other files?)"
    grant_traverse "$path" || die "Couldn't open the way to that folder for the Library (install the acl package)"
    write_dropin "$path"
    ok
    ;;

  mount-drive)
    uuid="${2:-}"
    [[ "$uuid" =~ ^[A-Za-z0-9-]{4,64}$ ]] || die "Bad drive id"
    dev="$(blkid -U "$uuid" 2>/dev/null || true)"
    [[ -n "$dev" ]] || die "That drive isn't connected"
    fstype="$(blkid -s TYPE -o value "$dev" 2>/dev/null || true)"
    label="$(blkid -s LABEL -o value "$dev" 2>/dev/null || true)"
    case "$fstype" in
      ext4|exfat) ;;
      vfat) die "FAT32 can't hold files over 4 GB — reformat the drive as ext4 or exFAT" ;;
      *) die "$fstype isn't supported — use ext4 or exFAT" ;;
    esac
    root_disk="$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null || true)"
    this_disk="$(lsblk -no PKNAME "$dev" 2>/dev/null || true)"
    [[ -n "$root_disk" && "$root_disk" == "$this_disk" ]] && die "That's the system disk"

    if [[ "$label" == "$BACKUP_LABEL" ]]; then
      # Sharing with backups: exFAT has no permissions, so backup files (other
      # apps' data) would become readable by the Library. ext4 only.
      [[ "$fstype" == "ext4" ]] || die "To share the backup drive with the Library it must be ext4"
      mp="$BACKUP_MOUNT"
    else
      # One mountpoint per drive, so a move can read the old drive while
      # writing the new one (switching drives never reuses a busy mountpoint).
      mp="$LIB_MOUNT-${uuid:0:8}"
    fi

    current="$(findmnt -rn -S "UUID=$uuid" -o TARGET 2>/dev/null | head -n1 || true)"
    if [[ -n "$current" && "$current" != "$mp" ]]; then
      # Already mounted elsewhere (desktop automount): use it as is.
      [[ "$current" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "That drive is mounted at an unusual path — unmount it first"
      mp="$current"
    else
      busy="$(findmnt -rn -M "$mp" -o SOURCE 2>/dev/null || true)"
      if [[ -n "$busy" && "$busy" != "$dev" ]]; then
        die "$mp already holds another drive"
      fi
      opts="defaults,nofail,x-systemd.device-timeout=10s"
      pass=2
      if [[ "$fstype" == "exfat" ]]; then
        uid="$(id -u "$APP_USER")"
        gid="$(id -g "$APP_USER")"
        opts="$opts,uid=$uid,gid=$gid,umask=0027"
        pass=0
      fi
      tmp="$(mktemp)"
      grep -v -- "$FSTAB_TAG $uuid" /etc/fstab > "$tmp" || true
      # Don't double-mount the backup drive if fstab already has it.
      if ! grep -Eq "^[^#]*[[:space:]]$mp[[:space:]]" "$tmp"; then
        echo "UUID=$uuid $mp $fstype $opts 0 $pass $FSTAB_TAG $uuid" >> "$tmp"
      fi
      cat "$tmp" > /etc/fstab
      rm -f "$tmp"
      systemctl daemon-reload
      mkdir -p "$mp"
      findmnt "$mp" >/dev/null 2>&1 || mount "$mp" || die "Couldn't mount the drive"
    fi
    prepare_dir "$mp/$DRIVE_SUBDIR" || die "Couldn't create the library folder on that drive"
    grant_traverse "$mp/$DRIVE_SUBDIR" || die "Couldn't open the way to that drive for the Library (install the acl package)"
    ok "\"mountpoint\": \"$(json_escape "$mp")\", \"fstype\": \"$fstype\""
    ;;

  unmount-drive)
    uuid="${2:-}"
    [[ "$uuid" =~ ^[A-Za-z0-9-]{4,64}$ ]] || die "Bad drive id"
    tmp="$(mktemp)"
    grep -v -- "$FSTAB_TAG $uuid" /etc/fstab > "$tmp" || true
    cat "$tmp" > /etc/fstab
    rm -f "$tmp"
    systemctl daemon-reload
    # Only the Library's own mountpoint for that drive; the backup drive stays
    # mounted as the backup scripts expect.
    mp="$LIB_MOUNT-${uuid:0:8}"
    if findmnt "$mp" >/dev/null 2>&1; then
      umount "$mp" || die "Couldn't unmount the drive (in use?)"
      rmdir "$mp" 2>/dev/null || true
    fi
    ok
    ;;

  backup-content)
    case "${2:-}" in
      on) value=1 ;;
      off) value=0 ;;
      *) die "Usage: backup-content on|off" ;;
    esac
    mkdir -p "$(dirname "$BACKUP_CONF")"
    touch "$BACKUP_CONF"
    if grep -q '^INCLUDE_LIBRARY_CONTENT=' "$BACKUP_CONF"; then
      sed -i "s/^INCLUDE_LIBRARY_CONTENT=.*/INCLUDE_LIBRARY_CONTENT=$value/" "$BACKUP_CONF"
    else
      echo "INCLUDE_LIBRARY_CONTENT=$value" >> "$BACKUP_CONF"
    fi
    chmod 644 "$BACKUP_CONF"
    ok "\"include_content\": $(include_content)"
    ;;

  *)
    die "Usage: stonepi-library-helper status|install-kiwix|remove-kiwix|kiwix|set-storage|prepare-folder|mount-drive|unmount-drive|backup-content"
    ;;
esac
