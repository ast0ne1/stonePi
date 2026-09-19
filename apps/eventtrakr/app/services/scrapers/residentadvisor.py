from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# RA's listing page is server-rendered Next.js, so the full event data --
# title, times, venue -- is already sitting in the page's own __NEXT_DATA__
# script tag as a normalized Apollo (GraphQL) cache, keyed like "Event:123"
# with "__ref" pointers to related nodes (e.g. "Venue:456"). Reading that
# structured JSON directly is far more robust than scraping RA's generic,
# hashed CSS-module class names.


def _resolve_ref(apollo: dict, ref: dict | None) -> dict | None:
    if not ref or "__ref" not in ref:
        return None
    return apollo.get(ref["__ref"])


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_listing_url(url: str) -> bool:
    return "/events/" in urlsplit(url).path


def with_date_window(url: str, days: int) -> str:
    if not is_listing_url(url):
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    today = date.today()
    end = today + timedelta(days=days)
    query["startDate"] = [today.isoformat()]
    query["endDate"] = [end.isoformat()]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


class ResidentAdvisorExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(content, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            return []

        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            return []

        apollo = data.get("props", {}).get("apolloState", {})
        events: list[ScrapedEvent] = []

        for key, node in apollo.items():
            if not key.startswith("Event:") or not isinstance(node, dict):
                continue

            title = node.get("title")
            start_time = _parse_iso(node.get("startTime")) or _parse_iso(node.get("date"))
            if not title or not start_time:
                continue

            venue = _resolve_ref(apollo, node.get("venue"))
            location = venue.get("name") if venue and venue.get("name") else "Unspecified"

            content_url = node.get("contentUrl")
            url = f"https://ra.co{content_url}" if content_url else base_url

            events.append(
                ScrapedEvent(
                    title=title,
                    description="",
                    start_time=start_time,
                    end_time=_parse_iso(node.get("endTime")),
                    location=location,
                    url=url,
                )
            )

        return events
