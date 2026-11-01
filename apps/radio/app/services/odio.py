from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.config import env

logger = logging.getLogger("radio.odio")

MPD_HINTS = ("mpd", "music player daemon")


class OdioError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _base() -> str:
    return (env.odio_api_url or "http://127.0.0.1:8018").rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base(), timeout=8.0)


def _player_path(bus_name: str) -> str:
    return quote(bus_name, safe="")


def list_players() -> list[dict[str, Any]]:
    with _client() as client:
        try:
            resp = client.get("/players")
        except httpx.HTTPError as exc:
            raise OdioError("Odio API is not reachable.") from exc
    if resp.status_code >= 400:
        raise OdioError(f"Odio returned {resp.status_code}", status=resp.status_code)
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("players"), list):
        return data["players"]
    return []


def pick_mpd_player(players: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    players = players if players is not None else list_players()
    for player in players:
        bus = str(player.get("bus_name") or "").lower()
        identity = str(player.get("identity") or "").lower()
        if any(h in bus or h in identity for h in MPD_HINTS):
            return player
    for player in players:
        if player.get("tracklist_supported"):
            return player
    return players[0] if players else None


def pick_active_player(players: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    players = players if players is not None else list_players()
    playing = [p for p in players if str(p.get("playback_status") or "").lower() == "playing"]
    if playing:
        mpd = pick_mpd_player(playing)
        return mpd or playing[0]
    paused = [p for p in players if str(p.get("playback_status") or "").lower() == "paused"]
    if paused:
        return pick_mpd_player(paused) or paused[0]
    return pick_mpd_player(players)


def _meta(player: dict[str, Any] | None) -> dict[str, str]:
    raw = (player or {}).get("metadata") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None}


def now_playing() -> dict[str, Any]:
    players = list_players()
    player = pick_active_player(players)
    meta = _meta(player)
    status = str((player or {}).get("playback_status") or "Stopped")
    volume = (player or {}).get("volume")
    if volume is None:
        volume = audio_server().get("volume", env.default_volume)
    try:
        vol = float(volume)
    except (TypeError, ValueError):
        vol = float(env.default_volume)
    source = "none"
    if player:
        identity = str(player.get("identity") or "")
        bus = str(player.get("bus_name") or "")
        if any(h in bus.lower() or h in identity.lower() for h in MPD_HINTS):
            source = "radio"
        elif "spotify" in bus.lower() or "spotify" in identity.lower():
            source = "spotify"
        elif "shairport" in bus.lower() or "airplay" in identity.lower():
            source = "airplay"
        else:
            source = identity or bus or "player"
    return {
        "ok": True,
        "connected": True,
        "source": source,
        "playback_status": status,
        "playing": status.lower() == "playing",
        "paused": status.lower() == "paused",
        "title": meta.get("xesam:title") or meta.get("title") or "",
        "artist": meta.get("xesam:artist") or meta.get("xesam:album") or "",
        "art_url": meta.get("mpris:artUrl") or meta.get("artUrl") or "",
        "volume": vol,
        "player": {
            "bus_name": (player or {}).get("bus_name") or "",
            "identity": (player or {}).get("identity") or "",
            "capabilities": (player or {}).get("capabilities") or {},
        },
        "players": [
            {
                "bus_name": p.get("bus_name"),
                "identity": p.get("identity"),
                "playback_status": p.get("playback_status"),
            }
            for p in players
        ],
    }


def audio_server() -> dict[str, Any]:
    with _client() as client:
        try:
            resp = client.get("/audio/server")
        except httpx.HTTPError:
            return {"volume": env.default_volume, "mute": False}
    if resp.status_code >= 400:
        return {"volume": env.default_volume, "mute": False}
    data = resp.json()
    return data if isinstance(data, dict) else {"volume": env.default_volume, "mute": False}


def ping() -> dict[str, Any]:
    with _client() as client:
        try:
            resp = client.get("/server")
        except httpx.HTTPError as exc:
            return {"ok": False, "reachable": False, "url": _base(), "error": str(exc)}
    return {
        "ok": resp.status_code < 400,
        "reachable": resp.status_code < 400,
        "url": _base(),
        "status": resp.status_code,
    }


def play_uri(uri: str, *, title: str = "") -> dict[str, Any]:
    uri = (uri or "").strip()
    if not uri:
        raise OdioError("No stream URL.")
    player = pick_mpd_player()
    if not player or not player.get("bus_name"):
        raise OdioError("No MPD player found on Odio. Is MPD running?")
    bus = str(player["bus_name"])
    path = _player_path(bus)
    with _client() as client:
        try:
            # Prefer replace-style add when supported; fall back to add + play.
            add = client.post(
                f"/players/{path}/tracklist/add",
                json={"uri": uri, "after_track": "", "set_as_current": True},
            )
            if add.status_code == 404:
                raise OdioError("Odio player does not support tracklist add.", status=404)
            if add.status_code >= 400:
                # Some builds reject set_as_current; retry simpler body.
                add = client.post(
                    f"/players/{path}/tracklist/add",
                    json={"uri": uri, "after_track": "", "set_as_current": False},
                )
            if add.status_code >= 400:
                raise OdioError(f"Could not queue stream ({add.status_code}).", status=add.status_code)
            play = client.post(f"/players/{path}/play")
            if play.status_code >= 400:
                raise OdioError(f"Could not start playback ({play.status_code}).", status=play.status_code)
        except httpx.HTTPError as exc:
            raise OdioError("Odio API is not reachable.") from exc
    state = now_playing()
    if title and not state.get("title"):
        state["title"] = title
    state["station_url"] = uri
    return state


def transport(action: str) -> dict[str, Any]:
    action = (action or "").strip().lower()
    allowed = {"play", "pause", "play_pause", "stop", "next", "previous"}
    if action not in allowed:
        raise OdioError(f"Unknown transport action: {action}")
    player = pick_active_player() or pick_mpd_player()
    if not player or not player.get("bus_name"):
        raise OdioError("No active Odio player.")
    path = _player_path(str(player["bus_name"]))
    with _client() as client:
        try:
            resp = client.post(f"/players/{path}/{action}")
        except httpx.HTTPError as exc:
            raise OdioError("Odio API is not reachable.") from exc
    if resp.status_code >= 400:
        raise OdioError(f"Transport failed ({resp.status_code}).", status=resp.status_code)
    return now_playing()


def set_volume(level: float) -> dict[str, Any]:
    level = max(0.0, min(1.0, float(level)))
    player = pick_active_player() or pick_mpd_player()
    with _client() as client:
        try:
            if player and player.get("bus_name"):
                path = _player_path(str(player["bus_name"]))
                resp = client.post(f"/players/{path}/volume", json={"volume": level})
                if resp.status_code < 400:
                    return now_playing()
            resp = client.post("/audio/server/volume", json={"volume": level})
        except httpx.HTTPError as exc:
            raise OdioError("Odio API is not reachable.") from exc
    if resp.status_code >= 400:
        raise OdioError(f"Volume failed ({resp.status_code}).", status=resp.status_code)
    state = now_playing()
    state["volume"] = level
    return state


def bump_volume(delta: float) -> dict[str, Any]:
    state = now_playing()
    current = float(state.get("volume") or env.default_volume)
    return set_volume(current + delta)
