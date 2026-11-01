from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app import db
from app.config import env
from app.services import mympd

logger = logging.getLogger("radio.stations")

RADIO_BROWSER_HOSTS = (
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
)


def _normalize_browser(row: dict[str, Any], fav_urls: set[str]) -> dict[str, Any] | None:
    url = str(row.get("url_resolved") or row.get("url") or "").strip()
    if not url:
        return None
    station_id = str(row.get("stationuuid") or row.get("changeuuid") or url)[:36]
    return {
        "id": station_id,
        "name": str(row.get("name") or "Station").strip() or "Station",
        "stream_url": url,
        "favicon": str(row.get("favicon") or "").strip(),
        "tags": str(row.get("tags") or "").strip(),
        "country": str(row.get("country") or "").strip(),
        "homepage": str(row.get("homepage") or "").strip(),
        "favorite": url in fav_urls,
        "source": "radio-browser",
        "codec": str(row.get("codec") or ""),
        "bitrate": row.get("bitrate") or 0,
    }


# Radio Browser answers are kept a while: Discover asks for the same lists all day, and a
# host that's down shouldn't make every page wait for its timeout again.
_RB_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_RB_LOCK = threading.Lock()
RB_KEEP_S = 30 * 60
RB_FAILED_S = 5 * 60


def radio_browser(path: str, params: dict[str, Any]) -> list[Any] | None:
    """One Radio Browser JSON list (``/json/<path>``), trying each mirror; None when none answer."""
    if env.mock:
        return None  # RADIO_MOCK: no network (dev, tests)
    key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    now = time.monotonic()
    with _RB_LOCK:
        hit = _RB_CACHE.get(key)
    if hit and now - hit[0] < (RB_KEEP_S if hit[1] else RB_FAILED_S):
        return hit[1] or None
    rows: list[Any] = []
    for host in RADIO_BROWSER_HOSTS:
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), headers={"User-Agent": "StonePi-Radio"}) as client:
                resp = client.get(f"{host}/json/{path}", params=params)
            if resp.status_code < 400 and isinstance(resp.json(), list):
                rows = resp.json()
                break
        except Exception:
            logger.debug("Radio Browser host failed: %s", host, exc_info=True)
    with _RB_LOCK:
        _RB_CACHE[key] = (now, rows)
    return rows or None


def radio_browser_stations(params: dict[str, Any], *, limit: int = 40) -> list[dict[str, Any]]:
    if env.mock:
        return []
    query = {"limit": limit, "hidebroken": "true", "order": "clickcount", "reverse": "true", **params}
    out = []
    for row in radio_browser("stations/search", query) or []:
        if isinstance(row, dict):
            station = _normalize_browser(row, set())
            if station:
                out.append(station)
    return out


def _search_radio_browser(query: str, *, limit: int = 40) -> list[dict[str, Any]]:
    query = (query or "").strip()
    return radio_browser_stations({"name": query}, limit=limit) if query else []


def _filter(query: str, stations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not query:
        return list(stations)
    q_lower = query.lower()
    return [
        s
        for s in stations
        if q_lower in s["name"].lower()
        or q_lower in (s.get("tags") or "").lower()
        or q_lower in (s.get("country") or "").lower()
    ]


def browse(query: str = "", *, limit: int = 40) -> dict[str, Any]:
    """The household's stations; a search adds myMPD's WebradioDB, else Radio Browser."""
    query = (query or "").strip()
    local = _filter(query, db.list_stations())
    if not query or env.mock:
        return {"stations": local[:limit] if query else local, "query": query}

    remote = mympd.search(query, limit=limit) or _search_radio_browser(query, limit=limit)
    listed = db.station_urls()
    seen = {s["stream_url"] for s in local}
    merged = list(local)
    for station in remote:
        if station["stream_url"] in seen:
            continue
        station["in_list"] = station["stream_url"] in listed
        merged.append(station)
        seen.add(station["stream_url"])
        if len(merged) >= limit:
            break
    return {"stations": merged[:limit], "query": query}


_NAME_SEPARATORS = (" - ", " – ", " — ", " | ", " :: ", ": ")


def station_label(name: str) -> tuple[str, str]:
    """(name, the rest) for a card: Radio Browser names often carry a description
    ("Radio X - The best rock 24/7 - ..."); the part before the first separator is the name."""
    name = " ".join((name or "").split())
    if len(name) <= 24:
        return name, ""
    cuts = [(name.find(sep), sep) for sep in _NAME_SEPARATORS if 2 <= name.find(sep)]
    if not cuts:
        return name, ""
    at, sep = min(cuts)
    return name[:at].strip(), name[at + len(sep):].strip(" -–—|:")


def check_stream_url(url: str) -> str:
    """A stream Odio may fetch: http(s) only, never this Pi's own loopback services."""
    import ipaddress
    from urllib.parse import urlparse

    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Stations must be an http:// or https:// stream.")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("That stream address isn't allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise ValueError("That stream address isn't allowed.")
    return url


def _target(station: dict[str, Any]) -> dict[str, str]:
    return {
        "id": station["id"],
        "name": station["name"],
        "stream_url": check_stream_url(station["stream_url"]),
        "favicon": station.get("favicon") or "",
    }


def resolve_play_target(*, owner: str | None, station_id: str = "", url: str = "", name: str = "") -> dict[str, str]:
    """``owner`` is the person playing (their favorites first); None for a display (Car Thing)."""
    station_id = (station_id or "").strip()
    url = (url or "").strip()
    name = (name or "").strip()
    if station_id:
        fav = db.get_favorite(owner, station_id) if owner is not None else None
        if fav:
            return _target(fav)
        station = db.get_station(station_id)
        if station:
            return _target(station)
        if owner is None:
            for station in db.household_favorites(limit=50):
                if station["id"] == station_id:
                    return _target(station)
    if url:
        fav = db.find_favorite_by_url(owner, url) if owner is not None else db.find_any_by_url(url)
        if fav:
            return _target(fav)
        station = db.find_station_by_url(url)
        if station:
            return _target(station)
        return {"id": "", "name": name or "Station", "stream_url": check_stream_url(url), "favicon": ""}
    raise ValueError("Need station_id or url")
