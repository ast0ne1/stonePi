from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from icalendar import Calendar, Event as IcsEvent, vText, vUri
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CalendarConnection, Event

logger = logging.getLogger("eventtrakr.calendar")


def format_gcal_datetime(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def build_google_calendar_url(event: Event) -> str:
    """Generate a 1-click URL that opens Google Calendar with pre-filled event fields."""
    start_str = format_gcal_datetime(event.start_time)
    end_time = event.end_time or (event.start_time + timedelta(hours=2))
    end_str = format_gcal_datetime(end_time)

    details = event.description or ""
    if event.cost and event.cost != "Free / Unspecified":
        details = f"Cost: {event.cost}\n\n{details}"
    if event.url:
        details = f"{details}\n\nEvent Link: {event.url}"

    params = {
        "action": "TEMPLATE",
        "text": event.title,
        "dates": f"{start_str}/{end_str}",
        "details": details.strip(),
        "location": event.location or "",
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def build_facebook_share_url(event: Event) -> str:
    """Generate Facebook share / redirect URL."""
    target_url = event.url or "https://facebook.com"
    return "https://www.facebook.com/sharer/sharer.php?u=" + quote(target_url)


def build_event_ics(event: Event) -> bytes:
    """Build an RFC 5545 .ics file for a single event."""
    cal = Calendar()
    cal.add("prodid", "-//EventTrakr//EventTrakr//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")

    ie = IcsEvent()
    ie.add("summary", event.title)
    ie.add("dtstart", event.start_time)
    end_time = event.end_time or (event.start_time + timedelta(hours=2))
    ie.add("dtend", end_time)
    ie.add("dtstamp", datetime.now(timezone.utc))
    ie.add("uid", f"event-{event.id}-{event.fingerprint[:12]}@eventtrakr")

    details = event.description or ""
    if event.cost and event.cost != "Free / Unspecified":
        details = f"Cost: {event.cost}\n\n{details}"
    if event.url:
        details = f"{details}\n\nSource: {event.url}"

    ie.add("description", details.strip())
    if event.location and event.location != "Unspecified":
        ie.add("location", event.location)
    if event.url:
        ie.add("url", event.url)

    cal.add_component(ie)
    return cal.to_ical()


def build_user_calendar_feed(db: Session, user_id: int) -> bytes:
    """Build an RFC 5545 calendar feed of all favourited/upcoming events for a user."""
    cal = Calendar()
    cal.add("prodid", "-//EventTrakr//User Feed//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "EventTrakr My Events")

    events = db.execute(
        select(Event).where(Event.user_id == user_id, Event.is_favourited == True).order_by(Event.start_time)
    ).scalars()

    for event in events:
        ie = IcsEvent()
        ie.add("summary", event.title)
        ie.add("dtstart", event.start_time)
        end_time = event.end_time or (event.start_time + timedelta(hours=2))
        ie.add("dtend", end_time)
        ie.add("dtstamp", event.created_at or datetime.now(timezone.utc))
        ie.add("uid", f"event-{event.id}-{event.fingerprint[:12]}@eventtrakr")
        if event.location and event.location != "Unspecified":
            ie.add("location", event.location)
        if event.description:
            ie.add("description", event.description[:500])
        if event.url:
            ie.add("url", event.url)
        cal.add_component(ie)

    return cal.to_ical()


def forward_event_to_google(db: Session, user_id: int, event: Event) -> tuple[bool, str]:
    """Forward a favourited event to Google Calendar if connected via OAuth.
    Returns (success, message_or_error).
    """
    conn = db.execute(
        select(CalendarConnection).where(
            CalendarConnection.user_id == user_id,
            CalendarConnection.provider == "google",
        )
    ).scalar_one_or_none()

    if not conn or not conn.access_token:
        return False, "Google Calendar account not connected. Use 1-click Google Calendar link or connect in Settings."

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from app.services import settings as settings_service

        client_id, client_secret = settings_service.get_google_oauth_credentials(db)
        creds = Credentials(
            token=conn.access_token,
            refresh_token=conn.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
        )

        service = build("calendar", "v3", credentials=creds)

        end_time = event.end_time or (event.start_time + timedelta(hours=2))
        body = {
            "summary": event.title,
            "description": f"{event.description}\n\nCost: {event.cost}\nLink: {event.url}",
            "location": event.location,
            "start": {"dateTime": event.start_time.isoformat()},
            "end": {"dateTime": end_time.isoformat()},
        }

        created = service.events().insert(calendarId="primary", body=body).execute()
        event.google_event_id = created.get("id")
        event.calendar_synced = True
        db.commit()
        return True, "Event successfully added to Google Calendar."
    except Exception as e:
        logger.exception("Failed to forward event to Google: %s", e)
        return False, f"Failed pushing to Google Calendar: {e}"
