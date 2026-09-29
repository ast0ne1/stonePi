from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.services import settings, user_settings

logger = logging.getLogger("newscast.ntfy")

USER_NTFY_KEYS = (
    "ntfy_last_publish_notified_day",
    "ntfy_last_push_notified_day",
)


def _today() -> date:
    return datetime.now().astimezone().date()


def _day_key(kind: str) -> str:
    return "ntfy_last_publish_notified_day" if kind == "publish" else "ntfy_last_push_notified_day"


def _already_notified(db: Session, kind: str, day: date, user_id: int | None) -> bool:
    key = _day_key(kind)
    if user_id is not None:
        return user_settings.get_value(db, user_id, key).strip() == day.isoformat()
    return settings.get_value(db, key).strip() == day.isoformat()


def _mark_notified(db: Session, kind: str, day: date, user_id: int | None) -> None:
    key = _day_key(kind)
    if user_id is not None:
        user_settings.set_value(db, user_id, key, day.isoformat())
    else:
        settings.set_value(db, key, day.isoformat())


def _owner_auth_id(db: Session, user_id: int | None) -> str | None:
    """Auth user id of the local NewsCast user, or None (standalone / legacy row)."""
    if user_id is None:
        return None
    from stonepi_auth.alerts import auth_user_id

    from app.models import User

    user = db.get(User, user_id)
    return auth_user_id(getattr(user, "auth_user_id", None)) if user is not None else None


def notify(db: Session, *, kind: str, title: str, body: str, user_id: int | None = None) -> bool:
    """Emit a personal alert via StonePi Notify. Day-deduped; never raises into callers.

    Events go to the owner's Auth user id; without one (standalone, legacy row or a
    single-user publish with no ``user_id``) nothing is sent. Whether the person
    may get phone alerts, and which ones they want, is decided by Notify (the
    Dashboard Notifications page), not here.
    """
    event = (kind or "").strip().lower()
    if event not in {"publish", "push"}:
        return False
    day = _today()
    if _already_notified(db, event, day, user_id):
        return False
    owner = _owner_auth_id(db, user_id)
    if owner is None:
        logger.debug("%s alert for user %s has no platform owner; not sent", event, user_id)
        return False

    event_id = "newscast.publication_available" if event == "publish" else "newscast.push_available"
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        ok = emit_event(
            EventEnvelope(
                id=event_id,
                source="newscast",
                title=(title or "NewsCast").strip()[:120] or "NewsCast",
                summary=(body or "").strip()[:500],
                severity="success",
                audience="personal",
                user=owner,
                dedupe_key=f"newscast:{event}:{owner}:{day.isoformat()}",
                data={"kind": event},
            )
        )
        if ok:
            _mark_notified(db, event, day, user_id)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        logger.debug("emit_event failed: %s", exc)
        return False
