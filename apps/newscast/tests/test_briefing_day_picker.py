"""Briefing day picker: any day in the retention window, not just Today / Yesterday / All."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services.briefing import (
    briefing_day_label,
    briefing_day_options,
    briefing_path,
    canonical_briefing_day,
    current_stories,
)

NOW = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr("app.services.briefing.utcnow", lambda: NOW)
    monkeypatch.setattr("app.services.briefing._local_today", lambda now=None: date(2026, 9, 29))
    monkeypatch.setattr("app.services.briefing._local_tz", lambda: timezone.utc)
    monkeypatch.setattr("app.services.briefing.env.story_retention_days", 7)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _story(n: int, days_ago: int, **kw) -> Story:
    when = NOW - timedelta(days=days_ago, minutes=n)
    return Story(
        title=f"Story {days_ago}-{n}",
        summary="Summary",
        source_name="BBC",
        canonical_url=f"https://example.com/{days_ago}/{n}",
        content_hash=f"h{days_ago}-{n}",
        cluster_key=f"k{days_ago}-{n}",
        published_at=when,
        created_at=when,
        importance=3,
        **kw,
    )


def test_specific_dates_normalise_and_round_trip():
    assert canonical_briefing_day("2026-09-29") == "today"
    assert canonical_briefing_day("2026-09-28") == "yesterday"
    assert canonical_briefing_day("2026-09-25") == "2026-09-25"
    assert canonical_briefing_day("2026-09-01") == "today"  # outside the 7-day window
    assert canonical_briefing_day("2026-10-02") == "today"  # future
    assert canonical_briefing_day("all") == "all"
    # Starring or saving a story on a picked day returns to that day.
    assert briefing_path("2026-09-25") == "/?day=2026-09-25"
    assert briefing_day_label("2026-09-05") == "5 Sep"
    assert briefing_day_label("today") == ""


def test_older_day_is_not_crowded_out_by_newer_stories():
    db = _session()
    # Plenty of newer stories (more than the Briefing's candidate pool) plus two from 5 days ago.
    db.add_all([_story(n, days_ago=0) for n in range(120)])
    db.add_all([_story(n, days_ago=5) for n in range(2)])
    db.commit()
    picked = current_stories(db, day="2026-09-24")
    assert sorted(story.title for story in picked) == ["Story 5-0", "Story 5-1"]


def test_day_options_cover_the_retention_window():
    db = _session()
    db.add_all([_story(0, days_ago=0), _story(0, days_ago=3), _story(1, days_ago=6, saved=True)])
    db.commit()
    options = briefing_day_options(db)
    assert [option["key"] for option in options] == [
        "today",
        "yesterday",
        "2026-09-27",
        "2026-09-26",
        "2026-09-25",
        "2026-09-24",
        "2026-09-23",
    ]
    assert options[0]["name"] == "Today" and options[2]["name"] == "Sunday"
    assert options[3]["href"] == "/?day=2026-09-26"
    has = {option["key"]: option["has_stories"] for option in options}
    assert has["today"] and has["2026-09-26"]
    assert not has["yesterday"]
    assert not has["2026-09-23"]  # only a saved long-read, which Briefing hides
