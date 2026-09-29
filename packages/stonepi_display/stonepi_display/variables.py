"""TRMNL merge_variables contract — one builder shared by Dashboard and Notify.

Every Liquid snippet in ``layout.py`` / ``templates.py`` reads keys from this
dict. ``SAMPLE_VARIABLES`` is the fixture the preview renders and the contract
test checks snippets against, so a renamed key fails a test instead of
blanking a field on the device.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEVEL_HEALTHY = "healthy"


def read_cpu_pct() -> int | None:
    if os.name == "nt":
        return None
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            line1 = handle.readline()
        time.sleep(0.12)
        with open("/proc/stat", encoding="utf-8") as handle:
            line2 = handle.readline()

        def parts(line: str) -> list[int]:
            return [int(x) for x in line.split()[1:8]]

        a, b = parts(line1), parts(line2)
        idle_a, idle_b = a[3] + a[4], b[3] + b[4]
        total_d = sum(b) - sum(a)
        idle_d = idle_b - idle_a
        if total_d <= 0:
            return 0
        return max(0, min(100, int(round(100 * (1 - idle_d / total_d)))))
    except Exception:
        return None


def read_mem_pct() -> int | None:
    if os.name == "nt":
        return None
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, raw = line.split(":", 1)
                info[key.strip()] = int(raw.strip().split()[0])
        total = info.get("MemTotal") or 0
        available = info.get("MemAvailable")
        if available is None:
            available = (info.get("MemFree") or 0) + (info.get("Buffers") or 0) + (info.get("Cached") or 0)
        if total <= 0:
            return None
        return max(0, min(100, int(round(100 * (total - available) / total))))
    except Exception:
        return None


def read_temp_c() -> int | None:
    if os.name == "nt":
        return None
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        match = re.search(r"temp=([0-9.]+)", result.stdout or "")
        if match:
            return int(round(float(match.group(1))))
    except Exception:
        pass
    try:
        raw = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text(encoding="utf-8").strip())
        return int(round(raw / 1000)) if raw > 200 else raw
    except Exception:
        return None


def read_uptime() -> str:
    if os.name == "nt":
        return "local"
    try:
        seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
        total = int(seconds)
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "—"


def relative_when(stamp: str | None) -> str:
    text = str(stamp or "").strip()
    if not text:
        return "Never"
    try:
        cleaned = text.replace("Z", "+00:00")
        if " " in cleaned and "T" not in cleaned:
            cleaned = cleaned.replace(" ", "T", 1)
        dt = datetime.fromisoformat(cleaned[:26])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return "Just now"
        if seconds < 3600:
            return f"{max(1, seconds // 60)}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        if delta.days == 1:
            return "Yesterday"
        if delta.days < 14:
            return f"{delta.days} days ago"
        return dt.astimezone(timezone.utc).strftime("%d %b")
    except Exception:
        return text[:19].replace("T", " ")


def backup_fields(backup: dict) -> tuple[str, bool]:
    status = str(backup.get("status") or "").strip().lower()
    stamp = str(backup.get("timestamp") or backup.get("finished_at") or backup.get("time") or "").strip()
    when = relative_when(stamp) if stamp else ("Never" if status in {"", "none", "unknown"} else status)
    ok = status in {"ok", "complete", "success"} or bool(stamp and status not in {"failed", "error", "none"})
    if status in {"failed", "error"}:
        ok = False
    if when == "Never" and status in {"", "none", "unknown"}:
        ok = False
    return when, ok


def _pct_disp(value: int | None) -> str:
    return f"{value}%" if value is not None else "n/a"


def _temp_disp(value: int | None) -> str:
    return f"{value}°" if value is not None else "n/a"


def _app_meta(app_id: str, fetched: dict[str, dict]) -> tuple[str, str]:
    """Return (detail, meta) for a Services row."""
    nc = fetched.get("newscast") or {}
    et = fetched.get("eventtrakr") or {}
    fs = fetched.get("fileserve") or {}
    pinboard = fetched.get("pinboard") or {}
    auth = fetched.get("auth") or {}
    studio = fetched.get("studio") or {}
    if app_id == "auth":
        sessions = auth.get("sessions")
        if sessions not in (None, ""):
            n = int(sessions)
            return (f"{n} active session{'s' if n != 1 else ''}", "")
        return ("—", "")
    if app_id == "newscast":
        feeds = int(nc.get("feeds") or 0)
        updated = str(nc.get("updated") or "").strip()
        return (f"{feeds} feeds" if feeds else "—", updated if updated and updated != "—" else "")
    if app_id == "eventtrakr":
        nxt = str(et.get("next") or "")
        nxt = "" if nxt == "None" else nxt
        favs = et.get("favourites")
        return (f"Next: {nxt}" if nxt else "—", f"{int(favs)} favourites" if favs not in (None, "") else "")
    if app_id == "fileserve":
        pages = int(fs.get("pages") or 0)
        return (f"{pages} pages" if pages else "—", f"{pages} hosted" if pages else "")
    if app_id == "studio":
        projects = studio.get("projects")
        detail = str(studio.get("detail") or "").strip()
        return (detail or "—", f"{int(projects)} projects" if projects not in (None, "") else "")
    if app_id == "pinboard":
        total = int(pinboard.get("total") or len(pinboard.get("lines") or []))
        return ("—", f"{total} notices" if total else "")
    if app_id in {"pricescout", "sportguide", "pricewatch"}:
        detail = str((fetched.get(app_id) or {}).get("detail") or "—").strip() or "—"
        return (detail, "")
    return ("—", "")


def build_merge_variables(
    *,
    hostname: str,
    watch: dict,
    backup: dict,
    fetched: dict[str, dict],
    cpu: int | None = None,
    mem: int | None = None,
    temp: int | None = None,
    uptime: str = "—",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the full merge_variables dict from raw inputs.

    ``watch`` is a ``stonepi_watch.evaluate()`` result, ``backup`` a backup
    stamp dict, ``fetched`` maps app id → that app's ``/api/display`` JSON.
    """
    now = now or datetime.now(timezone.utc)
    nc = fetched.get("newscast") or {}
    et = fetched.get("eventtrakr") or {}
    pinboard = fetched.get("pinboard") or {}
    fs = fetched.get("fileserve") or {}

    nc_feeds = int(nc.get("feeds") or 0)
    et_next = str(et.get("next") or "None")
    fs_pages = int(fs.get("pages") or 0)
    pinboard_lines = list(pinboard.get("lines") or [])[:5]
    pinboard_total = int(pinboard.get("total") or len(pinboard_lines))
    pinboard_more = max(0, pinboard_total - len(pinboard_lines))

    apps: list[dict] = []
    service_rows: list[dict] = []
    for row in watch.get("apps") or []:
        if not row.get("enabled", True):
            continue
        detail, meta = _app_meta(str(row.get("id") or ""), fetched)
        running = bool(row.get("running", False))
        apps.append(
            {
                "id": row["id"],
                "n": row.get("n") or row["id"],
                "installed": row.get("installed", True),
                "running": running,
                "d": detail or "—",
                "m": meta or "",
            }
        )
        service_rows.append(
            {
                "name": row.get("n") or row["id"],
                "status": "up" if running else "down",
                "detail": detail if detail and detail != "—" else (meta or ""),
            }
        )

    backup_when, backup_ok = backup_fields(backup or {})
    backup_status = str((backup or {}).get("status") or "").strip().lower()
    if backup_status in {"failed", "error"}:
        last_backup_status = "failed"
    elif backup_ok:
        last_backup_status = "ok"
    else:
        last_backup_status = backup_status or "none"

    disk = watch.get("disk_pct")
    disk_i = disk if isinstance(disk, int) else 0
    fs_app = next((row for row in apps if row.get("id") == "fileserve"), None)
    fs_ok = bool(fs_app.get("running")) if fs_app else bool(fs.get("ok"))

    watch_level = watch.get("level") or "attention"
    watch_summary = watch.get("summary") or "—"
    # "Multiple services are not running" + names (Watch snapshots carry them separately).
    watch_detail = str(watch.get("summary_detail") or "").strip()
    if watch_detail and watch_detail not in watch_summary:
        watch_summary = f"{watch_summary} ({watch_detail})"
    alerts: list[dict] = []
    if watch_level != LEVEL_HEALTHY:
        for reason in list(watch.get("reasons") or [])[:5]:
            alerts.append({"level": watch_level, "message": str(reason)})
        if not alerts and watch_summary != "—":
            alerts.append({"level": watch_level, "message": str(watch_summary)})

    events: list[dict] = []
    for item in list(et.get("events") or [])[:3]:
        if isinstance(item, dict):
            events.append(
                {"time": str(item.get("time") or "")[:8], "message": str(item.get("message") or "")[:80]}
            )
    if not events and et_next != "None":
        events.append({"time": now.astimezone().strftime("%H:%M"), "message": f"Next: {et_next}"[:80]})

    reminders: list[dict] = []
    for item in list(pinboard.get("reminders") or [])[:5]:
        if isinstance(item, dict) and str(item.get("title") or "").strip():
            reminders.append(
                {"title": str(item["title"]).strip()[:80], "due": str(item.get("due") or "")[:24]}
            )

    pricewatch = fetched.get("pricewatch") or {}
    sportguide = fetched.get("sportguide") or {}
    return {
        # Nested contract for custom TRMNL editor markup
        "system": {
            "cpu": int(cpu) if cpu is not None else 0,
            "ram": int(mem) if mem is not None else 0,
            "disk": disk_i,
            "temp": int(temp) if temp is not None else 0,
            "uptime": uptime,
        },
        "services": service_rows,
        "alerts": alerts,
        "backup": {
            "disk_used_pct": disk_i,
            "last_backup_ago": backup_when,
            "last_backup_status": last_backup_status,
        },
        "events": events,
        "reminders": reminders,
        # Flat keys read by the built-in Liquid snippets
        "hostname": hostname or "stonepi",
        "updated_at": now.strftime("%Y-%m-%d %H:%M"),
        # Raw collection time; each Display formats it (local time) for its title bar.
        "updated_ts": now.isoformat(timespec="seconds"),
        "refreshed_ago": "just now",
        "system_ok": watch_level == LEVEL_HEALTHY,
        "watch_level": watch_level,
        "watch_summary": watch_summary,
        "cpu_pct": cpu,
        "mem_pct": mem,
        "temp_c": temp,
        "cpu_disp": _pct_disp(cpu),
        "mem_disp": _pct_disp(mem),
        "temp_disp": _temp_disp(temp),
        "disk_disp": _pct_disp(disk if isinstance(disk, int) else None),
        "uptime": uptime,
        "apps": apps,
        "apps_up": sum(1 for row in apps if row.get("running")),
        "apps_total": len(apps),
        "nc_feeds": nc_feeds,
        "nc_updated": str(nc.get("updated") or "—"),
        "et_next": et_next,
        "fs_pages": fs_pages,
        "fs_ok": fs_ok,
        "pinboard_lines": pinboard_lines or ["No notices"],
        "pinboard_more": pinboard_more,
        "pw_detail": str(pricewatch.get("detail") or "—").strip() or "—",
        "sg_detail": str(sportguide.get("detail") or sportguide.get("on_now") or "—").strip() or "—",
        "disk_pct": disk,
        "backup_when": backup_when,
        "backup_ok": backup_ok,
    }


def _sample() -> dict[str, Any]:
    watch = {
        "level": "attention",
        "summary": "PriceWatch is not running",
        "reasons": ["PriceWatch is not running"],
        "disk_pct": 41,
        "apps": [
            {"id": "auth", "n": "Auth", "running": True},
            {"id": "newscast", "n": "NewsCast", "running": True},
            {"id": "eventtrakr", "n": "EventTrakr", "running": True},
            {"id": "fileserve", "n": "FileServe", "running": True},
            {"id": "pinboard", "n": "Pinboard", "running": True},
            {"id": "pricewatch", "n": "PriceWatch", "running": False},
        ],
    }
    fetched = {
        "newscast": {"feeds": 12, "updated": "08:15"},
        "eventtrakr": {"next": "Parents' evening, Thu", "favourites": 4},
        "fileserve": {"pages": 7},
        "pinboard": {"lines": ["Bins out tonight", "Dentist Fri 4pm"], "total": 3},
        "auth": {"sessions": 2},
        "pricewatch": {"detail": "3 watches · 1 below target"},
        "sportguide": {"detail": "Arsenal v Spurs · Sat 17:30"},
    }
    return build_merge_variables(
        hostname="stonepi",
        watch=watch,
        backup={"status": "ok", "timestamp": "2026-01-01T03:00:00+00:00"},
        fetched=fetched,
        cpu=12,
        mem=38,
        temp=47,
        uptime="6d 4h",
        now=datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc),
    ) | {"backup_when": "6h ago", "refreshed_ago": "2m ago"}


SAMPLE_VARIABLES: dict[str, Any] = _sample()
MERGE_VARIABLE_KEYS: frozenset[str] = frozenset(SAMPLE_VARIABLES)
