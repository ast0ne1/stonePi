"""Notify's agent read for Pebble: the weather at the household's place (Car Thing → Look)."""

from __future__ import annotations

import json

import httpx
import pytest
from stonepi_auth.internal import sign_internal_v2

from app import agent_routes
from tests.test_displays_trmnl import SECRET, admin_client, fresh  # noqa: F401  (fixture)

PATH = "/api/internal/agent"
FORECAST = {
    "current": {"temperature_2m": 8.6, "apparent_temperature": 5.2, "weather_code": 61, "is_day": 1, "wind_speed_10m": 18.4},
    "hourly": {
        "time": [f"2026-10-25T{h:02d}:00" for h in range(9, 22)],
        "temperature_2m": [9 + h / 10 for h in range(13)],
        "weather_code": [61] * 6 + [3] * 7,
        "is_day": [1] * 9 + [0] * 4,
        "precipitation_probability": [80] * 6 + [20] * 7,
    },
    "daily": {
        "time": ["2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28"],
        "weather_code": [61, 3, 0, 80],
        "temperature_2m_max": [11.4, 12.0, 13.2, 10.1],
        "temperature_2m_min": [6.2, 7.0, 5.5, 4.9],
        "precipitation_probability_max": [85, 30, 0, 60],
    },
}


def _ask(client, tool="get_weather"):
    body = json.dumps({"tool": tool, "user": "u1", "params": {}}).encode()
    headers = {"Content-Type": "application/json", **sign_internal_v2(SECRET, "POST", PATH, body)}
    return client.post(PATH, content=body, headers=headers)


@pytest.fixture
def place(monkeypatch):
    state = {"place": (55.68, 12.57, "Copenhagen", "metric"), "calls": 0, "fail": False}
    monkeypatch.setattr(agent_routes, "_place", lambda: state["place"])
    agent_routes._cache.clear()

    def get(url, params=None):
        state["calls"] += 1
        state["params"] = params
        if state["fail"]:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json=FORECAST, request=httpx.Request("GET", url))

    monkeypatch.setattr(agent_routes._client, "get", get)
    return state


def test_needs_a_signature(fresh):
    assert admin_client().post(PATH, json={"tool": "get_weather", "user": "u1"}).status_code == 404


def test_weather_now_today_hours_and_days(fresh, place):
    payload = _ask(admin_client()).json()["payload"]
    assert payload["place"] == "Copenhagen" and payload["units"]["temperature"] == "°C"
    assert payload["now"] == {"temp": 9, "feels_like": 5, "conditions": "light rain", "wind": 18}
    assert payload["today"]["day"] == "today" and payload["today"]["high"] == 11 and payload["today"]["rain_chance_pct"] == 85
    assert [h["time"] for h in payload["next_hours"]] == ["12:00", "15:00", "18:00", "21:00"]
    assert payload["next_hours"][1]["conditions"] == "overcast" and payload["next_hours"][1]["rain_chance_pct"] == 20
    assert [d["day"] for d in payload["next_days"]] == ["Monday", "Tuesday", "Wednesday"]
    assert place["params"]["latitude"] == 55.68 and "temperature_unit" not in place["params"]


def test_cached_and_an_old_forecast_beats_none(fresh, place):
    client = admin_client()
    _ask(client)
    _ask(client)
    assert place["calls"] == 1
    key = next(iter(agent_routes._cache))
    agent_routes._cache[key] = (-1e9, agent_routes._cache[key][1])  # expired
    place["fail"] = True
    assert _ask(client).json()["payload"]["now"]["temp"] == 9  # stale but there
    agent_routes._cache.clear()
    payload = _ask(client).json()["payload"]
    assert payload["unavailable"] is True and "isn't answering" in payload["message"]


def test_no_place_says_where_to_set_it(fresh, place):
    place["place"] = (None, None, "", "metric")
    payload = _ask(admin_client()).json()["payload"]
    assert payload["configured"] is False and "Car Thing → Look → Weather location" in payload["message"]
    assert place["calls"] == 0


def test_imperial_units_follow_the_car_thing(fresh, place):
    place["place"] = (40.7, -74.0, "New York", "imperial")
    payload = _ask(admin_client()).json()["payload"]
    assert place["params"]["temperature_unit"] == "fahrenheit" and payload["units"] == {"temperature": "°F", "wind": "mph"}


def test_unknown_tool(fresh, place):
    assert _ask(admin_client(), tool="get_secrets").status_code == 400
