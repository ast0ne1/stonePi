"""Which events are "on": a one-off by its start, a run (exhibition, festival)
for every day until its end. Shared by the Agenda, public pages, search, the
map and ingest, so a running exhibition doesn't drop off after its first day."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models import Event

# A one-off that started up to this long ago still counts as on now.
STARTED_GRACE = timedelta(hours=3)
# A run lasts at least a day. Shorter, it's a one-off that may end after
# midnight (a club night 22:00-04:00), which belongs to the day it starts.
RUN_MIN = timedelta(hours=24)


def still_on(now: datetime) -> ColumnElement[bool]:
    """Hasn't finished: started recently, or hasn't reached its end yet."""
    return or_(Event.start_time >= now - STARTED_GRACE, Event.end_time >= now)


def _is_run_sql() -> ColumnElement[bool]:
    return func.julianday(Event.end_time) - func.julianday(Event.start_time) >= RUN_MIN / timedelta(days=1)


def overlaps(start: datetime, end: datetime) -> ColumnElement[bool]:
    """On at some point in [start, end): starts in it, or a run that covers it."""
    return and_(
        Event.start_time < end,
        or_(Event.start_time >= start, and_(Event.end_time >= start, _is_run_sql())),
    )


def is_run(event) -> bool:
    """Lasts a day or more and crosses midnight (so it's on every day in between)."""
    if not event.end_time:
        return False
    # SQLite hands back naive datetimes; a fresh scrape has aware ones.
    start, end = event.start_time.replace(tzinfo=None), event.end_time.replace(tzinfo=None)
    return end.date() > start.date() and end - start >= RUN_MIN
