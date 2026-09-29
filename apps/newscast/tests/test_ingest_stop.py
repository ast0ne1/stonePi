"""Stop button: only a manual global refresh can be stopped; work in progress is kept or rolled back."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services import ingest
from app.services.ingest import request_stop, run_ingest, snapshot


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _feeds(db: Session, count: int = 3) -> list[Feed]:
    feeds = [
        Feed(name=f"Feed {i}", url=f"https://example.com/{i}.xml", enabled=True, type="rss", summarize=False)
        for i in range(1, count + 1)
    ]
    db.add_all(feeds)
    db.commit()
    return feeds


HEADLINES = {
    1: "Harbour ferry strike ends after overnight talks",
    2: "Chip maker unveils low power laptop processor",
    3: "Museum reopens medieval gallery after restoration",
}


def _item(feed: Feed, n: int = 1) -> dict:
    return {
        # Distinct headlines, or the refresh's duplicate check merges them into one story.
        "title": HEADLINES[feed.id],
        "url": f"https://example.com/{feed.id}/{n}",
        "excerpt": "A short excerpt for the test.",
        "published_at": datetime(2026, 9, 29, tzinfo=timezone.utc),
        "source": feed.name,
    }


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("app.services.briefing.maybe_publish_daily_briefing", lambda _db: None)
    yield
    ingest.state.running = False
    ingest.state.manual = False
    ingest.state.stop_requested = False


def test_stop_needs_a_manual_global_refresh():
    assert request_stop()["ok"] is False  # idle
    ingest.state.running = True
    ingest.state.manual = False  # scheduled or single-source run
    assert request_stop()["ok"] is False
    assert snapshot()["stoppable"] is False
    ingest.state.manual = True
    assert snapshot()["stoppable"] is True
    assert request_stop()["ok"] is True
    assert snapshot()["stopping"] is True


def test_stop_while_fetching_saves_nothing(monkeypatch):
    db = _session()
    feeds = _feeds(db)
    fetched: list[str] = []

    def fake_collect(feed):
        fetched.append(feed.name)
        if len(fetched) == 1:
            request_stop()  # user presses Stop while the first source is downloading
        return [_item(feed)]

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    result = run_ingest(db, force=True, manual=True)

    assert result["stopped"] is True and result["created"] == 0
    assert fetched == ["Feed 1"]  # halted before the next source
    assert db.query(Story).count() == 0
    db.expire_all()
    assert all(feed.last_fetched_at is None for feed in db.query(Feed).all())  # rolled back
    assert "No changes were saved" in ingest.state.last_message
    assert snapshot()["running"] is False and snapshot()["stoppable"] is False


def test_stop_while_saving_keeps_finished_stories(monkeypatch):
    db = _session()
    _feeds(db)
    monkeypatch.setattr("app.services.ingest._collect_feed_items", lambda feed: [_item(feed)])
    scored: list[str] = []

    def fake_score(title, *args, **kwargs):
        scored.append(title)
        request_stop()  # pressed after the first story is saved
        return 3

    monkeypatch.setattr("app.services.importance.score_importance", fake_score)
    result = run_ingest(db, force=True, manual=True)

    assert result["stopped"] is True and result["created"] == 1
    assert db.query(Story).count() == 1
    assert "Kept 1 new story" in result["message"]


def test_scheduled_refresh_ignores_stop(monkeypatch):
    db = _session()
    _feeds(db, count=2)

    def fake_collect(feed):
        assert request_stop()["ok"] is False  # not offered for scheduled runs
        return [_item(feed)]

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    result = run_ingest(db, force=True)
    assert result["ok"] is True and "stopped" not in result
    assert db.query(Story).count() == 2
