from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# Madbillet's date cells are plain Danish text with no year, e.g. "20. sep"
# for a single day or "1. maj - 31. dec" for a running campaign (a fundraiser
# or series that spans months, not a one-off event).
_RANGE_SPLIT_RE = re.compile(r"\s*-\s*")


def _parse_danish_date(raw: str, prefer_future: bool = True) -> datetime | None:
    dt = dateparser.parse(
        raw,
        languages=["da"],
        settings={"PREFER_DATES_FROM": "future" if prefer_future else "past"},
    )
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_event_date(raw_date: str, now: datetime) -> tuple[datetime | None, datetime | None]:
    """Returns (start_time, end_time). For a running campaign, start_time is
    clamped to "now" if it already started, so it still surfaces as
    currently-on rather than being filtered out as a past event."""
    parts = _RANGE_SPLIT_RE.split(raw_date.strip())
    if len(parts) != 2:
        dt = _parse_danish_date(raw_date)
        return dt, None

    end_time = _parse_danish_date(parts[1])
    if not end_time:
        return None, None

    # Resolve the start against the end's year -- parsing it independently
    # with PREFER_DATES_FROM="future" would wrongly push a campaign's early
    # start date (e.g. "1. maj" while we're already in September) a full
    # year out, since it has no idea the two dates are linked.
    start_time = _parse_danish_date(f"{parts[0]} {end_time.year}", prefer_future=False)
    if start_time and start_time > end_time:
        start_time = start_time.replace(year=start_time.year - 1)

    if start_time and start_time <= now <= end_time:
        start_time = now

    return start_time, end_time


class MadbilletExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(content, "html.parser")
        now = datetime.now(timezone.utc)
        events: list[ScrapedEvent] = []

        for item in soup.select(".event-item"):
            title_el = item.select_one(".event-title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            date_el = item.select_one(".event-date")
            if not date_el:
                continue
            start_time, end_time = _parse_event_date(date_el.get_text(strip=True), now)
            if not start_time:
                continue

            link_el = item.select_one("a[href]")
            ev_url = urljoin(base_url, link_el["href"]) if link_el else base_url

            price_el = item.select_one(".event-price")
            cost = f"{price_el.get_text(strip=True)}" if price_el else "Free / Unspecified"

            pretitle_el = item.select_one(".event-pre-title")
            description = pretitle_el.get_text(strip=True) if pretitle_el else ""

            events.append(
                ScrapedEvent(
                    title=title,
                    description=description,
                    start_time=start_time,
                    end_time=end_time,
                    location="Unspecified",
                    cost=cost,
                    category="Food & Drink",
                    url=ev_url,
                )
            )

        return self._dedupe(events)

    @staticmethod
    def _dedupe(events: list[ScrapedEvent]) -> list[ScrapedEvent]:
        # Some listing rows render twice (a hidden duplicate, same as
        # Eventbrite's listing cards).
        seen: set[tuple[str, object]] = set()
        deduped = []
        for ev in events:
            key = (ev.url or ev.title, ev.start_time)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ev)
        return deduped
