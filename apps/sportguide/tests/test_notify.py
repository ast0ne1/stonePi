"""Tests for SportGuide watched-match notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db
from app.services import notify as notify_service


def test_listing_matches_watched():
    assert notify_service.listing_matches_watched("Arsenal vs Chelsea", ["Arsenal"]) == "Arsenal"
    assert notify_service.listing_matches_watched("Denmark vs Sweden", ["denmark"]) == "denmark"
    assert notify_service.listing_matches_watched("Spurs vs Villa", ["Arsenal"]) is None


def test_check_approaching_watched_emits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sport.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    db.set_watched_teams("local", ["Arsenal"])
    db.set_approaching_lead_minutes("local", 30)
    db.set_approaching_sent("local", {})

    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    soon = (now + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.replace_source_listings(
        "wheresthematch",
        [
            {
                "external_id": "a1",
                "sport": "football",
                "league": "Premier League",
                "title": "Arsenal vs Chelsea",
                "starts_at": soon,
                "channels": ["Sky Sports"],
                "source_url": "https://example.com/a",
            },
            {
                "external_id": "a2",
                "sport": "football",
                "league": "Premier League",
                "title": "Liverpool vs Everton",
                "starts_at": soon,
                "channels": [],
                "source_url": "",
            },
            {
                "external_id": "a3",
                "sport": "football",
                "league": "Premier League",
                "title": "Arsenal vs Spurs",
                "starts_at": far,
                "channels": [],
                "source_url": "",
            },
        ],
    )

    emitted: list[object] = []

    def _fake_emit(envelope):
        emitted.append(envelope)
        return True

    monkeypatch.setattr("stonepi_contracts.emit_event", _fake_emit)

    first = notify_service.check_approaching_watched(now=now)
    assert first["checked"] == 1
    assert first["notified"] == 1
    assert len(emitted) == 1
    assert emitted[0].id == "sportguide.watched_match_approaching"
    assert "Arsenal" in emitted[0].title

    second = notify_service.check_approaching_watched(now=now + timedelta(minutes=5))
    assert second["notified"] == 0
    assert second["skipped"] == 1
    assert len(emitted) == 1
