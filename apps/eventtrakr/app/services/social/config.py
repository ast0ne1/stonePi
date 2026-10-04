"""Social discovery settings helpers (KV on settings table)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import settings as settings_service

DEFAULT_POLL_MINUTES = 30
MIN_POLL_MINUTES = 15
MAX_POLL_MINUTES = 1440
DEFAULT_NUM_OF_POSTS = 10

ACCOUNT_TYPES = [
    ("organiser", "Event organiser"),
    ("venue", "Venue"),
    ("artist", "Artist / performer"),
    ("festival", "Festival / organisation"),
    ("other", "Other"),
]


def _bool(db: Session, key: str, default: bool) -> bool:
    raw = settings_service.get_value(db, key, "1" if default else "0")
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _set_bool(db: Session, key: str, value: bool) -> None:
    settings_service.set_value(db, key, "1" if value else "0")


def instagram_enabled(db: Session) -> bool:
    return _bool(db, "social_instagram_enabled", False)


def set_instagram_enabled(db: Session, enabled: bool) -> None:
    _set_bool(db, "social_instagram_enabled", enabled)


def poll_minutes(db: Session) -> int:
    raw = settings_service.get_value(db, "social_poll_minutes", str(DEFAULT_POLL_MINUTES))
    try:
        value = int(str(raw or DEFAULT_POLL_MINUTES).strip())
    except (TypeError, ValueError):
        value = DEFAULT_POLL_MINUTES
    return max(MIN_POLL_MINUTES, min(MAX_POLL_MINUTES, value))


def set_poll_minutes(db: Session, minutes: int) -> None:
    value = max(MIN_POLL_MINUTES, min(MAX_POLL_MINUTES, int(minutes)))
    settings_service.set_value(db, "social_poll_minutes", str(value))


def auto_discover(db: Session) -> bool:
    return _bool(db, "social_auto_discover", True)


def set_auto_discover(db: Session, enabled: bool) -> None:
    _set_bool(db, "social_auto_discover", enabled)


def auto_create_high(db: Session) -> bool:
    return _bool(db, "social_auto_create_high", True)


def set_auto_create_high(db: Session, enabled: bool) -> None:
    _set_bool(db, "social_auto_create_high", enabled)


def candidate_review(db: Session) -> bool:
    return _bool(db, "social_candidate_review", True)


def set_candidate_review(db: Session, enabled: bool) -> None:
    _set_bool(db, "social_candidate_review", enabled)


def ocr_enabled(db: Session) -> bool:
    return _bool(db, "social_ocr_enabled", True)


def set_ocr_enabled(db: Session, enabled: bool) -> None:
    _set_bool(db, "social_ocr_enabled", enabled)


def ai_fallback_enabled(db: Session) -> bool:
    # Always treated as false in v1 (stub for settings UI).
    return False


def set_ai_fallback_enabled(db: Session, _enabled: bool) -> None:
    _set_bool(db, "social_ai_fallback", False)


def notify_enabled(db: Session) -> bool:
    return _bool(db, "social_notify_enabled", False)


def set_notify_enabled(db: Session, enabled: bool) -> None:
    _set_bool(db, "social_notify_enabled", enabled)


def posts_per_check(db: Session) -> int:
    raw = settings_service.get_value(db, "social_posts_per_check", str(DEFAULT_NUM_OF_POSTS))
    try:
        value = int(str(raw or DEFAULT_NUM_OF_POSTS).strip())
    except (TypeError, ValueError):
        value = DEFAULT_NUM_OF_POSTS
    return max(1, min(20, value))


def normalize_username(raw: str) -> str:
    return (raw or "").strip().lstrip("@").split("?")[0].strip("/").split("/")[-1].lower()


def profile_url_for(username: str) -> str:
    handle = normalize_username(username)
    return f"https://www.instagram.com/{handle}/" if handle else ""


# -- Bright Data usage: polling hours and a worst-case estimate ------------------------


def _parse_hhmm(raw: str) -> int | None:
    """Minutes after midnight for "HH:MM", or None."""
    try:
        hh, mm = (int(part) for part in str(raw or "").strip().split(":"))
    except ValueError:
        return None
    if not (0 <= hh <= 24 and 0 <= mm < 60) or hh * 60 + mm > 1440:
        return None
    return hh * 60 + mm


def active_hours(db: Session) -> tuple[str, str] | None:
    """Scheduled polls only run between these local times ("HH:MM", "HH:MM"); None = all day."""
    start = settings_service.get_value(db, "social_active_start", "")
    end = settings_service.get_value(db, "social_active_end", "")
    if _parse_hhmm(start) is None or _parse_hhmm(end) is None or start == end:
        return None
    return start, end


def set_active_hours(db: Session, start: str, end: str) -> None:
    """Both blank (or equal) means all day. Raises ValueError for a malformed time."""
    start, end = (start or "").strip(), (end or "").strip()
    if not start and not end:
        start = end = ""
    elif _parse_hhmm(start) is None or _parse_hhmm(end) is None:
        raise ValueError("Use HH:MM for polling hours")
    settings_service.set_value(db, "social_active_start", start)
    settings_service.set_value(db, "social_active_end", end)


def active_minutes_per_day(hours: tuple[str, str] | None) -> int:
    if hours is None:
        return 1440
    start, end = _parse_hhmm(hours[0]), _parse_hhmm(hours[1])
    return (end - start) % 1440 or 1440


def within_active_hours(hours: tuple[str, str] | None, now_local) -> bool:
    """True when ``now_local`` falls inside the window (which may cross midnight)."""
    if hours is None:
        return True
    start, end = _parse_hhmm(hours[0]), _parse_hhmm(hours[1])
    minute = now_local.hour * 60 + now_local.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def checks_per_day(schedule: dict, active_minutes: int) -> float:
    if schedule.get("mode") == "weekly":
        return len(schedule.get("days") or []) * len(schedule.get("times") or []) / 7
    interval = max(MIN_POLL_MINUTES, int(schedule.get("interval_minutes") or DEFAULT_POLL_MINUTES))
    return active_minutes / interval


def estimate_usage(db: Session, period_days: int) -> dict:
    """Worst case Bright Data records Instagram polling could use in a period: every
    scheduled check returning a full "posts per check". Real use is usually far lower,
    since only posts newer than the last check come back."""
    from sqlalchemy import select

    from app.models import SocialAccount

    accounts = list(
        db.execute(
            select(SocialAccount).where(
                SocialAccount.tracking_enabled == True,  # noqa: E712
                SocialAccount.platform == "instagram",
            )
        ).scalars()
    )
    global_minutes = poll_minutes(db)
    schedules = [settings_service.get_source_schedule(a, global_minutes) for a in accounts]
    return estimate_from(
        schedules,
        global_minutes=global_minutes,
        active_minutes=active_minutes_per_day(active_hours(db)),
        posts=posts_per_check(db),
        period_days=period_days,
    )


def estimate_from(
    schedules: list[dict | None], *, global_minutes: int, active_minutes: int, posts: int, period_days: int
) -> dict:
    """``schedules``: one per tracked account, None for one that follows the global interval."""
    global_schedule = {"mode": "interval", "interval_minutes": global_minutes}
    per_day = sum(checks_per_day(s or global_schedule, active_minutes) for s in schedules)
    checks = round(per_day * period_days)
    return {"accounts": len(schedules), "checks": checks, "posts_per_check": posts, "worst_case": checks * posts}
