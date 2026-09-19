from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent
from app.services.scrapers.schema_org import SchemaOrgExtractor

logger = logging.getLogger("eventtrakr.scrapers.meetup")


class MeetupExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        # 1. Schema.org JSON-LD
        schema_extractor = SchemaOrgExtractor()
        events = schema_extractor.extract(content, base_url)
        if events:
            return events

        # 2. HTML selectors
        soup = BeautifulSoup(content, "html.parser")
        cards = soup.select("div[data-testid='categoryEventCard'], div.eventCard, div[data-element-name='eventCard']")
        for card in cards:
            title_el = card.select_one("h2, h3, [data-testid='event-card-title']")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            link_el = card.select_one("a[href]")
            ev_url = urljoin(base_url, link_el["href"]) if link_el else base_url

            time_el = card.select_one("time, [data-testid='event-date-time']")
            raw_time = time_el.get_text(strip=True) if time_el else ""
            start_time = None
            if raw_time:
                start_time = dateparser.parse(raw_time)
                if start_time and start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)

            if not start_time:
                continue

            loc_el = card.select_one("[data-testid='event-card-location'], p.text-sm")
            location = loc_el.get_text(strip=True) if loc_el else "Online / In-person"

            events.append(
                ScrapedEvent(
                    title=title,
                    description="",
                    start_time=start_time,
                    location=location,
                    cost="Free",
                    category="Meetup",
                    url=ev_url,
                )
            )
        return events
