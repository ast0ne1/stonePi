"""Read the backup stamp files the backup helper writes under /var/lib/stonepi."""

from __future__ import annotations

import json
from pathlib import Path

STAMP_DIR = Path("/var/lib/stonepi")
_NONE = {"status": "none", "message": "No backup has been recorded yet."}


def parse_stamp_file(path: Path) -> dict:
    try:
        if not path.exists():
            return dict(_NONE)
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return dict(_NONE)
    if not text:
        return dict(_NONE)
    if path.suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    info: dict = {"path": str(path)}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            info[key.strip().lower()] = value.strip()
    return info


def read_backup_info(legacy_stamp: str | None = None) -> dict:
    """Return local and USB stamp info (plus legacy flat fields for older templates)."""
    local = parse_stamp_file(STAMP_DIR / "last-local-backup.txt")
    usb = parse_stamp_file(STAMP_DIR / "last-usb-backup.txt")
    legacy = None
    if legacy_stamp:
        legacy_path = Path(legacy_stamp)
        if legacy_path.exists():
            legacy = parse_stamp_file(legacy_path)
    if legacy is None:
        for candidate in (
            STAMP_DIR / "last-backup.txt",
            STAMP_DIR / "backup-info.txt",
            STAMP_DIR / "last-backup.json",
        ):
            try:
                if candidate.exists():
                    legacy = parse_stamp_file(candidate)
                    break
            except OSError:
                continue
    if local.get("status") in {None, "none"} and legacy and str(legacy.get("kind") or "").lower() == "local":
        local = legacy
    if usb.get("status") in {None, "none"} and legacy and str(legacy.get("kind") or "").lower() == "usb":
        usb = legacy
    # Prefer newest successful stamp for flat summary fields.
    summary = legacy or dict(_NONE)
    for candidate in (local, usb):
        if str(candidate.get("status") or "").lower() in {"ok", "complete", "success"}:
            summary = candidate
            break
    return {**summary, "local": local, "usb": usb}
