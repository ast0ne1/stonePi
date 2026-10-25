"""Household settings for Pebble, chosen in Notify → Sidekick: ``settings.json`` in the data dir.

    voice / pending_voice   the piper voice (app/voices.py)
    quiet_hours             {"enabled": bool, "start": "HH:MM", "end": "HH:MM"} — Pebble is
                            sleepy then (unless something is really wrong)
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, time
from typing import Any

from app.config import DATA_DIR

DEFAULT_QUIET = {"enabled": True, "start": "22:00", "end": "07:00"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_lock = threading.RLock()


def _path():
    return DATA_DIR / "settings.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def update(**changes: Any) -> dict[str, Any]:
    """Read-modify-write under one lock; a value of None removes the key."""
    with _lock:
        data = load()
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        save(data)
        return data


def quiet_hours() -> dict[str, Any]:
    raw = load().get("quiet_hours")
    out = dict(DEFAULT_QUIET)
    if isinstance(raw, dict):
        out["enabled"] = bool(raw.get("enabled", True))
        for key in ("start", "end"):
            if isinstance(raw.get(key), str) and _HHMM.match(raw[key]):
                out[key] = raw[key]
    return out


def set_quiet_hours(enabled: bool, start: str, end: str) -> tuple[bool, str]:
    if not (_HHMM.match(start or "") and _HHMM.match(end or "")):
        return False, "Use 24-hour times like 22:00 and 07:00."
    if start == end and enabled:
        return False, "Start and end can't be the same time."
    update(quiet_hours={"enabled": bool(enabled), "start": start, "end": end})
    return True, "Quiet hours saved."


def in_quiet_hours(now: datetime | None = None) -> bool:
    q = quiet_hours()
    if not q["enabled"]:
        return False
    current = (now or datetime.now()).time()
    start = time.fromisoformat(q["start"])
    end = time.fromisoformat(q["end"])
    if start < end:
        return start <= current < end
    return current >= start or current < end  # wraps past midnight, e.g. 22:00–07:00
