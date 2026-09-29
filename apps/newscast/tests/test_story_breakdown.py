"""Device → Status "Stories stored" breakdown: every stored story lands in exactly one bucket."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services import settings
from app.services.briefing import story_breakdown

NOW = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _story(key: str, title: str, days_ago: float, importance: int = 4, **kw) -> Story:
    when = NOW - timedelta(days=days_ago)
    return Story(
        title=title,
        summary=f"{title}.",
        source_name="BBC",
        canonical_url=f"https://example.com/{key}",
        content_hash=key,
        cluster_key=key,
        published_at=when,
        created_at=when,
        importance=importance,
        **kw,
    )


def test_breakdown_buckets_add_up(monkeypatch):
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: NOW)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 29))
    monkeypatch.setattr("app.services.briefing._local_tz", lambda: timezone.utc)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    settings.set_value(db, "keyword_exclude", "celebrity")
    db.add_all(
        [
            _story("a", "Harbour ferry strike ends after talks", 0),
            _story("b", "Chip maker unveils laptop processor", 1),
            _story("c", "Old story starred last month", 20, favourited=True),
            _story("d", "Long read on coral reefs", 3, saved=True),
            _story("e", "Minor council notice about parking", 2, importance=2),
            _story("f", "Minor tweak to bus timetable", 4, importance=1),
            _story("g", "Celebrity chef opens bistro", 1),
            _story("h", "Week old weather roundup", 9),
        ]
    )
    db.commit()

    counts = story_breakdown(db)

    assert counts == {
        "total": 8,
        "on_briefing": 3,  # a, b and the starred c
        "starred": 1,
        "saved": 1,
        "keyword_filtered": 1,
        "below_importance": 2,
        "awaiting_cleanup": 1,
    }
    parts = ("on_briefing", "saved", "keyword_filtered", "below_importance", "awaiting_cleanup")
    assert sum(counts[key] for key in parts) == counts["total"]
