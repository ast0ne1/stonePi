from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import DATA_DIR

STORE = DATA_DIR / "pinboard.json"
LEGACY_STORE = DATA_DIR / "board.json"


def _load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = STORE if STORE.exists() else LEGACY_STORE
    if not path.exists():
        return {"notices": [], "reminders": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"notices": [], "reminders": []}
    return {
        "notices": list(data.get("notices") or []),
        "reminders": list(data.get("reminders") or []),
    }


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_items() -> dict:
    return _load()


def add_notice(text: str, pinned: bool = True) -> dict:
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


def add_reminder(text: str, due: str = "", assignee: str = "") -> dict:
    data = _load()
    item = {
        "id": uuid.uuid4().hex[:12],
        "text": text.strip(),
        "due": due.strip() or date.today().isoformat(),
        "assignee": assignee.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data["reminders"].insert(0, item)
    _save(data)
    return item


def delete_item(kind: str, item_id: str) -> bool:
    data = _load()
    key = "notices" if kind == "notice" else "reminders"
    before = len(data[key])
    data[key] = [item for item in data[key] if item.get("id") != item_id]
    _save(data)
    return len(data[key]) < before


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
