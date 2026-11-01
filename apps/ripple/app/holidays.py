"""Upcoming public holidays on the family display (backlog U34), for both places Ripple knows.

    Christmas Day · Fri 25 Dec · both   /   Store Bededag · Fri 1 May · Copenhagen

From Nager.Date (free, no key): ``GET /api/v3/PublicHolidays/{year}/{countryCode}``. Each
country is fetched **at most once a day** (this year, plus next year in December) by a
background thread and kept in Ripple's database; the board and settings only ever read the
stored copy, and a failed fetch keeps the last good one.

Holidays that only apply to part of a country (``global: false`` with ``counties``, such as
the UK nations or German states) show only when the place's **region** matches; nationwide
ones always show. The region list comes from the stored holidays themselves.

A place gets its ISO country code from Open-Meteo's place search; places saved before
holidays existed get theirs looked up once in the background.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app import db, local

logger = logging.getLogger(__name__)

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
FETCH_EVERY_S = 24 * 3600
RETRY_AFTER_S = 3600  # a failed fetch is tried again in an hour, not every loop
DEFAULT_DAYS = 14
MAX_DAYS = 60
SHOWN = 3  # holidays on the board at most
PLACES = ("here", "family")

_wake = threading.Event()
_started = False
_start_lock = threading.Lock()


# ── Settings ─────────────────────────────────────────────────────────────────


def enabled() -> bool:
    return db.get_setting("holidays_on") == "1"


def days_ahead() -> int:
    try:
        return max(1, min(MAX_DAYS, int(db.get_setting("holidays_days") or DEFAULT_DAYS)))
    except ValueError:
        return DEFAULT_DAYS


def region(which: str) -> str:
    return db.get_setting(f"holidays_region_{which}")


def save(on: bool, days: int, regions: dict[str, str]) -> None:
    db.set_setting("holidays_on", "1" if on else "")
    db.set_setting("holidays_days", str(max(1, min(MAX_DAYS, days))))
    for which in PLACES:
        db.set_setting(f"holidays_region_{which}", (regions.get(which) or "").strip().upper()[:12])


# ── The stored copy ──────────────────────────────────────────────────────────


def _stored(country: str, year: int) -> tuple[list[dict[str, Any]], float]:
    """(holidays, when fetched as epoch seconds; 0 = never)."""
    raw = db.get_setting(f"holidays_{country}_{year}")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return list(data.get("items") or []), float(data.get("at") or 0)


def _store(country: str, year: int, items: list[dict[str, Any]], at: float) -> None:
    db.set_setting(f"holidays_{country}_{year}", json.dumps({"items": items, "at": at}))


def _clean(rows: Any) -> list[dict[str, Any]]:
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        name = " ".join(str(row.get("name") or row.get("localName") or "").split())[:60]
        if not name:
            continue
        counties = row.get("counties") if isinstance(row.get("counties"), list) else []
        out.append({
            "date": str(row["date"]),
            "name": name,
            "global": bool(row.get("global", True)) or not counties,
            "counties": sorted({str(c).upper() for c in counties if c}),
        })
    return out


def fetched(country: str) -> bool:
    """Whether this year's holidays for ``country`` have been fetched yet."""
    return _stored(country, datetime.now(timezone.utc).year)[1] > 0


def regions(country: str) -> list[str]:
    """Region codes this country's holidays use (e.g. GB-ENG, GB-SCT), from the stored copy."""
    year = datetime.now(timezone.utc).year
    found: set[str] = set()
    for y in (year, year + 1):
        for item in _stored(country, y)[0]:
            found.update(item["counties"])
    return sorted(found)


# ── Fetching (background, at most once a day per country) ────────────────────


def _years(now: datetime) -> list[int]:
    # Every year the longest look-ahead reaches (in November, 60 days ahead is next year).
    return sorted({now.year, (now + timedelta(days=MAX_DAYS + 1)).year})


def _fetch(country: str, year: int) -> list[dict[str, Any]]:
    response = httpx.get(NAGER_URL.format(year=year, country=country), timeout=10.0,
                         headers={"Accept": "application/json"})
    if response.status_code == 204:  # Nager.Date has no holidays for this country
        return []
    response.raise_for_status()
    return _clean(response.json())


def _ensure_country(which: str) -> None:
    """A place saved before holidays existed: look its country up once (Open-Meteo search)."""
    place = local.get_place(which)
    if place is None or place.get("country"):
        return
    try:
        tried = float(db.get_setting(f"country_lookup_{which}") or 0)
    except ValueError:
        tried = 0.0
    now = datetime.now(timezone.utc).timestamp()
    if now - tried < RETRY_AFTER_S:
        return  # failed or found nothing within the hour (no network at boot, say)
    try:
        matches = local.search_places(place["name"])
    except Exception:  # noqa: BLE001 - tried again in an hour
        db.set_setting(f"country_lookup_{which}", str(now))
        return
    near = [m for m in matches if m.get("country") and abs(m["lat"] - place["lat"]) < 0.5
            and abs(m["lon"] - place["lon"]) < 0.5]
    if not near:
        db.set_setting(f"country_lookup_{which}", str(now))
        return
    # The admin may have picked another city while the search ran: only fill in this one.
    current = local.get_place(which)
    if current and (current["lat"], current["lon"]) == (place["lat"], place["lon"]) and not current.get("country"):
        local.set_place(which, {**current, "country": near[0]["country"]})


def countries() -> list[str]:
    seen = []
    for which in PLACES:
        place = local.get_place(which)
        code = (place or {}).get("country") or ""
        if code and code not in seen:
            seen.append(code)
    return seen


def refresh(now: datetime | None = None, *, force: bool = False) -> bool:
    """Fetch what's due; True when anything new was stored."""
    now = now or datetime.now(timezone.utc)
    for which in PLACES:
        _ensure_country(which)
    changed = False
    for country in countries():
        for year in _years(now):
            items, at = _stored(country, year)
            failed_at = float(db.get_setting(f"holidays_failed_{country}_{year}") or 0)
            stamp = now.timestamp()
            if not force and stamp - at < FETCH_EVERY_S:
                continue
            if not force and stamp - failed_at < RETRY_AFTER_S:
                continue
            try:
                fresh = _fetch(country, year)
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("holidays %s %s: %s", country, year, str(exc)[:160])
                db.set_setting(f"holidays_failed_{country}_{year}", str(stamp))
                continue  # the last good copy stays
            if fresh != items:
                changed = True
            _store(country, year, fresh, stamp)
    return changed


def _loop(on_change) -> None:
    while True:
        try:
            # Missing countries first (once per place), so Settings can show the regions.
            for which in PLACES:
                _ensure_country(which)
            if enabled() and refresh():
                on_change()
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("holiday refresh failed")
        _wake.wait(3600)
        _wake.clear()


def start(on_change) -> None:
    """Start the background fetcher once (app startup)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(on_change,), name="ripple-holidays", daemon=True).start()


def wake() -> None:
    """Settings changed: check now rather than within the hour."""
    _wake.set()


# ── What the board shows ─────────────────────────────────────────────────────


def _applies(item: dict[str, Any], region_code: str) -> bool:
    return item["global"] or (bool(region_code) and region_code in item["counties"])


def upcoming(now: datetime | None = None) -> list[dict[str, Any]]:
    """Holidays in the next ``days_ahead()`` days at either place, soonest first:
    [{date, name, where}] where ``where`` is the place's name or "both". Dates are counted
    from the family's today (the board is at their home)."""
    if not enabled():
        return []
    now = now or datetime.now(timezone.utc)
    family = local.get_place("family")
    here = local.get_place("here")
    zone = ZoneInfo((family or here or {}).get("tz") or "UTC")
    today = now.astimezone(zone).date()
    last = today + timedelta(days=days_ahead())
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for which, place in (("family", family), ("here", here)):
        if not place or not place.get("country"):
            continue
        for year in {today.year, last.year}:
            for item in _stored(place["country"], year)[0]:
                day = date.fromisoformat(item["date"])
                if not (today <= day <= last) or not _applies(item, region(which)):
                    continue
                key = (item["date"], item["name"].lower())
                if key in found and found[key]["where"] != place["name"]:
                    found[key]["where"] = "both"
                else:
                    found.setdefault(key, {"date": day, "name": item["name"], "where": place["name"]})
    return sorted(found.values(), key=lambda h: (h["date"], h["name"]))[:SHOWN]


def lines(now: datetime | None = None) -> list[str]:
    """The board's holiday lines: "Christmas Day · Fri 25 Dec · both". A date, never "today"
    or "tomorrow": the TRMNL keeps showing a picture until the next push."""
    items = upcoming(now)
    two_places = bool(local.get_place("family")) and bool(local.get_place("here"))
    out = []
    for h in items:
        when = f"{h['date']:%a} {h['date'].day} {h['date']:%b}"
        where = "" if not two_places else (" · both" if h["where"] == "both" else f" · {h['where']}")
        out.append(f"{h['name']} · {when}{where}")
    return out
