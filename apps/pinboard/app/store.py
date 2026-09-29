from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import DATA_DIR

STORE = DATA_DIR / "pinboard.json"
LEGACY_STORE = DATA_DIR / "board.json"
DEFAULT_REMINDER_TIME = "08:00"
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
# One writer at a time: page saves and the reminder tick share the file.
_lock = threading.RLock()


def _load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = STORE if STORE.exists() else LEGACY_STORE
    empty = {"notices": [], "reminders": [], "settings": {}, "alerts_sent": {}}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    return {
        "notices": list(data.get("notices") or []),
        "reminders": list(data.get("reminders") or []),
        "settings": dict(data.get("settings") or {}),
        # reminder id -> due date it was alerted for (restarts never repeat an alert)
        "alerts_sent": dict(data.get("alerts_sent") or {}),
    }


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STORE)


def list_items() -> dict:
    return _load()


def board_cards() -> list[dict]:
    """Notices + reminders as one list (newest first) for the Board tab."""
    data = _load()
    cards: list[dict] = []
    for notice in data["notices"]:
        cards.append(
            {
                "id": notice.get("id"),
                "kind": "notice",
                "text": str(notice.get("text") or ""),
                "due": "",
                "due_label": "",
                "assignee": "",
                "created_at": str(notice.get("created_at") or ""),
            }
        )
    today = date.today()
    for rem in data["reminders"]:
        due_raw = str(rem.get("due") or "").strip()
        cards.append(
            {
                "id": rem.get("id"),
                "kind": "reminder",
                "text": str(rem.get("text") or ""),
                "due": due_raw,
                "due_label": _due_relative(due_raw, today) if due_raw else "",
                "assignee": str(rem.get("assignee") or "").strip(),
                "assignee_user": str(rem.get("assignee_user") or "").strip(),
                "created_at": str(rem.get("created_at") or ""),
            }
        )
    cards.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return cards


def add_notice(text: str, pinned: bool = True) -> dict:
    with _lock:
        data = _load()
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": text.strip(),
            "pinned": pinned,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        data["notices"].insert(0, item)
        _save(data)
        return item


def add_reminder(text: str, due: str = "", assignee: str = "", assignee_user: str = "") -> dict:
    """``assignee`` is the display name; ``assignee_user`` the household member's
    Auth user id (empty for unassigned or old free-text reminders)."""
    with _lock:
        data = _load()
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": text.strip(),
            "due": due.strip() or date.today().isoformat(),
            "assignee": assignee.strip(),
            "assignee_user": assignee_user.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        data["reminders"].insert(0, item)
        _save(data)
        return item


def delete_item(kind: str, item_id: str) -> bool:
    with _lock:
        data = _load()
        key = "notices" if kind == "notice" else "reminders"
        before = len(data[key])
        data[key] = [item for item in data[key] if item.get("id") != item_id]
        if key == "reminders":
            data["alerts_sent"].pop(item_id, None)
        _save(data)
        return len(data[key]) < before


def reminder_time() -> str:
    """Household time (HH:MM) when due reminders are sent."""
    raw = str(_load()["settings"].get("reminder_time") or "")
    return raw if _TIME_RE.match(raw) else DEFAULT_REMINDER_TIME


def set_reminder_time(value: str) -> str:
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        raise ValueError("Use a time like 08:00.")
    clean = f"{int(match.group(1)):02d}:{match.group(2)}"
    with _lock:
        data = _load()
        data["settings"]["reminder_time"] = clean
        _save(data)
    return clean


def reminders_due_for_alert(today: date) -> list[dict]:
    """Reminders due today that haven't been alerted for this due date."""
    data = _load()
    today_iso = today.isoformat()
    sent = data["alerts_sent"]
    return [
        dict(rem)
        for rem in data["reminders"]
        if str(rem.get("due") or "")[:10] == today_iso and sent.get(str(rem.get("id"))) != today_iso
    ]


def mark_reminder_alerted(reminder_id: str, due: str) -> None:
    with _lock:
        data = _load()
        known = {str(rem.get("id")) for rem in data["reminders"]}
        # Drop records for deleted reminders while we're here.
        data["alerts_sent"] = {k: v for k, v in data["alerts_sent"].items() if k in known}
        data["alerts_sent"][str(reminder_id)] = str(due)[:10]
        _save(data)


def display_payload() -> dict:
    data = _load()
    lines: list[str] = []
    for notice in data["notices"][:5]:
        lines.append(str(notice.get("text") or ""))
    today = date.today()
    today_iso = today.isoformat()
    reminders_out: list[dict] = []
    for rem in data["reminders"]:
        title = str(rem.get("text") or "").strip()
        if not title:
            continue
        due_raw = str(rem.get("due") or "").strip()
        due_label = _due_relative(due_raw, today)
        if rem.get("assignee"):
            title = f"{title} ({rem['assignee']})"
        reminders_out.append({"title": title, "due": due_label})
        # Keep legacy lines for Status/Household blocks (due today or overdue).
        if len(lines) < 5 and (not due_raw or due_raw <= today_iso):
            lines.append(title)
    total = len(data["notices"]) + len(data["reminders"])
    return {
        "ok": True,
        "lines": [line for line in lines if line],
        "total": total,
        "reminders": reminders_out[:5],
    }


def _due_relative(due_raw: str, today: date | None = None) -> str:
    """Human due label for Display: 'today', 'in 2 days', '3 days ago', or raw."""
    today = today or date.today()
    raw = (due_raw or "").strip()
    if not raw:
        return ""
    try:
        due = date.fromisoformat(raw[:10])
    except ValueError:
        return raw
    delta = (due - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if delta > 1:
        return f"in {delta} days"
    return f"{abs(delta)} days ago"
