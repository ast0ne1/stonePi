"""Background platform health snapshot for Overview and /system/health."""

from __future__ import annotations

import copy
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("stonepi.dashboard.collector")

CARDS_INTERVAL = 20.0
# Floor between card refreshes when someone is waiting on a change (Health page
# polling, a Services start/stop/restart, a unit mid-transition). One systemctl
# call + loopback probes per pass; never more often than this, however many tabs ask.
FAST_CARDS_INTERVAL = 3.0
TEMP_INTERVAL = 30.0
SLOW_INTERVAL = 300.0
TRANSITIONAL_UNIT_STATES = frozenset({"activating", "deactivating", "reloading"})

_lock = threading.RLock()
_thread: threading.Thread | None = None
_stop = threading.Event()
# Wakes the loop early (refresh requested, or stop).
_wake = threading.Event()
_cards_state: dict[str, float | bool] = {"last": -1e9, "early": False, "fast_until": -1e9}

_snapshot: dict[str, Any] = {
    "ready": False,
    "checked_at": None,
    "cards": [],
    "watch": {},
    "backup": {},
    "mem_pct": None,
    "temp_c": None,
    "uptime": None,
    "cpu_pct": None,
    "destinations": {},
    "listening": {},
    "hardware": {},
    "ntp": {},
    "journal_errors": {},
    "network": {},
    "system_health": None,
}


def get_snapshot() -> dict[str, Any]:
    with _lock:
        return copy.deepcopy(_snapshot)


def is_ready() -> bool:
    with _lock:
        return bool(_snapshot.get("ready"))


def _merge(partial: dict[str, Any]) -> None:
    with _lock:
        _snapshot.update(partial)
        _snapshot["checked_at"] = datetime.now(timezone.utc).isoformat()
        if partial.get("cards") is not None or partial.get("watch") is not None:
            _snapshot["ready"] = True


def _refresh_cards_and_watch() -> None:
    from app import services
    from app.system_health import maybe_emit_disk_warning

    # Background pass has no session cookies; disabled list stays empty here.
    # Overview can still overlay disabled from a live /api/apps if needed.
    cards = services.application_cards(cookies=None)
    # Reuse the same probe results for watch instead of probing again.
    health_by_id = {c["id"]: c.get("health") or {} for c in cards}
    unit_by_id = {c["id"]: c.get("unit_status") for c in cards}
    enabled_by_id = {c["id"]: c.get("enabled", True) is not False for c in cards}

    import stonepi_watch
    from stonepi_auth import APP_CATALOG

    LEVEL_HEALTHY = stonepi_watch.LEVEL_HEALTHY
    LEVEL_ATTENTION = stonepi_watch.LEVEL_ATTENTION
    LEVEL_CRITICAL = stonepi_watch.LEVEL_CRITICAL

    app_rows: list[dict] = []
    for item in APP_CATALOG:
        app_id = item["id"]
        enabled = enabled_by_id.get(app_id, True)
        unit = unit_by_id.get(app_id, "unknown")
        health = health_by_id.get(app_id) or {"ok": False, "status": 0}
        running = bool(health.get("ok")) and unit in {"active", "local", "activating"}
        level = LEVEL_HEALTHY
        if not enabled:
            level = LEVEL_HEALTHY
        elif app_id == "auth" and not running:
            level = LEVEL_CRITICAL
        elif not running:
            level = LEVEL_ATTENTION
        app_rows.append(
            {
                "id": app_id,
                "n": item.get("name") or app_id,
                "enabled": enabled,
                "installed": True,
                "running": running if enabled else False,
                "unit": unit,
                "health_ok": bool(health.get("ok")),
                "level": level,
            }
        )
    app_rows.sort(key=lambda row: str(row["id"]))

    backup = services.backup_info() or {}
    backup_status = str(backup.get("status") or "").strip().lower()
    # Inline age/disk helpers (same logic as stonepi_watch.evaluate) to avoid a second probe pass.
    from pathlib import Path
    import os
    import shutil

    age_days = None
    stamp = str(backup.get("timestamp") or backup.get("finished_at") or backup.get("time") or "").strip()
    if stamp:
        try:
            cleaned = stamp.replace("Z", "+00:00")
            if " " in cleaned and "T" not in cleaned:
                cleaned = cleaned.replace(" ", "T", 1)
            dt = datetime.fromisoformat(cleaned[:26])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400
        except Exception:
            age_days = None

    disk = None
    target = Path("/var/lib/stonepi") if Path("/var/lib/stonepi").exists() else Path("/")
    try:
        usage = os.statvfs(target)
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        if total > 0:
            disk = max(0, min(100, int(round(100 * (total - free) / total))))
    except Exception:
        if os.name == "nt":
            try:
                total, used, _free = shutil.disk_usage(Path.cwd())
                if total > 0:
                    disk = max(0, min(100, int(round(100 * used / total))))
            except Exception:
                disk = None

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
    elif age_days is not None and age_days >= stonepi_watch.BACKUP_ATTENTION_DAYS:
        raise_to(LEVEL_ATTENTION, f"Backup {int(age_days)}d ago")

    if disk is not None:
        if disk >= stonepi_watch.DISK_CRITICAL_PCT:
            raise_to(LEVEL_CRITICAL, f"Disk {disk}% full")
        elif disk >= stonepi_watch.DISK_SERIOUS_PCT:
            raise_to(LEVEL_ATTENTION, f"Disk {disk}% used — serious")
        elif disk >= stonepi_watch.DISK_ATTENTION_PCT:
            raise_to(LEVEL_ATTENTION, f"Disk {disk}% used")

    watch = {
        "level": level,
        **stonepi_watch.summarize(reasons, app_rows),
        "reasons": reasons[:8],
        "apps": app_rows,
        "disk_pct": disk,
        "backup_status": backup_status or "none",
        "backup_age_days": age_days,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    maybe_emit_disk_warning(watch)
    _merge({"cards": cards, "watch": watch, "backup": backup})
    with _lock:
        _cards_state["last"] = time.monotonic()
        _cards_state["early"] = False


def watch_in_transition(watch: dict | None) -> bool:
    """True while an enabled app's unit is starting/stopping/reloading."""
    for row in (watch or {}).get("apps") or []:
        if row.get("enabled", True) and str(row.get("unit") or "") in TRANSITIONAL_UNIT_STATES:
            return True
    return False


def request_refresh(*, window: float = 0.0) -> None:
    """Ask the loop to re-check cards/watch soon (rate-limited to FAST_CARDS_INTERVAL).

    ``window`` keeps the fast cadence for that many seconds (e.g. after a
    Services restart, while the app comes back). Never runs systemctl itself.
    """
    now = time.monotonic()
    with _lock:
        _cards_state["early"] = True
        if window > 0:
            _cards_state["fast_until"] = max(float(_cards_state["fast_until"]), now + window)
    _wake.set()


def _cards_due_at(now: float | None = None) -> float:
    """When the next cards/watch refresh is due."""
    now = time.monotonic() if now is None else now
    with _lock:
        last = float(_cards_state["last"])
        fast = (
            bool(_cards_state["early"])
            or now < float(_cards_state["fast_until"])
            or watch_in_transition(_snapshot.get("watch"))
        )
    return last + (FAST_CARDS_INTERVAL if fast else CARDS_INTERVAL)


def _refresh_temp() -> None:
    from app import display

    _merge(
        {
            "mem_pct": display._read_mem_pct(),
            "temp_c": display._read_temp_c(),
            "uptime": display._read_uptime(),
            "cpu_pct": display._read_cpu_pct(),
        }
    )


def _refresh_slow() -> None:
    from app import network as network_svc
    from app.system_health import (
        destinations_status,
        enrich_watch,
        hardware_status,
        listening_ports,
        ntp_status,
        recent_journal_errors,
    )

    destinations = destinations_status()
    listening = listening_ports()
    hardware = hardware_status()
    ntp = ntp_status()
    journal_errors = recent_journal_errors(lines=24)
    try:
        network = network_svc.network_snapshot()
    except Exception:  # noqa: BLE001
        network = {
            "internet": {"ok": False, "detail": "unavailable"},
            "tailscale": network_svc.parse_tailscale_status(
                {"Installed": False, "BackendState": "NoState"},
                wanted=False,
            ),
            "helper_available": False,
            "appliance": False,
        }
    with _lock:
        watch = copy.deepcopy(_snapshot.get("watch") or {})
    if watch:
        watch = enrich_watch(watch, listening=listening, hardware=hardware)
    _merge(
        {
            "destinations": destinations,
            "listening": listening,
            "hardware": hardware,
            "ntp": ntp,
            "journal_errors": journal_errors,
            "network": network,
            "watch": watch or _snapshot.get("watch") or {},
        }
    )


def _build_system_health() -> dict[str, Any]:
    """Assemble /system/health JSON from the current snapshot."""
    import os
    import shutil

    import stonepi_watch

    snap = get_snapshot()
    watch = snap.get("watch") or {}
    network = snap.get("network") or {}
    backup = snap.get("backup") or {}
    dest = snap.get("destinations") or {}
    listen = snap.get("listening") or {}
    ntp = snap.get("ntp") or {}
    hw = snap.get("hardware") or {}
    journal = snap.get("journal_errors") or {}
    apps = list(watch.get("apps") or [])
    notifications = next((a for a in apps if a.get("id") == "notify"), None)

    nginx_ok = _nginx_active()

    level = watch.get("level") or stonepi_watch.LEVEL_HEALTHY
    ntfy = dest.get("ntfy") or {"enabled": False, "configured": False, "token_set": False}
    trmnl = dest.get("trmnl") or {"enabled": False, "configured": False, "webhook_set": False}

    return {
        "ok": level == stonepi_watch.LEVEL_HEALTHY,
        "level": level,
        # Plain-text consumers (Notify, displays): "Multiple services are not running (A, B)".
        "summary": stonepi_watch.summary_text(watch) or "All clear",
        "summary_detail": watch.get("summary_detail") or "",
        "checked_at": snap.get("checked_at") or datetime.now(timezone.utc).isoformat(),
        "ready": bool(snap.get("ready")),
        "stonepi": "OK" if level == stonepi_watch.LEVEL_HEALTHY else level,
        "os": "OK",
        "disk_pct": watch.get("disk_pct"),
        "memory_pct": snap.get("mem_pct"),
        "cpu_pct": snap.get("cpu_pct"),
        "temperature_c": snap.get("temp_c"),
        "uptime": snap.get("uptime"),
        "network": {
            "internet": bool((network.get("internet") or {}).get("ok")),
            "tailscale": bool((network.get("tailscale") or {}).get("connected")),
            "appliance": bool(network.get("appliance")),
        },
        "nginx": {"ok": nginx_ok},
        "notifications": {
            "ok": bool(notifications.get("running")) if notifications else False,
            "unit": (notifications or {}).get("unit"),
            "destinations": dest,
        },
        "ntfy": ntfy,
        "trmnl": trmnl,
        "backup": {
            "status": watch.get("backup_status"),
            "age_days": watch.get("backup_age_days"),
            "raw": {k: backup.get(k) for k in ("status", "timestamp", "finished_at", "time") if k in backup},
        },
        "watch": {
            "level": watch.get("level"),
            "reasons": watch.get("reasons") or [],
            "apps": apps,
        },
        "listening": listen,
        "ntp": ntp,
        "hardware": hw,
        "journal_errors": journal,
    }


_NGINX: dict[str, float | bool] = {"at": -1e9, "ok": True}


def _nginx_active() -> bool:
    """nginx state, re-checked at most every CARDS_INTERVAL (a systemctl process each time)."""
    now = time.monotonic()
    if now - float(_NGINX["at"]) < CARDS_INTERVAL:
        return bool(_NGINX["ok"])
    import os
    import shutil

    ok = True
    if os.name != "nt" and shutil.which("systemctl"):
        try:
            import subprocess

            result = subprocess.run(
                ["systemctl", "is-active", "nginx"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
            ok = (result.stdout or "").strip() == "active"
        except Exception:
            ok = False
    _NGINX.update(at=now, ok=ok)
    return ok


def refresh_now(*, cards: bool = True, temp: bool = True, slow: bool = True) -> dict[str, Any]:
    """Synchronous refresh (tests / forced)."""
    if cards:
        try:
            _refresh_cards_and_watch()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cards refresh failed: %s", exc)
    if temp:
        try:
            _refresh_temp()
        except Exception as exc:  # noqa: BLE001
            logger.warning("temp refresh failed: %s", exc)
    if slow:
        try:
            _refresh_slow()
        except Exception as exc:  # noqa: BLE001
            logger.warning("slow refresh failed: %s", exc)
    with _lock:
        _snapshot["system_health"] = _build_system_health()
    return get_snapshot()


def _loop() -> None:
    # Prime quickly so first Overview is not empty for long, then honour intervals.
    refresh_now()
    last_temp = time.monotonic()
    last_slow = last_temp
    while not _stop.is_set():
        _wake.clear()
        now = time.monotonic()
        ran = False
        try:
            if now >= _cards_due_at(now):
                try:
                    _refresh_cards_and_watch()
                finally:
                    # A failing pass still counts, so a broken probe can't spin the loop.
                    with _lock:
                        _cards_state["last"] = time.monotonic()
                        _cards_state["early"] = False
                ran = True
            if now - last_temp >= TEMP_INTERVAL:
                _refresh_temp()
                last_temp = now
                ran = True
            if now - last_slow >= SLOW_INTERVAL:
                _refresh_slow()
                last_slow = now
                ran = True
            # Rebuild the summary only when its inputs changed (it used to run,
            # with a systemctl process, every 2 s whether or not anything had).
            if ran:
                with _lock:
                    _snapshot["system_health"] = _build_system_health()
        except Exception as exc:  # noqa: BLE001
            logger.warning("collector tick failed: %s", exc)
        # Sleep until the next refresh is due, or until request_refresh()/stop() wakes us.
        due = min(_cards_due_at(), last_temp + TEMP_INTERVAL, last_slow + SLOW_INTERVAL)
        _wake.wait(max(0.25, due - time.monotonic()))


def start() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="dashboard-collector", daemon=True)
        _thread.start()
        logger.info("dashboard collector started")


def stop() -> None:
    _stop.set()
    _wake.set()
