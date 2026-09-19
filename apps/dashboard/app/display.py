from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import stonepi_display
import stonepi_watch
from stonepi_auth import APP_CATALOG

from app.config import DATA_DIR, env
from app import services

logger = logging.getLogger("stonepi.display")

# Re-export layout helpers for templates/routes
BLOCK_CATALOG = stonepi_display.BLOCK_CATALOG
BLOCK_SNIPPETS = stonepi_display.BLOCK_SNIPPETS
DEFAULT_DESIGN = stonepi_display.DEFAULT_DESIGN
DEFAULT_DEVICE = stonepi_display.DEFAULT_DEVICE
DESIGN_PRESETS = stonepi_display.DESIGN_PRESETS
DEVICE_PROFILES = stonepi_display.DEVICE_PROFILES
STARTER_MARKUP = stonepi_display.STARTER_MARKUP
TITLE_BAR_MARKUP = stonepi_display.TITLE_BAR_MARKUP
build_markup = stonepi_display.build_markup
default_layout = stonepi_display.default_layout
design_profile = stonepi_display.design_profile
device_profile = stonepi_display.device_profile
normalize_design = stonepi_display.normalize_design
normalize_device = stonepi_display.normalize_device
normalize_layout = stonepi_display.normalize_layout
snippets_for = stonepi_display.snippets_for
load_config = stonepi_display.load_config
save_config = stonepi_display.save_config
push_overview = stonepi_display.push_overview
start_scheduler = stonepi_display.start_scheduler


def _read_cpu_pct() -> int | None:
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
        total_a, total_b = sum(a), sum(b)
        total_d = total_b - total_a
        idle_d = idle_b - idle_a
        if total_d <= 0:
            return 0
        return max(0, min(100, int(round(100 * (1 - idle_d / total_d)))))
    except Exception:
        return None


def _read_mem_pct() -> int | None:
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


def _read_temp_c() -> int | None:
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
    for path in (Path("/sys/class/thermal/thermal_zone0/temp"),):
        try:
            raw = int(path.read_text(encoding="utf-8").strip())
            return int(round(raw / 1000)) if raw > 200 else raw
        except Exception:
            continue
    return None


def _read_uptime() -> str:
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


def _relative_when(stamp: str | None) -> str:
    if not stamp:
        return "Never"
    text = str(stamp).strip()
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
        days = delta.days
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return "Just now"
        if seconds < 3600:
            return f"{max(1, seconds // 60)}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        if days == 1:
            return "Yesterday"
        if days < 14:
            return f"{days} days ago"
        return dt.astimezone(timezone.utc).strftime("%d %b")
    except Exception:
        return text[:19].replace("T", " ")


def _backup_fields(backup: dict) -> tuple[str, bool]:
    status = str(backup.get("status") or "").strip().lower()
    stamp = str(backup.get("timestamp") or backup.get("finished_at") or backup.get("time") or "").strip()
    when = _relative_when(stamp) if stamp else ("Never" if status in {"", "none", "unknown"} else status)
    ok = status in {"ok", "complete", "success"} or bool(stamp and status not in {"failed", "error", "none"})
    if status in {"failed", "error"}:
        ok = False
    if when == "Never" and status in {"", "none", "unknown"}:
        ok = False
    return when, ok


def _app_status_url(app_id: str, port: int) -> str:
    if os.name == "nt" or not Path("/etc/nginx/sites-enabled/stonepi").exists():
        return f"http://127.0.0.1:{port}/api/display"
    origin = env.public_origin.rstrip("/")
    prefix = {
        "newscast": "/news",
        "fileserve": "/files",
        "eventtrakr": "/events",
        "auth": "/auth",
        "pinboard": "/pinboard",
        "studio": "/studio",
    }.get(app_id, "")
    return f"{origin}{prefix}/api/display"


def _fetch_json(url: str) -> dict:
    try:
        with httpx.Client(timeout=2.0, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code >= 400:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def watch_snapshot(cookies: dict[str, str] | None = None) -> dict:
    disabled: set[str] = set()
    try:
        raw = services.auth_request("GET", "/api/apps", cookies or {})
        if isinstance(raw, dict):
            disabled = {str(x) for x in (raw.get("disabled") or [])}
    except Exception:
        pass
    return stonepi_watch.evaluate(
        health_url_for=services.health_url,
        backup_info=services.backup_info,
        disabled_ids=disabled,
        data_dir=DATA_DIR,
        catalog=list(APP_CATALOG),
    )


def collect_overview(cookies: dict[str, str] | None = None) -> dict:
    watch = watch_snapshot(cookies)
    nc = _fetch_json(_app_status_url("newscast", 8001))
    et = _fetch_json(_app_status_url("eventtrakr", 8003))
    pinboard = _fetch_json(_app_status_url("pinboard", 8004))
    fs = _fetch_json(_app_status_url("fileserve", 8002))
    auth = _fetch_json(_app_status_url("auth", 8011))
    studio = _fetch_json(_app_status_url("studio", 8005))

    nc_feeds = int(nc.get("feeds") or 0)
    et_next = str(et.get("next") or "None")
    et_favs = et.get("favourites")
    fs_pages = int(fs.get("pages") or 0)
    pinboard_lines = list(pinboard.get("lines") or [])[:5]
    pinboard_total = int(pinboard.get("total") or len(pinboard_lines))
    pinboard_more = max(0, pinboard_total - len(pinboard_lines))
    auth_sessions = auth.get("sessions")
    studio_projects = studio.get("projects")
    studio_detail = str(studio.get("detail") or "").strip()

    def app_meta(app_id: str) -> tuple[str, str]:
        """Return (detail, meta) for Services rows — matches Status wall photo."""
        if app_id == "auth":
            if auth_sessions not in (None, ""):
                n = int(auth_sessions)
                return (f"{n} active session{'s' if n != 1 else ''}", "")
            return ("—", "")
        if app_id == "dashboard":
            return ("—", "")
        if app_id == "newscast":
            detail = f"{nc_feeds} feeds" if nc_feeds else "—"
            updated = str(nc.get("updated") or "").strip()
            return (detail, updated if updated and updated != "—" else "")
        if app_id == "eventtrakr":
            nxt = et_next if et_next and et_next != "None" else ""
            detail = f"Next: {nxt}" if nxt else "—"
            meta = f"{int(et_favs)} favourites" if et_favs not in (None, "") else ""
            return (detail, meta)
        if app_id == "fileserve":
            detail = f"{fs_pages} pages" if fs_pages else "—"
            meta = f"{fs_pages} hosted" if fs_pages else ""
            return (detail, meta)
        if app_id == "studio":
            detail = studio_detail or "—"
            meta = f"{int(studio_projects)} projects" if studio_projects not in (None, "") else ""
            return (detail, meta)
        if app_id == "pinboard":
            meta = f"{pinboard_total} notices" if pinboard_total else ""
            return ("—", meta)
        return ("—", "")

    apps = []
    for row in watch.get("apps") or []:
        if not row.get("enabled", True):
            continue
        detail, meta = app_meta(str(row.get("id") or ""))
        apps.append(
            {
                "id": row["id"],
                "n": row["n"],
                "installed": row.get("installed", True),
                "running": row.get("running", False),
                "d": detail or "—",
                "m": meta or "",
            }
        )
    system_ok = watch.get("level") == stonepi_watch.LEVEL_HEALTHY

    backup = services.backup_info()
    backup_when, backup_ok = _backup_fields(backup)
    now = datetime.now(timezone.utc)
    cpu = _read_cpu_pct()
    mem = _read_mem_pct()
    temp = _read_temp_c()
    disk = watch.get("disk_pct")

    def pct_disp(value: int | None) -> str:
        return f"{value}%" if value is not None else "n/a"

    def temp_disp(value: int | None) -> str:
        return f"{value}°" if value is not None else "n/a"

    fs_app = next((row for row in apps if row.get("id") == "fileserve"), None)
    fs_ok = bool(fs_app.get("running")) if fs_app else bool(fs.get("ok"))
    apps_up = sum(1 for row in apps if row.get("running"))
    apps_total = len(apps)

    return {
        "hostname": env.hostname or "stonepi",
        "updated_at": now.strftime("%Y-%m-%d %H:%M"),
        "refreshed_ago": "just now",
        "system_ok": system_ok,
        "watch_level": watch.get("level") or "attention",
        "watch_summary": watch.get("summary") or "—",
        "cpu_pct": cpu,
        "mem_pct": mem,
        "temp_c": temp,
        "cpu_disp": pct_disp(cpu),
        "mem_disp": pct_disp(mem),
        "temp_disp": temp_disp(temp),
        "disk_disp": pct_disp(disk if isinstance(disk, int) else None),
        "uptime": _read_uptime(),
        "apps": apps,
        "apps_up": apps_up,
        "apps_total": apps_total,
        "nc_feeds": nc_feeds,
        "nc_updated": str(nc.get("updated") or "—"),
        "et_next": et_next,
        "fs_pages": fs_pages,
        "fs_ok": fs_ok,
        "pinboard_lines": pinboard_lines or ["No notices"],
        "pinboard_more": pinboard_more,
        "disk_pct": disk,
        "backup_when": backup_when,
        "backup_ok": backup_ok,
    }


def _vault_webhook() -> str:
    try:
        from stonepi_vault import get_secret

        return get_secret("DISPLAY_WEBHOOK_URL", env_name="STONEPI_DISPLAY_WEBHOOK", default="")
    except Exception:
        return ""


def configure_display() -> None:
    try:
        from stonepi_vault import get_vault

        get_vault()  # shared STONEPI_VAULT_DIR / repo data/vault
    except Exception:
        logger.debug("Vault not available yet", exc_info=True)
    stonepi_display.configure(
        data_dir=DATA_DIR,
        collect_fn=collect_overview,
        webhook_url_fn=_vault_webhook,
    )


configure_display()
