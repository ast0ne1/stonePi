"""Per-person ntfy subscriptions (personal alerts), keyed by Auth user id."""

from __future__ import annotations

import json
import re
import secrets
import string
import threading
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

_data_dir: Path | None = None
_lock = threading.Lock()

TOPIC_RANDOM_LEN = 16
_TOPIC_ALPHABET = string.ascii_lowercase + string.digits
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "07:00"


def configure_subscriptions(data_dir: Path) -> None:
    global _data_dir
    _data_dir = Path(data_dir)


def _path() -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_notify not configured")
    return _data_dir / "subscriptions.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_time(raw: Any, default: str) -> str:
    match = _TIME_RE.match(str(raw or "").strip())
    if not match:
        return default
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _normalize(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    events = data.get("events") if isinstance(data.get("events"), dict) else {}
    quiet = data.get("quiet_hours") if isinstance(data.get("quiet_hours"), dict) else {}
    return {
        "enabled": bool(data.get("enabled")),
        "topic": str(data.get("topic") or "").strip(),
        "events": {str(k): bool(v) for k, v in events.items()},
        "quiet_hours": {
            "enabled": bool(quiet.get("enabled")),
            "start": _clean_time(quiet.get("start"), DEFAULT_QUIET_START),
            "end": _clean_time(quiet.get("end"), DEFAULT_QUIET_END),
        },
        # Snapshot from the person's own signed session; refreshed on each save.
        "username": str(data.get("username") or "").strip()[:64],
        "is_admin": bool(data.get("is_admin")),
        "created_at": data.get("created_at") or None,
        "updated_at": data.get("updated_at") or None,
    }


def load_subscriptions() -> dict[str, dict[str, Any]]:
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(uid): _normalize(sub) for uid, sub in data.items() if str(uid).strip()}


def _write(subs: dict[str, dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(subs, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_subscription(user_id: str) -> dict[str, Any] | None:
    uid = str(user_id or "").strip()
    if not uid:
        return None
    return load_subscriptions().get(uid)


def generate_topic(username: str = "") -> str:
    slug = re.sub(r"[^a-z0-9]+", "", str(username or "").lower())[:12]
    rand = "".join(secrets.choice(_TOPIC_ALPHABET) for _ in range(TOPIC_RANDOM_LEN))
    return f"stonepi-{slug}-{rand}" if slug else f"stonepi-{rand}"


def save_subscription(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge ``updates`` into one person's subscription and return it.

    ``events`` replaces the person's ticks wholesale; ``quiet_hours`` merges.
    Only this user's record is touched.
    """
    uid = str(user_id or "").strip()
    if not uid:
        raise ValueError("user_id required")
    with _lock:
        subs = load_subscriptions()
        current = subs.get(uid) or _normalize({"created_at": _now()})
        merged = dict(current)
        for key in ("enabled", "topic", "username", "is_admin"):
            if key in updates:
                merged[key] = updates[key]
        if isinstance(updates.get("events"), dict):
            merged["events"] = dict(updates["events"])
        if isinstance(updates.get("quiet_hours"), dict):
            merged["quiet_hours"] = {**current["quiet_hours"], **updates["quiet_hours"]}
        merged["created_at"] = current.get("created_at") or _now()
        merged["updated_at"] = _now()
        sub = _normalize(merged)
        subs[uid] = sub
        _write(subs)
        return sub


def enable_subscription(user_id: str, *, username: str = "", is_admin: bool = False) -> dict[str, Any]:
    """Turn personal alerts on, creating a topic the first time."""
    existing = get_subscription(user_id)
    topic = (existing or {}).get("topic") or generate_topic(username)
    return save_subscription(
        user_id, {"enabled": True, "topic": topic, "username": username, "is_admin": is_admin}
    )


def rotate_topic(user_id: str, *, username: str = "") -> dict[str, Any]:
    existing = get_subscription(user_id) or {}
    return save_subscription(
        user_id, {"topic": generate_topic(username or existing.get("username") or "")}
    )


def disable_subscription(user_id: str) -> dict[str, Any]:
    return save_subscription(user_id, {"enabled": False})


def redact_topic(topic: str) -> str:
    """Enough of a topic to recognise it in history, never enough to subscribe."""
    value = str(topic or "")
    if len(value) <= 6:
        return "…" if value else ""
    keep = max(3, len(value) - TOPIC_RANDOM_LEN + 2) if len(value) > TOPIC_RANDOM_LEN else 3
    return value[:keep] + "…"


def _parse_hm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def in_quiet_hours(quiet: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """True when ``now`` (household local time) falls inside the quiet window.

    Start is inclusive, end exclusive; windows may cross midnight. A window
    whose start equals its end is treated as empty.
    """
    if not quiet or not quiet.get("enabled"):
        return False
    start = _parse_hm(_clean_time(quiet.get("start"), DEFAULT_QUIET_START))
    end = _parse_hm(_clean_time(quiet.get("end"), DEFAULT_QUIET_END))
    if start == end:
        return False
    local = (now or datetime.now().astimezone()).time().replace(second=0, microsecond=0)
    if start < end:
        return start <= local < end
    return local >= start or local < end
