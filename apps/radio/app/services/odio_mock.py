from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR, env

_lock = threading.RLock()
_state: dict[str, Any] | None = None


def _fixture_path() -> Path:
    return ROOT_DIR / "app" / "fixtures" / "mock_players.json"


def _load() -> dict[str, Any]:
    global _state
    with _lock:
        if _state is not None:
            return _state
        path = _fixture_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"players": [], "audio": {"volume": env.default_volume, "mute": False}}
        _state = data
        return _state


def reset() -> None:
    global _state
    with _lock:
        _state = None


def list_players() -> list[dict[str, Any]]:
    return copy.deepcopy(_load().get("players") or [])


def _mpd() -> dict[str, Any]:
    players = _load().setdefault("players", [])
    for player in players:
        bus = str(player.get("bus_name") or "").lower()
        if "mpd" in bus:
            return player
    player = {
        "bus_name": "org.mpris.MediaPlayer2.mpd",
        "identity": "Music Player Daemon",
        "playback_status": "Stopped",
        "volume": env.default_volume,
        "metadata": {},
        "capabilities": {
            "can_play": True,
            "can_pause": True,
            "can_go_next": False,
            "can_go_previous": False,
            "can_seek": False,
            "can_control": True,
        },
        "tracklist_supported": True,
    }
    players.append(player)
    return player


def now_playing() -> dict[str, Any]:
    with _lock:
        player = _mpd()
        meta = player.get("metadata") or {}
        status = str(player.get("playback_status") or "Stopped")
        volume = float(player.get("volume") if player.get("volume") is not None else env.default_volume)
        title = str(meta.get("xesam:title") or "")
        artist = str(meta.get("xesam:album") or meta.get("xesam:artist") or "")
        return {
            "ok": True,
            "connected": True,
            "source": "radio" if title or status.lower() != "stopped" else "none",
            "playback_status": status,
            "playing": status.lower() == "playing",
            "paused": status.lower() == "paused",
            "title": title,
            "artist": artist,
            "art_url": str(meta.get("mpris:artUrl") or ""),
            "volume": volume,
            "player": {
                "bus_name": player.get("bus_name") or "",
                "identity": player.get("identity") or "",
                "capabilities": player.get("capabilities") or {},
            },
            "players": [
                {
                    "bus_name": p.get("bus_name"),
                    "identity": p.get("identity"),
                    "playback_status": p.get("playback_status"),
                }
                for p in (_load().get("players") or [])
            ],
            "mock": True,
        }


def ping() -> dict[str, Any]:
    return {
        "ok": True,
        "reachable": True,
        "url": "mock://odio",
        "status": 200,
        "mock": True,
    }


def play_uri(uri: str, *, title: str = "") -> dict[str, Any]:
    with _lock:
        player = _mpd()
        player["playback_status"] = "Playing"
        player["metadata"] = {
            "xesam:title": title or uri.rsplit("/", 1)[-1] or "Radio",
            "xesam:album": "Digital radio",
            "mpris:artUrl": "",
            "xesam:url": uri,
        }
        player["volume"] = float(player.get("volume") if player.get("volume") is not None else env.default_volume)
        state = now_playing()
        state["station_url"] = uri
        return state


def transport(action: str) -> dict[str, Any]:
    action = (action or "").strip().lower()
    with _lock:
        player = _mpd()
        status = str(player.get("playback_status") or "Stopped").lower()
        if action == "play":
            player["playback_status"] = "Playing"
        elif action == "pause":
            player["playback_status"] = "Paused"
        elif action == "play_pause":
            player["playback_status"] = "Paused" if status == "playing" else "Playing"
        elif action == "stop":
            player["playback_status"] = "Stopped"
        elif action in {"next", "previous"}:
            pass
        else:
            raise RuntimeError(f"Unknown transport action: {action}")
        return now_playing()


def set_volume(level: float) -> dict[str, Any]:
    level = max(0.0, min(1.0, float(level)))
    with _lock:
        player = _mpd()
        player["volume"] = level
        audio = _load().setdefault("audio", {})
        audio["volume"] = level
        return now_playing()


def bump_volume(delta: float) -> dict[str, Any]:
    state = now_playing()
    return set_volume(float(state.get("volume") or env.default_volume) + delta)
