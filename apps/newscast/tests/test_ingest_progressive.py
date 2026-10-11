"""B3: a refresh saves each source's stories as it goes, so the Briefing can show them early."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services import ingest
from app.services.ingest import run_ingest, snapshot


@pytest.fixture
def engine(tmp_path):
    # A file database: a second connection only sees what the refresh has committed.
    eng = create_engine(f"sqlite:///{tmp_path / 'news.sqlite'}", future=True)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("app.services.briefing.maybe_publish_daily_briefing", lambda _db: None)
    yield
    ingest.state.running = False
    ingest.state.manual = False
    ingest.state.stop_requested = False


def _feeds(db: Session, names: list[str]) -> list[Feed]:
    feeds = [
        Feed(name=name, url=f"https://example.com/{i}.xml", enabled=True, type="rss", summarize=False)
        for i, name in enumerate(names, start=1)
    ]
    db.add_all(feeds)
    db.commit()
    return feeds


def _item(feed: Feed, title: str, excerpt: str = "A short excerpt for the test.") -> dict:
    return {
        "title": title,
        "url": f"https://example.com/{feed.id}/{abs(hash(title))}",
        "excerpt": excerpt,
        "published_at": datetime.now(timezone.utc) - timedelta(hours=1),
        "source": feed.name,
    }


def test_each_source_is_saved_before_the_next_is_fetched(engine, monkeypatch):
    db = Session(engine)
    _feeds(db, ["First", "Second"])
    seen_mid_refresh: dict[str, int] = {}

    def fake_collect(feed):
        if feed.name == "Second":
            # While the second source downloads, the first one's story is already in the
            # database for other readers (the Briefing page), and the live count says so.
            with Session(engine) as other:
                seen_mid_refresh["stories"] = other.query(Story).count()
            seen_mid_refresh["new_so_far"] = snapshot()["new_so_far"]
            return [_item(feed, "Chip maker unveils low power laptop processor")]
        return [_item(feed, "Harbour ferry strike ends after overnight talks")]

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    result = run_ingest(db, force=True)

    assert result["ok"] is True and result["created"] == 2
    assert seen_mid_refresh == {"stories": 1, "new_so_far": 1}
    notes = {feed.name: feed.last_ingest_note for feed in db.query(Feed).all()}
    assert notes == {"First": "1 new", "Second": "1 new"}
    assert snapshot()["new_so_far"] == 0  # idle again


def test_first_source_keeps_a_story_another_source_also_carries(engine, monkeypatch):
    db = Session(engine)
    _feeds(db, ["First", "Second"])
    title = "Museum reopens medieval gallery after restoration"

    def fake_collect(feed):
        if feed.name == "First":
            return [_item(feed, title, "Short.")]
        # Same story with a longer excerpt: it used to replace the first one; now the
        # first source to bring it keeps it, since that one is already saved and shown.
        return [_item(feed, title + " this week", "A much longer excerpt about the same reopening. " * 5)]

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    result = run_ingest(db, force=True)

    stories = db.query(Story).all()
    assert result["created"] == 1 and len(stories) == 1
    assert stories[0].source_name == "First"
    second = next(s for s in result["feed_stats"] if s["name"] == "Second")
    assert second["new"] == 0 and second["reason"] == "already in library"
