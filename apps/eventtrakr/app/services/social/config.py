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
