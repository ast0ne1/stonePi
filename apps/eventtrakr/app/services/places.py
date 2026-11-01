"""Coordinates for event locations, for the map (F18, ideation/eventtrakr-map.md §1).

Events only carry free text ("Union", "Kulturhuset Brønshøj, Brønshøjvej 17").
Each distinct location is looked up once with Nominatim (OpenStreetMap: no key,
finds venues and addresses), kept to the home city's area so "Union" is the
one on Nørrebro, and cached in ``places`` for good. Lookups run in the
background after a sync -- never while a page loads -- at Nominatim's limit of
one request a second.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import env
from app.db import SessionLocal
from app.models import Event, Place, Setting, utcnow
from app.services.dedupe import fold

logger = logging.getLogger("eventtrakr.places")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy: identify the app, at most one request a second.
USER_AGENT = "EventTrakr/1.0 (StonePi self-hosted event agenda)"
REQUEST_GAP_SECONDS = 1.1
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
MAX_LOOKUPS_PER_RUN = 150
RETRY_NOT_FOUND_AFTER = timedelta(days=30)
# The search area is the home city's box plus this much on every side (about
# 15 km), so venues just over the border (Hellerup, Lyngby, Hvidovre) are found.
CITY_MARGIN_DEGREES = (0.25, 0.14)  # longitude, latitude
# A map page asks for lookups at most this often while places are waiting.
RESTART_AFTER_SECONDS = 5 * 60

# Says nothing about where on the map ("Online"), or only names the city: a
# pin in the city centre would mislead.
_NOWHERE = frozenset(
    {
        "", "unspecified", "online", "tba", "tbd", "tbc", "various", "flere steder", "se beskrivelse",
        "copenhagen", "copenhagen denmark", "kobenhavn", "kobenhavn danmark", "kbh", "denmark", "danmark",
        "kbh og frederiksberg", "frederiksberg",
    }
)

_run_lock = threading.Lock()
_last_started = 0.0
_sleep = time.sleep  # tests replace this


def place_key(location: str | None) -> str:
    return fold(location)[:300]


def is_nowhere(location: str | None) -> bool:
    return place_key(location) in _NOWHERE or place_key(location) == place_key(env.default_location)


def _city_box(db: Session, client: httpx.Client) -> list[float] | None:
    """The home city's bounding box [west, south, east, north], looked up once."""
    key = "map_area:" + place_key(env.default_location)  # the city box plus CITY_MARGIN_DEGREES
    row = db.get(Setting, key)
    if row and row.value:
        try:
            return json.loads(row.value)
        except ValueError:
            pass
    results = _search(client, env.default_location, None)
    if not results:
        return None
    south, north, west, east = (float(v) for v in results[0]["boundingbox"])
    dlon, dlat = CITY_MARGIN_DEGREES
    box = [west - dlon, south - dlat, east + dlon, north + dlat]
    if row is None:
        row = Setting(key=key)
        db.add(row)
    row.value = json.dumps(box)
    row.updated_at = utcnow()
    db.commit()
    return box


def _search(client: httpx.Client, query: str, box: list[float] | None) -> list[dict]:
    params = {"q": query, "format": "jsonv2", "limit": "1", "addressdetails": "0"}
    if box:
        params["viewbox"] = ",".join(str(v) for v in box)
        params["bounded"] = "1"
    resp = client.get(NOMINATIM_URL, params=params)
    _sleep(REQUEST_GAP_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def _attempts(location: str) -> list[str]:
    """The full text, then its address part, then its venue part."""
    parts = [p.strip() for p in location.split(",") if p.strip()]
    tries = [location.strip()]
    if len(parts) > 1:
        tries += [", ".join(parts[1:]), parts[0]]
    return list(dict.fromkeys(t for t in tries if not is_nowhere(t)))


def queue_new_locations(db: Session) -> int:
    """A pending ``places`` row for every location of an upcoming event not seen before."""
    now = datetime.now(timezone.utc)
    locations = db.execute(
        select(Event.location)
        .where(or_(Event.start_time >= now - timedelta(days=1), Event.end_time >= now))
        .distinct()
    ).scalars().all()
    known = set(db.execute(select(Place.key)).scalars())
    added = 0
    for location in locations:
        key = place_key(location)
        if not key or key in known:
            continue
        known.add(key)
        db.add(Place(key=key, query=(location or "")[:300], status="skipped" if is_nowhere(location) else "pending"))
        added += 1
    if added:
        db.commit()
    return added


def pending_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Place).where(Place.status == "pending")).scalar_one()


def look_up_pending(max_lookups: int = MAX_LOOKUPS_PER_RUN) -> int:
    """Look up queued places (and not-found ones due a retry). Returns how many were found."""
    if not _run_lock.acquire(blocking=False):
        return 0
    found = 0
    try:
        with SessionLocal() as db, httpx.Client(
            timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            queue_new_locations(db)
            retry_before = utcnow() - RETRY_NOT_FOUND_AFTER
            due = db.execute(
                select(Place)
                .where(
                    or_(
                        Place.status == "pending",
                        (Place.status == "not_found") & (Place.looked_up_at < retry_before),
                    )
                )
                .order_by(Place.created_at)
            ).scalars().all()
            if not due:
                return 0
            box = _city_box(db, client)
            if box is None:
                # Without the home area every "Union" could be anywhere in the
                # world, and a wrong answer would be kept for good: wait instead.
                logger.warning("Place lookups waiting: couldn't find the home area %r", env.default_location)
                return 0
            lookups = 0
            for place in due:
                if lookups >= max_lookups:
                    break
                result = None
                for query in _attempts(place.query):
                    lookups += 1
                    hits = _search(client, query, box)
                    if hits:
                        result = hits[0]
                        break
                    if lookups >= max_lookups:
                        break
                place.looked_up_at = utcnow()
                if result:
                    place.lat, place.lon = float(result["lat"]), float(result["lon"])
                    place.display_name = str(result.get("display_name") or "")[:500]
                    place.status = "found"
                    found += 1
                else:
                    place.status = "not_found"
                db.commit()
    except Exception as e:  # noqa: BLE001 -- lookups are best effort; the next run carries on
        logger.warning("Place lookups stopped: %s", e)
    finally:
        _run_lock.release()
    return found


def look_up_pending_background() -> bool:
    """Start lookups in a daemon thread unless a run is already going."""
    global _last_started
    if _run_lock.locked():
        return False
    _last_started = time.monotonic()
    threading.Thread(target=look_up_pending, daemon=True, name="eventtrakr-places").start()
    return True


def nudge(pending: int) -> bool:
    """From a page: restart lookups for places still waiting (a run stops at
    MAX_LOOKUPS_PER_RUN, or after an error), at most every few minutes so an
    unreachable Nominatim isn't asked again on every poll."""
    if pending and not _run_lock.locked() and time.monotonic() - _last_started >= RESTART_AFTER_SECONDS:
        return look_up_pending_background()
    return False


def is_running() -> bool:
    return _run_lock.locked()


def search_live(query: str) -> list[dict]:
    """The map's place search ("Refshaleøen"): live, near the home city, not cached."""
    query = (query or "").strip()[:120]
    if not query:
        return []
    with SessionLocal() as db, httpx.Client(
        timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        box = _city_box(db, client)
        params = {"q": query, "format": "jsonv2", "limit": "5"}
        if box:
            params["viewbox"] = ",".join(str(v) for v in box)  # a preference, not a limit
        resp = client.get(NOMINATIM_URL, params=params)
        resp.raise_for_status()
        return [
            {"name": str(r.get("display_name") or "")[:200], "lat": float(r["lat"]), "lon": float(r["lon"])}
            for r in resp.json()
            if "lat" in r and "lon" in r
        ]
