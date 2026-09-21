from __future__ import annotations

from urllib.parse import quote

from sqlalchemy.orm import Session

from app.services import settings, user_settings

PAYWALL_SKIP_BASE = "https://www.paywallskip.com/article?url="
DEFAULT_ARTICLE_LINK_LABEL = "read article"
ARTICLE_LINK_LABEL_MAX = 40


def paywall_skip_enabled(db: Session) -> bool:
    return settings.flag_enabled(db, "paywall_skip_enabled")


def normalize_article_link_label(raw: str | None) -> str:
    text = " ".join((raw or "").split()).strip()
    if not text:
        return DEFAULT_ARTICLE_LINK_LABEL
    return text[:ARTICLE_LINK_LABEL_MAX]


def article_link_label(db: Session, user_id: int | None) -> str:
    if user_id is None:
        return DEFAULT_ARTICLE_LINK_LABEL
    return normalize_article_link_label(user_settings.get_value(db, int(user_id), "article_link_label"))


def article_open_url(canonical_url: str, *, use_paywall_skip: bool) -> str:
    url = (canonical_url or "").strip()
    if not url:
        return ""
    if use_paywall_skip:
        return f"{PAYWALL_SKIP_BASE}{quote(url, safe='')}"
    return url


def feed_uses_paywall_skip(db: Session, feed) -> bool:
    if feed is None or not paywall_skip_enabled(db):
        return False
    return bool(getattr(feed, "paywall_skip", False))


def story_open_url(db: Session, story, feeds_by_name: dict | None = None) -> str:
    url = getattr(story, "canonical_url", "") or ""
    use_skip = False
    if paywall_skip_enabled(db):
        name = getattr(story, "source_name", "") or ""
        feed = (feeds_by_name or {}).get(name)
        use_skip = bool(feed and getattr(feed, "paywall_skip", False))
    return article_open_url(url, use_paywall_skip=use_skip)
