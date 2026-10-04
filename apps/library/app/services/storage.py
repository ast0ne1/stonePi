"""Where ZIM files live: microSD default, a USB stick/SSD, or the backup drive."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from app import db
from app.config import (
    BACKUP_LABEL,
    BACKUP_MOUNT,
    DATA_DIR,
    DEFAULT_CONTENT_DIR,
    FILESYSTEMS_OK,
    GIB,
)

KINDS = ("sd", "drive", "custom")
KIND_LABELS = {"sd": "microSD (this Pi)", "drive": "USB drive", "custom": "Custom folder"}


def content_dir() -> Path:
    raw = db.get_setting("storage_path")
    return Path(raw) if raw else DEFAULT_CONTENT_DIR


def kind() -> str:
    value = db.get_setting("storage_kind", "sd")
    return value if value in KINDS else "sd"


def partial_dir() -> Path:
    return content_dir() / ".partial"


def usage(path: Path | None = None) -> dict[str, int]:
    target = path or content_dir()
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        total, used, free = shutil.disk_usage(probe)
    except OSError:
        return {"total": 0, "used": 0, "free": 0}
    return {"total": int(total), "used": int(used), "free": int(free)}


def margin_bytes(total: int) -> int:
    """Safety margin: 5 GB or 5% of the drive, whichever is larger."""
    return max(5 * GIB, int(total * 0.05))


# ---------- block devices (Linux only; empty off the Pi) ----------

def _cmd(args: list[str]) -> str:
    if os.name != "posix":
        return ""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def mount_info(path: Path | None = None) -> dict[str, str]:
    """Filesystem type, source device and mountpoint holding ``path``."""
    target = path or content_dir()
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    out = _cmd(["findmnt", "-T", str(probe), "-no", "FSTYPE,SOURCE,TARGET"]).split()
    if len(out) >= 3:
        return {"fstype": out[0], "source": out[1], "target": out[2]}
    return {"fstype": "", "source": "", "target": ""}


def disk_of(device: str) -> str:
    """Parent disk (e.g. ``sda``) of a partition like ``/dev/sda1``."""
    if not device.startswith("/dev/"):
        return ""
    lines = _cmd(["lsblk", "-no", "PKNAME", device]).split()
    return lines[0] if lines else Path(device).name


def _lsblk() -> list[dict[str, Any]]:
    raw = _cmd(["lsblk", "-J", "-b", "-o", "NAME,PATH,LABEL,UUID,FSTYPE,SIZE,MOUNTPOINT,PKNAME,TYPE,TRAN,MODEL"])
    try:
        tree = json.loads(raw)["blockdevices"]
    except (ValueError, KeyError, TypeError):
        return []
    flat: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]], disk: dict[str, Any] | None) -> None:
        for node in nodes:
            top = node if node.get("type") == "disk" else disk
            node["_disk"] = top
            flat.append(node)
            walk(node.get("children") or [], top)

    walk(tree, None)
    return flat


def system_disk() -> str:
    return disk_of(mount_info(Path("/")).get("source", ""))


def list_drives() -> list[dict[str, Any]]:
    """External filesystems the Library could use (never the system disk)."""
    sysdisk = system_disk()
    drives = []
    for node in _lsblk():
        fstype = (node.get("fstype") or "").lower()
        disk = node.get("_disk") or {}
        if not fstype or fstype in {"swap", "crypto_luks", "linux_raid_member"}:
            continue
        if disk.get("name") == sysdisk:
            continue
        label = node.get("label") or ""
        problem = ""
        if fstype not in FILESYSTEMS_OK:
            problem = "FAT32 can’t hold files over 4 GB — reformat as ext4 or exFAT" if fstype == "vfat" else f"{fstype} isn’t supported — use ext4 or exFAT"
        elif label == BACKUP_LABEL and fstype != "ext4":
            # exFAT has no permissions: sharing would expose other apps' backups.
            problem = "To share the backup drive with the Library it must be ext4"
        drives.append(
            {
                "uuid": node.get("uuid") or "",
                "path": node.get("path") or "",
                "label": label,
                "fstype": fstype,
                "size": int(node.get("size") or 0),
                "mountpoint": node.get("mountpoint") or "",
                "model": (disk.get("model") or "").strip(),
                "disk": disk.get("name") or "",
                "is_backup": label == BACKUP_LABEL,
                "problem": problem,
            }
        )
    return drives


def backup_drive() -> dict[str, Any] | None:
    for drive in list_drives():
        if drive["is_backup"]:
            return drive
    return None


def shares_backup_drive() -> bool:
    """Content and the backup destination sit on the same physical drive."""
    backup = backup_drive()
    if not backup:
        return False
    content_disk = disk_of(mount_info().get("source", ""))
    return bool(content_disk) and content_disk == backup["disk"]


# ---------- validation ----------

def validate(path: Path, *, need_bytes: int = 0) -> list[str]:
    problems: list[str] = []
    resolved = Path(os.path.abspath(path))
    if "RaspberryPi-Backup" in resolved.parts or "StonePi-Library-Backup" in resolved.parts:
        problems.append("That folder is inside the backups — pick another one.")
    if DATA_DIR in resolved.parents and resolved != DEFAULT_CONTENT_DIR:
        # Only zim/ is left out of the per-run backup copy.
        problems.append("Inside the Library’s data folder, only the default zim folder can hold content.")
    stonepi_data = Path("/var/lib/stonepi")
    if os.name == "posix" and stonepi_data in resolved.parents and DATA_DIR not in (resolved, *resolved.parents):
        problems.append("That folder belongs to another StonePi app.")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / f".write-test-{uuid.uuid4().hex[:8]}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        problems.append(f"Can’t write there ({exc.strerror or exc}).")
        return problems
    fstype = mount_info(resolved).get("fstype", "")
    if fstype and fstype not in FILESYSTEMS_OK and resolved != DEFAULT_CONTENT_DIR and DATA_DIR not in resolved.parents:
        if fstype == "vfat":
            problems.append("That drive is FAT32, which can’t hold files over 4 GB. Reformat it as ext4 or exFAT.")
        else:
            problems.append(f"That drive uses {fstype}; use ext4 or exFAT.")
    if need_bytes:
        free = usage(resolved)["free"]
        if free < need_bytes:
            problems.append("Not enough free space there for your current content.")
    return problems


def is_backup_mount(path: Path) -> bool:
    return BACKUP_MOUNT in (path, *path.parents)
