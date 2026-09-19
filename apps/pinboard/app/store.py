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
    today = date.today().isoformat()
    for rem in data["reminders"]:
        if len(lines) >= 5:
            break
        due = str(rem.get("due") or "")
        if due and due > today:
            continue
        label = str(rem.get("text") or "")
        if rem.get("assignee"):
            label = f"{label} ({rem['assignee']})"
        lines.append(label)
    total = len(data["notices"]) + len(data["reminders"])
    return {"lines": [line for line in lines if line], "total": total}
