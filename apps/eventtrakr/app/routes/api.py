from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Event, User
from app.services import auth, calendar_sync, capabilities, ingest

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
    payload = {
        "ok": True,
        "next": next_label,
        "favourites": favourites_count,
        "events": upcoming,
    }
    limit = request.args.get("items", default=0, type=int) or 0
    if limit > 0:  # Car Thing panel lists; the plain call (Dashboard tile, TRMNL) is unchanged
        payload.update(_panel_feed(now, limit))
    return jsonify(payload)


def _plain(text: str, limit: int) -> str:
    import html
    import re

    clean = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    clean = re.sub(r"[ \t]+", " ", clean)
    return "\n".join(line.strip() for line in clean.splitlines() if line.strip())[:limit]


def _panel_feed(now: datetime, limit: int) -> dict:
    """Upcoming favourites (the admin's, as on the household display) for ``?items=N``."""
    items: list[dict] = []
    with SessionLocal() as db:
        admin = db.execute(select(User).where(User.role == "admin").order_by(User.id.asc())).scalars().first()
        events = []
        if admin is not None:
            events = (
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
        for event in events:
            start = event.start_time
            if start is None:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            end = event.end_time
            if end is not None and end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if start < now and (end is None or end < now):
                continue
            local = start.astimezone()
            when = local.strftime("%a %d %b · %H:%M").replace(" 0", " ")
            where = (event.location or "").strip()
            where = "" if where.lower() == "unspecified" else where
            happening = start <= now
            details = [when, where]
            if event.cost and event.cost.strip().lower() != "unspecified":
                details.append(f"Cost: {event.cost.strip()}")
            details.append(_plain(event.description, 900))
            items.append(
                {
                    "id": str(event.id),
                    "title": (event.title or "Event").strip()[:90],
                    "sub": " · ".join(p for p in (when, where) if p),
                    "badge": "Now" if happening else "",
                    "detail": "\n".join(p for p in details if p),
                }
            )
            if len(items) >= min(50, limit):
                break
    lead = items[0] if items else None
    card = {
        "headline": lead["title"] if lead else "No upcoming favourites",
        "sub": lead["sub"] if lead else "",
        "badge": lead["badge"] if lead else "",
    }
    return {"card": card, "items": items, "refresh_s": 300}


@bp.route("/sync/status")
def sync_status():
    return jsonify({
        "running": ingest.state.running,
        "progress": ingest.state.progress,
        "message": ingest.state.last_message,
        "last_new_events": ingest.state.last_new_events,
        "last_error": ingest.state.last_error,
        "stopping": ingest.state.stopping,
    })


@bp.route("/sync/trigger", methods=["POST"])
@auth.login_required
def trigger_sync():
    user = auth.get_current_user()
    started = ingest.trigger_sync_background(user_id=user.user_id)
    return jsonify({"started": started, "running": ingest.state.running})


@bp.route("/sync/stop", methods=["POST"])
@auth.login_required
def stop_sync():
    return jsonify({"stopping": ingest.request_stop(), "running": ingest.state.running})


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
        if event.is_favourited and user.can(capabilities.SYNC_CALENDAR):
            success, msg = calendar_sync.forward_event_to_google(db, user.user_id, event)
            cal_msg = msg

        db.commit()
        favourited = bool(event.is_favourited)
        return jsonify({
            "favourited": favourited,
            "calendar_synced": bool(event.calendar_synced),
            "message": cal_msg,
        })


@bp.route("/social/accounts")
@auth.login_required
def social_accounts_list():
    from app.models import SocialAccount

    user = auth.get_current_user()
    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(SocialAccount).where(SocialAccount.user_id == user.user_id).order_by(SocialAccount.username)
            ).scalars()
        )
        return jsonify(
            {
                "accounts": [
                    {
                        "id": a.id,
                        "platform": a.platform,
                        "username": a.username,
                        "display_name": a.display_name,
                        "account_type": a.account_type,
                        "tracking_enabled": a.tracking_enabled,
                        "last_checked": a.last_checked.isoformat() if a.last_checked else None,
                        "last_successful_check": a.last_successful_check.isoformat() if a.last_successful_check else None,
                        "last_error": a.last_error,
                        "posts_processed": a.posts_processed,
                        "events_matched": a.events_matched,
                        "events_discovered": a.events_discovered,
                    }
                    for a in rows
                ]
            }
        )


@bp.route("/social/accounts/<int:account_id>/check", methods=["POST"])
@auth.login_required
@auth.capability_required(capabilities.USE_SOCIAL, redirect_endpoint="sources.social_accounts")
def social_account_check(account_id: int):
    from app.models import SocialAccount
    from app.services.social import poll as social_poll

    user = auth.get_current_user()
    with SessionLocal() as db:
        account = db.get(SocialAccount, account_id)
        if not account or account.user_id != user.user_id:
            return jsonify({"error": "Not found"}), 404
        result = social_poll.poll_account(db, account, force=True)
        return jsonify(result)


@bp.route("/discoveries")
@auth.login_required
def discoveries_list():
    from app.models import EventDiscovery

    user = auth.get_current_user()
    status = (request.args.get("status") or "open").strip()
    with SessionLocal() as db:
        query = select(EventDiscovery).where(EventDiscovery.user_id == user.user_id)
        if status == "open":
            query = query.where(EventDiscovery.status.in_(("candidate", "discovered")))
        elif status != "all":
            query = query.where(EventDiscovery.status == status)
        rows = list(db.execute(query.order_by(EventDiscovery.created_at.desc()).limit(100)).scalars())
        return jsonify(
            {
                "discoveries": [
                    {
                        "id": d.id,
                        "title": d.title,
                        "start_time": d.start_time.isoformat() if d.start_time else None,
                        "location": d.location,
                        "confidence": d.confidence,
                        "status": d.status,
                        "event_id": d.event_id,
                    }
                    for d in rows
                ]
            }
        )


@bp.route("/discoveries/<int:discovery_id>/confirm", methods=["POST"])
@auth.login_required
def discoveries_confirm(discovery_id: int):
    from app.services.social import discover as discover_service

    user = auth.get_current_user()
    with SessionLocal() as db:
        event = discover_service.confirm_discovery(db, discovery_id, user.user_id)
        if not event:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True, "event_id": event.id, "title": event.title})


@bp.route("/discoveries/<int:discovery_id>/ignore", methods=["POST"])
@auth.login_required
def discoveries_ignore(discovery_id: int):
    from app.services.social import discover as discover_service

    user = auth.get_current_user()
    with SessionLocal() as db:
        ok = discover_service.ignore_discovery(db, discovery_id, user.user_id)
        if not ok:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})


@bp.route("/events/<int:event_id>/social")
@auth.login_required
def event_social(event_id: int):
    from app.models import EventSocialLink, SocialAccount, SocialPost

    user = auth.get_current_user()
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if not event or event.user_id != user.user_id:
            return jsonify({"error": "Not found"}), 404
        links = list(db.execute(select(EventSocialLink).where(EventSocialLink.event_id == event_id)).scalars())
        out = []
        for link in links:
            post = db.get(SocialPost, link.social_post_id)
            account = db.get(SocialAccount, post.social_account_id) if post else None
            out.append(
                {
                    "link_kind": link.link_kind,
                    "post_url": post.post_url if post else "",
                    "published_at": post.published_at.isoformat() if post and post.published_at else None,
                    "username": account.username if account else "",
                    "platform": account.platform if account else "instagram",
                }
            )
        return jsonify({"origin": event.origin, "social": out})


@bp.route("/events/<int:event_id>/updates")
@auth.login_required
def event_updates(event_id: int):
    from app.models import EventUpdate

    user = auth.get_current_user()
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if not event or event.user_id != user.user_id:
            return jsonify({"error": "Not found"}), 404
        rows = list(
            db.execute(
                select(EventUpdate).where(EventUpdate.event_id == event_id).order_by(EventUpdate.created_at.desc())
            ).scalars()
        )
        return jsonify(
            {
                "updates": [
                    {
                        "id": u.id,
                        "update_kind": u.update_kind,
                        "summary": u.summary,
                        "priority": u.priority,
                        "created_at": u.created_at.isoformat() if u.created_at else None,
                    }
                    for u in rows
                ]
            }
        )
