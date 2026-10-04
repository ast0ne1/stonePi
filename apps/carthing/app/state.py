"""Everything the panel reads, cached on the Pi so the device only ever gets small HTML.

- config + device auth material: Notify ``/api/internal/carthing/state`` (signed)
- System mini-app: Notify ``/api/internal/carthing/system`` (signed)
- other mini-apps: each app's loopback ``/api/display?items=N``
- weather: Open-Meteo
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.config import DATA_DIR, env

logger = logging.getLogger("carthing.state")

ITEMS_PER_FEED = 20
STATE_TTL = 10.0
SYSTEM_TTL = 30.0
FEED_TTL = 60.0
WEATHER_TTL = 900.0
_ASSET = re.compile(r"^[a-f0-9]{16}$")

_client = httpx.Client(timeout=3.0)
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return env.session_secret.strip()


def _cached(key: str, ttl: float, fetch, *, keep_stale: bool = True) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        value = fetch()
    except Exception:  # noqa: BLE001 - a dead source must never break the panel
        logger.debug("fetch %s failed", key, exc_info=True)
        value = None
    if value is None and hit and keep_stale:
        value = hit[1]
    with _lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _signed_get(base: str, path: str, params: dict | None = None) -> httpx.Response | None:
    from stonepi_auth.internal import sign_internal

    secret = session_secret()
    if not secret:
        return None
    url = f"{base.rstrip('/')}{path}"
    response = _client.get(url, params=params, headers=sign_internal(secret, "GET", path))
    return response if response.status_code == 200 else None


def _notify_json(path: str, params: dict | None = None) -> dict | None:
    response = _signed_get(env.notify_url, path, params)
    if response is None:
        return None
    data = response.json()
    return data if isinstance(data, dict) and data.get("ok") else None


def pair_device() -> str:
    """Ask Notify for a fresh device token (the old one stops working). Plaintext, or ""."""
    from stonepi_auth.internal import sign_internal

    secret = session_secret()
    if not secret:
        return ""
    path = "/api/internal/carthing/pair"
    try:
        response = _client.post(f"{env.notify_url.rstrip('/')}{path}", headers=sign_internal(secret, "POST", path))
        data = response.json() if response.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return ""
    clear_cache()  # the token hash in the cached state is now stale
    return str(data.get("token") or "") if isinstance(data, dict) else ""


def panel_state(*, draft: bool = False) -> dict | None:
    """Active (or, for Notify's preview, draft) config + token hash + PIN record."""
    if draft:
        # Drafts change as the admin edits: always fresh, never cached.
        return _notify_json("/api/internal/carthing/state", {"draft": "1"})
    return _cached("state", STATE_TTL, lambda: _notify_json("/api/internal/carthing/state"))


def system_feed() -> dict | None:
    return _cached("feed:system", SYSTEM_TTL, lambda: _notify_json("/api/internal/carthing/system"))


def _app_port(app_id: str) -> int | None:
    from stonepi_auth.catalog import app_by_id

    item = app_by_id(app_id)
    return int(item["port"]) if item and item.get("port") else None


def _fetch_app_feed(app_id: str) -> dict | None:
    port = _app_port(app_id)
    if not port:
        return None
    response = _client.get(f"http://127.0.0.1:{port}/api/display", params={"items": ITEMS_PER_FEED})
    if response.status_code != 200:
        return None
    data = response.json()
    if not isinstance(data, dict):
        return None
    return {
        "ok": bool(data.get("ok", True)),
        "card": data.get("card") if isinstance(data.get("card"), dict) else {},
        "items": [i for i in (data.get("items") or []) if isinstance(i, dict)][:ITEMS_PER_FEED],
        "refresh_s": data.get("refresh_s") or FEED_TTL,
    }


def feed(mini_app: str) -> dict:
    """``{card, items}`` for one mini-app; empty (not None) when the source is down."""
    if mini_app == "system":
        data = system_feed()
    else:
        data = _cached(f"feed:{mini_app}", FEED_TTL, lambda: _fetch_app_feed(mini_app))
    if not isinstance(data, dict):
        return {"ok": False, "card": {}, "items": []}
    return data


def feed_item(mini_app: str, item_id: str) -> dict | None:
    for item in feed(mini_app).get("items") or []:
        if str(item.get("id")) == str(item_id):
            return item
    return None


def feeds_rev(mini_apps: list[str]) -> str:
    """Changes when any shown feed (or the weather) changes — the device then re-renders."""
    blob = json.dumps([feed(m) for m in mini_apps] + [weather_cached()], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


# ── Weather ──────────────────────────────────────────────────────────────────

# WMO weather codes → (icon, label). Icons are the inline SVGs in templates/_icons.html.
_WMO: dict[int, tuple[str, str]] = {
    0: ("sun", "Clear"), 1: ("sun", "Mostly clear"), 2: ("partly", "Partly cloudy"), 3: ("cloud", "Overcast"),
    45: ("fog", "Fog"), 48: ("fog", "Fog"),
    51: ("rain", "Drizzle"), 53: ("rain", "Drizzle"), 55: ("rain", "Drizzle"), 56: ("rain", "Freezing drizzle"),
    57: ("rain", "Freezing drizzle"), 61: ("rain", "Light rain"), 63: ("rain", "Rain"), 65: ("rain", "Heavy rain"),
    66: ("rain", "Freezing rain"), 67: ("rain", "Freezing rain"), 71: ("snow", "Light snow"), 73: ("snow", "Snow"),
    75: ("snow", "Heavy snow"), 77: ("snow", "Snow grains"), 80: ("rain", "Showers"), 81: ("rain", "Showers"),
    82: ("rain", "Heavy showers"), 85: ("snow", "Snow showers"), 86: ("snow", "Snow showers"),
    95: ("storm", "Thunderstorm"), 96: ("storm", "Thunderstorm"), 99: ("storm", "Thunderstorm"),
}
_weather_key: dict[str, Any] = {"key": None}
FORECAST_HOURS = 5  # every 3 h from now, for the large Clock & weather widget
FORECAST_DAYS = 3  # after today
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _wmo(code: Any, is_day: bool = True) -> tuple[str, str]:
    try:
        icon, label = _WMO.get(int(code), ("cloud", "—"))
    except (TypeError, ValueError):
        icon, label = "cloud", "—"
    if icon == "sun" and not is_day:
        icon = "moon"
    return icon, label


def _forecast(data: dict) -> tuple[list[dict], list[dict]]:
    """Next hours (3-hourly) and days from the same Open-Meteo answer. Times are local ("auto")."""
    hourly = data.get("hourly") or {}
    hours: list[dict] = []
    times, temps = hourly.get("time") or [], hourly.get("temperature_2m") or []
    codes, day_flags = hourly.get("weather_code") or [], hourly.get("is_day") or []
    for i in range(3, min(len(times), len(temps), len(codes)), 3):
        if len(hours) >= FORECAST_HOURS:
            break
        if temps[i] is None:
            continue
        stamp = str(times[i])
        icon, _ = _wmo(codes[i], bool(day_flags[i]) if i < len(day_flags) else True)
        hour = int(stamp[11:13]) if stamp[11:13].isdigit() else 0
        hours.append({"hour": hour, "temp": round(temps[i]), "icon": icon})
    daily = data.get("daily") or {}
    days: list[dict] = []
    d_times = daily.get("time") or []
    d_max, d_min = daily.get("temperature_2m_max") or [], daily.get("temperature_2m_min") or []
    d_codes = daily.get("weather_code") or []
    for i in range(1, min(len(d_times), len(d_max), len(d_min), len(d_codes))):
        if len(days) >= FORECAST_DAYS:
            break
        if d_max[i] is None or d_min[i] is None:
            continue
        try:
            name = _DAY_NAMES[date.fromisoformat(str(d_times[i])).weekday()]
        except ValueError:
            name = ""
        icon, label = _wmo(d_codes[i])
        days.append({"day": name, "high": round(d_max[i]), "low": round(d_min[i]), "icon": icon, "label": label})
    return hours, days


def _fetch_weather(lat: float, lon: float, units: str) -> dict | None:
    if not env.weather_url:
        return None
    # One call for now + today + the forecast (the large Clock & weather widget reads the rest).
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code,is_day",
        "hourly": "temperature_2m,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "forecast_days": FORECAST_DAYS + 1,
        "forecast_hours": FORECAST_HOURS * 3 + 1,
        "timezone": "auto",
    }
    if units == "imperial":
        params["temperature_unit"] = "fahrenheit"
    response = _client.get(env.weather_url, params=params, timeout=6.0)
    if response.status_code != 200:
        return None
    data = response.json()
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    icon, label = _wmo(current.get("weather_code") or 0, bool(current.get("is_day", 1)))

    def _first(key: str) -> int | None:
        values = daily.get(key) or []
        return round(values[0]) if values and values[0] is not None else None

    temp = current.get("temperature_2m")
    hours, days = _forecast(data)
    return {
        "temp": round(temp) if temp is not None else None,
        "high": _first("temperature_2m_max"),
        "low": _first("temperature_2m_min"),
        "icon": icon,
        "label": label,
        "unit": "°F" if units == "imperial" else "°C",
        "hours": hours,
        "days": days,
    }


def weather(config: dict) -> dict | None:
    w = config.get("weather") or {}
    lat, lon = w.get("lat"), w.get("lon")
    if lat is None or lon is None:
        return None
    units = (config.get("look") or {}).get("units") or "metric"
    key = f"weather:{lat}:{lon}:{units}"
    _weather_key["key"] = key
    result = _cached(key, WEATHER_TTL, lambda: _fetch_weather(float(lat), float(lon), units))
    if result:
        result = {**result, "place": w.get("label") or ""}
    return result


def weather_cached() -> dict | None:
    key = _weather_key.get("key")
    if not key:
        return None
    with _lock:
        hit = _cache.get(key)
    return hit[1] if hit else None


# ── Background images ────────────────────────────────────────────────────────


def background_path(asset_id: str) -> Path | None:
    """Uploaded background, fetched once from Notify and kept on disk."""
    if not _ASSET.match(asset_id or ""):
        return None
    folder = Path(DATA_DIR) / "assets"
    path = folder / f"{asset_id}.jpg"
    if path.is_file():
        return path
    response = _signed_get(env.notify_url, f"/api/internal/carthing/asset/{asset_id}")
    if response is None or not response.content:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(response.content)
    tmp.replace(path)
    return path


# ── Restart (Dashboard owns service control) ─────────────────────────────────


def restart_unit(unit: str) -> dict:
    from stonepi_auth.internal import sign_internal

    secret = session_secret()
    if not secret:
        return {"ok": False, "message": "StonePi sign-in isn't set up."}
    path = "/api/internal/units/restart"
    try:
        response = _client.post(
            f"{env.dashboard_url.rstrip('/')}{path}",
            json={"unit": unit, "source": "carthing"},
            headers=sign_internal(secret, "POST", path),
            timeout=40.0,
        )
        data = response.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "message": "Dashboard didn't answer."}
    if not isinstance(data, dict):
        return {"ok": False, "message": "Unexpected answer from Dashboard."}
    return {"ok": bool(data.get("ok")), "message": str(data.get("message") or "")[:160]}


class PinGuard:
    """Wrong-PIN counter with lockout. In memory: a restart of the service resets it."""

    def __init__(self) -> None:
        self.failures = 0
        self.locked_until = 0.0
        self._lock = threading.Lock()

    def locked_for(self) -> int:
        return max(0, int(self.locked_until - time.monotonic()))

    def record(self, ok: bool, *, max_attempts: int, lockout_s: int) -> None:
        with self._lock:
            if ok:
                self.failures = 0
                return
            self.failures += 1
            if self.failures >= max_attempts:
                self.failures = 0
                self.locked_until = time.monotonic() + lockout_s


pin_guard = PinGuard()


class DeviceSeen:
    """When the device last checked in (Notify's Device tab shows it)."""

    def __init__(self) -> None:
        self.at: float | None = None
        self.wall: str | None = None

    def mark(self) -> None:
        from datetime import datetime, timezone

        self.at = time.monotonic()
        self.wall = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def connected(self) -> bool:
        return self.at is not None and time.monotonic() - self.at < 60


device_seen = DeviceSeen()
