from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger("stonepi.automations")

RULE_CATALOG = (
    {
        "id": "usb_backup",
        "label": "USB backup disk → start backup",
        "when": "USB volume labeled STONEPI-BACKUP is present",
        "then": "systemctl start stonepi-backup",
    },
    {
        "id": "backup_ok_display",
        "label": "Backup OK → Display push",
        "when": "Backup status becomes ok/complete/success",
        "then": "Push Display (TRMNL)",
    },
    {
        "id": "watch_attention_display",
        "label": "Watch Attention/Critical → Display push",
        "when": "Watch level is attention or critical",
        "then": "Push Display (rate-limited)",
    },
)

_data_dir: Path | None = None
_push_display: Callable[[], dict] | None = None
_backup_info: Callable[[], dict] | None = None
_watch_evaluate: Callable[[], dict] | None = None
_lock = threading.Lock()
_started = False
_last_watch_push: float = 0.0
_last_backup_status: str = ""


def configure(
    *,
    data_dir: Path,
    push_display: Callable[[], dict],
    backup_info: Callable[[], dict],
    watch_evaluate: Callable[[], dict],
) -> None:
    global _data_dir, _push_display, _backup_info, _watch_evaluate
    _data_dir = Path(data_dir)
    _push_display = push_display
    _backup_info = backup_info
    _watch_evaluate = watch_evaluate


def _state_path() -> Path:
    assert _data_dir is not None
    return _data_dir / "automations.json"


def load_state() -> dict:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    defaults = {
        "enabled": {"usb_backup": False, "backup_ok_display": True, "watch_attention_display": True},
        "last_run": {},
    }
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    enabled = defaults["enabled"] | dict(data.get("enabled") or {})
    return {"enabled": enabled, "last_run": dict(data.get("last_run") or {})}


def save_state(updates: dict) -> dict:
    state = load_state()
    if "enabled" in updates and isinstance(updates["enabled"], dict):
        state["enabled"].update({str(k): bool(v) for k, v in updates["enabled"].items()})
    if "last_run" in updates and isinstance(updates["last_run"], dict):
        state["last_run"].update(updates["last_run"])
    path = _state_path()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _usb_backup_present() -> bool:
    if os.name == "nt":
        return False
    for base in (Path("/media"), Path("/mnt"), Path("/run/media")):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir() and path.name.upper() == "STONEPI-BACKUP":
                return True
    # Also check lsblk LABEL if available
    if shutil.which("lsblk"):
        try:
            result = subprocess.run(
                ["lsblk", "-o", "LABEL", "-n"],
                capture_output=True,
                text=True,
                check=False,
            )
            labels = (result.stdout or "").upper()
            return "STONEPI-BACKUP" in labels
        except Exception:
            pass
    return False


def _start_backup() -> tuple[bool, str]:
    if os.name == "nt" or shutil.which("systemctl") is None:
        return False, "Backup start is available on the Pi via systemd."
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "start", "stonepi-backup"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "failed").strip()
        return True, "stonepi-backup started"
    except Exception as exc:
        return False, str(exc)


def run_once() -> list[dict]:
    global _last_watch_push, _last_backup_status
    if not all((_data_dir, _push_display, _backup_info, _watch_evaluate)):
        return []
    state = load_state()
    enabled = state["enabled"]
    events: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    if enabled.get("usb_backup") and _usb_backup_present():
        ok, message = _start_backup()
        events.append({"id": "usb_backup", "ok": ok, "message": message, "at": now})
        state["last_run"]["usb_backup"] = events[-1]

    backup = _backup_info() or {}
    status = str(backup.get("status") or "").strip().lower()
    if enabled.get("backup_ok_display") and status in {"ok", "complete", "success"} and status != _last_backup_status:
        if _last_backup_status:
            result = _push_display()
            events.append(
                {
                    "id": "backup_ok_display",
                    "ok": bool(result.get("ok")),
                    "message": result.get("message") or "pushed",
                    "at": now,
                }
            )
            state["last_run"]["backup_ok_display"] = events[-1]
        _last_backup_status = status
    elif status:
        _last_backup_status = status

    if enabled.get("watch_attention_display"):
        watch = _watch_evaluate() or {}
        level = str(watch.get("level") or "")
        if level in {"attention", "critical"} and (time.time() - _last_watch_push) > 1800:
            result = _push_display()
            events.append(
                {
                    "id": "watch_attention_display",
                    "ok": bool(result.get("ok")),
                    "message": result.get("message") or "pushed",
                    "at": now,
                }
            )
            state["last_run"]["watch_attention_display"] = events[-1]
            _last_watch_push = time.time()

    if events:
        save_state({"last_run": state["last_run"]})
    return events


def _loop() -> None:
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Automations tick failed")
        time.sleep(60)


def start_scheduler() -> None:
    global _started
    with _lock:
        if _started:
            return
        thread = threading.Thread(target=_loop, name="stonepi-automations", daemon=True)
        thread.start()
        _started = True
