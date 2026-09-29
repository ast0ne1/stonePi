"""Tests for stopping a manual EventTrakr sync between sources."""

from __future__ import annotations

from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import EventSource
from app.services import ingest

# Isolated owner so the batch only sees the sources this test creates.
TEST_USER_ID = 987654


def _seed_sources(count: int) -> None:
    init_db()
    with SessionLocal() as db:
        db.execute(delete(EventSource).where(EventSource.user_id == TEST_USER_ID))
        for n in range(count):
            # .ics URLs keep the batch browser-free (no Chromium in tests).
            db.add(EventSource(user_id=TEST_USER_ID, name=f"Feed {n}", url=f"https://example.com/{n}.ics", source_type="ics"))
        db.commit()


def _cleanup() -> None:
    with SessionLocal() as db:
        db.execute(delete(EventSource).where(EventSource.user_id == TEST_USER_ID))
        db.commit()


def test_request_stop_is_noop_when_idle():
    assert ingest.state.running is False
    assert ingest.request_stop() is False
    assert ingest.state.stopping is False


def test_stop_halts_sync_before_next_source(monkeypatch):
    _seed_sources(3)
    fetched: list[str] = []

    def _fake_fetch(db, src, browser=None):
        fetched.append(src.name)
        if len(fetched) == 1:
            assert ingest.request_stop() is True
            assert ingest.state.stopping is True
        return 2, 5

    monkeypatch.setattr(ingest, "fetch_and_extract_source", _fake_fetch)
    try:
        ingest.sync_all_sources(user_id=TEST_USER_ID)
    finally:
        _cleanup()

    assert fetched == ["Feed 0"]
    assert ingest.state.running is False
    assert ingest.state.stopping is False
    assert ingest.state.last_message == "Stopped"
    assert ingest.state.progress.startswith("Stopped after 1/3 sources")


def test_full_sync_still_completes_and_clears_stop(monkeypatch):
    _seed_sources(2)
    monkeypatch.setattr(ingest, "fetch_and_extract_source", lambda db, src, browser=None: (1, 1))
    try:
        ingest.sync_all_sources(user_id=TEST_USER_ID)
    finally:
        _cleanup()

    assert ingest.state.last_message == "Idle"
    assert ingest.state.progress == "Complete: 2 new events found."
    assert ingest.state.stopping is False
