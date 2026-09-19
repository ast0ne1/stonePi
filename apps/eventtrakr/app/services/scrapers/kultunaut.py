from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# Kultunaut's card date/venue line is a single <time> text node, e.g.
# "Fre. 18. sep. 2026, Enghave Plads Metro" or, when there's a start time and
# an extra note, "Fre. 18. sep. 2026 19:00 - mulighed for faellesspisning kl.
# 18., Kulturhuset Broek". The venue is always the text after the *last*
# comma; anything after " - " within the date portion is a human note, not
# part of the date, and would otherwise confuse the parser with a second time.


def _parse_date_location(raw: str) -> tuple[datetime | None, str]:
    date_part, _, location_part = raw.rpartition(",")
    if not date_part:
        date_part = raw
    date_part = date_part.split(" - ", 1)[0].strip()
    dt = dateparser.parse(date_part, languages=["da"], settings={"PREFER_DATES_FROM": "future"})
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt, location_part.strip() or "Unspecified"


def is_listing_url(url: str) -> bool:
    return "/perl/arrlist/" in urlsplit(url).path


def with_date_window(url: str, days: int) -> str:
    """Pin the ArrStartdato/ArrSlutdato query params to today..today+days, the
    same way eventbrite.with_date_window() does -- otherwise Kultunaut falls
    back to a much broader (and mostly irrelevant) default range."""
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


class KultunautExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(content, "html.parser")
        events: list[ScrapedEvent] = []

        for card in soup.select(".products.arrlist .product"):
            link_el = card.select_one("a.product-content[href]")
            if not link_el:
                continue

            title_el = card.select_one(".arr-genre h3")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            time_el = card.select_one(".kult-month-day time")
            if not time_el:
                continue
            start_time, location = _parse_date_location(time_el.get_text(strip=True))
            if not start_time:
                continue

            genre_el = card.select_one(".arr-genre .genre_cat")
            desc_el = card.select_one(".arr-description")

            events.append(
                ScrapedEvent(
                    title=title,
                    description=desc_el.get_text(strip=True) if desc_el else "",
                    start_time=start_time,
                    location=location,
                    # The card's a[data-price] attribute is a constant ad/
                    # tracking value (identical across every card, including
                    # free events), not the actual ticket price -- the
                    # listing simply doesn't expose real price in its DOM, so
                    # this is left at the ScrapedEvent default rather than
                    # reporting a made-up number.
                    category=genre_el.get_text(strip=True) if genre_el else "Kultunaut",
                    url=urljoin(base_url, link_el["href"]),
                )
            )

        return events
