"""Homepage + RSS URL pair for feeds; Feed.url stays the active fetch endpoint."""

from __future__ import annotations

from urllib.parse import urlparse

from app.models import Feed

SOURCE_TYPES = {"rss", "webpage", "auto"}


def clean_http_url(value: str | None) -> str:
    return (value or "").strip()


def is_http_url(value: str | None) -> bool:
    text = clean_http_url(value)
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_feed_type(value: str | None, *, default: str = "rss") -> str:
    kind = (value or default).strip().lower()
    return kind if kind in SOURCE_TYPES else default


def active_url_for(
    feed_type: str | None,
    homepage_url: str | None,
    rss_url: str | None,
) -> str:
    """Pick the URL ingest should hit for the selected source type."""
    kind = normalize_feed_type(feed_type)
    home = clean_http_url(homepage_url)
    rss = clean_http_url(rss_url)
    if kind == "rss":
        return rss or home
    if kind == "webpage":
        return home or rss
    # auto: prefer homepage for discovery, fall back to RSS-only sources
    return home or rss


def required_url_for_type(feed_type: str | None, homepage_url: str | None, rss_url: str | None) -> str | None:
    """Return an error message when the selected type lacks its URL; else None."""
    kind = normalize_feed_type(feed_type)
    home = clean_http_url(homepage_url)
    rss = clean_http_url(rss_url)
    if kind == "rss" and not rss and not home:
        return "Enter an RSS feed URL (or a homepage to fall back on)."
    if kind == "rss" and not rss and home:
        # Allow homepage-only when switching before they paste RSS — but prefer requiring RSS.
        # Plan: RSS type requires RSS URL.
        return "Enter an RSS feed URL for RSS mode."
    if kind == "webpage" and not home:
        return "Enter a homepage / scrape URL for scrape mode."
    if kind == "auto" and not home and not rss:
        return "Enter a homepage or RSS URL."
    if home and not is_http_url(home):
        return "Homepage URL must be http(s)."
    if rss and not is_http_url(rss):
        return "RSS URL must be http(s)."
    if not active_url_for(kind, home, rss):
        return "Enter a homepage or RSS URL for the selected source type."
    return None


def sync_feed_urls(feed: Feed) -> str:
    """Set feed.url from type + homepage_url / rss_url. Raises ValueError if empty."""
    home = clean_http_url(getattr(feed, "homepage_url", None))
    rss = clean_http_url(getattr(feed, "rss_url", None))
    feed.homepage_url = home or None
    feed.rss_url = rss or None
    active = active_url_for(feed.type, home, rss)
    if not active:
        raise ValueError("Need a homepage or RSS URL for the selected source type.")
    feed.url = active
    return active


def set_feed_url_pair(
    feed: Feed,
    *,
    homepage_url: str | None = None,
    rss_url: str | None = None,
    feed_type: str | None = None,
) -> str:
    """Update stored pair and/or type, then sync active url."""
    if homepage_url is not None:
        cleaned = clean_http_url(homepage_url)
        feed.homepage_url = cleaned or None
    if rss_url is not None:
        cleaned = clean_http_url(rss_url)
        feed.rss_url = cleaned or None
    if feed_type is not None:
        feed.type = normalize_feed_type(feed_type, default=feed.type or "rss")
    return sync_feed_urls(feed)


def apply_catalog_url_pair(feed: Feed, item: dict, feed_type: str | None = None) -> str:
    """Fill missing homepage/rss from catalog without wiping user-customized sides; sync url."""
    from app.services.catalog import catalog_homepage_url, catalog_rss_url, source_kind

    kind = normalize_feed_type(feed_type or feed.type or source_kind(item), default=source_kind(item))
    feed.type = kind
    catalog_home = catalog_homepage_url(item)
    catalog_rss = catalog_rss_url(item)
    if not clean_http_url(getattr(feed, "homepage_url", None)) and catalog_home:
        feed.homepage_url = catalog_home
    if not clean_http_url(getattr(feed, "rss_url", None)) and catalog_rss:
        feed.rss_url = catalog_rss
    # If still empty on one side and catalog only has one URL under `url`, use it.
    if kind == "rss" and not clean_http_url(feed.rss_url) and catalog_home and not catalog_rss:
        feed.rss_url = catalog_home
    if kind in {"webpage", "auto"} and not clean_http_url(feed.homepage_url) and catalog_home:
        feed.homepage_url = catalog_home
    return sync_feed_urls(feed)
