"""Browse webradio via myMPD (Odio’s WebradioDB UI backend on :8080).

Odio’s own REST API (:8018) has no station catalog — only playback/volume.
Station name, genres, country, and Image come from myMPD JSON-RPC.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.config import env

logger = logging.getLogger("radio.mympd")


def _base() -> str:
    return (env.mympd_url or "http://127.0.0.1:8080").rstrip("/")


def ping() -> dict[str, Any]:
    if env.mock:
        return {"ok": True, "reachable": True, "url": "mock://mympd", "mock": True}
    with httpx.Client(timeout=4.0) as client:
        try:
            resp = client.get(f"{_base()}/")
        except httpx.HTTPError as exc:
            return {"ok": False, "reachable": False, "url": _base(), "error": str(exc)}
    return {
        "ok": resp.status_code < 500,
        "reachable": resp.status_code < 500,
        "url": _base(),
        "status": resp.status_code,
    }


def _rpc(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    with httpx.Client(timeout=8.0) as client:
        try:
            resp = client.post(f"{_base()}/api", json=payload)
        except httpx.HTTPError as exc:
            logger.debug("myMPD RPC failed: %s", exc)
            return None
    if resp.status_code >= 400:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("error"):
        logger.debug("myMPD error: %s", data.get("error"))
        return None
    result = data.get("result")
    return result if isinstance(result, dict) else None


# WebradioDB names each station's picture ("Stream1_liferadio_at.webp"); the pictures are
# published here. myMPD's own copy is on loopback, which a phone or laptop can't reach.
WEBRADIODB_PICS = "https://jcorporation.github.io/webradiodb/db/pics/"


def _image_url(image: str) -> str:
    image = (image or "").strip()
    if not image:
        return ""
    if image.startswith("http://") or image.startswith("https://"):
        return image
    if image.startswith("/"):
        return ""  # a picture only myMPD serves: the card shows the station's letter
    return WEBRADIODB_PICS + quote(image.rsplit("/", 1)[-1], safe="")


def _normalize(row: dict[str, Any], fav_urls: set[str]) -> dict[str, Any] | None:
    url = str(row.get("StreamUri") or row.get("streamUri") or "").strip()
    if not url:
        return None
    name = str(row.get("Name") or row.get("name") or "Station").strip() or "Station"
    genres = row.get("Genres") or row.get("genres") or []
    if isinstance(genres, list):
        tags = ",".join(str(g) for g in genres if g)
    else:
        tags = str(genres or "")
    station_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return {
        "id": station_id,
        "name": name,
        "stream_url": url,
        "favicon": _image_url(str(row.get("Image") or row.get("image") or "")),
        "tags": tags,
        "country": str(row.get("Country") or row.get("country") or "").strip(),
        "homepage": str(row.get("Homepage") or row.get("homepage") or "").strip(),
        "favorite": url in fav_urls,
        "source": "mympd",
        "codec": str(row.get("Codec") or ""),
        "bitrate": row.get("Bitrate") or 0,
    }


def _expression(query: str) -> str:
    """myMPD webradio search expression; empty → match all."""
    q = (query or "").strip().replace("'", "")
    if not q:
        return "((any contains ''))"
    return f"((any contains '{q}'))"


def search(query: str = "", *, limit: int = 40, offset: int = 0) -> list[dict[str, Any]]:
    """WebradioDB search (with images). Falls back to favorites list if DB search empty."""
    if env.mock:
        return []
    fav_urls: set[str] = set()  # routes mark the signed-in person's favorites
    result = _rpc(
        "MYMPD_API_WEBRADIODB_SEARCH",
        {
            "offset": offset,
            "limit": limit,
            "expression": _expression(query),
            "sort": "Name",
            "sortdesc": False,
        },
    )
    rows = (result or {}).get("data") if result else None
    if not isinstance(rows, list) or not rows:
        # Favorites curated on the Odio node (myMPD)
        result = _rpc(
            "MYMPD_API_WEBRADIO_FAVORITE_SEARCH",
            {
                "offset": offset,
                "limit": limit,
                "expression": _expression(query),
                "sort": "Name",
                "sortdesc": False,
            },
        )
        rows = (result or {}).get("data") if result else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            station = _normalize(row, fav_urls)
            if station:
                out.append(station)
    return out
