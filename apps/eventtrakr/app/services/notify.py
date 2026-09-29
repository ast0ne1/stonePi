"""Emit EventTrakr personal alerts via StonePi Notify.

Every event is personal: ``user`` is the owner's Auth user id (favourite's
``events.user_id`` or the social account's ``user_id`` -> ``users.auth_user_id``).
Owners without a platform account (standalone mode) are never sent.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, User
from app.services import settings as settings_service

logger = logging.getLogger("eventtrakr.notify")

DEFAULT_LEAD_MINUTES = 30
MIN_LEAD_MINUTES = 5
MAX_LEAD_MINUTES = 24 * 60
SENT_KEY = "notify_approaching_sent"


def approaching_lead_minutes(db: Session) -> int:
    raw = settings_service.get_value(db, "notify_approaching_minutes", str(DEFAULT_LEAD_MINUTES))
    try:
        value = int(str(raw or DEFAULT_LEAD_MINUTES).strip())
    except (TypeError, ValueError):
        value = DEFAULT_LEAD_MINUTES
    return max(MIN_LEAD_MINUTES, min(MAX_LEAD_MINUTES, value))


def _load_sent(db: Session) -> dict[str, str]:
    raw = settings_service.get_value(db, SENT_KEY, "{}")
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _save_sent(db: Session, sent: dict[str, str]) -> None:
    settings_service.set_value(db, SENT_KEY, json.dumps(sent))


def owner_auth_id(db: Session, local_user_id: int | None) -> str | None:
    """Auth user id for a local EventTrakr user id, or None (standalone / unlinked)."""
    if not local_user_id:
        return None
    from stonepi_auth.alerts import auth_user_id

    row = db.get(User, local_user_id)
    return auth_user_id(row.auth_user_id) if row is not None else None


def emit_event_approaching(
    *,
    user: str | None,
    title: str,
    summary: str,
    event_key: str,
    url: str | None = None,
    start_time: str | None = None,
) -> bool:
    from stonepi_auth.alerts import auth_user_id

    owner = auth_user_id(user)
    if owner is None:
        logger.debug("approaching event %s has no platform owner; not sent", event_key)
        return False
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        return emit_event(
            EventEnvelope(
                id="eventtrakr.event_approaching",
                source="eventtrakr",
                title=title[:200],
                summary=summary[:500],
                severity="warning",
                audience="personal",
                user=owner,
                dedupe_key=f"eventtrakr:approaching:{event_key}:{start_time or ''}",
                url=url,
                data={"start_time": start_time, "event_key": event_key},
            )
        )
    except Exception:
        logger.debug("emit_event_approaching failed", exc_info=True)
        return False


def _emit_social(
    *,
    user: str | None,
    event_id: str,
    title: str,
    summary: str,
    dedupe_key: str,
    severity: str = "info",
    url: str | None = None,
    data: dict | None = None,
) -> bool:
    from stonepi_auth.alerts import auth_user_id

    owner = auth_user_id(user)
    if owner is None:
        logger.debug("%s has no platform owner; not sent", event_id)
        return False
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        return emit_event(
            EventEnvelope(
                id=event_id,
                source="eventtrakr",
                title=title[:200],
                summary=summary[:500],
                severity=severity,
                audience="personal",
                user=owner,
                dedupe_key=dedupe_key,
                url=url,
                data=data or {},
            )
        )
    except Exception:
        logger.debug("social notify failed for %s", event_id, exc_info=True)
        return False


def emit_social_discovered(
    *,
    user: str | None,
    title: str,
    summary: str,
    discovery_key: str,
    url: str | None = None,
    auto_created: bool = False,
) -> bool:
    return _emit_social(
        user=user,
        event_id="eventtrakr.social_discovered",
        title=title,
        summary=summary,
        dedupe_key=f"eventtrakr:social_discovered:{discovery_key}",
        severity="info",
        url=url,
        data={"auto_created": auto_created},
    )


def emit_social_event_updated(
    *,
    user: str | None,
    title: str,
    summary: str,
    update_key: str,
    url: str | None = None,
) -> bool:
    return _emit_social(
        user=user,
        event_id="eventtrakr.social_event_updated",
        title=title,
        summary=summary,
        dedupe_key=f"eventtrakr:social_updated:{update_key}",
        severity="warning",
        url=url,
    )


def emit_social_cancelled(
    *,
    user: str | None,
    title: str,
    summary: str,
    update_key: str,
    url: str | None = None,
) -> bool:
    return _emit_social(
        user=user,
        event_id="eventtrakr.social_cancelled",
        title=title,
        summary=summary,
        dedupe_key=f"eventtrakr:social_cancelled:{update_key}",
        severity="critical",
        url=url,
    )


def notify_from_process_result(
    db: Session,
    result: dict,
    *,
    username: str = "",
    account_user_id: int | None = None,
) -> bool:
    """Gate social notifications on Settings → Discovery toggle.

    ``account_user_id`` is the social account's local owner; the alert goes to
    that person's Auth user id (nothing is sent when they have none).
    """
    if not result or not result.get("notify"):
        return False
    from app.services.social import config as social_config

    if not social_config.notify_enabled(db):
        return False
    owner = owner_auth_id(db, account_user_id)
    if owner is None:
        logger.debug("social notify for @%s has no platform owner; not sent", username)
        return False
    kind = result.get("notify_kind") or ""
    title = (result.get("title") or "EventTrakr").strip()
    handle = f"@{username}" if username else "Instagram"
    if kind == "discovered":
        summary = f"New event discovered from {handle}: {title}"
        return emit_social_discovered(
            user=owner,
            title=title,
            summary=summary,
            discovery_key=str(result.get("event_id") or result.get("discovery_id") or ""),
            url=None,
            auto_created=True,
        )
    if kind == "candidate":
        summary = f"Possible event from {handle} needs review: {title}"
        return emit_social_discovered(
            user=owner,
            title=title,
            summary=summary,
            discovery_key=f"candidate:{result.get('discovery_id')}",
            auto_created=False,
        )
    if kind == "cancelled":
        return emit_social_cancelled(
            user=owner,
            title=title,
            summary=(result.get("summary") or f"Cancellation noted from {handle}")[:500],
            update_key=str(result.get("event_id") or ""),
        )
    if kind == "updated":
        return emit_social_event_updated(
            user=owner,
            title=title,
            summary=(result.get("summary") or f"Event update from {handle}")[:500],
            update_key=f"{result.get('event_id')}:{result.get('update_kind')}",
        )
    return False


def check_approaching_favourites(db: Session, *, now: datetime | None = None) -> dict:
    """Notify for favourited events starting within the lead window. Returns counts."""
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    lead = approaching_lead_minutes(db)
    horizon = now_utc + timedelta(minutes=lead)
    rows = list(
        db.execute(
            select(Event).where(
                Event.is_favourited == True,  # noqa: E712
                Event.is_cancelled == False,  # noqa: E712
                Event.start_time >= now_utc,
                Event.start_time <= horizon,
            )
        ).scalars()
    )
    sent = _load_sent(db)
    # Drop entries for events that have already started (or vanished).
    active_keys = {str(e.id) for e in rows}
    pruned = {k: v for k, v in sent.items() if k in active_keys}
    notified = 0
    skipped = 0
    owners: dict[int, str | None] = {}
    for event in rows:
        start = event.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        else:
            start = start.astimezone(timezone.utc)
        start_iso = start.isoformat()
        key = str(event.id)
        if pruned.get(key) == start_iso:
            skipped += 1
            continue
        minutes = max(0, int((start - now_utc).total_seconds() // 60))
        where = (event.location or "").strip()
        summary = f"Starts in {minutes} min"
        if where and where.lower() != "unspecified":
            summary = f"{summary} · {where}"
        if event.user_id not in owners:
            owners[event.user_id] = owner_auth_id(db, event.user_id)
        ok = emit_event_approaching(
            user=owners[event.user_id],
            title=event.title,
            summary=summary,
            event_key=key,
            url=(event.url or "").strip() or None,
            start_time=start_iso,
        )
        if ok:
            pruned[key] = start_iso
            notified += 1
    if pruned != sent:
        _save_sent(db, pruned)
    return {
        "checked": len(rows),
        "notified": notified,
        "skipped": skipped,
        "lead_minutes": lead,
    }
