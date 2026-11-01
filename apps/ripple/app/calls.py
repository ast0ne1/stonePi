"""Quiet hours and "good time to call" on the family display (backlog U36).

Our quiet hours (sleep, and work on weekdays) are set in our city's time zone; the board says
whether now is a good time to call us, in the family's time when their city is set:

    Good time to call · until 23:00 your time
    We're asleep · back from 07:00 your time
    We're at work · free from 17:00 your time

The TRMNL keeps showing a picture until the next push, so the line always gives the range's
end rather than "now". Notify's hourly push picks up the change once the range is over (an
unchanged picture isn't sent), so it's at most about an hour behind at a boundary.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app import db, local

STEP = timedelta(minutes=5)
LOOK_AHEAD = timedelta(days=3)
WEEKDAYS = (0, 1, 2, 3, 4)  # Monday-Friday
DEFAULTS = {"sleep_from": "23:00", "sleep_to": "07:00", "work_from": "09:00", "work_to": "17:00"}
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ── Settings ─────────────────────────────────────────────────────────────────


def enabled() -> bool:
    return db.get_setting("calls_on") == "1"


def work_on() -> bool:
    return db.get_setting("calls_work_on") == "1"


def clean_time(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if _TIME.match(text) else default


def times() -> dict[str, str]:
    return {key: clean_time(db.get_setting(f"calls_{key}"), default) for key, default in DEFAULTS.items()}


def save(on: bool, work: bool, values: dict[str, Any]) -> dict[str, str] | None:
    """Store the settings; None when a time isn't HH:MM (nothing is saved then)."""
    cleaned = {}
    for key in DEFAULTS:
        text = str(values.get(key) or "").strip()
        if not _TIME.match(text):
            return None
        cleaned[key] = text
    db.set_setting("calls_on", "1" if on else "")
    db.set_setting("calls_work_on", "1" if work else "")
    for key, text in cleaned.items():
        db.set_setting(f"calls_{key}", text)
    return cleaned


# ── Working it out ───────────────────────────────────────────────────────────


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def _within(minute: int, start: str, end: str) -> bool:
    a, b = _minutes(start), _minutes(end)
    if a == b:
        return False
    return a <= minute < b if a < b else (minute >= a or minute < b)  # overnight wraps


def state_at(moment: datetime, settings: dict[str, str] | None = None, work: bool | None = None) -> str:
    """"asleep", "work" or "free" for ``moment`` in our time zone."""
    settings = settings or times()
    work = work_on() if work is None else work
    minute = moment.hour * 60 + moment.minute
    if _within(minute, settings["sleep_from"], settings["sleep_to"]):
        return "asleep"
    if work and moment.weekday() in WEEKDAYS and _within(minute, settings["work_from"], settings["work_to"]):
        return "work"
    return "free"


def status(now: datetime | None = None) -> dict[str, Any] | None:
    """{state, until (aware datetime)} for now, or None when off or our city isn't set."""
    if not enabled():
        return None
    here = local.get_place("here")
    if here is None:
        return None
    zone = ZoneInfo(here["tz"])
    settings, work = times(), work_on()
    start = (now or datetime.now(timezone.utc)).astimezone(zone).replace(second=0, microsecond=0)
    current = state_at(start, settings, work)
    # Step forward to the next change (5-minute steps; settings are whole minutes, so start
    # from the minute and land on the boundary itself).
    moment = start
    while moment - start < LOOK_AHEAD:
        moment = (moment + timedelta(minutes=1)) if moment == start else moment + STEP
        if state_at(moment, settings, work) != current:
            back = moment
            while state_at(back - timedelta(minutes=1), settings, work) != current:
                back -= timedelta(minutes=1)
            return {"state": current, "until": back}
    return {"state": current, "until": None}


def line(now: datetime | None = None) -> str | None:
    """The board's line, in the family's time when their city is set."""
    found = status(now)
    if found is None:
        return None
    family = local.get_place("family")
    until = found["until"]
    suffix = ""
    if until is not None:
        if family:
            until = until.astimezone(ZoneInfo(family["tz"]))
            suffix = " your time"
        today = (now or datetime.now(timezone.utc)).astimezone(until.tzinfo).date()
        day = "" if until.date() == today else f"{until:%a} "
        when = f"{day}{until:%H:%M}{suffix}"
    if found["state"] == "asleep":
        return f"We're asleep · back from {when}" if until else "We're asleep"
    if found["state"] == "work":
        return f"We're at work · free from {when}" if until else "We're at work"
    return f"Good time to call · until {when}" if until else "Good time to call"
