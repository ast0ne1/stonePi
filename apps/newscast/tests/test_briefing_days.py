from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services.briefing import current_stories, normalize_briefing_day


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_normalize_briefing_day():
    assert normalize_briefing_day(None) == "today"
    assert normalize_briefing_day("Yesterday") == "yesterday"
    assert normalize_briefing_day("All") == "all"
    assert normalize_briefing_day("2026-09-13") == "2026-09-13"
    assert normalize_briefing_day("nope") == "today"


def test_briefing_path_for_day_filters():
    from app.services.briefing import briefing_path

    assert briefing_path("today") == "/"
    assert briefing_path("yesterday") == "/?day=yesterday"
    assert briefing_path("all") == "/?day=all"


def test_today_vs_yesterday_selection(monkeypatch):
    now = datetime(2026, 9, 13, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 13))
    monkeypatch.setattr("app.services.briefing._local_tz", lambda: timezone.utc)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add_all(
        [
            Story(
                title="Today story",
                summary="Today",
                source_name="BBC",
                canonical_url="https://example.com/today",
                content_hash="t",
                cluster_key="t",
                published_at=now,
                created_at=now,
                importance=3,
            ),
            Story(
                title="Yesterday story",
                summary="Yesterday",
                source_name="BBC",
                canonical_url="https://example.com/yesterday",
                content_hash="y",
                cluster_key="y",
                published_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
                importance=3,
            ),
            Story(
                title="Older story",
                summary="Older",
                source_name="BBC",
                canonical_url="https://example.com/older",
                content_hash="o",
                cluster_key="o",
                published_at=now - timedelta(days=3),
                created_at=now,
                importance=3,
            ),
            Story(
                title="Saved long-read",
                summary="Keep",
                source_name="Saved",
                canonical_url="https://example.com/saved",
                content_hash="s",
                cluster_key="s",
                saved=True,
                published_at=now - timedelta(days=3),
                created_at=now - timedelta(days=3),
                importance=3,
            ),
        ]
    )
    db.commit()
    today = current_stories(db, day="today")
    yesterday = current_stories(db, day="yesterday")
    all_days = current_stories(db, day="all")
    # Saved long-reads follow publish date — last week's saved is All only, not Today.
    assert [story.title for story in today] == ["Today story"]
    assert [story.title for story in yesterday] == ["Yesterday story"]
    assert {story.title for story in all_days} >= {
        "Today story",
        "Yesterday story",
        "Older story",
        "Saved long-read",
    }


def test_today_uses_publish_date_not_ingest_time(monkeypatch):
    now = datetime(2026, 9, 20, 6, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 20))
    monkeypatch.setattr("app.services.briefing._local_tz", lambda: timezone.utc)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add(
        Story(
            title="BBC overnight",
            summary="From yesterday's RSS pubDate",
            source_name="BBC World",
            canonical_url="https://example.com/overnight",
            content_hash="o",
            cluster_key="o",
            published_at=now - timedelta(hours=20),
            created_at=now - timedelta(minutes=5),
            importance=3,
        )
    )
    db.commit()
    assert [story.title for story in current_stories(db, day="today")] == []
    assert [story.title for story in current_stories(db, day="yesterday")] == ["BBC overnight"]
    assert [story.title for story in current_stories(db, day="all")] == ["BBC overnight"]


def test_undated_stories_only_appear_on_all(monkeypatch):
    now = datetime(2026, 9, 20, 6, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 20))
    monkeypatch.setattr("app.services.briefing._local_tz", lambda: timezone.utc)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add(
        Story(
            title="No publish date",
            summary="Undated",
            source_name="Scrape Source",
            canonical_url="https://example.com/undated",
            content_hash="u",
            cluster_key="u",
            published_at=None,
            created_at=now,
            importance=3,
        )
    )
    db.commit()
    assert [story.title for story in current_stories(db, day="today")] == []
    assert [story.title for story in current_stories(db, day="yesterday")] == []
    assert [story.title for story in current_stories(db, day="all")] == ["No publish date"]


def test_saved_long_read_follows_publish_day(monkeypatch):
    now = datetime(2026, 9, 13, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 13))
    monkeypatch.setattr("app.services.briefing._local_tz", lambda: timezone.utc)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add(
        Story(
            title="Saved yesterday",
            summary="Long read",
            source_name="Saved",
            canonical_url="https://example.com/saved-y",
            content_hash="sy",
            cluster_key="sy",
            saved=True,
            published_at=now - timedelta(days=1),
            created_at=now - timedelta(days=1),
            importance=3,
        )
    )
    db.commit()
    assert [story.title for story in current_stories(db, day="today")] == []
    assert [story.title for story in current_stories(db, day="yesterday")] == ["Saved yesterday"]


def test_publication_include_saved_only_affects_payload(monkeypatch):
    from app.services import user_settings
    from app.services.briefing import current_briefing_payload

    now = datetime(2026, 9, 13, 15, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 13))
    monkeypatch.setattr("app.services.briefing._local_tz", lambda: timezone.utc)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add_all(
        [
            Story(
                user_id=1,
                title="Today story",
                summary="Today",
                source_name="BBC",
                canonical_url="https://example.com/today-p",
                content_hash="tp",
                cluster_key="tp",
                published_at=now,
                created_at=now,
                importance=3,
            ),
            Story(
                user_id=1,
                title="Old saved",
                summary="Keep",
                source_name="Saved",
                canonical_url="https://example.com/old-saved",
                content_hash="os",
                cluster_key="os",
                saved=True,
                published_at=now - timedelta(days=4),
                created_at=now - timedelta(days=4),
                importance=3,
            ),
        ]
    )
    db.commit()
    off = current_briefing_payload(db, day="today", user_id=1)
    assert {s["title"] for s in off["stories"]} == {"Today story"}
    user_settings.set_value(db, 1, "publication_include_saved", "1")
    on = current_briefing_payload(db, day="today", user_id=1)
    assert {s["title"] for s in on["stories"]} == {"Today story", "Old saved"}
    # Live chip stays publish-day only.
    assert [story.title for story in current_stories(db, day="today", user_id=1)] == ["Today story"]
