"""Library content in StonePi backups: the opt-in setting and capacity checks.

Settings, the content list and library.xml are always in the platform backup.
ZIM files are copied only when the user turns that on, only to the USB backup
drive, into one shared ``library-content/`` store (not per timestamped run).
The drive may be the same one the content lives on; that makes a full second
copy, so it is the user's call and is checked like any other destination.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from app import db
from app.config import BACKUP_MOUNT, GIB, env
from app.platform import emit
from app.services import helper, kiwix, storage

STORE = "RaspberryPi-Backup/library-content"
DEFAULT_PLATFORM_ESTIMATE = 2 * GIB


def _kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def include_enabled() -> bool:
    if helper.simulated():
        return kiwix.status()["include_content"]
    return _kv_file(env.backup_conf).get("INCLUDE_LIBRARY_CONTENT", "0") in {"1", "true", "yes"}


def last_backup() -> dict[str, str]:
    return _kv_file(env.backup_stamp)


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _store_manifest() -> dict[str, int] | None:
    """{file_name: size} already in the backup store, when the drive is mounted."""
    manifest = BACKUP_MOUNT / STORE / "manifest.json"
    if not os.path.ismount(BACKUP_MOUNT):
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {f["file_name"]: int(f.get("size") or 0) for f in data.get("files", [])}


def content_total(rows: list[dict[str, Any]] | None = None) -> int:
    rows = rows if rows is not None else db.list_content()
    return sum(int(r["size"]) for r in rows if r["status"] == "installed")


def check(extra_bytes: int = 0) -> dict[str, Any]:
    """Will the backup drive hold the library content (plus the platform backup)?"""
    rows = [r for r in db.list_content() if r["status"] == "installed"]
    total_content = content_total(rows) + extra_bytes
    drive = storage.backup_drive()
    stamp = last_backup()
    live = os.path.ismount(BACKUP_MOUNT)
    if live:
        use = storage.usage(BACKUP_MOUNT)
        free, size = use["free"], use["total"]
        as_of = "now"
    else:
        free = _int(stamp.get("DEST_AVAIL_KB")) * 1024
        size = _int(stamp.get("DEST_TOTAL_KB")) * 1024
        as_of = stamp.get("TIMESTAMP", "")[:16].replace("T", " ")
    on_dest = _store_manifest()
    if on_dest is None:
        already = min(_int(stamp.get("LIBRARY_CONTENT_BYTES")), total_content)
    else:
        already = sum(int(r["size"]) for r in rows if on_dest.get(r["file_name"]) == int(r["size"]))
    new_bytes = max(0, total_content - already)
    platform = _int(stamp.get("SIZE_KB")) * 1024 or DEFAULT_PLATFORM_ESTIMATE
    margin = storage.margin_bytes(size) if size else 5 * GIB
    required = new_bytes + platform + margin
    remaining = free - required
    if not size:
        state = "unknown"
    elif remaining < 0:
        state = "insufficient"
    elif remaining < size * 0.10:
        state = "low"
    else:
        state = "ok"
    return {
        "state": state,
        "content_total": total_content,
        "already": already,
        "new_bytes": new_bytes,
        "platform": platform,
        "margin": margin,
        "required": required,
        "free": free,
        "total": size,
        "remaining": remaining,
        "after": free - required + margin,
        "live": live,
        "as_of": as_of,
        "drive_present": drive is not None or live,
        "drive_label": (drive or {}).get("label") or "STONEPI-BACKUP",
        "same_drive": storage.shares_backup_drive(),
        "enabled": include_enabled(),
    }


def install_need(size: int) -> dict[str, Any]:
    """Space a new download needs on content storage (twice over when the
    content is also backed up to that same drive)."""
    use = storage.usage()
    doubled = include_enabled() and storage.shares_backup_drive()
    margin = storage.margin_bytes(use["total"]) if use["total"] else 5 * GIB
    need = size * (2 if doubled else 1) + margin
    return {"need": need, "free": use["free"], "ok": use["free"] >= need, "doubled": doubled, "size": size, "margin": margin}


def set_enabled(on: bool) -> tuple[bool, str]:
    if on:
        result = check()
        if result["state"] in {"insufficient", "unknown"}:
            return False, (
                "Connect the backup drive (or run one backup) so StonePi can check its space."
                if result["state"] == "unknown"
                else "The backup drive doesn’t have room for your library content."
            )
    ok, data = helper.run("backup-content", "on" if on else "off")
    kiwix.forget_status()
    if not ok:
        return False, data.get("error", "Couldn’t change the backup setting")
    db.set_setting("backup_warning", "")
    return True, "Library content will be included in USB backups" if on else "Library content left out of backups"


def recheck(reason: str) -> dict[str, Any] | None:
    """After content or storage changes: warn early if the backup no longer fits."""
    if not include_enabled():
        db.set_setting("backup_warning", "")
        return None
    result = check()
    if result["state"] == "insufficient":
        message = f"After {reason}, the backup drive may no longer have room for your library content."
        if db.get_setting("backup_warning") != message:
            db.set_setting("backup_warning", message)
            emit(
                "backup_capacity",
                "Library backup needs more space",
                message,
                severity="warning",
                dedupe=f"library:backup_capacity:{date.today().isoformat()}",
                url=None,
            )
    else:
        db.set_setting("backup_warning", "")
    return result
