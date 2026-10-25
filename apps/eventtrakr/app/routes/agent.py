"""Agent reads for AgentLink (Pebble now, MCP later): one signed internal endpoint.

    POST /api/internal/agent   {"tool": "...", "user": "<platform user id>", "params": {...}}
    → {"ok": true, "payload": {...}} | {"ok": false, "error": "...", "message": "..."}

Signed with stonepi_auth.internal v2 (the body, so the user, is covered); nginx never
routes /events/api/internal/. Answers only for the platform user named, through their own
EventTrakr account, the same rows they'd see on their Agenda. Read-only, compact, shaped
for speech: the next few events, nothing else.

``get_events`` takes ``when`` (today, tonight, tomorrow, this_weekend, this_week, next_week; in
the Pi's local time) or ``days``, and an optional ``category`` ("community", "music") matched
loosely against the user's event categories. It always lists the window's categories with
counts, so Pebble can offer to narrow a long evening down.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Event, EventSource, User
from app.services import auth

bp = Blueprint("agent", __name__, url_prefix="/api/internal")

MAX_ITEMS = 10
MAX_DAYS = 31


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event_row(event: Event, source_names: dict[int, str]) -> dict:
    start = _aware(event.start_time).astimezone()  # the Pi's local time, as the displays show it
    return {
        "title": event.title,
        "when": start.strftime("%a %d %b, %H:%M"),
        "start": start.isoformat(timespec="minutes"),
        "location": event.location if event.location and event.location != "Unspecified" else "",
        "category": event.category or "",
        "price": event.cost if event.cost and event.cost != "Unspecified" else "",
        "favourite": bool(event.is_favourited),
        "source": source_names.get(event.source_id or -1, ""),
    }


def _sources(db, user_id: int) -> dict[int, str]:
    rows = db.execute(select(EventSource.id, EventSource.name).where(EventSource.user_id == user_id)).all()
    return {sid: name for sid, name in rows}


WINDOWS = ("today", "tonight", "tomorrow", "this_weekend", "this_week", "next_week")
EVENING_HOUR = 17


def _window(when: str, now: datetime) -> tuple[datetime, datetime, str] | None:
    """(from, to, label) in UTC for a named window, in the Pi's local time; None if unknown."""
    local = now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    recent = now - timedelta(hours=3)  # as Agenda: still on if it started recently
    if when == "today":
        start, end, label = recent, midnight + timedelta(days=1, hours=4), "today"
    elif when == "tonight":
        start = max(recent, midnight.replace(hour=EVENING_HOUR) - timedelta(hours=1))
        end, label = midnight + timedelta(days=1, hours=4), "tonight"
    elif when == "tomorrow":
        start, end, label = midnight + timedelta(days=1), midnight + timedelta(days=2, hours=4), "tomorrow"
    elif when == "this_weekend":
        saturday = midnight + timedelta(days=(5 - local.weekday()) % 7)
        if local.weekday() == 6:
            saturday = midnight - timedelta(days=1)
        start, end, label = max(recent, saturday), saturday + timedelta(days=2, hours=4), "this weekend"
    elif when == "this_week":
        monday = midnight - timedelta(days=local.weekday())
        start, end, label = recent, monday + timedelta(days=7), "this week (to Sunday)"
    elif when == "next_week":
        monday = midnight - timedelta(days=local.weekday()) + timedelta(days=7)
        start, end, label = monday, monday + timedelta(days=7), "next week (Monday to Sunday)"
    else:
        return None
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label


def _norm(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split()
    return " ".join(_singular(w) for w in words)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _match_category(wanted: str, names: list[str]) -> str | None:
    """The event category for "community" / "Music" / "kids", matched loosely; None if none."""
    q = _norm(wanted)
    if not q:
        return None
    for name in names:
        if _norm(name) == q:
            return name
    for name in names:
        n = _norm(name)
        if q in n.split() or n.startswith(q) or (len(q) > 3 and q in n):
            return name
    return None


def _get_events(db, user: User, params: dict) -> dict:
    now = datetime.now(timezone.utc)
    when = str(params.get("when") or "").strip().lower().replace(" ", "_")
    window = _window(when, now) if when else None
    if window:
        start, end, label = window
        out: dict = {"when": label}
    else:
        try:
            days = int(params.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(days, MAX_DAYS))
        start, end = now - timedelta(hours=3), now + timedelta(days=days)
        out = {"days": days}
        if when:
            out["when_not_understood"] = when
    events = list(
        db.execute(
            select(Event)
            .where(
                Event.user_id == user.id,
                Event.is_cancelled == False,  # noqa: E712
                Event.start_time >= start,
                Event.start_time <= end,
            )
            .order_by(Event.start_time)
        ).scalars()
    )
    counts: dict[str, int] = {}
    for event in events:
        name = event.category or "General"
        counts[name] = counts.get(name, 0) + 1
    out["categories"] = [{"name": k, "events": n} for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    wanted = str(params.get("category") or "").strip()[:60]
    if wanted:
        name = _match_category(wanted, list(counts))
        if name is None:
            out["category_not_found"] = wanted
        else:
            out["category"] = name
            events = [e for e in events if (e.category or "General") == name]
    # Favourites first among the soonest, so a long Agenda doesn't bury what they starred.
    picked = sorted(events, key=lambda e: (not e.is_favourited, _aware(e.start_time)))[:MAX_ITEMS]
    picked.sort(key=lambda e: _aware(e.start_time))
    names = _sources(db, user.id)
    out.update(total=len(events), events=[_event_row(e, names) for e in picked])
    return out


def _search_events(db, user: User, params: dict) -> dict:
    from app.routes.ui import run_search, search_tokens

    query = str(params.get("query") or "").strip()[:120]
    if not query:
        return {"query": "", "total": 0, "events": []}
    now = datetime.now(timezone.utc)
    found = run_search(db, user.id, tokens=search_tokens(query), now=now)
    names = _sources(db, user.id)
    return {"query": query, "total": len(found), "events": [_event_row(e, names) for e in found[:MAX_ITEMS]]}


TOOLS = {"get_events": _get_events, "search_events": _search_events}


@bp.route("/agent", methods=["POST"])
def agent_read():
    from stonepi_auth.internal import verify_internal_v2

    body = request.get_data(cache=True) or b""
    if not verify_internal_v2(auth._platform_session_secret(), "POST", request.path, request.headers, body):
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return jsonify({"ok": False, "error": "bad_request", "message": "Body must be JSON"}), 400
    tool = TOOLS.get(str(data.get("tool") or ""))
    user_id = str(data.get("user") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if tool is None or not user_id:
        return jsonify({"ok": False, "error": "bad_request", "message": "Unknown tool or no user"}), 400
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.auth_user_id == user_id, User.active == True)).scalars().first()  # noqa: E712
        if user is None:
            # Never opened EventTrakr: nothing of theirs here yet.
            return jsonify({"ok": True, "payload": {"events": [], "total": 0, "note": "No EventTrakr account yet"}})
        return jsonify({"ok": True, "payload": tool(db, user, params)})
