"""The map view (F18, ideation/eventtrakr-map.md): events on a map, a week at a time."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import Event, EventSighting, EventSource, Place
from app.services import auth, places, timespan
from app.services.category_style import category_style
from app.services.cost import cost_badge_class, cost_pill_text, cost_pill_title, is_free_cost

bp = Blueprint("map", __name__)

# How far ahead the week arrows go (open question 1 in the spec).
MAX_WEEKS_AHEAD = 8


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_start(raw: str | None, today: date) -> date:
    this_week = _monday(today)
    try:
        asked = _monday(date.fromisoformat(raw or ""))
    except ValueError:
        return this_week
    return min(max(asked, this_week), this_week + timedelta(weeks=MAX_WEEKS_AHEAD))


def _week_label(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    first = f"{monday.day} {monday:%b}"
    return f"Wk {monday.isocalendar().week} · {first} – {sunday.day} {sunday:%b}"


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


@bp.route("/")
@bp.route("/map")
@auth.login_required
def map_page():
    """The home page: EventTrakr opens on the map (the Agenda is a tab away)."""
    # Venues seen since the last sync get looked up now, in the background.
    places.look_up_pending_background()
    return render_template("map.html", view_title="Map")


@bp.route("/api/map/week")
@auth.login_required
def map_week():
    """One calendar week's events with their coordinates (spec §4): the page
    filters by view and day and draws the bars itself, so only the arrows fetch."""
    user = auth.get_current_user()
    now = datetime.now(timezone.utc)
    today = now.date()
    monday = _week_start(request.args.get("start"), today)
    week_start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=7)

    with SessionLocal() as db:
        events = list(
            db.execute(
                select(Event)
                .where(
                    Event.user_id == user.user_id,
                    Event.is_cancelled == False,  # noqa: E712
                    timespan.overlaps(week_start, week_end),
                    timespan.still_on(now),
                )
                .order_by(Event.start_time)
            ).scalars()
        )
        keys = {places.place_key(ev.location) for ev in events}
        by_key = {p.key: p for p in db.execute(select(Place).where(Place.key.in_(keys))).scalars()} if keys else {}
        source_names = dict(db.execute(select(EventSource.id, EventSource.name).where(EventSource.user_id == user.user_id)).all())
        also = {}
        if events:
            for event_id, source_id in db.execute(
                select(EventSighting.event_id, EventSighting.source_id).where(
                    EventSighting.event_id.in_([e.id for e in events]), EventSighting.dropped_at.is_(None)
                )
            ).all():
                also.setdefault(event_id, []).append(source_id)
        pending = places.pending_count(db)

    out = []
    for ev in events:
        place = by_key.get(places.place_key(ev.location))
        located = place is not None and place.lat is not None and place.status in ("found", "manual")
        style = category_style(ev.category)
        out.append({
            "id": ev.id,
            "title": ev.title,
            "start": _iso(ev.start_time),
            "end": _iso(ev.end_time),
            "is_run": timespan.is_run(ev),
            "category": ev.category or "General",
            "icon": style["icon"],
            "tint": style["tint"],
            "location": ev.location,
            "free": is_free_cost(ev.cost),
            "cost_text": cost_pill_text(ev.cost),
            "cost_title": cost_pill_title(ev.cost),
            "cost_class": cost_badge_class(ev.cost),
            "url": ev.url,
            "favourited": bool(ev.is_favourited),
            "source": source_names.get(ev.source_id, ""),
            "also_on": [source_names[s] for s in also.get(ev.id, []) if s != ev.source_id and s in source_names],
            "place_id": place.id if place else None,
            "lat": place.lat if located else None,
            "lon": place.lon if located else None,
        })

    synced_until = (now + timedelta(days=env.max_lookahead_days)).date()
    return jsonify({
        "week": {
            "start": monday.isoformat(),
            "label": _week_label(monday),
            "number": monday.isocalendar().week,
            "is_current": monday == _monday(today),
            "prev": (monday - timedelta(weeks=1)).isoformat() if monday > _monday(today) else None,
            "next": (monday + timedelta(weeks=1)).isoformat()
            if monday < _monday(today) + timedelta(weeks=MAX_WEEKS_AHEAD)
            else None,
        },
        "today": today.isoformat(),
        "synced_until": synced_until.isoformat(),
        "searching": _searching(user.user_id, monday),
        # The page polls while lookups run; places still waiting after a run
        # get another one at most every few minutes (places.nudge).
        "locating": places.nudge(pending) or places.is_running(),
        "events": out,
    })


def _search_key_date(monday: date) -> date:
    return monday + timedelta(days=6)


def _searching(user_id: int, monday: date) -> bool:
    from app.routes import ui

    key = (user_id, _search_key_date(monday).isoformat(), "all")
    with ui._deep_search_lock:
        return key in ui._deep_search_inflight


@bp.route("/api/map/look-further", methods=["POST"])
@auth.login_required
def map_look_further():
    """ "Look for more in wk 46": a deep search out to the end of that week, only when asked."""
    from app.routes import ui

    user = auth.get_current_user()
    today = datetime.now(timezone.utc).date()
    monday = _week_start((request.get_json(silent=True) or {}).get("start"), today)
    started = ui._start_deep_search_background(user.user_id, _search_key_date(monday), "all")
    return jsonify({"started": started, "searching": True})


@bp.route("/api/map/search")
@auth.login_required
def map_search():
    try:
        return jsonify({"results": places.search_live(request.args.get("q", ""))})
    except Exception:  # noqa: BLE001 -- shown as "couldn't search" on the page
        return jsonify({"results": [], "error": "Place search isn't available right now"}), 502


@bp.route("/api/map/places/<int:place_id>", methods=["POST"])
@auth.login_required
def map_fix_place(place_id: int):
    """ "Wrong place?": a pin dragged to where the venue really is. Saved on the
    place, so every event there moves, and never looked up again."""
    data = request.get_json(silent=True) or {}
    try:
        lat, lon = float(data["lat"]), float(data["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Missing position"}), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify({"error": "Position out of range"}), 400
    with SessionLocal() as db:
        place = db.get(Place, place_id)
        if place is None:
            return jsonify({"error": "Place not found"}), 404
        place.lat, place.lon, place.status = lat, lon, "manual"
        db.commit()
    return jsonify({"saved": True})
