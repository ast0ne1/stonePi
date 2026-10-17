"""U1 per-story AI record during a refresh, and U18b's catch-up of stories that fell back."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story, User
from app.services import ai as ai_mod
from app.services import catchup, ingest
from app.services.ingest import run_ingest
from tests.ai_helpers import platform_ai, use_ai

LONG = (
    "The harbour ferry strike ended after overnight talks between the union and the operator. "
    "Services resume on Monday with a reduced timetable while crews return to work. "
    "Commuters had faced two weeks of disruption across the city's busiest routes. "
    "The union said the agreement includes a pay rise and new rules on rest breaks. "
    "The operator apologised to passengers and promised refunds for unused passes. "
    "Ministers welcomed the deal but warned that the network still needs investment."
)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(User(username="adam", password="x", role="admin"))
    db.commit()
    return db


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("app.services.briefing.maybe_publish_daily_briefing", lambda _db: None)
    yield
    ingest.state.running = False


def _ingest_one(monkeypatch, db, *, summarize=True):
    db.add(Feed(name="Harbour News", url="https://h.example/rss", enabled=True, type="rss", summarize=summarize))
    db.commit()
    monkeypatch.setattr(
        "app.services.ingest._collect_feed_items",
        lambda feed: [{
            "title": "Harbour ferry strike ends after overnight talks",
            "url": "https://h.example/1",
            "excerpt": LONG,
            "published_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "source": feed.name,
        }],
    )
    result = run_ingest(db, force=True)
    assert result["ok"], result
    return result, db.query(Story).one()


def test_story_records_which_model_wrote_it(monkeypatch):
    db = _session()
    platform_ai(monkeypatch, lambda prompt, system: "3" if "integer" in system else "Ferry strike over; services resume Monday.")
    use_ai(db, summaries=True, importance=True)
    _result, story = _ingest_one(monkeypatch, db)
    record = ai_mod.load_record(story)
    assert story.summary == "Ferry strike over; services resume Monday."
    assert record["summary"]["by"] == "ai" and record["summary"]["model"] == "GPT-4.1 mini"
    assert record["importance"]["by"] == "ai" and story.importance == 3


def test_existing_key_alone_never_uses_ai(monkeypatch):
    db = _session()
    sent = platform_ai(monkeypatch, "AI text")  # a key exists, but NewsCast's switch is off
    _result, story = _ingest_one(monkeypatch, db)
    assert sent == []
    assert ai_mod.load_record(story)["summary"] == {"by": "fallback", "reason": "off"}
    assert story.summary != "AI text"


def test_failed_ai_falls_back_and_says_so(monkeypatch):
    db = _session()
    platform_ai(monkeypatch, lambda prompt, system: httpx.Response(503, json={"error": {"message": "down"}}))
    use_ai(db, summaries=True)
    result, story = _ingest_one(monkeypatch, db)
    assert ai_mod.load_record(story)["summary"] == {"by": "fallback", "reason": "unavailable"}
    assert "AI was unavailable for 1 story" in result["message"]


def test_full_article_source_is_opted_out(monkeypatch):
    db = _session()
    platform_ai(monkeypatch, "AI text")
    use_ai(db, summaries=True)
    _result, story = _ingest_one(monkeypatch, db, summarize=False)
    assert ai_mod.load_record(story)["summary"] == {"by": "opted_out"}


# -- catch-up ---------------------------------------------------------------------------


def _story(db, n, *, record, created=None, importance=3, source="Harbour News"):
    story = Story(
        user_id=1,
        title=f"Story {n}",
        summary="On-device summary.",
        source_name=source,
        canonical_url=f"https://h.example/{n}",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=n),
        content_hash=f"h{n}",
        cluster_key=f"k{n}",
        raw_excerpt=LONG,
        importance=importance,
        ai_record=json.dumps(record),
        created_at=created or datetime.now(timezone.utc),
    )
    db.add(story)
    return story


FELL_BACK = {"summary": {"by": "fallback", "reason": "unavailable"}}


def _batch_reply(prompt, system):
    count = prompt.count("### Story ")
    return json.dumps([f"AI summary {i}." for i in range(1, count + 1)])


def test_catchup_redoes_fell_back_stories_with_free_models(monkeypatch):
    db = _session()
    db.add(Feed(name="Harbour News", url="https://h.example/rss", summarize=True))
    db.add(Feed(name="Other", url="https://o.example/rss", summarize=False))
    sent = platform_ai(monkeypatch, _batch_reply, provider="gemini", model="gemini-3.5-flash-lite", free=True)
    use_ai(db, summaries=True)
    redo = [_story(db, n, record=FELL_BACK) for n in range(1, 8)]
    opted = _story(db, 20, record={"summary": {"by": "opted_out"}})
    stopped = _story(db, 21, record=FELL_BACK, source="Other")  # that source stopped summarising
    done = _story(db, 22, record={"summary": {"by": "ai", "model": "X", "model_id": "x/x"}})
    db.commit()
    assert catchup.run_catchup(db) == 7
    assert len(sent) == 2  # 5 + 2, several stories per request
    for story in redo:
        db.refresh(story)
        assert story.summary.startswith("AI summary")
        assert ai_mod.load_record(story)["summary"]["model_id"] == "gemini/gemini-3.5-flash-lite"
    for story in (opted, stopped, done):
        db.refresh(story)
        assert not story.summary.startswith("AI summary")


def test_catchup_skips_stories_already_in_a_published_paper(monkeypatch):
    from app.services.briefing import dated_briefing_path

    db = _session()
    platform_ai(monkeypatch, _batch_reply, provider="gemini", model="gemini-3.5-flash-lite", free=True)
    use_ai(db, summaries=True)
    before = _story(db, 1, record=FELL_BACK, created=datetime.now(timezone.utc) - timedelta(hours=3))
    after = _story(db, 2, record=FELL_BACK)
    db.commit()
    paper = dated_briefing_path(datetime.now().date(), "txt", user_id=1)
    paper.write_text("NewsCast\n\n1. Story 1\nOn-device summary.\n" + "x" * 100, encoding="utf-8")
    written = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    os.utime(paper, (written, written))
    try:
        assert catchup.run_catchup(db) == 1
    finally:
        paper.unlink()
    db.refresh(before)
    db.refresh(after)
    assert before.summary == "On-device summary." and after.summary.startswith("AI summary")


def test_catchup_uses_free_models_only(monkeypatch):
    db = _session()
    sent = platform_ai(monkeypatch, _batch_reply)  # OpenAI: always Paid
    use_ai(db, summaries=True)
    _story(db, 1, record=FELL_BACK)
    db.commit()
    assert catchup.run_catchup(db) == 0 and sent == []


def test_catchup_stops_on_rate_limit_and_cools_the_model(monkeypatch):
    from stonepi_ai import health

    db = _session()
    sent = platform_ai(
        monkeypatch,
        lambda prompt, system: httpx.Response(429, json={"error": {"message": "Quota exceeded per day"}}),
        provider="gemini", model="gemini-3.5-flash-lite", free=True,
    )
    use_ai(db, summaries=True)
    for n in range(1, 12):
        _story(db, n, record=FELL_BACK)
    db.commit()
    assert catchup.run_catchup(db) == 0
    assert len(sent) == 1
    assert health.cooling_until("gemini/gemini-3.5-flash-lite") is not None


def test_catchup_is_capped_and_most_important_first(monkeypatch):
    db = _session()
    platform_ai(monkeypatch, _batch_reply, provider="gemini", model="gemini-3.5-flash-lite", free=True)
    use_ai(db, summaries=True)
    low = [_story(db, n, record=FELL_BACK, importance=1) for n in range(1, 31)]
    high = _story(db, 40, record=FELL_BACK, importance=5)
    db.commit()
    assert catchup.run_catchup(db) == catchup.CATCHUP_LIMIT
    db.refresh(high)
    assert high.summary.startswith("AI summary")
    assert sum(1 for s in low if (db.refresh(s) or s.summary.startswith("AI summary"))) == catchup.CATCHUP_LIMIT - 1
