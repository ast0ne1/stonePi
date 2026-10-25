"""The "here" strip on the family display: where we are, the part of the day and the time
difference from the family, and our weather (Open-Meteo, no key).

    Copenhagen · Sunday evening · 10 h behind you · 8° overcast

There's no clock on purpose: the TRMNL shows the last picture it was sent, and a Webhook
Image plugin takes about 12 an hour, so "14:05" could be most of an hour wrong. The part of
the day and the difference stay true between pushes (and follow daylight saving on both
sides). Notify's hourly push picks up a new part of the day or new weather; an unchanged
picture isn't sent.

Places are picked with Open-Meteo's place search and saved with their time zone.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app import db

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TTL_S = 30 * 60
# A failed fetch keeps showing the last weather this long, then leaves it out.
WEATHER_KEEP_S = 3 * 3600
PLACE_NAME_MAX = 60

_weather: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}
_weather_lock = threading.Lock()


# ── Settings ─────────────────────────────────────────────────────────────────


def show_time() -> bool:
    return db.get_setting("display_show_time") == "1"


def show_weather() -> bool:
    return db.get_setting("display_show_weather") == "1"


def clean_place(raw: Any) -> dict[str, Any] | None:
    """A place from the picker (or the database), or None if it isn't usable."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        lat, lon = float(raw["lat"]), float(raw["lon"])
        tz = str(raw["tz"])
        ZoneInfo(tz)
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return None
    name = " ".join(str(raw.get("name") or "").split())[:PLACE_NAME_MAX]
    if not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    label = " ".join(str(raw.get("label") or name).split())[:120]
    return {"name": name, "label": label, "lat": round(lat, 4), "lon": round(lon, 4), "tz": tz}


def get_place(which: str) -> dict[str, Any] | None:
    """``which`` is "here" (where StonePi is) or "family" (where the TRMNL is)."""
    return clean_place(db.get_setting(f"place_{which}"))


def set_place(which: str, place: dict[str, Any] | None) -> None:
    db.set_setting(f"place_{which}", json.dumps(place) if place else "")


def search_places(query: str) -> list[dict[str, Any]]:
    """Open-Meteo place search: [{name, label, lat, lon, tz}]. Raises httpx.HTTPError."""
    response = httpx.get(
        GEOCODE_URL, params={"name": query, "count": 8, "language": "en", "format": "json"}, timeout=6.0
    )
    response.raise_for_status()
    places = []
    for row in (response.json() or {}).get("results") or []:
        label = ", ".join(str(p) for p in (row.get("name"), row.get("admin1"), row.get("country")) if p)
        place = clean_place(
            {"name": row.get("name"), "label": label, "lat": row.get("latitude"), "lon": row.get("longitude"),
             "tz": row.get("timezone")}
        )
        if place:
            places.append(place)
    return places


# ── Time ─────────────────────────────────────────────────────────────────────


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _hours(minutes: int) -> str:
    whole, rest = divmod(abs(minutes), 60)
    fraction = {0: "", 15: "¼", 30: "½", 45: "¾"}.get(rest, "")
    return f"{whole or ''}{fraction} h" if fraction else f"{whole} h"


def difference(here_tz: str, family_tz: str, now: datetime) -> str:
    """How our clock compares with the family's, from their side: "10 h behind you"."""
    here = now.astimezone(ZoneInfo(here_tz)).utcoffset()
    there = now.astimezone(ZoneInfo(family_tz)).utcoffset()
    minutes = round((there - here).total_seconds() / 60)
    if minutes == 0:
        return "same time as you"
    return f"{_hours(minutes)} {'behind' if minutes > 0 else 'ahead of'} you"


# ── Weather ──────────────────────────────────────────────────────────────────


def _fetch_weather(place: dict[str, Any]) -> dict[str, Any]:
    from stonepi_display.weather import wmo

    response = httpx.get(
        FORECAST_URL,
        params={"latitude": place["lat"], "longitude": place["lon"], "current": "temperature_2m,weather_code,is_day"},
        timeout=4.0,
    )
    response.raise_for_status()
    current = (response.json() or {}).get("current") or {}
    if current.get("temperature_2m") is None:
        raise ValueError("no current temperature")
    _, label = wmo(current.get("weather_code"), bool(current.get("is_day", 1)))
    return {"temp": round(current["temperature_2m"]), "text": label.lower()}


def weather(place: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
    """{temp, text} for ``place``, at most WEATHER_TTL_S old; None when there's nothing recent."""
    now = time.monotonic() if now is None else now
    key = (place["lat"], place["lon"])
    with _weather_lock:
        hit = _weather.get(key)
    if hit and now - hit[0] < WEATHER_TTL_S:
        return hit[1]
    try:
        fresh = _fetch_weather(place)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return hit[1] if hit and now - hit[0] < WEATHER_KEEP_S else None
    with _weather_lock:
        _weather[key] = (now, fresh)
    return fresh


# ── The strip ────────────────────────────────────────────────────────────────


def strip(now: datetime | None = None) -> str | None:
    """The header line, or None to show the date as before (both parts off, or no city yet)."""
    if not (show_time() or show_weather()):
        return None
    here = get_place("here")
    if here is None:
        return None
    now = now or datetime.now(timezone.utc)
    parts = [here["name"]]
    if show_time():
        local = now.astimezone(ZoneInfo(here["tz"]))
        parts.append(f"{local:%A} {part_of_day(local.hour)}")
        family = get_place("family")
        if family:
            parts.append(difference(here["tz"], family["tz"], now))
    if show_weather():
        current = weather(here)
        if current:
            parts.append(f"{current['temp']}° {current['text']}")
    return " · ".join(parts)
