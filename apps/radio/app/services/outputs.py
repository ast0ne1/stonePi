"""Where Radio plays (radio-outputs.md §1): This device, StonePi (Odio on the Pi) or a speaker.

Output ids: ``browser`` (the page's own player; the server only says what to play),
``stonepi`` (Odio / MPD, Radio's original path) and ``dlna:<udn>`` (a speaker from
``dlna``). Everything that plays, stops or turns the volume goes through here, so the web
pages and the Car Thing (``/api/internal/*``) behave the same.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

from app import db
from app.config import env
from app.services import dlna, streams
from app.services.backend import client as odio_client

BROWSER = "browser"
STONEPI = "stonepi"
DLNA_PREFIX = "dlna:"
PREF_KEY = "output"


class OutputError(RuntimeError):
    """Playing on that output didn't work (shown to the person as-is)."""


def speaker_id(udn: str) -> str:
    return f"{DLNA_PREFIX}{udn}"


def _speaker(output_id: str) -> dict | None:
    if not output_id.startswith(DLNA_PREFIX):
        return None
    return db.get_speaker(output_id[len(DLNA_PREFIX):])


def list_outputs(*, browser: bool) -> list[dict[str, Any]]:
    """The picker's rows: This device (web only), then speakers, then StonePi."""
    now = time.time()
    rows: list[dict[str, Any]] = []
    if browser:
        rows.append({"id": BROWSER, "kind": "browser", "name": "This device", "detail": "Plays in this browser"})
    for spk in db.list_speakers(now):
        stale = now - spk["last_seen"] > dlna.REFRESH_EVERY_S + 60
        rows.append(
            {
                "id": speaker_id(spk["udn"]),
                "kind": "speaker",
                "name": spk["shown_name"],
                "detail": spk["model"] or "Speaker",
                "stale": stale,
                "last_seen": spk["last_seen"],
            }
        )
    rows.append({"id": STONEPI, "kind": "stonepi", "name": "StonePi", "detail": "The Pi's own audio out (Odio)"})
    return rows


def name_of(output_id: str) -> str:
    if output_id == BROWSER:
        return "This device"
    if output_id == STONEPI:
        return "StonePi"
    spk = _speaker(output_id)
    return spk["shown_name"] if spk else "A speaker that's gone"


def valid(output_id: str, *, browser: bool) -> bool:
    if output_id == BROWSER:
        return browser
    if output_id == STONEPI:
        return True
    return _speaker(output_id) is not None


def chosen(owner: str) -> str:
    """This person's output on the web: their last choice, else This device."""
    pick = db.get_person_pref(owner, PREF_KEY, BROWSER)
    return pick if valid(pick, browser=True) else BROWSER


def choose(owner: str, output_id: str) -> None:
    if not valid(output_id, browser=True):
        raise OutputError("That speaker isn't on the network any more.")
    db.set_person_pref(owner, PREF_KEY, output_id)


# -- Playing ----------------------------------------------------------------------------------------


def browser_source(target: dict, *, https_page: bool) -> dict[str, Any]:
    """What the page's player loads: the stream, or the relay for http on an https page."""
    try:
        found = streams.resolve(target["stream_url"])
    except streams.NotAStream as exc:
        raise OutputError(str(exc)) from exc
    src = found["url"]
    if not found["hls"] and https_page and urlsplit(src).scheme == "http":
        src = streams.relay_path(src)
    return {
        "src": src,
        "hls": found["hls"],
        "mime": found["mime"],
        "name": target.get("name") or "Radio",
        "station_url": target["stream_url"],
        "favicon": target.get("favicon") or "",
    }


def check_playable(output_id: str, target: dict) -> None:
    """Raise OutputError if ``target`` can't go to this output, before switching."""
    if not output_id.startswith(DLNA_PREFIX):
        return
    try:
        streams.resolve(target["stream_url"])
    except streams.NotAStream as exc:
        raise OutputError(f"{target.get('name') or 'This station'}: {exc}") from exc


def play(output_id: str, target: dict) -> dict[str, Any]:
    """Start ``target`` ({stream_url, name, favicon}) on a server-side output."""
    if output_id == STONEPI:
        state = odio_client().play_uri(target["stream_url"], title=target.get("name") or "")
        # Odio doesn't report the stream it's playing; kept so switching output can carry it over.
        db.set_pref("stonepi_station", json.dumps({"url": target["stream_url"], "name": target.get("name") or ""}))
        _remember_logo(STONEPI, target)
        return {**state, "output": STONEPI}
    spk = _speaker(output_id)
    if not spk:
        raise OutputError("That speaker isn't on the network any more.")
    try:
        found = streams.resolve(target["stream_url"])
    except streams.NotAStream as exc:
        raise OutputError(str(exc)) from exc
    url, mime = found["url"], found["mime"]
    try:
        if found["hls"]:
            # Speakers can't follow HLS: the relay joins it into one AAC stream for them.
            mime = streams.HLS_MIME
            if spk["av_control"] == "mock":
                url = streams.relay_path(url, hls=True)
            else:
                url = streams.relay_url_for_speaker(url, spk["host"], hls=True)
        elif urlsplit(url).scheme == "https" and not spk["direct_https"] and spk["av_control"] != "mock":
            url = streams.relay_url_for_speaker(url, spk["host"])
    except streams.NotAStream as exc:
        raise OutputError(str(exc)) from exc
    try:
        dlna.play(spk, url, mime, target.get("name") or "Radio")
    except dlna.SpeakerError as exc:
        raise OutputError(f"{spk['shown_name']}: {exc}") from exc
    _remember_logo(output_id, target)
    return now(output_id)


def transport(output_id: str, action: str) -> dict[str, Any]:
    if output_id == STONEPI:
        return {**odio_client().transport(action), "output": STONEPI}
    spk = _speaker(output_id)
    if not spk:
        raise OutputError("That speaker isn't on the network any more.")
    try:
        dlna.transport(spk, action)
    except dlna.SpeakerError as exc:
        raise OutputError(f"{spk['shown_name']}: {exc}") from exc
    return now(output_id)


def set_volume(output_id: str, level: float) -> dict[str, Any]:
    level = max(0.0, min(1.0, float(level)))
    if output_id == STONEPI:
        return {**odio_client().set_volume(level), "output": STONEPI}
    spk = _speaker(output_id)
    if not spk:
        raise OutputError("That speaker isn't on the network any more.")
    try:
        dlna.set_volume(spk, level)
    except dlna.SpeakerError as exc:
        raise OutputError(f"{spk['shown_name']}: {exc}") from exc
    return {**now(output_id), "volume": level}


def bump_volume(output_id: str, delta: float) -> dict[str, Any]:
    current = now(output_id).get("volume")
    base = float(current) if current is not None else env.default_volume
    return set_volume(output_id, base + float(delta))


def station_logo(output_id: str) -> str:
    """The logo of the station last started on this output (speakers and Odio don't report one)."""
    return db.get_pref(f"now_logo:{output_id}", "")


def station_url(output_id: str) -> str:
    """The address of the station last started on this output (its song / stream info come from there)."""
    url = db.get_pref(f"now_url:{output_id}", "")
    if url:
        return url
    # StonePi also keeps the URL in stonepi_station (Odio doesn't report it); use that if the
    # dedicated pref was never set (older plays, or a path that only wrote the JSON).
    if output_id == STONEPI:
        try:
            last = json.loads(db.get_pref("stonepi_station", "{}"))
        except ValueError:
            last = {}
        return str(last.get("url") or "")
    return ""


def _remember_logo(output_id: str, target: dict) -> None:
    db.set_pref(f"now_logo:{output_id}", target.get("favicon") or "")
    db.set_pref(f"now_url:{output_id}", target.get("stream_url") or "")


def now(output_id: str) -> dict[str, Any]:
    """What's playing on a server-side output, in Odio's ``now_playing`` shape."""
    if output_id == STONEPI:
        state = odio_client().now_playing()
        try:
            last = json.loads(db.get_pref("stonepi_station", "{}"))
        except ValueError:
            last = {}
        return {**state, "output": STONEPI, "output_name": "StonePi", "station_url": last.get("url", ""),
                "station_logo": station_logo(STONEPI)}
    spk = _speaker(output_id)
    base = {
        "ok": True,
        "output": output_id,
        "output_name": spk["shown_name"] if spk else name_of(output_id),
        "source": "speaker",
        "artist": "",
        "art_url": "",
        "station_logo": station_logo(output_id),
        "player": {},
        "players": [],
    }
    if not spk:
        return {**base, "ok": False, "connected": False, "playing": False, "paused": False,
                "playback_status": "Stopped", "title": "", "volume": None, "error": "That speaker isn't on the network any more."}
    try:
        st = dlna.state(spk)
        vol = dlna.get_volume(spk)
    except dlna.SpeakerError as exc:
        return {**base, "connected": False, "playing": False, "paused": False, "playback_status": "Unavailable",
                "title": spk["now_title"], "volume": None, "error": str(exc)}
    status = {"PLAYING": "Playing", "PAUSED_PLAYBACK": "Paused", "TRANSITIONING": "Starting"}.get(st, "Stopped")
    return {
        **base,
        "connected": True,
        "playing": st in {"PLAYING", "TRANSITIONING"},
        "paused": st == "PAUSED_PLAYBACK",
        "playback_status": status,
        "title": spk["now_title"] if st != "STOPPED" else "",
        "volume": vol,
    }
