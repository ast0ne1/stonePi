"""Source favicon capture for AusSportGuide / WheresTheMatch cards."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

import httpx

from app.config import DATA_DIR

logger = logging.getLogger("sportguide.favicon")

FAVICON_DIR = DATA_DIR / "favicons"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(8.0, connect=5.0)
MAX_BYTES = 256 * 1024
ICON_HELPERS = (
    "https://icons.duckduckgo.com/ip3/{host}.ico",
    "https://www.google.com/s2/favicons?domain={host}&sz=64",
)

SOURCE_SITES: dict[str, str] = {
    "ausportguide": "https://ausportguide.com/",
    "wheresthematch": "https://www.wheresthematch.com/",
}


def ensure_dir() -> Path:
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    return FAVICON_DIR


def filename_for(source_id: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]+", "", (source_id or "").lower())[:40] or "source"
    return f"{safe}.png"


def disk_path(source_id: str) -> Path:
    return ensure_dir() / filename_for(source_id)


def cached_src(source_id: str) -> str | None:
    path = disk_path(source_id)
    if path.is_file() and path.stat().st_size > 32:
        return f"/favicons/{path.name}"
    return None


def fallback_src(source_id: str) -> str | None:
    site = SOURCE_SITES.get(source_id) or ""
    host = (urlparse(site).hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    return f"https://www.google.com/s2/favicons?domain={host}&sz=64"


def src_for(source_id: str) -> str | None:
    return cached_src(source_id) or fallback_src(source_id)


def map_for_sources(source_ids: list[str] | None = None) -> dict[str, str]:
    ids = source_ids or list(SOURCE_SITES)
    out: dict[str, str] = {}
    for sid in ids:
        src = src_for(sid)
        if src:
            out[sid] = src
    return out


def capture(source_id: str, site_url: str | None = None) -> Path | None:
    url = (site_url or SOURCE_SITES.get(source_id) or "").strip()
    if not url:
        return None
    dest = disk_path(source_id)
    if dest.is_file() and dest.stat().st_size > 32:
        return dest
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    headers = {"User-Agent": USER_AGENT, "Accept": "image/*,*/*"}
    candidates = [h.format(host=host) for h in ICON_HELPERS]
    candidates.append(f"https://{host}/favicon.ico")
    candidates.append(f"https://www.{host}/favicon.ico")
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=headers) as client:
        for candidate in candidates:
            try:
                resp = client.get(candidate)
                if resp.status_code >= 400:
                    continue
                data = resp.content[:MAX_BYTES]
                if len(data) < 32:
                    continue
                ctype = (resp.headers.get("content-type") or "").lower()
                if "svg" in ctype or "html" in ctype:
                    continue
                dest.write_bytes(data)
                return dest
            except Exception:
                logger.debug("favicon fetch failed %s %s", source_id, candidate, exc_info=True)
    return None


def capture_all_async() -> None:
    def _worker() -> None:
        for sid, site in SOURCE_SITES.items():
            try:
                capture(sid, site)
            except Exception:
                logger.debug("favicon capture failed for %s", sid, exc_info=True)

    Thread(target=_worker, daemon=True, name="sportguide-favicon").start()
