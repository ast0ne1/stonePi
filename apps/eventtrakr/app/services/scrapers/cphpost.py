from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# The Copenhagen Post's calendar is an English-language white-label of the
# same Kultunaut events platform (compare its "Buy ticket" links, which point
# straight at kultunaut.dk) -- same event data, different markup and already
# in English, so no Danish date parsing is needed here. Its card splits date
# and time into two separate spans, e.g. "Fri 18 Sep 2026" + "06:45 - 7.45 am"
# (or a longer human note like "7:00 PM - possibility of dining together at 6
# PM."), or no time span at all for an all-day/unspecified-time listing.


def _parse_date_time(date_text: str, time_text: str) -> datetime | None:
    time_part = time_text.split(" - ", 1)[0].strip()
    if time_part.lower().startswith("at "):
        time_part = time_part[3:].strip()
    raw = f"{date_text.strip()} {time_part}".strip()
    dt = dateparser.parse(raw, settings={"PREFER_DATES_FROM": "future"})
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_listing_url(url: str) -> bool:
    return "/calendar" in urlsplit(url).path


def with_date_window(url: str, days: int) -> str:
    """Pin ArrStartdato/ArrSlutdato the same way kultunaut.with_date_window()
    does -- this is the same underlying platform, just white-labelled."""
    if not is_listing_url(url):
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    today = date.today()
    end = today + timedelta(days=days)
    query["ArrStartdato"] = [f"{today.day}/{today.month} {today.year}"]
    query["ArrSlutdato"] = [f"{end.day}/{end.month} {end.year}"]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


class CphPostExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(content, "html.parser")
        events: list[ScrapedEvent] = []

        for card in soup.select(".nautmasonryitem"):
            title_el = card.select_one(".eventtitle")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            date_time_el = card.select_one(".date_time")
            if not date_time_el:
                continue
            spans = date_time_el.find_all("span")
            if not spans:
                continue
            date_text = spans[0].get_text(strip=True)
            time_text = spans[1].get_text(strip=True) if len(spans) > 1 else ""
            start_time = _parse_date_time(date_text, time_text)
            if not start_time:
                continue

            place_el = card.select_one(".eventplace")
            location = place_el.get_text(strip=True) if place_el else "Unspecified"

            link_el = card.select_one(".button_rm_ticket a.readmore[href]")
            ev_url = urljoin(base_url, link_el["href"]) if link_el else base_url

            events.append(
                ScrapedEvent(
                    title=title,
                    description="",
                    start_time=start_time,
                    location=location,
                    url=ev_url,
                )
            )

        return events
