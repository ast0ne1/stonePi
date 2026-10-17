from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, Story
from app.services.ingest import MAX_EXTRACTS_PER_FEED, run_ingest
from app.services.ai import AiSettings


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


@pytest.mark.parametrize("ai_on", [False, True])
def test_article_extracts_capped_only_when_ai_writes_summaries(monkeypatch, ai_on):
    db = _session()
    db.add(Feed(name="The Age", url="https://www.theage.com.au/", enabled=True, type="webpage", summarize=True))
    db.commit()
    headlines = [
        "Caulfield Cup stewards open inquiry into fall",
        "Council votes to rebuild the old velodrome",
        "Smart traps target feral foxes in Victoria",
        "Treasurer defends budget forecast in parliament",
        "Heritage home in Carlton sells above reserve",
        "Lamb exporters reject blame for US downturn",
        "Melbourne trams to run all night at weekends",
    ]
    count = MAX_EXTRACTS_PER_FEED + 3
    assert count <= len(headlines)

    def fake_collect(_feed):
        return [
            {
                "title": headlines[n],
                "url": f"https://www.theage.com.au/national/story-{n}-20261017-p6{n}.html",
                "excerpt": "",
                "published_at": datetime(2026, 10, 17, tzinfo=timezone.utc),
                "source": "The Age",
            }
            for n in range(count)
        ]

    extracted: list[str] = []

    def fake_extract(url, fallback, **_kwargs):
        extracted.append(url)
        return f"Body text for {url}."

    # AI on for importance only must not cap: the on-device summary still needs the text.
    ai = AiSettings(available=True, enabled=True, summaries=ai_on, translate=False, importance=True)
    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    monkeypatch.setattr("app.services.ingest._extract_article", fake_extract)
    monkeypatch.setattr("app.services.ai.ai_settings", lambda _db: ai)
    monkeypatch.setattr("app.services.summarize.summarize_story", lambda title, excerpt, *_a, **_k: (excerpt or title, {}))
    monkeypatch.setattr("app.services.importance.score_story", lambda *_a, **_k: (5, {}))
    monkeypatch.setattr("app.services.briefing.enqueue_latest_briefing", lambda _db: None)
    monkeypatch.setattr("app.services.briefing.purge_expired_stories", lambda _db: None)

    assert run_ingest(db, force=True)["ok"] is True
    assert db.query(Story).count() == count
    assert len(extracted) == (MAX_EXTRACTS_PER_FEED if ai_on else count)
