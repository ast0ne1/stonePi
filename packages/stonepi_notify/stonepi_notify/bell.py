"""Per-person, per-app bell state: the last alert an app sent you, and when you last looked.

The header bell in every app shows a dot when that app has sent you an alert
since you last opened its panel. "Sent you" means a delivery to your own topic
(not the household topic); "last opened" is stored here per person and app.

**Clear** in a panel hides what it lists for that person only (``bell_cleared.json``):
nothing is deleted, Notify's history keeps every delivery. Clearing ``"dashboard"``
clears every app; clearing one app clears just that app's alerts.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .history import MAX_HISTORY, load_history

_data_dir: Path | None = None
_lock = threading.Lock()


def configure_bell(data_dir: Path) -> None:
    global _data_dir
    _data_dir = Path(data_dir)


def _path(name: str = "bell_seen.json") -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_notify not configured")
    return _data_dir / name


CLEARED_FILE = "bell_cleared.json"


def _load(name: str = "bell_seen.json") -> dict[str, dict[str, str]]:
    path = _path(name)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(uid): {str(app): str(at) for app, at in apps.items()}
        for uid, apps in data.items()
        if isinstance(apps, dict)
    }


def seen_at(user_id: str, app_id: str) -> str | None:
    return _load().get(str(user_id or "").strip(), {}).get(str(app_id or "").strip())


def _stamp(name: str, user_id: str, app_id: str, now: datetime | None) -> str:
    uid = str(user_id or "").strip()
    app = str(app_id or "").strip()
    if not uid or not app:
        raise ValueError("user_id and app_id required")
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    with _lock:
        data = _load(name)
        data.setdefault(uid, {})[app] = stamp
        path = _path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    return stamp


def mark_seen(user_id: str, app_id: str, *, now: datetime | None = None) -> str:
    """Record that this person opened ``app_id``'s bell panel; returns the stamp."""
    return _stamp("bell_seen.json", user_id, app_id, now)


def cleared_at(user_id: str, app_id: str) -> str | None:
    return _load(CLEARED_FILE).get(str(user_id or "").strip(), {}).get(str(app_id or "").strip())


def mark_cleared(user_id: str, app_id: str, *, now: datetime | None = None) -> str:
    """Hide everything ``app_id``'s panel lists for this person, up to now (also marks it seen)."""
    stamp = _stamp(CLEARED_FILE, user_id, app_id, now)
    _stamp("bell_seen.json", user_id, app_id, datetime.fromisoformat(stamp))
    return stamp


def _after(at: str, stamp: str | None) -> bool:
    if not stamp:
        return True
    try:
        return datetime.fromisoformat(at) > datetime.fromisoformat(stamp)
    except ValueError:
        return True


def _entry_app(entry: dict[str, Any]) -> str:
    try:
        from stonepi_contracts import event_type

        ev = event_type(entry.get("event_id") or "")
        if ev is not None:
            return ev.app
    except Exception:  # noqa: BLE001
        pass
    event_id = str(entry.get("event_id") or "")
    return event_id.split(".", 1)[0] if "." in event_id else str(entry.get("source") or "")


# The Dashboard's bell covers every app.
ALL_APPS = "dashboard"


def recent_alerts(user_id: str, app_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Newest deliveries from ``app_id`` (any app for ``"dashboard"``) to this person's own topic.

    Each ``{"title", "at", "app", "ok", "silent", "message"}`` — ``ok`` says whether ntfy
    took it for the phone; ``silent`` that it arrived during quiet hours.
    """
    uid = str(user_id or "").strip()
    app = str(app_id or "").strip()
    if not uid or not app:
        return []
    out: list[dict[str, Any]] = []
    cleared = _load(CLEARED_FILE).get(uid, {})
    for entry in load_history(limit=MAX_HISTORY):
        entry_app = _entry_app(entry)
        if app != ALL_APPS and entry_app != app:
            continue
        at = str(entry.get("at") or "")
        # Cleared on the Dashboard (every app) or in this entry's own app: hidden, not deleted.
        if not (_after(at, cleared.get(ALL_APPS)) and _after(at, cleared.get(entry_app))):
            continue
        for delivery in entry.get("deliveries") or []:
            if delivery.get("user") != uid:
                continue
            out.append(
                {
                    "title": str(entry.get("title") or ""),
                    "at": str(entry.get("at") or ""),
                    "app": entry_app,
                    "ok": bool(delivery.get("ok")),
                    "silent": bool(delivery.get("silent")),
                    "message": "" if delivery.get("ok") else str(delivery.get("message") or ""),
                }
            )
            break
        if len(out) >= limit:
            break
    return out


def last_alert(user_id: str, app_id: str) -> dict[str, Any] | None:
    """Newest delivery from ``app_id`` (any app for ``"dashboard"``) to this person, or None."""
    recent = recent_alerts(user_id, app_id, limit=1)
    return recent[0] if recent else None


def unseen(last: dict[str, Any] | None, seen: str | None) -> bool:
    """True when ``last`` arrived after ``seen`` (ISO stamps, UTC)."""
    if not last or not last.get("at"):
        return False
    if not seen:
        return True
    try:
        return datetime.fromisoformat(last["at"]) > datetime.fromisoformat(seen)
    except ValueError:
        return False
