from __future__ import annotations

import logging
import re
from datetime import timedelta
from threading import Thread
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.config import CACHE_DIR
from app.models import utcnow

logger = logging.getLogger("eventtrakr.favicon")

FAVICON_DIR = CACHE_DIR / "favicons"
FAVICON_DIR.mkdir(parents=True, exist_ok=True)

# Once we have an icon (or have confirmed a source has none), don't hit the
# network again on every restart -- only recheck occasionally.
RECHECK_COOLDOWN = timedelta(days=7)
MAX_BYTES = 256 * 1024
TIMEOUT = httpx.Timeout(8.0, connect=5.0)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 EventTrakr/1.0"
)

_EXT_BY_CONTENT_TYPE = {
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/png": "png",
    "image/svg+xml": "svg",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def host_key(url: str) -> str:
    """Sanitized bare hostname used as the cache filename stem."""
    raw = url if "://" in url else f"https://{url}"
    host = (urlparse(raw).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return re.sub(r"[^a-z0-9.-]+", "-", host).strip("-.")[:80]


def cached_src(favicon_path: str | None) -> str | None:
    return f"/favicon-cache/{favicon_path}" if favicon_path else None


def _ext_for(content_type: str, url: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _EXT_BY_CONTENT_TYPE:
        return _EXT_BY_CONTENT_TYPE[ct]
    path = urlparse(url).path.lower()
    for ext in ("ico", "png", "svg", "jpg", "jpeg", "gif", "webp"):
        if path.endswith("." + ext):
            return "jpg" if ext == "jpeg" else ext
    return "ico"


def _looks_like_image(content: bytes, content_type: str) -> bool:
    if not content or len(content) > MAX_BYTES:
        return False
    return not (content_type or "").lower().startswith("text/html")


def _find_link_icons(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    hrefs = []
    for link in soup.find_all("link", rel=True):
        rel = " ".join(link.get("rel")).lower() if isinstance(link.get("rel"), list) else str(link.get("rel")).lower()
        href = link.get("href")
        if href and "icon" in rel:
            hrefs.append(urljoin(base_url, href))
    return hrefs


def fetch_favicon(source_url: str) -> str | None:
    """Fetch and cache a favicon for a source's site, returning the cached
    filename to store on the model, or None if nothing usable was found.
    Cheap plain HTTP -- favicon <link> tags are static head content, no need
    for a rendered browser fetch here."""
    key = host_key(source_url)
    if not key:
        return None

    parsed = urlparse(source_url if "://" in source_url else f"https://{source_url}")
    origin = f"{parsed.scheme}://{parsed.netloc}"

    candidates: list[str] = []
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            try:
                resp = client.get(origin)
                if resp.status_code == 200 and "html" in resp.headers.get("content-type", "").lower():
                    candidates.extend(_find_link_icons(resp.text, origin))
            except Exception as e:
                logger.debug("Favicon: homepage fetch failed for %s: %s", origin, e)

            candidates.append(urljoin(origin, "/favicon.ico"))
            candidates.append(f"https://icons.duckduckgo.com/ip3/{key}.ico")

            for candidate in candidates:
                try:
                    icon_resp = client.get(candidate)
                    content_type = icon_resp.headers.get("content-type", "")
                    if icon_resp.status_code == 200 and _looks_like_image(icon_resp.content, content_type):
                        dest = FAVICON_DIR / f"{key}.{_ext_for(content_type, candidate)}"
                        dest.write_bytes(icon_resp.content)
                        return dest.name
                except Exception as e:
                    logger.debug("Favicon: candidate failed %s: %s", candidate, e)
    except Exception as e:
        logger.warning("Favicon fetch failed for %s: %s", source_url, e)

    return None


def backfill(db: Session, rows: list) -> int:
    """Fetch favicons for any of the given rows (CatalogSource/EventSource
    instances) that don't have one yet and aren't within the recheck
    cooldown. Commits as it goes. Returns how many were newly captured."""
    now = utcnow()
    updated = 0
    for row in rows:
        if row.favicon_path:
            continue
        if row.favicon_checked_at and now - row.favicon_checked_at < RECHECK_COOLDOWN:
            continue
        row.favicon_checked_at = now
        path = fetch_favicon(row.url)
        if path:
            row.favicon_path = path
            updated += 1
        db.commit()
    return updated


def backfill_all_async() -> None:
    """Kick off a background pass covering the catalog and every user's
    subscribed sources, so app startup itself doesn't block on network
    calls. Cooldown in backfill() keeps repeat restarts cheap."""

    def _run() -> None:
        from app.db import SessionLocal
        from app.models import CatalogSource, EventSource

        try:
            with SessionLocal() as db:
                catalog_rows = list(db.query(CatalogSource).filter(CatalogSource.is_recommended == True))
                catalog_count = backfill(db, catalog_rows)
            with SessionLocal() as db:
                source_rows = list(db.query(EventSource))
                source_count = backfill(db, source_rows)
            if catalog_count or source_count:
                logger.info(
                    "Favicon backfill: captured %d catalog + %d source icons",
                    catalog_count,
                    source_count,
                )
        except Exception:
            logger.exception("Favicon backfill failed")

    Thread(target=_run, daemon=True).start()
