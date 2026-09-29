"""Tests for EventTrakr approaching notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal, init_db
from app.models import Event, User
from app.services import notify as notify_service
from app.services import settings as settings_service


def _admin_id() -> int:
    init_db()
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        assert admin is not None
        return int(admin.id)


def test_approaching_lead_minutes_clamped():
    init_db()
    with SessionLocal() as db:
        settings_service.set_value(db, "notify_approaching_minutes", "2")
        assert notify_service.approaching_lead_minutes(db) == notify_service.MIN_LEAD_MINUTES
        settings_service.set_value(db, "notify_approaching_minutes", "99999")
        assert notify_service.approaching_lead_minutes(db) == notify_service.MAX_LEAD_MINUTES
        settings_service.set_value(db, "notify_approaching_minutes", "45")
        assert notify_service.approaching_lead_minutes(db) == 45
        settings_service.set_value(db, "notify_approaching_minutes", "bogus")
        assert notify_service.approaching_lead_minutes(db) == notify_service.DEFAULT_LEAD_MINUTES


def test_check_approaching_favourites_emits_once(monkeypatch):
    init_db()
    admin_id = _admin_id()
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    emitted: list[object] = []

    def _fake_emit(envelope):
        emitted.append(envelope)
        return True

    monkeypatch.setattr("stonepi_contracts.emit_event", _fake_emit)
    monkeypatch.setattr(
        notify_service, "owner_auth_id", lambda _db, uid: "7c9e6679-7425-40de-944b-e07fc1f90ae7" if uid else None
    )

    with SessionLocal() as db:
        settings_service.set_value(db, "notify_approaching_minutes", "30")
        settings_service.set_value(db, notify_service.SENT_KEY, "{}")
        db.query(Event).filter_by(fingerprint="notify-test-fp").delete()
        db.commit()
        ev = Event(
            user_id=admin_id,
            fingerprint="notify-test-fp",
            title="Soon Concert",
            description="",
            start_time=now + timedelta(minutes=20),
            location="Vega",
            cost="Free",
            category="Music",
            url="https://example.com/soon",
            is_favourited=True,
        )
        far = Event(
            user_id=admin_id,
            fingerprint="notify-test-far",
            title="Far Concert",
            description="",
            start_time=now + timedelta(hours=3),
            location="Elsewhere",
            cost="Free",
            category="Music",
            url="",
            is_favourited=True,
        )
        unfav = Event(
            user_id=admin_id,
            fingerprint="notify-test-unfav",
            title="Unfaved",
            description="",
            start_time=now + timedelta(minutes=10),
            location="Here",
            cost="Free",
            category="Music",
            url="",
            is_favourited=False,
        )
        db.add_all([ev, far, unfav])
        db.commit()
        event_id = ev.id

        first = notify_service.check_approaching_favourites(db, now=now)
        assert first["checked"] == 1
        assert first["notified"] == 1
        assert first["skipped"] == 0
        assert len(emitted) == 1
        assert emitted[0].id == "eventtrakr.event_approaching"
        assert emitted[0].title == "Soon Concert"
        assert "Vega" in emitted[0].summary

        second = notify_service.check_approaching_favourites(db, now=now + timedelta(minutes=5))
        assert second["notified"] == 0
        assert second["skipped"] == 1
        assert len(emitted) == 1

        # Reschedule → notify again
        row = db.get(Event, event_id)
        row.start_time = now + timedelta(minutes=25)
        db.commit()
        third = notify_service.check_approaching_favourites(db, now=now + timedelta(minutes=5))
        assert third["notified"] == 1
        assert len(emitted) == 2

        db.query(Event).filter(
            Event.fingerprint.in_(["notify-test-fp", "notify-test-far", "notify-test-unfav"])
        ).delete(synchronize_session=False)
        settings_service.set_value(db, notify_service.SENT_KEY, "{}")
        db.commit()
