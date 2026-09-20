from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Event, User
from app.services import auth, calendar_sync, ingest

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/display")
def display_status():
    """Compact household stats for StonePi → TRMNL overview push."""
    now = datetime.now(timezone.utc)
    next_label = "None"
    favourites_count = 0
    upcoming: list[dict] = []
    with SessionLocal() as db:
        admin = db.execute(select(User).where(User.role == "admin").order_by(User.id.asc())).scalars().first()
        if admin is not None:
            favourites = (
                db.execute(
                    select(Event)
                    .where(
                        Event.user_id == admin.id,
                        Event.is_favourited == True,  # noqa: E712
                        Event.is_cancelled == False,  # noqa: E712
                    )
                    .order_by(Event.start_time.asc())
                )
                .scalars()
                .all()
            )
            favourites_count = len(favourites)
            chosen = None
            for event in favourites:
                start = event.start_time
                if start is None:
                    continue
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                end = event.end_time
                if end is not None and end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if start >= now or (end is not None and end >= now):
                    if chosen is None:
                        chosen = event
                    title = (event.title or "Event").strip()
                    if len(title) > 48:
                        title = title[:45] + "…"
                    upcoming.append(
                        {
                            "time": start.astimezone().strftime("%H:%M"),
                            "message": title,
                        }
                    )
                    if len(upcoming) >= 3:
                        break
            if chosen is not None:
                start = chosen.start_time
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                when = start.astimezone().strftime("%a %H:%M")
                title = (chosen.title or "Event").strip()
                if len(title) > 42:
                    title = title[:39] + "…"
                next_label = f"{title} · {when}"
    return jsonify(
        {
            "ok": True,
            "next": next_label,
            "favourites": favourites_count,
            "events": upcoming,
        }
    )


@bp.route("/sync/status")
def sync_status():
    return jsonify({
        "running": ingest.state.running,
        "progress": ingest.state.progress,
        "message": ingest.state.last_message,
        "last_new_events": ingest.state.last_new_events,
        "last_error": ingest.state.last_error,
    })


@bp.route("/sync/trigger", methods=["POST"])
@auth.login_required
def trigger_sync():
    user = auth.get_current_user()
    started = ingest.trigger_sync_background(user_id=user.user_id)
    return jsonify({"started": started, "running": ingest.state.running})


@bp.route("/sources/<int:source_id>/sync", methods=["POST"])
@auth.login_required
def trigger_source_sync(source_id: int):
    user = auth.get_current_user()
    started = ingest.trigger_single_source_sync_background(source_id, user.user_id)
    return jsonify({"started": started, "running": ingest.state.running})


@bp.route("/events/<int:event_id>/favourite", methods=["POST"])
@auth.login_required
def toggle_favourite(event_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if not event or event.user_id != user.user_id:
            return jsonify({"error": "Event not found"}), 404

        event.is_favourited = not bool(event.is_favourited)
        cal_msg = ""

        # If favourited and not already synced, try forwarding to Google Calendar
        if event.is_favourited:
            success, msg = calendar_sync.forward_event_to_google(db, user.user_id, event)
            cal_msg = msg

        db.commit()
        favourited = bool(event.is_favourited)
        return jsonify({
            "favourited": favourited,
            "calendar_synced": bool(event.calendar_synced),
            "message": cal_msg,
        })
