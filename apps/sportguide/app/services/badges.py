"""Team badges on the match cards (U28), downloaded once to the Pi and served from there.

The football sources give each team's badge URL (timezone.football's from football-data.org or
TheSportsDB, a guessed WheresTheMatch crest). After a refresh the missing ones are fetched from those hosts only, kept
under DATA_DIR/badges and served at /badges/<key>.<ext>; a miss is remembered for a week so a
guessed crest that doesn't exist isn't asked for on every refresh. Cards fall back to initials.
"""

from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import DATA_DIR

logger = logging.getLogger("sportguide.badges")

BADGE_DIR = DATA_DIR / "badges"
ALLOWED_HOSTS = frozenset(
    {"crests.football-data.org", "r2.thesportsdb.com", "www.thesportsdb.com", "www.wheresthematch.com"}
)
# No SVG: opened straight from /badges/ an SVG would run its scripts on our origin.
TYPES = {"image/png": "png", "image/gif": "gif", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_BYTES = 300 * 1024
MISS_RETRY_S = 7 * 24 * 3600
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
USER_AGENT = "StonePi-SportGuide/0.0.1 (household LAN; +https://github.com/ast0ne1)"


def ensure_dir() -> Path:
    BADGE_DIR.mkdir(parents=True, exist_ok=True)
    return BADGE_DIR


def _key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def _allowed(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_HOSTS


def _cached(url: str) -> Path | None:
    key = _key(url)
    for ext in TYPES.values():
        path = BADGE_DIR / f"{key}.{ext}"
        if path.is_file():
            return path
    return None


def local_src(url: str | None) -> str | None:
    """/badges/<file> once the badge is on the Pi, else None (the card shows initials)."""
    if not url or not _allowed(url):
        return None
    path = _cached(url)
    return f"/badges/{path.name}" if path else None


def _recently_missed(url: str) -> bool:
    marker = BADGE_DIR / f"{_key(url)}.miss"
    return marker.is_file() and time.time() - marker.stat().st_mtime < MISS_RETRY_S


def _fetch_one(client: httpx.Client, url: str) -> bool:
    key = _key(url)
    try:
        resp = client.get(url)
        kind = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if resp.status_code != 200 or kind not in TYPES or not resp.content or len(resp.content) > MAX_BYTES:
            (BADGE_DIR / f"{key}.miss").touch()
            return False
        tmp = BADGE_DIR / f"{key}.tmp"
        tmp.write_bytes(resp.content)
        tmp.replace(BADGE_DIR / f"{key}.{TYPES[kind]}")
        (BADGE_DIR / f"{key}.miss").unlink(missing_ok=True)
        return True
    except Exception as exc:  # noqa: BLE001 — a badge must never fail a refresh
        logger.info("badge %s: %s", url, exc)
        return False


def fetch_missing(urls: list[str]) -> int:
    """Download the badges not on the Pi yet (allowed hosts only). Returns how many were added."""
    ensure_dir()
    todo = sorted({u for u in urls if u and _allowed(u) and not _cached(u) and not _recently_missed(u)})
    if not todo:
        return 0
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            added = sum(pool.map(lambda u: _fetch_one(client, u), todo))
    logger.info("badges: %s new of %s missing", added, len(todo))
    return added


def initials(name: str) -> str:
    """Two letters for a team without a badge: "Manchester United" -> "MU", "Arsenal" -> "AR"."""
    words = [w for w in (name or "").replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()
