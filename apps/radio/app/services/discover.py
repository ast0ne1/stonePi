"""Discover: stations to try, from Radio Browser (popular near you, trending, genres, more
like your favourites). The household's country is an admin setting; until it's set, it's
read from the Pi's time zone. Under ``RADIO_MOCK`` (no network) the household's own
stations stand in, so the page still has something to show.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app import db
from app.config import env
from app.services import stations

ROW = 12
GRID = 40

# (label, Radio Browser tag)
GENRES: tuple[tuple[str, str], ...] = (
    ("Pop", "pop"),
    ("Rock", "rock"),
    ("Jazz", "jazz"),
    ("Electronic", "electronic"),
    ("Classical", "classical"),
    ("News", "news"),
    ("Talk", "talk"),
    ("Indie", "indie"),
    ("Chill", "chillout"),
    ("Hip-hop", "hiphop"),
    ("Dance", "dance"),
    ("Ambient", "ambient"),
    ("Folk", "folk"),
    ("Country", "country"),
    ("Blues", "blues"),
    ("Soul", "soul"),
    ("Metal", "metal"),
    ("Oldies", "oldies"),
    ("Kids", "kids"),
)
GENRE_TAGS = {tag: label for label, tag in GENRES}


def country_from_timezone() -> str:
    """The Pi's country from its time zone (Debian's /etc/timezone + zone.tab); "" if unknown."""
    try:
        tz = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        for line in Path("/usr/share/zoneinfo/zone.tab").read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and not line.startswith("#") and parts[2] == tz:
                return parts[0].upper()
    except OSError:
        pass
    return ""


def household_country() -> str:
    code = db.get_pref("country", "").strip().upper()
    if not code:
        return country_from_timezone()
    return code if code != "-" else ""  # "-" = admin chose "no country" (worldwide)


def countries() -> list[dict[str, str]]:
    """Countries Radio Browser has stations for, biggest first: [{code, name}]."""
    rows = stations.radio_browser("countries", {"order": "stationcount", "reverse": "true"}) or []
    out = []
    for row in rows:
        code = str(row.get("iso_3166_1") or "").upper() if isinstance(row, dict) else ""
        if len(code) == 2 and int(row.get("stationcount") or 0) >= 5:
            out.append({"code": code, "name": str(row.get("name") or code)})
    return sorted(out, key=lambda c: c["name"])


def country_name(code: str) -> str:
    for country in countries():
        if country["code"] == code:
            return country["name"].replace("The United States Of America", "the US").replace(
                "The United Kingdom Of Great Britain And Northern Ireland", "the UK"
            )
    return code


def _mock_pick(tag: str = "", *, reverse: bool = False) -> list[dict[str, Any]]:
    rows = db.list_stations()
    if tag:
        rows = [s for s in rows if tag in (s.get("tags") or "").lower()]
    return list(reversed(rows)) if reverse else rows


def _favourite_tags(owner: str) -> list[str]:
    counts: Counter[str] = Counter()
    for fav in db.list_favorites(owner):
        for tag in (fav.get("tags") or "").lower().split(","):
            tag = tag.strip()
            if tag and len(tag) > 2:
                counts[tag] += 1
    return [tag for tag, _ in counts.most_common(2)]


def sections(owner: str) -> list[dict[str, Any]]:
    """The Discover rows: [{id, title, stations}], empty rows left out."""
    country = household_country()
    fav_urls = db.favorite_urls(owner)
    tags = _favourite_tags(owner)

    if env.mock:
        found = {
            "popular": _mock_pick(),
            "trending": _mock_pick(reverse=True),
            "similar": _mock_pick(tags[0]) if tags else [],
        }
    else:
        jobs = {"trending": {"order": "clicktrend"}}
        if country:
            jobs["popular"] = {"countrycode": country}
        if tags:
            jobs["similar"] = {"tag": tags[0], "tagExact": "true"}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {key: pool.submit(stations.radio_browser_stations, params, limit=ROW * (4 if key == "similar" else 2)) for key, params in jobs.items()}
            found = {key: future.result() for key, future in futures.items()}

    titles = {
        "popular": f"Popular in {country_name(country)}" if country else "Popular",
        "trending": "Trending now",
        "similar": "More like your favourites",
    }
    out = []
    for key in ("popular", "trending", "similar"):
        rows = [s for s in found.get(key) or [] if not (key == "similar" and s["stream_url"] in fav_urls)]
        if key == "similar" and tags and not env.mock:
            rows = genre_fit(rows, tags[0])
        if rows:
            out.append({"id": key, "title": titles[key], "stations": _dedupe(rows)[:ROW]})
    return out


# Radio Browser's tags are typed by whoever adds a station, and some stations stuff in dozens
# (the "breakz" / club-chart streams list 50+, hiphop among them) to show up everywhere; sorted
# by plays they topped genres they aren't. A station with more tags than this is left out of a
# genre unless its name says the genre.
TAG_STUFFED = 12


def _tags(station: dict[str, Any]) -> list[str]:
    return [t.strip().lower().lstrip("#") for t in (station.get("tags") or "").split(",") if t.strip()]


def _words(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _named(name: str, *genres: str) -> bool:
    """The station's name says the genre as a word of its own ("Hip Hop Radio", "hiphop 24/7",
    "BBC Jazz"), not inside another word ("Bedrock FM", "Radio Grapevine")."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
    joined = ["".join(tokens[i:j]) for i in range(len(tokens)) for j in range(i + 1, min(i + 3, len(tokens)) + 1)]
    return any(g and g in joined for g in genres)


def genre_fit(stations_in: list[dict[str, Any]], tag: str) -> list[dict[str, Any]]:
    """Genre results that are that genre: tag-stuffed stations out (unless named for it), then
    stations named for the genre, then by where the tag sits among their tags; Radio Browser's
    order (plays) only breaks ties."""
    want = _words(tag)
    label = _words(GENRE_TAGS.get(tag, tag))
    scored = []
    for order, station in enumerate(stations_in):
        tags = _tags(station)
        named = bool(want) and _named(station.get("name") or "", want, label)
        positions = [i for i, t in enumerate(tags) if _words(t) == want]
        if not positions and not named:
            continue
        if len(tags) > TAG_STUFFED and not named:
            continue
        scored.append((0 if named else 1, positions[0] if positions else 0, order, station))
    scored.sort(key=lambda row: row[:3])
    return [row[3] for row in scored]


def genre(tag: str) -> list[dict[str, Any]]:
    if env.mock:
        return _mock_pick(tag)
    rows = stations.radio_browser_stations({"tag": tag, "tagExact": "true"}, limit=GRID * 4)
    return _dedupe(genre_fit(rows, tag))[:GRID]


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Radio Browser lists the same station many times (bitrates, mirrors): one card per name."""
    seen: set[str] = set()
    out = []
    for station in rows:
        key = " ".join(station["name"].lower().split())
        if key in seen or station["stream_url"] in seen:
            continue
        seen.update({key, station["stream_url"]})
        out.append(station)
    return out
