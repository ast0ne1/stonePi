from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import re

from lxml import html

COMMON_FEED_PATHS = ("/RSS/rss.ashx", "/feed/", "/rss.xml", "/rss")
DATE_PATH = re.compile(r"/(\d{4})/(\d{2})/(\d{2})(?:/|$)")
YEAR_MONTH_PATH = re.compile(r"/(\d{4})/(\d{2})(?:/|$)")
NOISE_TITLE = re.compile(
    r"^(see all\b|read more|…read more|\d+\s+comments?|no comments|older posts|next page|previous page)\b",
    re.I,
)
FEATURED_CLASS = re.compile(r"featured|carousel|slider|slides|hero-promo", re.I)

SKIP_PATHS = {
    "",
    "rss",
    "feed",
    "feeds",
    "atom",
    "login",
    "signin",
    "signup",
    "register",
    "about",
    "contact",
    "privacy",
    "terms",
    "search",
    "tag",
    "tags",
    "category",
    "author",
    "wp-login.php",
    "cart",
    "account",
    "blog",
}
SKIP_EXT = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js", ".pdf", ".zip", ".mp4"}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def discover_rss(page_url: str, page_html: str) -> str | None:
    try:
        tree = html.fromstring(page_html)
    except Exception:
        tree = None
    if tree is not None:
        for link in tree.xpath("//link[@rel='alternate']"):
            kind = (link.get("type") or "").lower()
            if "rss" in kind or "atom" in kind or "xml" in kind:
                href = (link.get("href") or "").strip()
                if href:
                    return urljoin(page_url, href)
    for candidate in guess_feed_urls(page_url):
        return candidate
    return None


def guess_feed_urls(page_url: str) -> list[str]:
    parsed = urlparse(page_url)
    if not parsed.scheme or not parsed.netloc:
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    found: list[str] = []
    for path in COMMON_FEED_PATHS:
        candidate = root + path
        if candidate.rstrip("/") != page_url.rstrip("/"):
            found.append(candidate)
    return found


def looks_like_feed_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(token in path for token in ("/feed", "/rss", "/atom", ".xml", ".rss", ".atom"))


def published_from_url(url: str) -> datetime | None:
    path = urlparse(url).path
    match = DATE_PATH.search(path)
    if match:
        try:
            return datetime(int(match[1]), int(match[2]), int(match[3]), tzinfo=timezone.utc)
        except ValueError:
            return None
    match = YEAR_MONTH_PATH.search(path)
    if not match:
        return None
    try:
        # Year/month blogs (e.g. Krebs) omit the day; use the 1st for ordering.
        return datetime(int(match[1]), int(match[2]), 1, tzinfo=timezone.utc)
    except ValueError:
        return None


def _anchor_title(anchor) -> str:
    for node in anchor.xpath(".//h1|.//h2|.//h3|.//h4"):
        text = " ".join((node.text_content() or "").split())
        if len(text) >= 16:
            return text
    parent = anchor.getparent()
    if parent is not None:
        for node in parent.xpath("./h1|./h2|./h3|./h4|.//h1|.//h2|.//h3|.//h4"):
            text = " ".join((node.text_content() or "").split())
            if len(text) >= 16:
                return text
    text = " ".join((anchor.text_content() or "").split())
    if len(text) > 120:
        # Card links often concatenate headline + blurb; keep the first clause.
        for sep in (". ", "! ", "? "):
            cut = text.find(sep)
            if 16 <= cut <= 120:
                return text[: cut + 1].strip()
        return text[:120].rsplit(" ", 1)[0].strip()
    return text


def _in_featured(anchor) -> bool:
    for node in anchor.iterancestors():
        classes = " ".join(node.get("class") or "").strip()
        if classes and FEATURED_CLASS.search(classes):
            return True
        if node.tag in {"body", "html"}:
            break
    return False


def article_candidates(page_url: str, page_html: str, limit: int = 12) -> list[dict]:
    try:
        tree = html.fromstring(page_html)
    except Exception:
        return []
    host = _host(page_url)
    seen: set[str] = set()
    found: list[dict] = []
    for anchor in tree.xpath("//a[@href]"):
        href = urljoin(page_url, anchor.get("href") or "")
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.fragment:
            continue
        if _host(href) != host:
            continue
        path = parsed.path.rstrip("/")
        parts = [part for part in path.lstrip("/").split("/") if part]
        first = parts[0].lower() if parts else ""
        if first in SKIP_PATHS and not DATE_PATH.search(path):
            continue
        if any(parsed.path.lower().endswith(ext) for ext in SKIP_EXT):
            continue
        title = _anchor_title(anchor)
        if len(title) < 16 or NOISE_TITLE.match(title):
            continue
        clean = href.split("#", 1)[0].rstrip("/")
        if clean in seen or clean == page_url.rstrip("/"):
            continue
        published = published_from_url(clean)
        featured = _in_featured(anchor)
        seen.add(clean)
        found.append(
            {
                "title": title,
                "url": clean,
                "published": published,
                "featured": featured,
            }
        )
    dated = [item for item in found if item.get("published")]
    if dated:
        found = dated
    found.sort(
        key=lambda item: (
            1 if item.get("featured") else 0,
            -(item["published"].timestamp() if item.get("published") else 0),
        )
    )
    return [{"title": item["title"], "url": item["url"]} for item in found[:limit]]
