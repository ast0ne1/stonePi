from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# BrugByen's card date text is one of two shapes:
#  - "Fri. 18 Sep. 2026 At 11.00 - 18.00" (a single day, with a time range)
#  - "18.09.2026 - 26.09.2026" (a multi-day run, no time of day)
# dateparser chokes on either combined string as-is, so the "At"/time-range
# and any trailing end-date both need stripping before parsing the start.


def _parse_date_text(raw: str) -> datetime | None:
    date_part, sep, time_part = raw.partition(" At ")
    if sep:
        time_start = time_part.split(" - ", 1)[0].strip()
        candidate = f"{date_part.strip()} {time_start}"
    else:
        candidate = raw.split(" - ", 1)[0].strip()
    dt = dateparser.parse(candidate, settings={"PREFER_DATES_FROM": "future"})
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_listing_url(url: str) -> bool:
    return "/whats-on" in urlsplit(url).path


def with_date_window(url: str, days: int) -> str:
    if not is_listing_url(url):
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    today = date.today()
    end = today + timedelta(days=days)
    query["dates[min]"] = [today.isoformat()]
    query["dates[max]"] = [end.isoformat()]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


class BrugbyenExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(content, "html.parser")
        events: list[ScrapedEvent] = []

        for card in soup.select(".node--type-event"):
            title_el = card.select_one(".title h3")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            date_el = card.select_one(".field--name-scheduled-dates .date-text")
            if not date_el:
                continue
            start_time = _parse_date_text(date_el.get_text(strip=True))
            if not start_time:
                continue

            location_el = card.select_one(".pre-title")
            location = location_el.get_text(strip=True) if location_el else "Unspecified"

            link_el = card.select_one("a[href]")
            url = urljoin(base_url, link_el["href"]) if link_el else base_url

            events.append(
                ScrapedEvent(
                    title=title,
                    description="",
                    start_time=start_time,
                    location=location,
                    url=url,
                )
            )

        return events
