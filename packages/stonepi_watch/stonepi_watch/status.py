from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from stonepi_auth import APP_CATALOG

LEVEL_HEALTHY = "healthy"
LEVEL_ATTENTION = "attention"
LEVEL_CRITICAL = "critical"

DISK_ATTENTION_PCT = 87
DISK_CRITICAL_PCT = 95
BACKUP_ATTENTION_DAYS = 8


def _unit_status(unit: str) -> str:
    if os.name == "nt" or shutil.which("systemctl") is None:
        return "local"
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        return (result.stdout or result.stderr or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _probe(url: str, timeout: float = 1.5) -> dict:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
        return {"ok": response.status_code < 500, "status": response.status_code}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc)}


def _disk_pct(data_dir: Path | None = None) -> int | None:
    target = Path("/var/lib/stonepi") if Path("/var/lib/stonepi").exists() else Path("/")
    try:
        usage = os.statvfs(target)
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        if total <= 0:
            return None
        return max(0, min(100, int(round(100 * (total - free) / total))))
    except Exception:
        if os.name == "nt":
            try:
                total, used, _free = shutil.disk_usage(data_dir or Path.cwd())
                if total <= 0:
                    return None
                return max(0, min(100, int(round(100 * used / total))))
            except Exception:
                return None
        return None


def _backup_age_days(backup: dict) -> float | None:
    stamp = str(backup.get("timestamp") or backup.get("finished_at") or backup.get("time") or "").strip()
    if not stamp:
        return None
    try:
        cleaned = stamp.replace("Z", "+00:00")
        if " " in cleaned and "T" not in cleaned:
            cleaned = cleaned.replace(" ", "T", 1)
        dt = datetime.fromisoformat(cleaned[:26])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400
    except Exception:
        return None


def evaluate(
    *,
    health_url_for: Callable[[dict], str],
    backup_info: Callable[[], dict],
    disabled_ids: set[str] | None = None,
    data_dir: Path | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> dict:
    """Return Watch rollup for every catalog app plus host checks."""
    disabled = disabled_ids or set()
    apps_meta = list(catalog if catalog is not None else APP_CATALOG)
    app_rows: list[dict] = []

    def check_one(item: dict) -> dict:
        app_id = item["id"]
        enabled = app_id not in disabled
        unit = _unit_status(str(item.get("unit") or ""))
        health = {"ok": False, "status": 0}
        if enabled:
            health = _probe(health_url_for(item))
        running = bool(health.get("ok")) and unit in {"active", "local", "activating"}
        level = LEVEL_HEALTHY
        if not enabled:
            level = LEVEL_HEALTHY
        elif app_id == "auth" and not running:
            level = LEVEL_CRITICAL
        elif not running:
            level = LEVEL_ATTENTION
        return {
            "id": app_id,
            "n": item.get("name") or app_id,
            "enabled": enabled,
            "installed": True,
            "running": running if enabled else False,
            "unit": unit,
            "health_ok": bool(health.get("ok")),
            "level": level,
        }

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(apps_meta)))) as pool:
        futures = {pool.submit(check_one, item): item for item in apps_meta}
        for future in as_completed(futures):
            app_rows.append(future.result())
    app_rows.sort(key=lambda row: str(row["id"]))

    backup = backup_info() or {}
    backup_status = str(backup.get("status") or "").strip().lower()
    age_days = _backup_age_days(backup)
    disk = _disk_pct(data_dir)

    reasons: list[str] = []
    level = LEVEL_HEALTHY

    def raise_to(next_level: str, reason: str) -> None:
        nonlocal level
        order = {LEVEL_HEALTHY: 0, LEVEL_ATTENTION: 1, LEVEL_CRITICAL: 2}
        if order[next_level] > order[level]:
            level = next_level
        reasons.append(reason)

    for row in app_rows:
        if not row["enabled"]:
            continue
        if row["level"] == LEVEL_CRITICAL:
            raise_to(LEVEL_CRITICAL, f"{row['n']} is down")
        elif row["level"] == LEVEL_ATTENTION:
            raise_to(LEVEL_ATTENTION, f"{row['n']} is not running")

    if backup_status in {"failed", "error"}:
        raise_to(LEVEL_CRITICAL, "Backup failed")
    elif age_days is None and backup_status in {"", "none", "unknown"}:
        raise_to(LEVEL_ATTENTION, "No backup recorded")
    elif age_days is not None and age_days >= BACKUP_ATTENTION_DAYS:
        raise_to(LEVEL_ATTENTION, f"Backup {int(age_days)}d ago")

    if disk is not None:
        if disk >= DISK_CRITICAL_PCT:
            raise_to(LEVEL_CRITICAL, f"Disk {disk}% full")
        elif disk >= DISK_ATTENTION_PCT:
            raise_to(LEVEL_ATTENTION, f"Disk {disk}% used")

    summary = reasons[0] if reasons else "All clear"
    return {
        "level": level,
        "summary": summary,
        "reasons": reasons[:8],
        "apps": app_rows,
        "disk_pct": disk,
        "backup_status": backup_status or "none",
        "backup_age_days": age_days,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
