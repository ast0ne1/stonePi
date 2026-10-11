from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.services import settings, user_settings

logger = logging.getLogger("newscast.ntfy")
EVENT_IDS = {
    "publish": "newscast.publication_available",
    "push": "newscast.push_available",
    "missed": "newscast.push_missed",
}

# One "already sent today" marker per kind, so a "not delivered" alert never
# swallows the "on the reader" one that follows a manual push (or the reverse).
USER_NTFY_KEYS = tuple(f"ntfy_last_{kind}_notified_day" for kind in EVENT_IDS)


def _today() -> date:
    return datetime.now().astimezone().date()


def _day_key(kind: str) -> str:
    return f"ntfy_last_{kind}_notified_day"


def _marker(day: date, version: str) -> str:
    """Stored "already sent" value: the day, plus the paper version when there is one."""
    return f"{day.isoformat()}:{version}" if version else day.isoformat()


def _already_notified(db: Session, kind: str, day: date, user_id: int | None, version: str = "") -> bool:
    key = _day_key(kind)
    stored = user_settings.get_value(db, user_id, key) if user_id is not None else settings.get_value(db, key)
    return stored.strip() == _marker(day, version)


def _mark_notified(db: Session, kind: str, day: date, user_id: int | None, version: str = "") -> None:
    key = _day_key(kind)
    if user_id is not None:
        user_settings.set_value(db, user_id, key, _marker(day, version))
    else:
        settings.set_value(db, key, _marker(day, version))


def _owner_auth_id(db: Session, user_id: int | None) -> str | None:
    """Auth user id of the local NewsCast user, or None (standalone / legacy row)."""
    if user_id is None:
        return None
    from stonepi_auth.alerts import auth_user_id

    from app.models import User

    user = db.get(User, user_id)
    return auth_user_id(getattr(user, "auth_user_id", None)) if user is not None else None


def notify(
    db: Session, *, kind: str, title: str, body: str, user_id: int | None = None, version: str = ""
) -> bool:
    """Emit a personal alert via StonePi Notify. Deduped per day; never raises into callers.

    ``version`` identifies one edition of the paper: the same edition alerts once,
    a regenerated one with different content alerts again the same day.

    Events go to the owner's Auth user id; without one (standalone, legacy row or a
    single-user publish with no ``user_id``) nothing is sent. Whether the person
    may get phone alerts, and which ones they want, is decided by Notify (the
    Dashboard Notifications page), not here.
    """
    event = (kind or "").strip().lower()
    if event not in EVENT_IDS:
        return False
    day = _today()
    version = (version or "").strip()[:64]
    if _already_notified(db, event, day, user_id, version):
        return False
    owner = _owner_auth_id(db, user_id)
    if owner is None:
        logger.debug("%s alert for user %s has no platform owner; not sent", event, user_id)
        return False

    event_id = EVENT_IDS[event]
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
                dedupe_key=f"newscast:{event}:{owner}:{_marker(day, version)}",
                data={"kind": event},
            )
        )
        if ok:
            _mark_notified(db, event, day, user_id, version)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        logger.debug("emit_event failed: %s", exc)
        return False
