"""Music mini-app (F7): Spotify Connect, server-side in the panel service.

Notify runs the sign-in and keeps the refresh token in the vault. This module turns it into
access tokens (memory only), reads what's playing and sends the commands. No audio goes through
the Pi: the speakers stream from Spotify themselves.

Polling is driven by the device: each request says how old the cached state may be
(Now Playing 3 s, a widget 10 s, the screensaver 30 s), so nothing calls Spotify while no music
is on screen. Artwork is fetched once from Spotify's CDN and served from a small disk cache,
because the device can't reach the internet.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from stonepi_display.spotify import API_URL, SpotifyAuthError, TokenSource, uri_kind

from app.config import DATA_DIR

logger = logging.getLogger("carthing.music")

ART_HOST = "https://i.scdn.co/"
ART_TARGET_PX = 300
ART_KEEP = 150
_REPEAT_NEXT = {"off": "context", "context": "track", "track": "off"}


class MusicError(Exception):
    """A command that didn't work. ``str()`` is shown on the panel as a toast."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _art_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _pick_image(images: Any) -> str:
    """The image nearest 300 px (Spotify sends 640, 300 and 64)."""
    rows = [i for i in images or [] if isinstance(i, dict) and str(i.get("url") or "").startswith(ART_HOST)]
    if not rows:
        return ""
    rows.sort(key=lambda i: abs(int(i.get("width") or ART_TARGET_PX) - ART_TARGET_PX))
    return str(rows[0]["url"])


def _device(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    volume = raw.get("volume_percent")
    return {
        "id": str(raw.get("id")),
        "name": str(raw.get("name") or "Speaker")[:60],
        "type": str(raw.get("type") or "")[:20],
        "volume": int(volume) if isinstance(volume, (int, float)) and not isinstance(volume, bool) else None,
        "supports_volume": bool(raw.get("supports_volume", volume is not None)),
        "active": bool(raw.get("is_active")),
        "restricted": bool(raw.get("is_restricted")),
    }


def empty_state(**extra: Any) -> dict[str, Any]:
    state = {
        "connected": False,
        "active": False,
        "playing": False,
        "kind": "",
        "uri": "",
        "track": "",
        "artist": "",
        "album": "",
        "art": "",
        "duration_ms": 0,
        "progress_ms": 0,
        "at_ms": int(time.time() * 1000),
        "device": None,
        "shuffle": False,
        "repeat": "off",
        "error": "",
        "stale": False,
    }
    state.update(extra)
    return state


class Player:
    def __init__(
        self,
        tokens: TokenSource | None = None,
        *,
        client: httpx.Client | None = None,
        art_dir: Path | None = None,
        clock=time.monotonic,
    ):
        self.tokens = tokens or TokenSource()
        self._client = client or httpx.Client(timeout=6.0)
        self._art_dir = art_dir or (DATA_DIR / "music-art")
        self._clock = clock
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._state_at = -1e9
        self._devices: list[dict[str, Any]] | None = None
        self._devices_at = -1e9
        self._backoff_until = 0.0
        self._art_urls: dict[str, str] = {}

    # ── Spotify calls ────────────────────────────────────────────────────
    def connected(self) -> bool:
        return self.tokens.connected()

    def _call(self, method: str, path: str, *, params: dict | None = None, body: Any = None, retry: bool = True) -> Any:
        if self._clock() < self._backoff_until:
            raise MusicError("rate", "Spotify asked StonePi to slow down. Try again in a moment.")
        try:
            token = self.tokens.access_token()
        except SpotifyAuthError as exc:
            raise MusicError(exc.code, str(exc)) from exc
        try:
            response = self._client.request(
                method, f"{API_URL}{path}", params=params, json=body, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise MusicError("network", "Spotify isn't reachable right now.") from exc
        status = response.status_code
        if status == 401 and retry:
            self.tokens.drop_access()
            return self._call(method, path, params=params, body=body, retry=False)
        if status == 429:
            try:
                wait = float(response.headers.get("Retry-After") or 5)
            except ValueError:
                wait = 5.0
            self._backoff_until = self._clock() + max(1.0, min(600.0, wait))
            raise MusicError("rate", "Spotify asked StonePi to slow down. Try again in a moment.")
        if status == 403:
            raise MusicError("premium", "Spotify refused. Controls need Premium, and some speakers can't be controlled remotely.")
        if status == 404:
            reason = ""
            try:
                reason = str(((response.json() or {}).get("error") or {}).get("reason") or "")
            except (ValueError, AttributeError):
                pass
            if reason == "NO_ACTIVE_DEVICE" or path.startswith("/me/player"):
                raise MusicError("no_device", "Nothing is playing. Pick a speaker under Speakers first.")
            raise MusicError("not_found", "Spotify couldn't find that.")
        if status >= 400:
            raise MusicError("error", f"Spotify said no ({status}).")
        if status == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    # ── State ────────────────────────────────────────────────────────────
    def _parse(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            return empty_state(connected=True)
        item = body.get("item") if isinstance(body.get("item"), dict) else {}
        kind = str(body.get("currently_playing_type") or item.get("type") or "")
        if kind == "episode":
            show = item.get("show") if isinstance(item.get("show"), dict) else {}
            artist, album = str(show.get("name") or ""), ""
            image = _pick_image(item.get("images")) or _pick_image(show.get("images"))
        else:
            album_raw = item.get("album") if isinstance(item.get("album"), dict) else {}
            artist = ", ".join(str(a.get("name") or "") for a in item.get("artists") or [] if isinstance(a, dict))
            album = str(album_raw.get("name") or "")
            image = _pick_image(album_raw.get("images"))
        art = ""
        if image:
            key = _art_key(image)
            with self._lock:
                if len(self._art_urls) > 400:
                    self._art_urls.clear()
                self._art_urls[key] = image
            art = f"art/{key}"
        device = _device(body.get("device"))
        title = str(item.get("name") or ("Advert" if kind == "ad" else ""))
        return empty_state(
            connected=True,
            active=device is not None,
            playing=bool(body.get("is_playing")),
            kind=kind,
            uri=str(item.get("uri") or ""),
            track=title[:120],
            artist=artist[:120],
            album=album[:120],
            art=art,
            duration_ms=int(item.get("duration_ms") or 0),
            progress_ms=int(body.get("progress_ms") or 0),
            device=device,
            shuffle=bool(body.get("shuffle_state")),
            repeat=str(body.get("repeat_state") or "off"),
        )

    def now_playing(self, max_age: float = 3.0) -> dict[str, Any]:
        """What's playing, at most ``max_age`` seconds old. Never raises."""
        if not self.connected():
            return empty_state(error="Connect Spotify in Notify → Displays → Car Thing.")
        now = self._clock()
        with self._lock:
            cached, age = self._state, now - self._state_at
        if cached is not None and age < max_age:
            return cached
        if not self._fetch_lock.acquire(blocking=cached is None):
            return cached  # another request is already asking Spotify
        try:
            state = self._parse(self._call("GET", "/me/player", params={"additional_types": "episode"}))
        except MusicError as exc:
            if cached is not None and exc.code in {"rate", "network"}:
                state = {**cached, "stale": True}
            else:
                state = empty_state(connected=exc.code != "not_connected", error=str(exc))
        finally:
            self._fetch_lock.release()
        with self._lock:
            self._state, self._state_at = state, self._clock()
        return state

    def signature(self, max_age: float) -> str:
        """Changes when the track, play state or speaker changes (the device then re-renders)."""
        if not self.connected():
            return ""
        st = self.now_playing(max_age)
        device = (st.get("device") or {}).get("id") or ""
        return f"{st['uri']}|{st['playing']}|{device}|{bool(st['error'])}"

    def _stale(self) -> None:
        with self._lock:
            self._state_at = -1e9

    def devices(self, max_age: float = 10.0) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            if self._devices is not None and now - self._devices_at < max_age:
                return self._devices
        body = self._call("GET", "/me/player/devices") or {}
        rows = [d for d in (_device(raw) for raw in body.get("devices") or []) if d]
        with self._lock:
            self._devices, self._devices_at = rows, self._clock()
        return rows

    def playlists(self) -> list[dict[str, str]]:
        body = self._call("GET", "/me/playlists", params={"limit": 50}) or {}
        out = []
        for row in body.get("items") or []:
            if isinstance(row, dict) and row.get("uri"):
                out.append({"name": str(row.get("name") or "Playlist")[:80], "uri": str(row["uri"])})
        return out

    # ── Commands ─────────────────────────────────────────────────────────
    def _resolve(self, wanted: dict | None) -> str:
        """A saved speaker's current id: by id, else by name (ids can change after a reset)."""
        wanted = wanted or {}
        if not wanted.get("id") and not wanted.get("name"):
            return ""
        rows = self.devices(max_age=5.0)
        for row in rows:
            if wanted.get("id") and row["id"] == wanted["id"]:
                return row["id"]
        for row in rows:
            if wanted.get("name") and row["name"].lower() == str(wanted["name"]).lower():
                return row["id"]
        name = wanted.get("name") or "That speaker"
        raise MusicError("speaker_missing", f"{name} isn't available. Open Spotify on it once to wake it.")

    def play_pause(self, music_cfg: dict) -> None:
        st = self.now_playing(max_age=5.0)
        if st["active"]:
            self._call("PUT", "/me/player/pause" if st["playing"] else "/me/player/play")
        else:
            device = self._resolve(music_cfg.get("default_device"))
            if not device:
                raise MusicError("no_device", "Nothing is playing. Pick a speaker under Speakers first.")
            self._call("PUT", "/me/player", body={"device_ids": [device], "play": True})
        self._stale()

    def skip(self, forward: bool) -> None:
        self._call("POST", "/me/player/next" if forward else "/me/player/previous")
        self._stale()

    def volume(self, delta: int) -> int:
        st = self.now_playing(max_age=5.0)
        device = st.get("device") or {}
        if not device:
            raise MusicError("no_device", "Nothing is playing.")
        if not device.get("supports_volume") or device.get("volume") is None:
            raise MusicError("no_volume", f"{device.get('name') or 'This speaker'} doesn't let StonePi set its volume.")
        level = max(0, min(100, int(device["volume"]) + int(delta)))
        self._call("PUT", "/me/player/volume", params={"volume_percent": level, "device_id": device["id"]})
        with self._lock:
            if self._state is st and isinstance(st.get("device"), dict):
                st["device"]["volume"] = level  # the next read confirms it
        return level

    def shuffle(self) -> None:
        st = self.now_playing(max_age=5.0)
        self._call("PUT", "/me/player/shuffle", params={"state": "false" if st["shuffle"] else "true"})
        self._stale()

    def repeat(self) -> None:
        st = self.now_playing(max_age=5.0)
        self._call("PUT", "/me/player/repeat", params={"state": _REPEAT_NEXT.get(st["repeat"], "off")})
        self._stale()

    def save(self) -> str:
        st = self.now_playing(max_age=5.0)
        if uri_kind(st["uri"]) not in ("track", "episode"):
            raise MusicError("nothing", "Nothing to save right now.")
        self._call("PUT", "/me/library", params={"uris": st["uri"]})
        return st["track"]

    def transfer(self, device_id: str) -> None:
        self._call("PUT", "/me/player", body={"device_ids": [device_id], "play": True})
        with self._lock:
            self._devices_at = -1e9
        self._stale()

    def preset(self, number: int, music_cfg: dict) -> str:
        presets = music_cfg.get("presets") or []
        preset = presets[number - 1] if 1 <= number <= len(presets) else {}
        uri = str(preset.get("uri") or "")
        if not uri:
            raise MusicError("no_preset", f"Music preset {number} isn't set. Choose one in Notify → Car Thing → Music.")
        device = self._resolve(preset.get("device"))
        if not device and not self.now_playing(max_age=5.0)["active"]:
            device = self._resolve(music_cfg.get("default_device"))
            if not device:
                raise MusicError("no_device", "Nothing is playing. Pick a speaker under Speakers first.")
        body = {"uris": [uri]} if uri_kind(uri) in ("track", "episode") else {"context_uri": uri}
        self._call("PUT", "/me/player/play", params={"device_id": device} if device else None, body=body)
        self._stale()
        return str(preset.get("name") or "")

    # ── Artwork ──────────────────────────────────────────────────────────
    def art_path(self, key: str) -> Path | None:
        """Cached artwork for ``key`` (fetched from Spotify's CDN the first time)."""
        if len(key) != 16 or not all(c in "0123456789abcdef" for c in key):
            return None
        path = self._art_dir / f"{key}.jpg"
        if path.is_file():
            return path
        with self._lock:
            url = self._art_urls.get(key)
        if not url:
            return None
        try:
            response = self._client.get(url, timeout=8.0)
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or not response.content or len(response.content) > 600_000:
            return None
        self._art_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(path)
        self._prune_art()
        return path

    def _prune_art(self) -> None:
        try:
            files = sorted(self._art_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for old in files[:-ART_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass

    # ── Status for Notify ────────────────────────────────────────────────
    def reset(self) -> None:
        self.tokens.invalidate()
        with self._lock:
            self._state, self._state_at = None, -1e9
            self._devices, self._devices_at = None, -1e9
            self._backoff_until = 0.0


player = Player()
# S1 name, kept for the token endpoints.
tokens = player.tokens


def check() -> dict:
    """Notify's Test connection button: can this service get an access token right now?"""
    try:
        player.tokens.access_token()
    except SpotifyAuthError as exc:
        return {"ok": False, "code": exc.code, "message": str(exc)}
    return {"ok": True}


def reset() -> None:
    """Notify connected, reconnected or disconnected: forget tokens and state, reread the vault."""
    player.reset()


def status() -> dict:
    return player.tokens.status()


def catalog() -> dict:
    """Speakers and playlists for Notify's Music tab (Notify never holds a Spotify token itself)."""
    if not player.connected():
        return {"ok": False, "connected": False, "message": "Spotify isn't connected.", "devices": [], "playlists": []}
    try:
        return {"ok": True, "connected": True, "devices": player.devices(max_age=0), "playlists": player.playlists()}
    except MusicError as exc:
        return {"ok": False, "connected": True, "message": str(exc), "devices": [], "playlists": []}
