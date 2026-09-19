from __future__ import annotations

import logging
from datetime import date, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent
from app.services.scrapers.schema_org import SchemaOrgExtractor

logger = logging.getLogger("eventtrakr.scrapers.eventbrite")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 EventTrakr/1.0"
)
# Eventbrite's listing page renders price client-side (a loading skeleton sits
# in the DOM at fetch time), so a plain HTTP GET of the listing never sees it.
# Individual event pages, by contrast, ship a full schema.org Event with a
# real AggregateOffer server-side. We only follow up for events that came
# back without a price, and cap how many per sync to bound the extra requests.
MAX_PRICE_LOOKUPS = 15


def is_listing_url(url: str) -> bool:
    """Whether this is an Eventbrite discovery/listing URL (as opposed to a
    specific event or organizer page) -- only those support the
    start_date/end_date query params used by with_date_window()."""
    return "/d/" in urlsplit(url).path


def with_date_window(url: str, days: int) -> str:
    """Pin an Eventbrite discovery/listing URL's date range to today..today+days
    so the (browser-rendered) result set matches what we'll keep after our own
    lookahead filter, instead of whatever broad default range Eventbrite picks."""
    if not is_listing_url(url):
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    today = date.today()
    query["start_date"] = [today.isoformat()]
    query["end_date"] = [(today + timedelta(days=days)).isoformat()]
    query.setdefault("page", ["1"])
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


class EventbriteExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        soup = BeautifulSoup(content, "html.parser")
        cards = soup.select("section.event-card-details")
        for card in cards:
            title_el = card.select_one("h3.event-card__clamp-line--two, h2, h3")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            link_el = card.select_one("a[href]")
            ev_url = urljoin(base_url, link_el["href"]) if link_el else base_url

            loc_el = card.select_one("p.event-card__clamp-line--one")
            date_el = loc_el.find_previous_sibling("p") if loc_el else None
            raw_date = date_el.get_text(strip=True) if date_el else ""
            start_time = None
            if raw_date:
                start_time = dateparser.parse(raw_date)
                if start_time and start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)

            if not start_time:
                continue

            location = loc_el.get_text(strip=True) if loc_el else "Unspecified"

            # The listing card's price node is usually still a loading
            # skeleton in the raw HTML, but check it anyway in case a future
            # markup change ships it server-rendered.
            cost_el = card.select_one('[class*="priceWrapper"] p')
            cost = cost_el.get_text(strip=True) if cost_el else "Free / Unspecified"

            events.append(
                ScrapedEvent(
                    title=title,
                    description="",
                    start_time=start_time,
                    location=location,
                    cost=cost,
                    category="Eventbrite",
                    url=ev_url,
                )
            )

        events = self._dedupe(events)
        self._fill_missing_prices(events, base_url)
        return events

    @staticmethod
    def _dedupe(events: list[ScrapedEvent]) -> list[ScrapedEvent]:
        # The listing page renders each event card twice (a hidden duplicate
        # kept in the DOM, presumably for a responsive layout variant).
        seen: set[tuple[str, object]] = set()
        deduped = []
        for ev in events:
            key = (ev.url or ev.title, ev.start_time)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ev)
        return deduped

    def _fill_missing_prices(self, events: list[ScrapedEvent], base_url: str) -> None:
        pending = [ev for ev in events if ev.cost == "Free / Unspecified" and ev.url][:MAX_PRICE_LOOKUPS]
        if not pending:
            return

        schema_extractor = SchemaOrgExtractor()
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
                for ev in pending:
                    try:
                        resp = client.get(ev.url)
                        if resp.status_code != 200:
                            continue
                        detail_events = schema_extractor.extract(resp.text, ev.url)
                        if detail_events and detail_events[0].cost != "Free / Unspecified":
                            ev.cost = detail_events[0].cost
                    except Exception as e:
                        logger.debug("Eventbrite price lookup failed for %s: %s", ev.url, e)
        except Exception as e:
            logger.warning("Eventbrite price lookup batch failed: %s", e)
