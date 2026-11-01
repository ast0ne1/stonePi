"""Radio mini-app: thin HTTP client to StonePi Radio (Odio) on loopback.

Playback goes through Radio's signed ``/api/internal/*`` (stonepi_auth.internal), never to
Odio directly; the presets come from its unsigned loopback ``/api/display``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("carthing.radio")

_CACHE_LOCK = threading.Lock()
_NOW: dict[str, tuple[float, dict[str, Any]]] = {}  # output → (when, state)


def empty_state(**extra: Any) -> dict[str, Any]:
    state = {
        "ok": False,
        "connected": False,
        "playing": False,
        "paused": False,
        "title": "",
        "artist": "",
        "art_url": "",
        "station_logo": "",
        "volume": 0.6,
        "source": "none",
        "playback_status": "Stopped",
        "presets": [],
        "error": "",
        # The station's bitrate and sample rate (stream headers via Radio); 0 = not known.
        "kbps": 0,
        "khz": 0,
    }
    state.update(extra)
    return state


def _base() -> str:
    from app.state import _app_port

    return f"http://127.0.0.1:{_app_port('radio') or 8016}"


def _headers(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    from stonepi_auth.internal import sign_internal_v2

    from app.state import session_secret

    secret = session_secret()
    # No secret only in solo dev, where Radio lets unsigned internal calls through.
    return sign_internal_v2(secret, method, path, body) if secret else {}


def _get(path: str, *, timeout: float = 4.0) -> dict[str, Any]:
    # Signed over the path alone: Radio (like every StonePi service) checks request.url.path,
    # so signing "?output=…" too made every call 403 on the Pi.
    headers = _headers("GET", path.split("?", 1)[0]) if path.startswith("/api/internal/") else {}
    with httpx.Client(base_url=_base(), timeout=timeout) as client:
        resp = client.get(path, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"Radio HTTP {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _post(path: str, body: dict[str, Any], *, timeout: float = 6.0) -> dict[str, Any]:
    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", **_headers("POST", path, raw)}
    with httpx.Client(base_url=_base(), timeout=timeout) as client:
        resp = client.post(path, content=raw, headers=headers)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        raise RuntimeError(str(data.get("error") or f"Radio HTTP {resp.status_code}"))
    return data if isinstance(data, dict) else {}


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def now_playing(max_age: float = 5.0, output: str = "stonepi") -> dict[str, Any]:
    """What's playing on this panel's Radio output (its "Play on": StonePi or a speaker)."""
    with _CACHE_LOCK:
        hit = _NOW.get(output)
        if hit is not None:
            age = time.monotonic() - hit[0]
            cached = hit[1]
            # Stream info is read in the background: don't keep a blank kbps/kHz answer for the
            # full TTL, or the Winamp skin stays empty until something else reloads the page.
            soft = 1.0 if cached.get("playing") and not (cached.get("kbps") or cached.get("khz")) else max_age
            if age <= min(max_age, soft):
                return dict(cached)
    try:
        data = _get(f"/api/internal/now?output={quote(output, safe='')}&info=1")
        display = _get("/api/display")
        state = empty_state(
            ok=bool(data.get("ok", True)),
            connected=bool(data.get("connected", True)),
            playing=bool(data.get("playing")),
            paused=bool(data.get("paused")),
            title=str(data.get("title") or ""),
            artist=str(data.get("artist") or ""),
            art_url=str(data.get("art_url") or ""),
            station_logo=str(data.get("station_logo") or ""),
            volume=float(data.get("volume") if data.get("volume") is not None else 0.6),
            source=str(data.get("source") or "none"),
            playback_status=str(data.get("playback_status") or "Stopped"),
            output_name=str(data.get("output_name") or ""),
            presets=list(display.get("presets") or [])[:4],
            kbps=_count(data.get("kbps")),
            khz=_count(data.get("khz")),
        )
    except Exception as exc:
        logger.debug("radio now_playing failed: %s", exc)
        state = empty_state(error=str(exc))
    with _CACHE_LOCK:
        _NOW[output] = (time.monotonic(), state)
    return dict(state)


def saver_now(output: str = "stonepi") -> dict[str, Any] | None:
    """What's on the panel's Radio output for the artwork screensaver: the station, its logo's
    address and (when the station sends it) the song. None when nothing plays or Radio is away."""
    try:
        data = _get(f"/api/internal/now?output={quote(output, safe='')}&song=1", timeout=8.0)
    except Exception as exc:
        logger.debug("radio saver_now failed: %s", exc)
        return None
    if not data.get("playing") or not data.get("title"):
        return None
    return {
        "title": str(data.get("title") or ""),
        "song": str(data.get("song") or ""),
        "logo": str(data.get("station_logo") or ""),
        "output_name": str(data.get("output_name") or ""),
    }


def logo(url: str) -> tuple[bytes, str] | None:
    """A station logo through Radio's signed /api/internal/art (the panel can't sign in to Radio)."""
    path = "/api/internal/art"
    try:
        with httpx.Client(base_url=_base(), timeout=8.0) as client:
            resp = client.get(path, params={"u": url}, headers=_headers("GET", path))
    except httpx.HTTPError as exc:
        logger.debug("radio logo failed: %s", exc)
        return None
    if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
        return None
    return resp.content, resp.headers["content-type"]


def invalidate() -> None:
    with _CACHE_LOCK:
        _NOW.clear()


def play(*, station_id: str = "", url: str = "", name: str = "", output: str = "stonepi") -> dict[str, Any]:
    state = _post("/api/internal/play", {"station_id": station_id, "url": url, "name": name, "output": output})
    invalidate()
    return state


def transport(action: str, *, output: str = "stonepi") -> dict[str, Any]:
    state = _post("/api/internal/transport", {"action": action, "output": output})
    invalidate()
    return state


def volume(*, level: float | None = None, delta: float | None = None, output: str = "stonepi") -> dict[str, Any]:
    body: dict[str, Any] = {"output": output}
    if delta is not None:
        body["delta"] = float(delta)
    else:
        body["level"] = float(level if level is not None else 0.5)
    state = _post("/api/internal/volume", body)
    invalidate()
    return state


def play_preset(config: dict[str, Any], number: int) -> dict[str, Any]:
    presets = (config.get("radio") or {}).get("presets") or []
    output = str((config.get("radio") or {}).get("output") or "stonepi")
    idx = max(1, int(number)) - 1
    if idx < 0 or idx >= len(presets):
        raise RuntimeError(f"No radio preset {number}")
    preset = presets[idx] if isinstance(presets[idx], dict) else {}
    station_id = str(preset.get("station_id") or "")
    url = str(preset.get("url") or "")
    name = str(preset.get("name") or f"Preset {number}")
    if not station_id and not url:
        # Fall back to Radio app favorites order from /api/display.
        now = now_playing(0, output)
        favs = now.get("presets") or []
        if idx >= len(favs):
            raise RuntimeError(f"Radio preset {number} is empty")
        fav = favs[idx]
        station_id = str(fav.get("id") or "")
        url = str(fav.get("stream_url") or "")
        name = str(fav.get("name") or name)
    return play(station_id=station_id, url=url, name=name, output=output)
