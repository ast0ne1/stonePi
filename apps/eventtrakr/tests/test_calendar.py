from datetime import datetime, timezone
from app.models import Event
from app.services.calendar_sync import build_event_ics, build_google_calendar_url


def test_calendar_urls_and_ics():
    ev = Event(
        id=42,
        user_id=1,
        fingerprint="abcd1234efgh5678",
        title="Open Source Summit",
        description="Annual conference on open source tech.",
        start_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
        location="ExCeL London",
        cost="Free",
        category="Tech",
        url="https://example.com/summit",
    )

    gcal_url = build_google_calendar_url(ev)
    assert "calendar.google.com/calendar/render" in gcal_url
    assert "action=TEMPLATE" in gcal_url
    assert "Open+Source+Summit" in gcal_url or "Open%20Source%20Summit" in gcal_url
    assert "20260921T100000Z" in gcal_url

    ics_bytes = build_event_ics(ev)
    assert b"BEGIN:VCALENDAR" in ics_bytes
    assert b"SUMMARY:Open Source Summit" in ics_bytes
    assert b"LOCATION:ExCeL London" in ics_bytes
    assert b"END:VCALENDAR" in ics_bytes
