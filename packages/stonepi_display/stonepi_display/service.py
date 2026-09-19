from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from .layout import (
    BLOCK_CATALOG,
    BLOCK_SNIPPETS,
    DEFAULT_DESIGN,
    DEFAULT_DEVICE,
    DEFAULT_INTERVAL_MIN,
    DESIGN_PRESETS,
    DEVICE_PROFILES,
    MAX_INTERVAL_MIN,
    MIN_INTERVAL_MIN,
    STARTER_MARKUP,
    TITLE_BAR_MARKUP,
    build_markup,
    default_layout,
    design_profile,
    device_profile,
    normalize_design,
    normalize_device,
    normalize_layout,
    snippets_for,
)
from .webhook import validate_webhook_url

logger = logging.getLogger("stonepi.display")

_data_dir: Path | None = None
_collect_fn: Callable[..., dict] | None = None
_webhook_url_fn: Callable[[], str] | None = None
_lock = threading.Lock()
_scheduler_started = False


def configure(
    *,
    data_dir: Path,
    collect_fn: Callable[..., dict],
    webhook_url_fn: Callable[[], str] | None = None,
) -> None:
    global _data_dir, _collect_fn, _webhook_url_fn
    _data_dir = Path(data_dir)
    _collect_fn = collect_fn
    _webhook_url_fn = webhook_url_fn


def _config_path() -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_display.configure() was not called")
    return _data_dir / "display.json"


def _default_config() -> dict:
    return {
        "enabled": False,
        "webhook_url": "",
        "interval_minutes": DEFAULT_INTERVAL_MIN,
        "device": DEFAULT_DEVICE,
        "design": DEFAULT_DESIGN,
        "layout": default_layout(DEFAULT_DEVICE, DEFAULT_DESIGN),
        "last_push_at": None,
        "last_push_ok": None,
        "last_push_message": None,
        "last_push_status": None,
    }


def load_config() -> dict:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return _default_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()
    cfg = _default_config()
    for key in cfg:
        if key == "layout":
            continue
        if key in data:
            cfg[key] = data[key]
    cfg["device"] = normalize_device(data.get("device") or cfg.get("device"))
    cfg["design"] = normalize_design(data.get("design") or cfg.get("design"))
    cfg["layout"] = normalize_layout(data.get("layout"), device=cfg["device"], design=cfg["design"])
    try:
        cfg["interval_minutes"] = max(
            MIN_INTERVAL_MIN, min(MAX_INTERVAL_MIN, int(cfg.get("interval_minutes") or DEFAULT_INTERVAL_MIN))
        )
    except (TypeError, ValueError):
        cfg["interval_minutes"] = DEFAULT_INTERVAL_MIN
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["webhook_url"] = str(cfg.get("webhook_url") or "").strip()
    if _webhook_url_fn and not cfg["webhook_url"]:
        cfg["webhook_url"] = str(_webhook_url_fn() or "").strip()
    return cfg


def save_config(updates: dict) -> dict:
    cfg = load_config()
    if "enabled" in updates:
        cfg["enabled"] = bool(updates["enabled"])
    if "webhook_url" in updates:
        cfg["webhook_url"] = str(updates["webhook_url"] or "").strip()
    if "device" in updates:
        cfg["device"] = normalize_device(updates["device"])
    if "design" in updates:
        cfg["design"] = normalize_design(updates["design"])
    if "interval_minutes" in updates:
        try:
            cfg["interval_minutes"] = max(
                MIN_INTERVAL_MIN, min(MAX_INTERVAL_MIN, int(updates["interval_minutes"]))
            )
        except (TypeError, ValueError):
            pass
    if "layout" in updates:
        cfg["layout"] = normalize_layout(updates["layout"], device=cfg["device"], design=cfg["design"])
    elif "device" in updates or "design" in updates:
        cfg["layout"] = normalize_layout(cfg.get("layout"), device=cfg["device"], design=cfg["design"])
    for key in ("last_push_at", "last_push_ok", "last_push_message", "last_push_status"):
        if key in updates:
            cfg[key] = updates[key]
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def collect_overview(cookies: dict[str, str] | None = None) -> dict:
    if _collect_fn is None:
        raise RuntimeError("stonepi_display.configure() was not called")
    return _collect_fn(cookies)


def push_overview(cookies: dict[str, str] | None = None) -> dict:
    cfg = load_config()
    url = cfg.get("webhook_url") or ""
    if not url and _webhook_url_fn:
        url = str(_webhook_url_fn() or "").strip()
    if not url:
        result = {"ok": False, "message": "Add a Display webhook URL first.", "status": None}
        save_config(
            {
                "last_push_at": datetime.now(timezone.utc).isoformat(),
                "last_push_ok": False,
                "last_push_message": result["message"],
                "last_push_status": None,
            }
        )
        return result

    try:
        url = validate_webhook_url(url)
    except ValueError as exc:
        result = {"ok": False, "message": str(exc)[:240], "status": None}
        save_config(
            {
                "last_push_at": datetime.now(timezone.utc).isoformat(),
                "last_push_ok": False,
                "last_push_message": result["message"],
                "last_push_status": None,
            }
        )
        return result

    variables = collect_overview(cookies)
    body = {"merge_variables": variables}
    try:
        raw = json.dumps(variables, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 2048:
            variables["et_next"] = str(variables.get("et_next") or "")[:40]
            variables["pinboard_lines"] = list(variables.get("pinboard_lines") or [])[:3]
            body = {"merge_variables": variables}
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.post(url, json=body)
        ok = response.status_code < 400
        message = "Pushed to TRMNL." if ok else (response.text or f"HTTP {response.status_code}")[:240]
        if response.status_code == 429:
            message = "TRMNL rate limit (try again later)."
            ok = False
        result = {"ok": ok, "message": message, "status": response.status_code, "payload": variables}
    except Exception as exc:
        result = {"ok": False, "message": str(exc)[:240], "status": None, "payload": variables}

    save_config(
        {
            "last_push_at": datetime.now(timezone.utc).isoformat(),
            "last_push_ok": result["ok"],
            "last_push_message": result["message"],
            "last_push_status": result.get("status"),
        }
    )
    return result


def _scheduler_loop() -> None:
    while True:
        try:
            cfg = load_config()
            if cfg.get("enabled") and (cfg.get("webhook_url") or (_webhook_url_fn and _webhook_url_fn())):
                last = cfg.get("last_push_at")
                due = True
                if last:
                    try:
                        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        interval = timedelta(minutes=int(cfg.get("interval_minutes") or DEFAULT_INTERVAL_MIN))
                        due = datetime.now(timezone.utc) - last_dt.astimezone(timezone.utc) >= interval
                    except Exception:
                        due = True
                if due:
                    logger.info("Scheduled Display push")
                    push_overview(None)
        except Exception:
            logger.exception("Display scheduler error")
        import time

        time.sleep(60)


def start_scheduler() -> None:
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_scheduler_loop, name="stonepi-display", daemon=True)
        thread.start()
        _scheduler_started = True


__all__ = [
    "BLOCK_CATALOG",
    "BLOCK_SNIPPETS",
    "DEFAULT_DESIGN",
    "DEFAULT_DEVICE",
    "DESIGN_PRESETS",
    "DEVICE_PROFILES",
    "STARTER_MARKUP",
    "TITLE_BAR_MARKUP",
    "build_markup",
    "collect_overview",
    "configure",
    "default_layout",
    "design_profile",
    "device_profile",
    "load_config",
    "normalize_design",
    "normalize_device",
    "normalize_layout",
    "push_overview",
    "save_config",
    "snippets_for",
    "start_scheduler",
]
