"""Pinboard's SQLite store: cards on one shared canvas (ideation/pinboard-canvas.md).

Every item is a card (``note``, ``link``, ``reminder`` or ``checklist``) with a place
on the canvas (``x``, ``y``, ``w``, ``h``, ``z``), an optional area, an optional pin
(``pin_color`` + ``pin_style``) and its own fields in the ``data`` JSON column. A pinned
card goes on the displays; so does a reminder that is due today or overdue and not
done, pinned or not.

``list_items`` keeps the old JSON store's shapes (``notices`` and ``reminders``) for the
Notices and Reminders pages, PriceScout and AgentLink. A household's ``pinboard.json``
(or the older ``board.json``) is imported once on first start and kept as
``*.migrated``. Schema changes go in MIGRATIONS and are applied in order using
``PRAGMA user_version``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from app.config import DATA_DIR

logger = logging.getLogger("pinboard.store")

DB_FILE = "pinboard.sqlite"
PREVIEWS_DIR = "previews"
STORE = DATA_DIR / "pinboard.json"
LEGACY_STORE = DATA_DIR / "board.json"
DEFAULT_REMINDER_TIME = "08:00"
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

CARD_TYPES = ("note", "link", "reminder", "checklist")
NOTE_COLORS = ("yellow", "pink", "blue", "green")
PIN_COLORS = ("red", "blue", "green", "yellow")
PIN_STYLES = ("push", "tack", "map", "tape")
BACKGROUNDS = ("cork", "plain", "dotted", "whiteboard")
AUTO_REMOVE_DAYS = (0, 1, 3, 7, 14, 30)
DEFAULT_PIN = ("blue", "push")
HOUSEHOLD_AREA = "household"
TEXT_MAX, TITLE_MAX, NAME_MAX, URL_MAX, ITEMS_MAX = 1000, 120, 40, 2000, 30
# Cards added from a list go into their area's next spot, in rows of five; on the canvas
# they go wherever they're put.
CARD_W, CARD_H, CARD_GAP, AREA_PAD, AREA_HEADER, PER_ROW = 220, 160, 20, 24, 40, 5
CARD_MIN, CARD_MAX = (140, 90), (720, 720)
AREA_MIN, AREA_MAX = (260, 160), (6000, 6000)
CANVAS_LIMIT = 50_000  # keeps coordinates sane if a drag goes wild

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE areas (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        color TEXT NOT NULL DEFAULT '',
        x REAL NOT NULL DEFAULT 0,
        y REAL NOT NULL DEFAULT 0,
        w REAL NOT NULL DEFAULT 0,
        h REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE cards (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        area_id TEXT REFERENCES areas (id) ON DELETE SET NULL,
        x REAL NOT NULL DEFAULT 0,
        y REAL NOT NULL DEFAULT 0,
        w REAL NOT NULL DEFAULT 220,
        h REAL NOT NULL DEFAULT 160,
        z INTEGER NOT NULL DEFAULT 0,
        color TEXT NOT NULL DEFAULT '',
        pin_color TEXT NOT NULL DEFAULT '',
        pin_style TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        data TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX cards_type ON cards (type, created_at DESC);
    CREATE INDEX cards_updated ON cards (updated_at);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    -- reminder id -> due date it was alerted for (restarts never repeat an alert)
    CREATE TABLE alerts_sent (
        card_id TEXT PRIMARY KEY,
        due TEXT NOT NULL
    );
    """,
)

# One writer at a time: page saves and the reminder tick share the database.
_lock = threading.RLock()


def db_path() -> Path:
    return DATA_DIR / DB_FILE


def previews_dir() -> Path:
    return DATA_DIR / PREVIEWS_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            _migrate(conn)
            with conn:
                _ensure_household_area(conn)
                _import_json_once(conn)
                yield conn
        finally:
            conn.close()


# --- areas and layout --------------------------------------------------------------


def _ensure_household_area(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM areas WHERE id = ?", (HOUSEHOLD_AREA,)).fetchone():
        return
    _insert_area(conn, HOUSEHOLD_AREA, "Household", 0)


def _area_width() -> int:
    return AREA_PAD * 2 + PER_ROW * CARD_W + (PER_ROW - 1) * CARD_GAP


def _insert_area(conn: sqlite3.Connection, area_id: str, name: str, y: float) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO areas (id, name, color, x, y, w, h, created_at, updated_at) VALUES (?, ?, '', 0, ?, ?, ?, ?, ?)",
        (area_id, name, y, _area_width(), AREA_HEADER + AREA_PAD * 2 + CARD_H, now, now),
    )


def _bottom(conn: sqlite3.Connection) -> float:
    """The lowest edge of anything on the canvas (areas and loose cards)."""
    row = conn.execute(
        "SELECT MAX(b) FROM (SELECT y + h AS b FROM areas UNION ALL SELECT y + h FROM cards WHERE area_id IS NULL)"
    ).fetchone()
    return float(row[0] or 0)


def _next_slot(conn: sqlite3.Connection, area_id: str | None) -> tuple[float, float, int]:
    """The area's next free grid spot (x, y) and the next z; grows the area to fit.

    A card with no area goes below everything on the canvas."""
    z = conn.execute("SELECT COALESCE(MAX(z), 0) + 1 FROM cards").fetchone()[0]
    area = conn.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone() if area_id else None
    if area is None:
        return 0.0, _bottom(conn) + CARD_GAP, z
    slot = conn.execute("SELECT COUNT(*) FROM cards WHERE area_id = ?", (area_id,)).fetchone()[0]
    col, row = slot % PER_ROW, slot // PER_ROW
    x = area["x"] + AREA_PAD + col * (CARD_W + CARD_GAP)
    y = area["y"] + AREA_HEADER + AREA_PAD + row * (CARD_H + CARD_GAP)
    needed = y + CARD_H + AREA_PAD - area["y"]
    if needed > area["h"]:
        grow = needed - area["h"]
        conn.execute("UPDATE areas SET h = ?, updated_at = ? WHERE id = ?", (needed, _now(), area_id))
        # Keep the areas below clear of the one that grew.
        conn.execute("UPDATE areas SET y = y + ? WHERE y > ? AND id != ?", (grow, area["y"], area_id))
        conn.execute(
            "UPDATE cards SET y = y + ? WHERE area_id IN (SELECT id FROM areas WHERE y > ? AND id != ?)",
            (grow, area["y"] + grow, area_id),
        )
    return x, y, z


def _area_or_default(conn: sqlite3.Connection, area_id: str | None) -> str | None:
    """A known area id, '' meaning no area, anything unknown falls back to Household."""
    if area_id == "":
        return None
    if area_id and conn.execute("SELECT 1 FROM areas WHERE id = ?", (area_id,)).fetchone():
        return area_id
    return HOUSEHOLD_AREA


def areas() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM areas ORDER BY y, x, created_at")]


def add_area(name: str) -> dict[str, Any]:
    clean = name.strip()[:NAME_MAX]
    if not clean:
        raise ValueError("Give the area a name.")
    with connect() as conn:
        area_id = uuid.uuid4().hex[:12]
        _insert_area(conn, area_id, clean, _bottom(conn) + CARD_GAP * 2)
        return dict(conn.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone())


def rename_area(area_id: str, name: str) -> bool:
    clean = name.strip()[:NAME_MAX]
    if not clean:
        raise ValueError("Give the area a name.")
    with connect() as conn:
        return conn.execute(
            "UPDATE areas SET name = ?, updated_at = ? WHERE id = ?", (clean, _now(), area_id)
        ).rowcount > 0


def delete_area(area_id: str) -> bool:
    """Removes an area; its cards stay on the board without one. Household stays."""
    if area_id == HOUSEHOLD_AREA:
        raise ValueError("Household is where new cards go, so it can't be removed.")
    with connect() as conn:
        conn.execute("UPDATE cards SET area_id = NULL, updated_at = ? WHERE area_id = ?", (_now(), area_id))
        return conn.execute("DELETE FROM areas WHERE id = ?", (area_id,)).rowcount > 0


# --- cards -----------------------------------------------------------------------------


def _insert_card(
    conn: sqlite3.Connection,
    kind: str,
    data: dict[str, Any],
    *,
    card_id: str = "",
    pin: tuple[str, str] | None = DEFAULT_PIN,
    color: str = "",
    author: str = "",
    created_at: str = "",
    area_id: str | None = HOUSEHOLD_AREA,
    at: tuple[float, float] | None = None,
) -> dict[str, Any]:
    if at is not None:
        # Put down on the canvas: the area is whichever one it landed in.
        x, y = _clamp(at[0]), _clamp(at[1])
        area = _area_at(conn, x + CARD_W / 2, y + CARD_H / 2)
        z = conn.execute("SELECT COALESCE(MAX(z), 0) + 1 FROM cards").fetchone()[0]
    else:
        area = _area_or_default(conn, area_id)
        x, y, z = _next_slot(conn, area)
    created = created_at or _now()
    pin_color, pin_style = _clean_pin(pin)
    row = {
        "id": card_id or uuid.uuid4().hex[:12],
        "type": kind,
        "area_id": area,
        "x": x,
        "y": y,
        "w": CARD_W,
        "h": CARD_H,
        "z": z,
        "color": color,
        "pin_color": pin_color,
        "pin_style": pin_style,
        "author": author,
        "data": json.dumps(data),
        "created_at": created,
        "updated_at": _now(),
    }
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO cards ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    return _card(row)


def _clean_pin(pin: tuple[str, str] | None) -> tuple[str, str]:
    if not pin or pin[0] not in PIN_COLORS:
        return "", ""
    return pin[0], pin[1] if pin[1] in PIN_STYLES else DEFAULT_PIN[1]


def _card(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    card = dict(row)
    try:
        card["data"] = json.loads(card.get("data") or "{}")
    except ValueError:
        card["data"] = {}
    return card


def _cards(conn: sqlite3.Connection, kind: str | None = None) -> list[dict[str, Any]]:
    """Cards newest first (rowid breaks ties between cards made in the same instant)."""
    if kind:
        rows = conn.execute("SELECT * FROM cards WHERE type = ? ORDER BY created_at DESC, rowid DESC", (kind,))
    else:
        rows = conn.execute("SELECT * FROM cards ORDER BY created_at DESC, rowid DESC")
    return [_card(row) for row in rows.fetchall()]


def _get(conn: sqlite3.Connection, card_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return _card(row) if row else None


def _save_data(conn: sqlite3.Connection, card_id: str, data: dict[str, Any], **cols: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(
        f"UPDATE cards SET data = ?, updated_at = ?{', ' + sets if sets else ''} WHERE id = ?",
        (json.dumps(data), _now(), *cols.values(), card_id),
    )


def get_card(card_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return _get(conn, card_id)


def is_done(card: dict[str, Any]) -> bool:
    data = card["data"]
    if card["type"] == "reminder":
        return bool(data.get("done"))
    if card["type"] == "checklist":
        items = data.get("items") or []
        return bool(items) and all(i.get("done") for i in items)
    return False


def card_title(card: dict[str, Any]) -> str:
    data = card["data"]
    if card["type"] == "link":
        return str(data.get("title") or "").strip() or _host(str(data.get("url") or ""))
    if card["type"] == "checklist":
        return str(data.get("title") or "").strip() or "Checklist"
    return str(data.get("text") or "").strip()


def _host(url: str) -> str:
    host = urlsplit(url).hostname or url
    return host[4:] if host.startswith("www.") else host


def clean_url(raw: str) -> str:
    """An http(s) URL to save, or ValueError. A bare 'example.com' gets https://."""
    url = (raw or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or len(url) > URL_MAX:
        raise ValueError("Use a web address starting with http:// or https://.")
    return url


def _clean_items(lines: list[str] | str, keep: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Checklist items from text lines; an item that's still there keeps its tick."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    ticked = {str(i.get("text")): bool(i.get("done")) for i in keep or []}
    out = []
    for line in lines:
        text = line.strip().lstrip("-*•").strip()[:TITLE_MAX]
        if text:
            out.append({"text": text, "done": ticked.get(text, False)})
    return out[:ITEMS_MAX]


def board_groups(today: date | None = None) -> list[dict[str, Any]]:
    """The list view: each area (canvas order) with its cards top-left to bottom-right,
    then the cards that aren't in an area under "Board"."""
    today = today or date.today()
    with connect() as conn:
        area_rows = [dict(r) for r in conn.execute("SELECT * FROM areas ORDER BY y, x, created_at")]
        cards = [_card(r) for r in conn.execute("SELECT * FROM cards ORDER BY y, x, z, rowid")]
    by_area: dict[str | None, list[dict[str, Any]]] = {}
    for card in cards:
        by_area.setdefault(card["area_id"], []).append(card_view(card, today))
    groups = [{"area": a, "cards": by_area.get(a["id"], [])} for a in area_rows]
    if by_area.get(None):
        groups.append({"area": None, "cards": by_area[None]})
    return groups


def card_view(card: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Everything a card face needs, flattened for the templates."""
    today = today or date.today()
    data = card["data"]
    due = str(data.get("due") or "").strip() if card["type"] == "reminder" else ""
    items = list(data.get("items") or []) if card["type"] == "checklist" else []
    label = _due_relative(due, today) if due else ""
    return {
        "id": card["id"],
        "type": card["type"],
        "area_id": card["area_id"] or "",
        "x": card["x"],
        "y": card["y"],
        "w": card["w"],
        "h": card["h"],
        "z": card["z"],
        "rev": _content_rev(card),
        "title": card_title(card),
        "text": str(data.get("text") or ""),
        "color": card["color"] if card["color"] in NOTE_COLORS else "yellow",
        "pin_color": card["pin_color"],
        "pin_style": card["pin_style"] or DEFAULT_PIN[1],
        "author": card["author"],
        "created_at": card["created_at"],
        "done": is_done(card),
        # reminder
        "due": due,
        "due_label": label,
        "overdue": bool(due) and due[:10] < today.isoformat() and not data.get("done"),
        "assignee": str(data.get("assignee") or "").strip(),
        "assignee_user": str(data.get("assignee_user") or "").strip(),
        # link
        "url": str(data.get("url") or ""),
        "host": _host(str(data.get("url") or "")) if card["type"] == "link" else "",
        "site": str(data.get("site") or ""),
        "description": str(data.get("description") or ""),
        "note": str(data.get("note") or ""),
        "image": str(data.get("image") or ""),
        "icon": str(data.get("icon") or ""),
        "preview": str(data.get("preview") or ""),
        # checklist
        "checks": items,
        "ticked": sum(1 for i in items if i.get("done")),
    }


def add_card(
    kind: str,
    fields: dict[str, Any],
    *,
    pin: tuple[str, str] | None = None,
    author: str = "",
    area_id: str | None = HOUSEHOLD_AREA,
    at: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """A new note, link or checklist (reminders go through ``add_reminder``). ValueError
    when a required field is missing."""
    color = ""
    if kind == "note":
        text = str(fields.get("text") or "").strip()[:TEXT_MAX]
        if not text:
            raise ValueError("Write something on the note.")
        data: dict[str, Any] = {"text": text}
        color = str(fields.get("color") or "")
        color = color if color in NOTE_COLORS else "yellow"
    elif kind == "link":
        data = {
            "url": clean_url(str(fields.get("url") or "")),
            "note": str(fields.get("note") or "").strip()[:TEXT_MAX],
            # A shared page's title, until (or unless) the preview finds a better one.
            "title": " ".join(str(fields.get("title") or "").split())[:TITLE_MAX],
            "site": "",
            "description": "",
            "image": "",
            "icon": "",
            "preview": "pending",
        }
    elif kind == "checklist":
        title = str(fields.get("title") or "").strip()[:TITLE_MAX]
        items = _clean_items(fields.get("items") or "")
        if not title and not items:
            raise ValueError("Give the checklist a title or an item.")
        data = {"title": title, "items": items, "done_at": ""}
    else:
        raise ValueError("Unknown card type.")
    with connect() as conn:
        return _insert_card(conn, kind, data, pin=pin, color=color, author=author, area_id=area_id, at=at)


def update_card(card_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Edit a card's own fields (not its pin, place or done state). ValueError on bad input,
    LookupError when it's gone."""
    with connect() as conn:
        card = _get(conn, card_id)
        if card is None:
            raise LookupError(card_id)
        data, cols = dict(card["data"]), {}
        kind = card["type"]
        if kind == "note":
            text = str(fields.get("text") or "").strip()[:TEXT_MAX]
            if not text:
                raise ValueError("Write something on the note.")
            data["text"] = text
            if fields.get("color") in NOTE_COLORS:
                cols["color"] = fields["color"]
        elif kind == "link":
            url = clean_url(str(fields.get("url") or data.get("url") or ""))
            if url != data.get("url"):
                data.update(url=url, title="", site="", description="", image="", icon="", preview="pending")
            data["note"] = str(fields.get("note") or "").strip()[:TEXT_MAX]
        elif kind == "reminder":
            text = str(fields.get("text") or "").strip()[:TEXT_MAX]
            if not text:
                raise ValueError("Reminder text required.")
            data["text"] = text
            if "due" in fields:
                data["due"] = str(fields.get("due") or "").strip() or data.get("due") or date.today().isoformat()
        elif kind == "checklist":
            title = str(fields.get("title") or "").strip()[:TITLE_MAX]
            items = _clean_items(fields.get("items") or "", keep=data.get("items"))
            if not title and not items:
                raise ValueError("Give the checklist a title or an item.")
            data.update(title=title, items=items)
            data["done_at"] = (data.get("done_at") or _now()) if items and all(i["done"] for i in items) else ""
        _save_data(conn, card_id, data, **cols)
        return _get(conn, card_id) or card


def set_pin(card_id: str, color: str, style: str = "") -> bool:
    """Pin (one of PIN_COLORS) or unpin ('') a card."""
    pin_color, pin_style = _clean_pin((color, style)) if color else ("", "")
    with connect() as conn:
        return conn.execute(
            "UPDATE cards SET pin_color = ?, pin_style = ?, updated_at = ? WHERE id = ?",
            (pin_color, pin_style, _now(), card_id),
        ).rowcount > 0


def move_card(card_id: str, area_id: str) -> bool:
    """Into another area ('' for none), at that area's next free spot."""
    with connect() as conn:
        card = _get(conn, card_id)
        if card is None:
            return False
        area = _area_or_default(conn, area_id)
        if area == card["area_id"]:
            return True
        # Take the card out of the count first so it doesn't hold a slot in the new area.
        conn.execute("UPDATE cards SET area_id = NULL, y = -1e9 WHERE id = ?", (card_id,))
        x, y, z = _next_slot(conn, area)
        conn.execute(
            "UPDATE cards SET area_id = ?, x = ?, y = ?, z = ?, updated_at = ? WHERE id = ?",
            (area, x, y, z, _now(), card_id),
        )
    return True


def _clamp(value: float, low: float = -CANVAS_LIMIT, high: float = CANVAS_LIMIT) -> float:
    return round(max(low, min(high, float(value))), 1)


def _area_at(conn: sqlite3.Connection, cx: float, cy: float) -> str | None:
    """The area a point falls in (the smallest, if areas overlap), or None."""
    row = conn.execute(
        "SELECT id FROM areas WHERE ? BETWEEN x AND x + w AND ? BETWEEN y AND y + h ORDER BY w * h LIMIT 1",
        (cx, cy),
    ).fetchone()
    return row[0] if row else None


def place_card(card_id: str, x: float, y: float, w: float | None = None, h: float | None = None) -> dict | None:
    """Put a card down on the canvas: new position (and size), brought to the front, in
    whichever area its middle landed in. Returns its new area and z, or None if gone."""
    with connect() as conn:
        card = _get(conn, card_id)
        if card is None:
            return None
        w = _clamp(card["w"] if w is None else w, CARD_MIN[0], CARD_MAX[0])
        h = _clamp(card["h"] if h is None else h, CARD_MIN[1], CARD_MAX[1])
        x, y = _clamp(x), _clamp(y)
        area = _area_at(conn, x + w / 2, y + h / 2)
        z = conn.execute("SELECT COALESCE(MAX(z), 0) + 1 FROM cards").fetchone()[0]
        conn.execute(
            "UPDATE cards SET x = ?, y = ?, w = ?, h = ?, z = ?, area_id = ?, updated_at = ? WHERE id = ?",
            (x, y, w, h, z, area, _now(), card_id),
        )
    return {"area_id": area or "", "z": z, "x": x, "y": y, "w": w, "h": h}


def place_area(area_id: str, x: float, y: float, w: float | None = None, h: float | None = None) -> bool:
    """Move (and resize) an area on the canvas; its cards move with it."""
    with connect() as conn:
        area = conn.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone()
        if area is None:
            return False
        x, y = _clamp(x), _clamp(y)
        w = _clamp(area["w"] if w is None else w, AREA_MIN[0], AREA_MAX[0])
        h = _clamp(area["h"] if h is None else h, AREA_MIN[1], AREA_MAX[1])
        dx, dy = x - area["x"], y - area["y"]
        now = _now()
        conn.execute("UPDATE areas SET x = ?, y = ?, w = ?, h = ?, updated_at = ? WHERE id = ?", (x, y, w, h, now, area_id))
        if dx or dy:
            conn.execute(
                "UPDATE cards SET x = x + ?, y = y + ?, updated_at = ? WHERE area_id = ?", (dx, dy, now, area_id)
            )
    return True


def _content_rev(card: dict[str, Any]) -> str:
    """Changes when what a card shows changes (not when it's moved)."""
    raw = json.dumps([card["type"], card["data"], card["color"], card["pin_color"], card["pin_style"]], sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def canvas_state() -> dict[str, Any]:
    """Where everything is, for the canvas and for noticing other people's changes."""
    with connect() as conn:
        cards = [_card(r) for r in conn.execute("SELECT * FROM cards ORDER BY z, rowid")]
        area_rows = [dict(r) for r in conn.execute("SELECT * FROM areas ORDER BY y, x, created_at")]
    return {
        "cards": [
            {k: c[k] for k in ("id", "x", "y", "w", "h", "z")} | {"area_id": c["area_id"] or "", "rev": _content_rev(c)}
            for c in cards
        ],
        "areas": [{k: a[k] for k in ("id", "name", "color", "x", "y", "w", "h")} for a in area_rows],
    }


def set_reminder_done(reminder_id: str, done: bool = True) -> bool:
    with connect() as conn:
        card = _get(conn, reminder_id)
        if card is None or card["type"] != "reminder":
            return False
        data = card["data"]
        data["done"] = bool(done)
        data["done_at"] = _now() if done else ""
        _save_data(conn, reminder_id, data)
    return True


def toggle_check(card_id: str, index: int) -> bool | None:
    """Tick or untick one checklist item; returns its new state (None if not found)."""
    with connect() as conn:
        card = _get(conn, card_id)
        if card is None or card["type"] != "checklist":
            return None
        data = card["data"]
        items = data.get("items") or []
        if not 0 <= index < len(items):
            return None
        items[index]["done"] = not items[index].get("done")
        data["done_at"] = (data.get("done_at") or _now()) if all(i.get("done") for i in items) else ""
        _save_data(conn, card_id, data)
        return bool(items[index]["done"])


def set_link_preview(card_id: str, fields: dict[str, str]) -> None:
    with connect() as conn:
        card = _get(conn, card_id)
        if card is None or card["type"] != "link":
            return
        data = card["data"]
        data.update({k: v for k, v in fields.items() if k in {"title", "site", "description", "image", "icon", "preview"}})
        _save_data(conn, card_id, data)


def delete_card(card_id: str) -> bool:
    with connect() as conn:
        gone = conn.execute("DELETE FROM cards WHERE id = ?", (card_id,)).rowcount
        conn.execute("DELETE FROM alerts_sent WHERE card_id = ?", (card_id,))
    if gone:
        _delete_preview_files(card_id)
    return gone > 0


def _delete_preview_files(card_id: str) -> None:
    folder = previews_dir()
    if folder.is_dir():
        for path in folder.glob(f"{card_id}-*"):
            path.unlink(missing_ok=True)


def remove_done_items(now: datetime | None = None) -> int:
    """Auto-remove (§9a): done reminders and finished checklists older than the setting."""
    days = auto_remove_days()
    if not days:
        return 0
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    with connect() as conn:
        old = [
            c["id"]
            for c in _cards(conn)
            if is_done(c) and str(c["data"].get("done_at") or "") and str(c["data"]["done_at"]) <= cutoff
        ]
    for card_id in old:
        delete_card(card_id)
    return len(old)


# --- one-off import of the old JSON store ----------------------------------------------


def _import_json_once(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM settings WHERE key = 'json_imported'").fetchone():
        return
    path = STORE if STORE.exists() else LEGACY_STORE
    old: dict[str, Any] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Couldn't read %s; starting with an empty board", path)
            old = {}
    items: list[tuple[str, dict[str, Any]]] = [("note", n) for n in old.get("notices") or []]
    items += [("reminder", r) for r in old.get("reminders") or []]
    # Newest first, matching the old Board order: they fill the Household grid in that order.
    items.sort(key=lambda pair: str(pair[1].get("created_at") or ""), reverse=True)
    for kind, item in items:
        text = str(item.get("text") or "")
        if kind == "note":
            data: dict[str, Any] = {"text": text}
        else:
            data = {
                "text": text,
                "due": str(item.get("due") or ""),
                "assignee": str(item.get("assignee") or ""),
                "assignee_user": str(item.get("assignee_user") or ""),
                "done": False,
                "done_at": "",
            }
        # Every notice and reminder showed on the displays before, so they keep a pin.
        _insert_card(
            conn,
            kind,
            data,
            card_id=str(item.get("id") or ""),
            pin=DEFAULT_PIN,
            color="yellow" if kind == "note" else "",
            created_at=str(item.get("created_at") or ""),
        )
    for key, value in (old.get("settings") or {}).items():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (str(key), str(value)))
    for card_id, due in (old.get("alerts_sent") or {}).items():
        conn.execute("INSERT OR REPLACE INTO alerts_sent (card_id, due) VALUES (?, ?)", (str(card_id), str(due)))
    conn.execute("INSERT INTO settings (key, value) VALUES ('json_imported', ?)", (_now(),))
    if path.exists():
        conn.commit()  # the cards are safe before the old file is moved aside
        try:
            path.replace(path.with_name(path.name + ".migrated"))
        except OSError:
            logger.warning("Imported %s but couldn't rename it", path)
        logger.info("Imported %s notice(s) and reminder(s) from %s", len(items), path.name)


# --- the old shapes (Notices and Reminders pages, PriceScout, AgentLink) ---------------


def _notice(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": card["id"],
        "text": str(card["data"].get("text") or ""),
        "pinned": bool(card["pin_color"]),
        "created_at": card["created_at"],
    }


def _reminder(card: dict[str, Any]) -> dict[str, Any]:
    data = card["data"]
    return {
        "id": card["id"],
        "text": str(data.get("text") or ""),
        "due": str(data.get("due") or ""),
        "assignee": str(data.get("assignee") or ""),
        "assignee_user": str(data.get("assignee_user") or ""),
        "done": bool(data.get("done")),
        "done_at": str(data.get("done_at") or ""),
        "pinned": bool(card["pin_color"]),
        "created_at": card["created_at"],
    }


def _settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}


def list_items() -> dict:
    with connect() as conn:
        return {
            "notices": [_notice(c) for c in _cards(conn, "note")],
            "reminders": [_reminder(c) for c in _cards(conn, "reminder")],
            "count": conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0],
            "settings": {k: v for k, v in _settings(conn).items() if k != "json_imported" and ":" not in k},
            "alerts_sent": {r["card_id"]: r["due"] for r in conn.execute("SELECT * FROM alerts_sent")},
        }


def board_cards() -> list[dict]:
    """Every card, newest first (the Car Thing and older callers)."""
    today = date.today()
    with connect() as conn:
        cards = _cards(conn)
    out = []
    for card in cards:
        view = card_view(card, today)
        view["kind"] = "notice" if card["type"] == "note" else card["type"]
        view["text"] = view["text"] or view["title"]
        out.append(view)
    return out


def add_notice(text: str, pinned: bool = True, author: str = "") -> dict:
    with connect() as conn:
        card = _insert_card(
            conn, "note", {"text": text.strip()}, pin=DEFAULT_PIN if pinned else None, color="yellow", author=author
        )
    return _notice(card)


def add_reminder(
    text: str,
    due: str = "",
    assignee: str = "",
    assignee_user: str = "",
    author: str = "",
    *,
    pin: tuple[str, str] | None = DEFAULT_PIN,
    area_id: str | None = HOUSEHOLD_AREA,
    at: tuple[float, float] | None = None,
) -> dict:
    """``assignee`` is the display name; ``assignee_user`` the household member's
    Auth user id (empty for unassigned or old free-text reminders).

    Reminders from the Reminders page and PriceScout are pinned (blue) so the displays
    show them as before; the Board's add sheet lets people choose."""
    data = {
        "text": text.strip(),
        "due": due.strip() or date.today().isoformat(),
        "assignee": assignee.strip(),
        "assignee_user": assignee_user.strip(),
        "done": False,
        "done_at": "",
    }
    with connect() as conn:
        card = _insert_card(conn, "reminder", data, pin=pin, author=author, area_id=area_id, at=at)
    return _reminder(card)


def delete_item(kind: str, item_id: str) -> bool:
    card_type = "note" if kind == "notice" else kind
    with connect() as conn:
        gone = conn.execute("DELETE FROM cards WHERE id = ? AND type = ?", (item_id, card_type)).rowcount
        conn.execute("DELETE FROM alerts_sent WHERE card_id = ?", (item_id,))
    if gone:
        _delete_preview_files(item_id)
    return gone > 0


# --- settings --------------------------------------------------------------------------


def _get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else default


def _set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def reminder_time() -> str:
    """Household time (HH:MM) when due reminders are sent."""
    raw = _get_setting("reminder_time")
    return raw if _TIME_RE.match(raw) else DEFAULT_REMINDER_TIME


def set_reminder_time(value: str) -> str:
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        raise ValueError("Use a time like 08:00.")
    clean = f"{int(match.group(1)):02d}:{match.group(2)}"
    _set_setting("reminder_time", clean)
    return clean


def auto_remove_days() -> int:
    try:
        days = int(_get_setting("auto_remove_days", "0"))
    except ValueError:
        return 0
    return days if days in AUTO_REMOVE_DAYS else 0


def set_auto_remove_days(days: int) -> int:
    if days not in AUTO_REMOVE_DAYS:
        raise ValueError("Pick one of the listed choices.")
    _set_setting("auto_remove_days", str(days))
    return days


def user_pref(user_id: str, key: str, allowed: tuple[str, ...]) -> str:
    """A person's own choice (background, last pin style); the first allowed value by default.
    Without sign-in everyone shares ``user_id`` ''."""
    value = _get_setting(f"{key}:{user_id}")
    return value if value in allowed else allowed[0]


def set_user_pref(user_id: str, key: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValueError("Pick one of the listed choices.")
    _set_setting(f"{key}:{user_id}", value)
    return value


# --- reminder alerts -------------------------------------------------------------------


def reminders_due_for_alert(today: date) -> list[dict]:
    """Reminders due today, not done, that haven't been alerted for this due date."""
    today_iso = today.isoformat()
    with connect() as conn:
        sent = {r["card_id"]: r["due"] for r in conn.execute("SELECT * FROM alerts_sent")}
        reminders = [_reminder(c) for c in _cards(conn, "reminder")]
    return [
        rem
        for rem in reminders
        if not rem["done"] and rem["due"][:10] == today_iso and sent.get(str(rem["id"])) != today_iso
    ]


def mark_reminder_alerted(reminder_id: str, due: str) -> None:
    with connect() as conn:
        # Drop records for deleted reminders while we're here.
        conn.execute("DELETE FROM alerts_sent WHERE card_id NOT IN (SELECT id FROM cards)")
        conn.execute(
            "INSERT OR REPLACE INTO alerts_sent (card_id, due) VALUES (?, ?)", (str(reminder_id), str(due)[:10])
        )


# --- the displays: pinned cards plus due reminders ------------------------------------


def _is_due(card: dict[str, Any], today_iso: str) -> bool:
    due = str(card["data"].get("due") or "").strip()
    return card["type"] == "reminder" and (not due or due[:10] <= today_iso)


def _display_cards(today: date) -> tuple[list[dict[str, Any]], int]:
    """Cards for the TRMNL and Car Thing (red pins first, then newest) and the board's count."""
    today_iso = today.isoformat()
    with connect() as conn:
        cards = [c for c in _cards(conn) if c["type"] in CARD_TYPES and not is_done(c)]
    shown = [c for c in cards if c["pin_color"] or _is_due(c, today_iso)]
    shown.sort(key=lambda c: c["pin_color"] != "red")  # stable: newest first within each group
    return shown, len(cards)


def _line(card: dict[str, Any]) -> str:
    """A non-reminder card as one display line."""
    title = card_title(card)
    if card["type"] == "link":
        site = str(card["data"].get("site") or "") or _host(str(card["data"].get("url") or ""))
        return f"{title} · {site}" if site and site != title else title
    if card["type"] == "checklist":
        items = card["data"].get("items") or []
        if items:
            return f"{title} ({sum(1 for i in items if i.get('done'))}/{len(items)})"
    return title


def display_payload() -> dict:
    today = date.today()
    today_iso = today.isoformat()
    shown, total = _display_cards(today)
    lines: list[str] = [_line(c) for c in shown if c["type"] != "reminder"][:5]
    reminders_out: list[dict] = []
    for card in shown:
        if card["type"] != "reminder":
            continue
        data = card["data"]
        title = str(data.get("text") or "").strip()
        if not title:
            continue
        due_raw = str(data.get("due") or "").strip()
        if data.get("assignee"):
            title = f"{title} ({data['assignee']})"
        reminders_out.append({"title": title, "due": _due_relative(due_raw, today)})
        # Keep legacy lines for Status/Household blocks (due today or overdue).
        if len(lines) < 5 and (not due_raw or due_raw <= today_iso):
            lines.append(title)
    return {
        "ok": True,
        "lines": [line for line in lines if line],
        "total": total,
        "reminders": reminders_out[:5],
    }


def panel_feed(limit: int) -> dict:
    """Car Thing panel extras for ``/api/display?items=N``: a card + the displayed cards."""
    today = date.today()
    shown, total = _display_cards(today)
    items: list[dict] = []
    for card in shown[: max(1, min(50, limit))]:
        data = card["data"]
        title = card_title(card).splitlines()[0][:80] if card_title(card) else "—"
        detail = str(data.get("text") or data.get("note") or "").strip()
        sub, badge = "Notice", ""
        if card["type"] == "reminder":
            label = _due_relative(str(data.get("due") or ""), today)
            parts = [f"Due {label}" if label else "Reminder"]
            if data.get("assignee"):
                parts.append(str(data["assignee"]).strip())
            sub = " · ".join(parts)
            if label in {"today", "yesterday"} or label.endswith("ago"):
                badge = "Due"
        elif card["type"] == "link":
            sub = str(data.get("site") or "") or _host(str(data.get("url") or "")) or "Link"
            detail = detail or str(data.get("url") or "")
        elif card["type"] == "checklist":
            its = data.get("items") or []
            sub = f"{sum(1 for i in its if i.get('done'))} of {len(its)} done" if its else "Checklist"
            detail = "\n".join(("✓ " if i.get("done") else "• ") + str(i.get("text") or "") for i in its)
        if not badge and card["pin_color"] == "red":
            badge = "Important"
        items.append({"id": card["id"], "title": title, "sub": sub, "badge": badge, "detail": detail[:1200]})
    due_now = next((i for i in items if i["badge"] == "Due"), None)
    lead = due_now or (items[0] if items else None)
    card = {
        "headline": lead["title"] if lead else "No notices",
        "sub": f"{total} on the board" if total else "",
        "badge": "Due" if due_now else "",
    }
    return {"card": card, "items": items, "refresh_s": 60}


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
