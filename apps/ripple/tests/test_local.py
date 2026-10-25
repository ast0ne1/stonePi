"""The "here" strip on the family display: places, part of the day, time difference, weather."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, local, notify, routes
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

SECRET = "ripple-secret"
CPH = {"name": "Copenhagen", "label": "Copenhagen, Capital Region, Denmark", "lat": 55.6759, "lon": 12.5655,
       "tz": "Europe/Copenhagen"}
SYD = {"name": "Sydney", "label": "Sydney, New South Wales, Australia", "lat": -33.8679, "lon": 151.2073,
       "tz": "Australia/Sydney"}
# 25 Oct 2026, 18:00 in Copenhagen (CET, UTC+1 since that night) = 05:00 Monday in Sydney (AEDT, UTC+11).
SUNDAY_EVENING = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    local._weather.clear()
    monkeypatch.setattr(notify, "display_changed", lambda secret: None)


def _client(monkeypatch, *, is_admin=True) -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    monkeypatch.setattr(notify, "display_status", lambda secret: [])
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    c.cookies.set(COOKIE_NAME, encode_session(secret=SECRET, user_id="u1", username="adam", display_name="Adam",
                                              is_admin=is_admin, apps=["ripple"], session_id="s", permissions={}))
    return c


def _setup(show_time=True, show_weather=True, family=SYD):
    db.set_setting("display_show_time", "1" if show_time else "")
    db.set_setting("display_show_weather", "1" if show_weather else "")
    local.set_place("here", CPH)
    local.set_place("family", family)


# -- time ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "family_tz, when, expected",
    [
        ("Australia/Sydney", SUNDAY_EVENING, "10 h behind you"),
        ("Australia/Sydney", datetime(2026, 7, 1, 12, tzinfo=timezone.utc), "8 h behind you"),  # both sides' DST
        ("Australia/Adelaide", SUNDAY_EVENING, "9½ h behind you"),
        ("Asia/Kathmandu", SUNDAY_EVENING, "4¾ h behind you"),
        ("America/Los_Angeles", SUNDAY_EVENING, "8 h ahead of you"),
        ("Europe/Paris", SUNDAY_EVENING, "same time as you"),
        ("Asia/Kolkata", datetime(2026, 1, 15, 12, tzinfo=timezone.utc), "4½ h behind you"),
    ],
)
def test_time_difference_from_the_familys_side(family_tz, when, expected):
    assert local.difference("Europe/Copenhagen", family_tz, when) == expected


@pytest.mark.parametrize("hour, part", [(4, "night"), (5, "morning"), (11, "morning"), (12, "afternoon"),
                                        (17, "evening"), (21, "evening"), (22, "night"), (0, "night")])
def test_part_of_day(hour, part):
    assert local.part_of_day(hour) == part


# -- the strip -------------------------------------------------------------------------


def test_strip_reads_like_a_sentence(monkeypatch):
    monkeypatch.setattr(local, "_fetch_weather", lambda place: {"temp": 8, "text": "overcast"})
    _setup()
    assert local.strip(SUNDAY_EVENING) == "Copenhagen · Sunday evening · 10 h behind you · 8° overcast"


def test_strip_parts_follow_the_switches(monkeypatch):
    monkeypatch.setattr(local, "_fetch_weather", lambda place: {"temp": 8, "text": "overcast"})
    _setup(show_weather=False)
    assert local.strip(SUNDAY_EVENING) == "Copenhagen · Sunday evening · 10 h behind you"
    _setup(show_time=False)
    assert local.strip(SUNDAY_EVENING) == "Copenhagen · 8° overcast"
    _setup(family=None)
    assert local.strip(SUNDAY_EVENING) == "Copenhagen · Sunday evening · 8° overcast"
    _setup(show_time=False, show_weather=False)
    assert local.strip(SUNDAY_EVENING) is None


def test_no_strip_without_our_city():
    db.set_setting("display_show_time", "1")
    assert local.strip(SUNDAY_EVENING) is None


def test_weather_is_cached_and_survives_a_short_outage(monkeypatch):
    calls = []

    def fetch(place):
        calls.append(place["name"])
        if len(calls) > 1:
            raise httpx.ConnectError("offline")
        return {"temp": 8, "text": "overcast"}

    monkeypatch.setattr(local, "_fetch_weather", fetch)
    assert local.weather(CPH, now=0) == {"temp": 8, "text": "overcast"}
    assert local.weather(CPH, now=60) == {"temp": 8, "text": "overcast"} and calls == ["Copenhagen"]
    stale = local.WEATHER_TTL_S + 1
    assert local.weather(CPH, now=stale) == {"temp": 8, "text": "overcast"}  # fetch failed: last known
    assert local.weather(CPH, now=local.WEATHER_KEEP_S + 1) is None  # too old to show


def test_weather_reading_from_open_meteo(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"current": {"temperature_2m": 7.6, "weather_code": 3, "is_day": 0}}

    seen = {}

    def fake_get(url, params, timeout):
        seen.update(url=url, params=params)
        return _Response()

    monkeypatch.setattr(local.httpx, "get", fake_get)
    assert local._fetch_weather(CPH) == {"temp": 8, "text": "overcast"}
    assert seen["params"]["latitude"] == CPH["lat"] and "weather_code" in seen["params"]["current"]


def test_display_header_shows_the_strip_instead_of_the_date(monkeypatch):
    monkeypatch.setattr(local, "_fetch_weather", lambda place: {"temp": 8, "text": "overcast"})
    c = _client(monkeypatch)
    assert 'class="date"' in c.get("/display").text
    _setup()
    html = c.get("/display").text
    assert 'class="here"' in html and "Copenhagen · " in html and "° overcast" in html


# -- places and settings ---------------------------------------------------------------


def test_clean_place_rejects_bad_input():
    assert local.clean_place(json.dumps(CPH))["tz"] == "Europe/Copenhagen"
    assert local.clean_place({**CPH, "tz": "Mars/Olympus"}) is None
    assert local.clean_place({**CPH, "lat": 123}) is None
    assert local.clean_place({**CPH, "name": "  "}) is None
    assert local.clean_place("not json") is None and local.clean_place("") is None


def test_place_search_parses_open_meteo(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [
                {"name": "Perth", "admin1": "Western Australia", "country": "Australia", "latitude": -31.95,
                 "longitude": 115.86, "timezone": "Australia/Perth"},
                {"name": "Perth", "admin1": "Scotland", "country": "United Kingdom", "latitude": 56.39,
                 "longitude": -3.43, "timezone": "Europe/London"},
                {"name": "Nowhere", "latitude": 1, "longitude": 1, "timezone": "Not/AZone"},
            ]}

    monkeypatch.setattr(local.httpx, "get", lambda url, params, timeout: _Response())
    places = local.search_places("Perth")
    assert [p["label"] for p in places] == ["Perth, Western Australia, Australia", "Perth, Scotland, United Kingdom"]
    assert places[0]["tz"] == "Australia/Perth"


def test_place_search_is_for_admins(monkeypatch):
    monkeypatch.setattr(local, "search_places", lambda q: [CPH])
    assert _client(monkeypatch).get("/api/places?q=Cope").json() == {"ok": True, "places": [CPH]}
    assert _client(monkeypatch, is_admin=False).get("/api/places?q=Cope").status_code == 403

    def broken(q):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(local, "search_places", broken)
    r = _client(monkeypatch).get("/api/places?q=Cope")
    assert r.status_code == 502 and "isn't reachable" in r.json()["message"]


def test_settings_save_the_strip(monkeypatch):
    nudges = []
    monkeypatch.setattr(notify, "display_changed", lambda secret: nudges.append(secret))
    c = _client(monkeypatch)
    form = {"csrf_token": "csrf", "show_time": "1", "show_weather": "1",
            "place_here": json.dumps(CPH), "place_family": json.dumps(SYD)}
    r = c.post("/settings/here", data=form, follow_redirects=False)
    assert r.headers["location"].endswith("msg=Saved#here")
    assert local.show_time() and local.show_weather()
    assert local.get_place("here")["name"] == "Copenhagen" and local.get_place("family")["tz"] == "Australia/Sydney"
    assert nudges == [SECRET]
    html = c.get("/settings?tab=display").text
    assert "Copenhagen, Capital Region, Denmark" in html and "Sydney, New South Wales, Australia" in html
    # Clearing the family's city keeps the rest.
    c.post("/settings/here", data={**form, "place_family": ""})
    assert local.get_place("family") is None and local.get_place("here") is not None


def test_settings_need_our_city_to_switch_on(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/settings/here", data={"csrf_token": "csrf", "show_time": "1", "place_here": ""}, follow_redirects=False)
    assert "err=" in r.headers["location"] and not local.show_time()


def test_members_cannot_change_it(monkeypatch):
    c = _client(monkeypatch, is_admin=False)
    r = c.post("/settings/here", data={"csrf_token": "csrf", "show_time": "1", "place_here": json.dumps(CPH)},
               follow_redirects=False)
    assert "err=" in r.headers["location"] and local.get_place("here") is None
    assert 'action="/settings/here"' not in c.get("/settings?tab=display").text
