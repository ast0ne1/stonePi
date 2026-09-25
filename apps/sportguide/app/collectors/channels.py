from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

# Filename tails used on ausportguide.com channel logo assets.
_ICON_SUFFIXES = (
    "-australia-tv-guide-live-sm",
    "-australia-tv-guide-live",
    "-tv-guide-live-sm",
    "-tv-guide-live",
    "-live-sm",
    "-sm",
)

# Prefer these display labels when the slug matches.
_SLUG_ALIASES: dict[str, str] = {
    "kayo": "Kayo",
    "kayo-sports": "Kayo Sports",
    "fox-footy": "Fox Footy",
    "fox-sports": "Fox Sports",
    "fox-league": "Fox League",
    "fox-cricket": "Fox Cricket",
    "7plus": "7plus",
    "7mate": "7mate",
    "9now": "9Now",
    "9gem": "9Gem",
    "9go": "9Go!",
    "10-play": "10 Play",
    "10play": "10 Play",
    "stan-sport": "Stan Sport",
    "stan-sports": "Stan Sport",
    "espn": "ESPN",
    "channel-7": "Channel 7",
    "channel-9": "Channel 9",
    "channel-10": "Channel 10",
    "sbs": "SBS",
    "sbs-viceland": "SBS Viceland",
    "abc": "ABC",
    "abc-iview": "ABC iview",
    "beinand": "beIN Sports",
    "bein-sports": "beIN Sports",
}

_UPPER_TOKENS = frozenset({"abc", "sbs", "nrl", "afl", "espn", "aflw", "nba", "nfl"})


def channel_from_icon_src(src: str | None) -> str | None:
    """Derive a channel label from an AusSportGuide logo URL/path.

    Examples:
      .../fox-footy-australia-tv-guide-live-sm.png → Fox Footy
      .../kayo-sports-australia-tv-guide-live-sm.png → Kayo Sports
    """
    raw = (src or "").strip()
    if not raw:
        return None
    path = unquote(urlparse(raw).path if "://" in raw or raw.startswith("/") else raw)
    stem = PurePosixPath(path).name
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    low = stem.lower().strip()
    if not low:
        return None
    for suf in _ICON_SUFFIXES:
        if low.endswith(suf):
            low = low[: -len(suf)]
            break
    low = low.strip("-")
    if not low or low in {"channel", "logo", "icon", "placeholder"}:
        return None
    if low in _SLUG_ALIASES:
        return _SLUG_ALIASES[low]
    parts = [p for p in re.split(r"[-_]+", low) if p]
    if not parts:
        return None
    titled: list[str] = []
    for part in parts:
        if part in _UPPER_TOKENS:
            titled.append(part.upper())
        elif part.isdigit():
            titled.append(part)
        else:
            titled.append(part.capitalize())
    return " ".join(titled)


def channels_from_icon_srcs(srcs: list[str] | tuple[str, ...] | None) -> list[str]:
    found: list[str] = []
    for src in srcs or ():
        label = channel_from_icon_src(src)
        if label and label not in found:
            found.append(label)
    return found
