from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services.briefing import search_stories


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_search_hits_title_and_starred(monkeypatch):
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()
    db.add_all(
        [
            Story(
                title="Climate talks reopen",
                summary="Diplomats meet",
                source_name="BBC World",
                canonical_url="https://example.com/climate",
                content_hash="1",
                cluster_key="1",
                raw_excerpt="climate",
                published_at=now,
                created_at=now,
            ),
            Story(
                title="Old favourite",
                summary="Kept",
                source_name="Guardian",
                canonical_url="https://example.com/old",
                content_hash="2",
                cluster_key="2",
                raw_excerpt="",
                favourited=True,
                published_at=now - timedelta(days=30),
                created_at=now - timedelta(days=30),
            ),
            Story(
                title="Unrelated",
                summary="Sport",
                source_name="BBC Sport",
                canonical_url="https://example.com/sport",
                content_hash="3",
                cluster_key="3",
                published_at=now,
                created_at=now,
            ),
        ]
    )
    db.commit()
    titles = [story.title for story in search_stories(db, "climate")]
    assert titles == ["Climate talks reopen"]
    starred = [story.title for story in search_stories(db, "favourite")]
    assert starred == ["Old favourite"]
    assert search_stories(db, "") == []


def _outlet_world(monkeypatch):
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: now)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)
    db = _session()

    def story(key, title, source, days_ago=0, **kw):
        when = now - timedelta(days=days_ago)
        return Story(
            title=title, summary=title, source_name=source, canonical_url=f"https://example.com/{key}",
            content_hash=key, cluster_key=key, published_at=when, created_at=when, **kw,
        )

    db.add_all(
        [
            story("a", "Climate talks reopen", "BBC World"),
            story("b", "Election night live", "BBC World", 1),
            story("c", "Climate protest in Oslo", "NRK"),
            story("d", "Week-old BBC piece", "BBC World", 20),  # outside retention, not kept
            story("e", "Starred Guardian essay", "Guardian", 30, favourited=True),
        ]
    )
    db.commit()
    return db


def test_search_by_outlet_alone_and_with_keyword(monkeypatch):
    from app.services.briefing import search_stories

    db = _outlet_world(monkeypatch)
    assert [s.title for s in search_stories(db, "", source="BBC World")] == [
        "Climate talks reopen",
        "Election night live",
    ]
    assert [s.title for s in search_stories(db, "climate", source="BBC World")] == ["Climate talks reopen"]
    assert {s.title for s in search_stories(db, "climate")} == {"Climate talks reopen", "Climate protest in Oslo"}
    assert search_stories(db, "", source="") == []


def test_search_outlets_lists_searchable_sources_with_counts(monkeypatch):
    from app.services.briefing import search_outlets

    db = _outlet_world(monkeypatch)
    assert search_outlets(db) == [("BBC World", 2), ("Guardian", 1), ("NRK", 1)]
