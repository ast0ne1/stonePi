"""Agent reads for AgentLink (Pebble now, MCP later): one signed internal endpoint.

    POST /api/internal/agent   {"tool": "...", "user": "<platform user id>", "params": {...}}
    → {"ok": true, "payload": {...}} | {"ok": false, "error": "...", "message": "..."}

Signed with stonepi_auth.internal v2; nginx never routes /notify/api/internal/
(stonepi-public-deny-display.conf). Notify holds the household's weather place (Car Thing →
Look → Weather location), so ``get_weather`` answers from it: the same for everyone, nothing personal.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("notify.agent")
router = APIRouter()

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_S = 10 * 60
NEXT_HOURS = 4  # every 3 hours from now
NEXT_DAYS = 3  # after today
_client = httpx.Client(timeout=6.0)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _secret() -> str:
    from app.people import session_secret

    return session_secret()


def _place() -> tuple[float | None, float | None, str, str]:
    from stonepi_display import carthing

    config = carthing.active_config()
    weather = config.get("weather") or {}
    units = (config.get("look") or {}).get("units") or "metric"
    return weather.get("lat"), weather.get("lon"), str(weather.get("label") or ""), units


def _round(value: Any) -> int | None:
    return round(value) if isinstance(value, (int, float)) else None


def _at(values: list, i: int) -> Any:
    return values[i] if i < len(values) else None


def _forecast(lat: float, lon: float, units: str) -> dict[str, Any]:
    from stonepi_display.weather import wmo

    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
        "hourly": "temperature_2m,weather_code,is_day,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": NEXT_DAYS + 1,
        "forecast_hours": NEXT_HOURS * 3 + 1,
        "timezone": "auto",
    }
    if units == "imperial":
        params["temperature_unit"] = "fahrenheit"
        params["wind_speed_unit"] = "mph"
    response = _client.get(FORECAST_URL, params=params)
    response.raise_for_status()
    data = response.json()

    current = data.get("current") or {}
    hourly = data.get("hourly") or {}
    daily = data.get("daily") or {}
    _icon, now_label = wmo(current.get("weather_code") or 0, bool(current.get("is_day", 1)))
    hours = []
    times = hourly.get("time") or []
    for i in range(3, len(times), 3):
        if len(hours) >= NEXT_HOURS:
            break
        is_day = _at(hourly.get("is_day") or [], i)
        _icon, label = wmo(_at(hourly.get("weather_code") or [], i) or 0, True if is_day is None else bool(is_day))
        hours.append({
            "time": str(times[i])[11:16],
            "temp": _round(_at(hourly.get("temperature_2m") or [], i)),
            "conditions": label.lower(),
            "rain_chance_pct": _round(_at(hourly.get("precipitation_probability") or [], i)),
        })
    days = []
    for i, day in enumerate(daily.get("time") or []):
        _icon, label = wmo(_at(daily.get("weather_code") or [], i) or 0)
        try:
            name = "today" if i == 0 else date.fromisoformat(str(day)).strftime("%A")
        except ValueError:
            name = str(day)
        days.append({
            "day": name,
            "high": _round(_at(daily.get("temperature_2m_max") or [], i)),
            "low": _round(_at(daily.get("temperature_2m_min") or [], i)),
            "conditions": label.lower(),
            "rain_chance_pct": _round(_at(daily.get("precipitation_probability_max") or [], i)),
        })
    return {
        "now": {
            "temp": _round(current.get("temperature_2m")),
            "feels_like": _round(current.get("apparent_temperature")),
            "conditions": now_label.lower(),
            "wind": _round(current.get("wind_speed_10m")),
        },
        "today": days[0] if days else {},
        "next_hours": hours,
        "next_days": days[1:NEXT_DAYS + 1],
    }


def _get_weather(_user_id: str, _params: dict) -> dict:
    lat, lon, label, units = _place()
    if lat is None or lon is None:
        return {"configured": False,
                "message": "No weather place is set. An admin can pick one in Notify → Car Thing → Look → Weather location."}
    key = f"{lat}:{lon}:{units}"
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_S:
        forecast = hit[1]
    else:
        try:
            forecast = _forecast(float(lat), float(lon), units)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Weather for Pebble failed: %s", exc)
            if hit:
                forecast = hit[1]  # an older forecast beats none
            else:
                return {"configured": True, "place": label, "unavailable": True,
                        "message": "The weather service isn't answering right now."}
        else:
            with _cache_lock:
                _cache[key] = (now, forecast)
    return {
        "configured": True,
        "place": label,
        "units": {"temperature": "°F" if units == "imperial" else "°C", "wind": "mph" if units == "imperial" else "km/h"},
        "checked": datetime.now().strftime("%H:%M"),
        **forecast,
    }


TOOLS = {"get_weather": _get_weather}


@router.post("/api/internal/agent")
async def agent_read(request: Request):
    from starlette.concurrency import run_in_threadpool
    from stonepi_auth.internal import verify_internal_v2

    body = await request.body()
    if not verify_internal_v2(_secret(), "POST", request.url.path, request.headers, body):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Body must be JSON"}, status_code=400)
    tool = TOOLS.get(str(data.get("tool") or ""))
    user_id = str(data.get("user") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if tool is None or not user_id:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Unknown tool or no user"}, status_code=400)
    # The forecast is a network call: off the event loop.
    return {"ok": True, "payload": await run_in_threadpool(tool, user_id, params)}
