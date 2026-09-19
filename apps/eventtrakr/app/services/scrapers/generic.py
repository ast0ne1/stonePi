from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent
from app.services.scrapers.schema_org import SchemaOrgExtractor

logger = logging.getLogger("eventtrakr.scrapers.generic")

PRICE_RE = re.compile(r"(?:[\$£€]\s*\d+(?:\.\d{2})?|free|admission:\s*[^\n,]+)", re.IGNORECASE)


class GenericExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        # Try schema.org first
        schema_extractor = SchemaOrgExtractor()
        events = schema_extractor.extract(content, base_url)
        if events:
            return events

        soup = BeautifulSoup(content, "html.parser")
        extracted: list[ScrapedEvent] = []

        # Look for article or event container elements
        containers = soup.find_all(["article", "li", "div"], class_=re.compile(r"event|listing|show|calendar", re.I))
        if not containers:
            # Fall back to page title / headline as single event if it looks like an event
            h1 = soup.find("h1")
            if h1:
                containers = [soup]

        for container in containers[:25]:
            title_el = container.find(["h1", "h2", "h3", "h4", "a"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if len(title) < 4 or len(title) > 200:
                continue

            link_el = container.find("a", href=True)
            ev_url = urljoin(base_url, link_el["href"]) if link_el else base_url

            # Find date/time
            time_el = container.find(["time", "span", "p"], class_=re.compile(r"date|time", re.I))
            raw_date = time_el.get_text(strip=True) if time_el else ""
            if not raw_date:
                # search for date in text
                text = container.get_text(" ", strip=True)
                match = re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))\b[^\n,]{0,30}", text, re.I)
                if match:
                    raw_date = match.group(0)

            if not raw_date:
                continue

            start_time = dateparser.parse(raw_date)
            if not start_time:
                continue
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)

            # Location
            loc_el = container.find(class_=re.compile(r"location|venue|place|address", re.I))
            location = loc_el.get_text(strip=True) if loc_el else "Unspecified"

            # Cost
            container_text = container.get_text(" ", strip=True)
            cost_match = PRICE_RE.search(container_text)
            cost = cost_match.group(0).strip().capitalize() if cost_match else "Free / Unspecified"

            # Description
            desc_el = container.find(["p", "div"], class_=re.compile(r"desc|summary|info", re.I))
            desc = desc_el.get_text(strip=True)[:500] if desc_el else ""

            extracted.append(
                ScrapedEvent(
                    title=title,
                    description=desc,
                    start_time=start_time,
                    location=location[:250],
                    cost=cost[:90],
                    category="General",
                    url=ev_url,
                )
            )

        return extracted
