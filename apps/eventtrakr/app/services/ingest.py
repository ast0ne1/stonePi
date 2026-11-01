from __future__ import annotations

import hashlib
import logging
import re
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event as ThreadEvent, Lock, Thread
from urllib.parse import urlparse

import httpx
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import env
from app.db import SessionLocal
from app.models import Category, DedupeExclusion, Event, EventSighting, EventSource, SourceFetch, utcnow
from app.services.cost import is_unspecified_cost
from app.services import timespan
from app.services.auto_category import category_key_for_title
from app.services.dedupe import Listing, compute_event_fingerprint, fold, has_time_of_day, match_reason, venue_similarity
from app.services.scrapers.base import ScrapedEvent
from app.services.scrapers.bandsintown import is_listing_url as is_bandsintown_listing_url
from app.services.scrapers.brightdata_facebook import BrightDataError, fetch_events as fetch_brightdata_facebook_events
from app.services.scrapers.bandsintown import with_date_window as with_bandsintown_date_window
from app.services.scrapers.browser_fetch import browser_session, fetch_rendered_html
from app.services.scrapers.brugbyen import BrugbyenExtractor, is_kk_event_site, read_schedule
from app.services.scrapers.brugbyen import is_listing_url as is_brugbyen_listing_url
from app.services.scrapers.brugbyen import with_date_window as with_brugbyen_date_window
from app.services.scrapers.cphpost import CphPostExtractor
from app.services.scrapers.cphpost import is_listing_url as is_cphpost_listing_url
from app.services.scrapers.cphpost import with_date_window as with_cphpost_date_window
from app.services.scrapers.eventbrite import EventbriteExtractor, is_listing_url, with_date_window
from app.services.scrapers.generic import GenericExtractor
from app.services.scrapers.ics_feed import IcsFeedExtractor
from app.services.scrapers.kultunaut import KultunautExtractor
from app.services.scrapers.kultunaut import is_listing_url as is_kultunaut_listing_url
from app.services.scrapers.kultunaut import with_date_window as with_kultunaut_date_window
from app.services.scrapers.madbillet import MadbilletExtractor
from app.services.scrapers.meetup import MeetupExtractor
from app.services.scrapers.jungle import JungleExtractor, data_url as jungle_data_url, is_jungle_url
from app.services.scrapers.migogkbh import MigogKbhExtractor
from app.services.scrapers.residentadvisor import ResidentAdvisorExtractor
from app.services.scrapers.residentadvisor import is_listing_url as is_ra_listing_url
from app.services.scrapers.residentadvisor import with_date_window as with_ra_date_window
from app.services.scrapers.songkick import is_listing_url as is_songkick_listing_url
from app.services.scrapers.songkick import with_date_window as with_songkick_date_window
from app.services.scrapers.schema_org import SchemaOrgExtractor
from app.services import settings as settings_service

logger = logging.getLogger("eventtrakr.ingest")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 EventTrakr/1.0"
)
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_lock = Lock()


class IngestState:
    running: bool = False
    progress: str = ""
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_error: str | None = None
    last_new_events: int = 0
    last_message: str = "Idle"
    stopping: bool = False


state = IngestState()
# Set by request_stop(); manual syncs check it between sources (a source mid-fetch finishes first).
_stop_requested = ThreadEvent()


def request_stop() -> bool:
    """Ask the running manual sync to stop before its next source. False when nothing is running."""
    if not state.running:
        return False
    _stop_requested.set()
    state.stopping = True
    return True


def _reset_stop() -> None:
    _stop_requested.clear()
    state.stopping = False


def get_scraper_for_source(source: EventSource):
    url_lower = source.url.lower()
    if source.source_type == "ics" or url_lower.endswith(".ics") or "webcal://" in url_lower:
        return IcsFeedExtractor()
    if "eventbrite." in url_lower:
        return EventbriteExtractor()
    if "meetup.com" in url_lower:
        return MeetupExtractor()
    if "madbillet.dk" in url_lower:
        return MadbilletExtractor()
    if "kultunaut.dk" in url_lower:
        return KultunautExtractor()
    if "migogkbh.dk" in url_lower:
        return MigogKbhExtractor()
    if is_jungle_url(source.url):
        return JungleExtractor()
    if "cphpost.dk" in url_lower:
        return CphPostExtractor()
    if "ra.co" in url_lower:
        return ResidentAdvisorExtractor()
    if is_kk_event_site(source.url):
        return BrugbyenExtractor()
    if source.source_type == "supported":
        return SchemaOrgExtractor()
    return GenericExtractor()


def is_ics_source(source: EventSource) -> bool:
    url = source.url
    if url.startswith("webcal://"):
        return True
    return source.source_type == "ics" or url.lower().endswith(".ics")


def _passes_keywords(title: str, desc: str, location: str, inc: str, exc: str) -> bool:
    blob = f"{title} {desc} {location}".lower()
    if inc:
        terms = [t.strip().lower() for t in inc.split(",") if t.strip()]
        if terms and not any(t in blob for t in terms):
            return False
    if exc:
        terms = [t.strip().lower() for t in exc.split(",") if t.strip()]
        if any(t in blob for t in terms):
            return False
    return True


def fetch_and_extract_source(
    db: Session,
    source: EventSource,
    browser=None,
    max_lookahead_days: int | None = None,
) -> tuple[int, int]:
    """Fetch a single source, applying caching, a lookahead horizon, and
    deduplication. Returns (new_events_count, total_events_found).

    Pass an existing `browser` (from browser_fetch.browser_session()) when
    fetching several sources in one pass, so headless Chromium is only
    launched once for the whole batch instead of per source.

    `max_lookahead_days` defaults to env.max_lookahead_days (the normal 7-day
    Agenda horizon). Pass a larger value for an on-demand deep search (see
    ui.py search()) that needs to look further out than the routine sync
    does; events found that way are stored and treated exactly like any
    other event (favouritable, kept until their date passes) -- they just
    won't show up in the Agenda's own 7-day view.
    """
    effective_lookahead_days = max_lookahead_days if max_lookahead_days is not None else env.max_lookahead_days

    url = source.url
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]

    url_lower = url.lower()
    is_ics = is_ics_source(source)
    is_eventbrite_listing = "eventbrite." in url_lower and is_listing_url(url)
    is_kultunaut_listing = "kultunaut.dk" in url_lower and is_kultunaut_listing_url(url)
    is_migogkbh = "migogkbh.dk" in url_lower
    is_cphpost_listing = "cphpost.dk" in url_lower and is_cphpost_listing_url(url)
    is_ra_listing = "ra.co" in url_lower and is_ra_listing_url(url)
    is_songkick_listing = "songkick.com" in url_lower and is_songkick_listing_url(url)
    is_bandsintown_listing = "bandsintown.com" in url_lower and is_bandsintown_listing_url(url)
    is_brugbyen_listing = is_kk_event_site(url) and is_brugbyen_listing_url(url)
    is_facebook = "facebook.com" in url_lower
    is_jungle = is_jungle_url(url)

    # Facebook's own event search requires a login our browser-based scraper
    # can't provide (an unauthenticated fetch gets an empty page shell), so
    # it's routed through Bright Data's hosted scraper instead of the normal
    # browser-render/HTTP paths below -- see brightdata_facebook.py.
    if is_facebook:
        from app.models import User
        from app.services import capabilities

        if not capabilities.user_can(db.get(User, source.user_id), capabilities.USE_SOCIAL):
            # Paid Bright Data: only for people allowed "Instagram & Facebook".
            source.last_error = capabilities.denied_message(capabilities.USE_SOCIAL)
            source.last_fetched_at = utcnow()
            db.commit()
            return 0, 0
        api_key = settings_service.get_brightdata_api_key(db)
        if not api_key:
            source.last_error = "Bright Data API key not configured (Settings > Data Providers)"
            source.last_fetched_at = utcnow()
            db.commit()
            return 0, 0
        try:
            scraped_events = fetch_brightdata_facebook_events(api_key, url)
        except BrightDataError as e:
            source.last_error = str(e)
            source.last_fetched_at = utcnow()
            db.commit()
            logger.warning("Bright Data Facebook fetch failed for %s: %s", url, e)
            return 0, 0
        source.last_status_code = 200
        source.last_fetched_at = utcnow()
        return _apply_scraped_events(db, source, scraped_events, effective_lookahead_days)

    # Jungle is a SvelteKit app: its events come straight from the page's
    # __data.json over plain HTTP (see scrapers/jungle.py), no browser needed.
    if is_jungle:
        fetch_url = jungle_data_url(url)
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(fetch_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        except Exception as e:
            source.last_error = str(e)
            source.last_fetched_at = utcnow()
            db.commit()
            logger.warning("Failed fetching %s: %s", fetch_url, e)
            return 0, 0
        source.last_status_code = resp.status_code
        source.last_fetched_at = utcnow()
        if resp.status_code != 200:
            source.last_error = f"HTTP {resp.status_code}"
            db.commit()
            return 0, 0
        scraped_events = JungleExtractor().extract(resp.text, url)
        return _apply_scraped_events(db, source, scraped_events, effective_lookahead_days, resp.status_code)

    # Every scraped web source is rendered with a real headless browser rather
    # than a plain HTTP GET -- several sites hand a plain HTTP client
    # different/incomplete content (client-rendered data, query params only
    # honoured for browser-shaped requests, etc.; see EventbriteExtractor for
    # the clearest example), and a real browser sidesteps that entirely. ICS/
    # webcal feeds are plain data files, not pages, so they stay on the
    # lightweight HTTP path. There's no meaningful conditional-GET/cache story
    # for a rendered page, so that path skips the SourceFetch etag cache.
    browser_rendered = not is_ics
    cached_fetch = db.get(SourceFetch, source.url)

    if browser_rendered:
        if is_eventbrite_listing:
            fetch_url = with_date_window(url, effective_lookahead_days)
        elif is_kultunaut_listing:
            fetch_url = with_kultunaut_date_window(url, effective_lookahead_days)
        elif is_cphpost_listing:
            fetch_url = with_cphpost_date_window(url, effective_lookahead_days)
        elif is_brugbyen_listing:
            fetch_url = with_brugbyen_date_window(url, effective_lookahead_days)
        elif is_ra_listing:
            fetch_url = with_ra_date_window(url, effective_lookahead_days)
        elif is_songkick_listing:
            fetch_url = with_songkick_date_window(url, effective_lookahead_days)
        elif is_bandsintown_listing:
            fetch_url = with_bandsintown_date_window(url, effective_lookahead_days)
        else:
            fetch_url = url
        if is_eventbrite_listing:
            wait_selector = "section.event-card-details"
        elif is_kultunaut_listing:
            wait_selector = ".products.arrlist"
        elif is_cphpost_listing:
            wait_selector = ".nautmasonryitem"
        elif is_migogkbh:
            # The card markup is present on domcontentloaded, but only as a
            # shimmering loading placeholder -- real events are filled in by
            # a client-side fetch afterwards, so wait for a real detail-page
            # link (absent from the placeholder) before reading the page.
            wait_selector = 'a[href*="/kalender/begivenhed/"]'
        else:
            wait_selector = None
        try:
            content = fetch_rendered_html(fetch_url, wait_selector=wait_selector, browser=browser)
            status_code = 200
            etag = None
            last_mod = None
        except Exception as e:
            source.last_error = str(e)
            source.last_fetched_at = utcnow()
            db.commit()
            logger.warning("Failed rendering %s: %s", fetch_url, e)
            return 0, 0
    else:
        fetch_url = url
        headers = {"User-Agent": USER_AGENT}
        if cached_fetch:
            if cached_fetch.etag:
                headers["If-None-Match"] = cached_fetch.etag
            if cached_fetch.last_modified:
                headers["If-Modified-Since"] = cached_fetch.last_modified

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(fetch_url, headers=headers)
        except Exception as e:
            source.last_error = str(e)
            source.last_fetched_at = utcnow()
            db.commit()
            logger.warning("Failed fetching %s: %s", fetch_url, e)
            return 0, 0

        status_code = resp.status_code

        # If 304 Not Modified, we don't re-parse
        if status_code == 304:
            source.last_status_code = status_code
            source.last_fetched_at = utcnow()
            source.last_error = None
            db.commit()
            return 0, cached_fetch.item_count or 0

        content = resp.text
        etag = resp.headers.get("etag")
        last_mod = resp.headers.get("last-modified")

    source.last_status_code = status_code
    source.last_fetched_at = utcnow()

    if status_code != 200:
        source.last_error = f"HTTP {status_code}"
        db.commit()
        return 0, 0

    scraper = get_scraper_for_source(source)
    scraped_events: list[ScrapedEvent] = scraper.extract(content, fetch_url)
    if is_kk_event_site(url):
        _fill_more_dates(scraped_events)
    return _apply_scraped_events(db, source, scraped_events, effective_lookahead_days, status_code, etag, last_mod, content)


# How far ahead a show's other dates are kept (they're stored even past the
# sync window, so later weeks on the map fill in without another scan).
REPEAT_HORIZON_DAYS = 120
# Event pages read for "2 other dates" per sync, and how long one is trusted.
MAX_SCHEDULE_FETCHES = 40
SCHEDULE_CACHE_SECONDS = 12 * 60 * 60
_schedule_cache: dict[str, tuple[float, list]] = {}


def _fill_more_dates(scraped_events: list[ScrapedEvent]) -> None:
    """Read the other dates of events whose card only says "2 andre datoer"
    from each event's own page (server-rendered, plain HTTP is enough)."""
    fetched = 0
    for ev in scraped_events:
        if not ev.has_more_dates or ev.more_dates or not ev.url:
            continue
        cached = _schedule_cache.get(ev.url)
        if cached and time.monotonic() - cached[0] < SCHEDULE_CACHE_SECONDS:
            ev.more_dates = list(cached[1])
            continue
        if fetched >= MAX_SCHEDULE_FETCHES:
            continue
        fetched += 1
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(ev.url, headers={"User-Agent": USER_AGENT})
            dates = read_schedule(resp.text) if resp.status_code == 200 else []
        except Exception as e:  # noqa: BLE001 -- one page failing must not stop the sync
            logger.info("Couldn't read other dates from %s: %s", ev.url, e)
            continue
        _schedule_cache[ev.url] = (time.monotonic(), dates)
        ev.more_dates = list(dates)


def _expand_dates(scraped_events: list[ScrapedEvent]) -> list[tuple[ScrapedEvent, bool]]:
    """Each listing, then one copy per further date. True marks a further date."""
    out: list[tuple[ScrapedEvent, bool]] = []
    for ev in scraped_events:
        out.append((ev, False))
        for start, end in ev.more_dates:
            out.append((replace(ev, start_time=start, end_time=end, more_dates=[], has_more_dates=False), True))
    return out


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _same_start_slot(a: datetime, b: datetime) -> bool:
    """True when two listings start at (almost) the same minute.

    Same-source fuzzy merge uses this so "Søndagsbrød" and "Søndagsbrød: Floor" at
    08:00 fold together, while "Jazz Night" at 20:00 and "Jazz Night late" at 20:10
    stay two sessions.
    """
    return abs(_aware(a) - _aware(b)) <= timedelta(minutes=1)


def _is_specific_location(location: str | None) -> bool:
    return bool(location) and location != "Unspecified"


def _fold_event_into(db: Session, winner: Event, loser: Event) -> None:
    """Collapse a leftover duplicate into ``winner`` (same source listed it twice)."""
    if winner.id == loser.id or winner.user_id != loser.user_id:
        return
    from app.models import EventDiscovery, EventSocialLink, EventUpdate

    if loser.is_favourited:
        winner.is_favourited = True
    if loser.calendar_synced and not winner.calendar_synced:
        winner.calendar_synced = True
        winner.google_event_id = winner.google_event_id or loser.google_event_id
    # Prefer the more specific location / longer description the way a second source would.
    _merge_into(
        winner,
        ScrapedEvent(
            loser.title, loser.description or "", _aware(loser.start_time),
            end_time=loser.end_time, location=loser.location, cost=loser.cost or "",
            url=loser.url or "", image_url=loser.image_url or "",
        ),
        is_primary=False,
        category_label=winner.category,
    )
    winner_sources = {
        s.source_id: s
        for s in db.execute(select(EventSighting).where(EventSighting.event_id == winner.id)).scalars()
    }
    for sighting in list(db.execute(select(EventSighting).where(EventSighting.event_id == loser.id)).scalars()):
        existing = winner_sources.get(sighting.source_id)
        if existing is None:
            sighting.event_id = winner.id
            winner_sources[sighting.source_id] = sighting
        else:
            # Same source already on the winner: keep the live row, drop the duplicate.
            if sighting.dropped_at is None and existing.dropped_at is not None:
                existing.dropped_at = None
                existing.last_seen = sighting.last_seen
                existing.fingerprint = sighting.fingerprint
                existing.title = sighting.title
                existing.url = sighting.url
                existing.location = sighting.location
                existing.cost = sighting.cost
                existing.start_time = sighting.start_time
            db.delete(sighting)
    for link in list(db.execute(select(EventSocialLink).where(EventSocialLink.event_id == loser.id)).scalars()):
        already = db.execute(
            select(EventSocialLink.id).where(
                EventSocialLink.event_id == winner.id, EventSocialLink.social_post_id == link.social_post_id
            )
        ).scalar_one_or_none()
        if already:
            db.delete(link)
        else:
            link.event_id = winner.id
    db.execute(update(EventUpdate).where(EventUpdate.event_id == loser.id).values(event_id=winner.id))
    db.execute(update(EventDiscovery).where(EventDiscovery.event_id == loser.id).values(event_id=winner.id))
    db.execute(delete(DedupeExclusion).where(DedupeExclusion.event_id == loser.id))
    db.delete(loser)


def _merge_into(event: Event, ev: ScrapedEvent, is_primary: bool, category_label: str) -> None:
    """Fold one source's listing into an event (eventtrakr-dedup.md §3): the
    primary source's title, link and category stay; anything better fills in."""
    if ev.cost and not is_unspecified_cost(ev.cost) and (is_primary or is_unspecified_cost(event.cost)):
        event.cost = ev.cost
    if _is_specific_location(ev.location) and ev.location != event.location:
        # Only ever a better location: one where there was none, or an address
        # for the same venue where there was only its name. Never a step back
        # ("Union, Nørrebrogade 1" -> "Union"), or two sources would swap it
        # back and forth every sync. (A listing at another venue has another
        # fingerprint, so it never gets here as the same event.)
        has_address = any(ch.isdigit() for ch in ev.location)
        similar = venue_similarity(ev.location, event.location)
        if not _is_specific_location(event.location) or (
            has_address and not any(ch.isdigit() for ch in event.location) and (similar is None or similar >= 0.5)
        ):
            event.location = ev.location
    if ev.description and len(ev.description) > len(event.description or ""):
        event.description = ev.description
    if ev.image_url and not event.image_url:
        event.image_url = ev.image_url
    if ev.end_time and not event.end_time:
        event.end_time = ev.end_time
    # A time of day beats a date-only listing of the same day.
    start = _aware(event.start_time)
    if has_time_of_day(ev.start_time) and not has_time_of_day(start) and ev.start_time.date() == start.date():
        event.start_time = ev.start_time
    if is_primary:
        event.category = category_label
    event.is_cancelled = False


def _find_match(db: Session, source: EventSource, ev: ScrapedEvent, fp: str) -> Event | None:
    """The event this listing belongs to: the same fingerprint, a listing
    already merged under that fingerprint, or (U33) the same event from
    another source under a slightly different title or venue."""
    found = db.execute(
        select(Event).where(Event.user_id == source.user_id, Event.fingerprint == fp)
    ).scalar_one_or_none()
    if found:
        return found
    found = db.execute(
        select(Event)
        .join(EventSighting, EventSighting.event_id == Event.id)
        .where(Event.user_id == source.user_id, EventSighting.fingerprint == fp)
        .limit(1)
    ).scalar_one_or_none()
    if found:
        return found

    day_start = datetime(ev.start_time.year, ev.start_time.month, ev.start_time.day, tzinfo=timezone.utc)
    candidates = db.execute(
        select(Event).where(
            Event.user_id == source.user_id,
            Event.is_cancelled == False,  # noqa: E712
            Event.start_time >= day_start,
            Event.start_time < day_start + timedelta(days=1),
        )
    ).scalars().all()
    if not candidates:
        return None
    ids = [c.id for c in candidates]
    # A source's own two listings are two events (two sessions the same day),
    # and a split ("Not the same event") is never merged back.
    own = set(
        db.execute(
            select(EventSighting.event_id).where(
                EventSighting.event_id.in_(ids),
                EventSighting.source_id == source.id,
                EventSighting.dropped_at.is_(None),
            )
        ).scalars()
    )
    excluded = set(
        db.execute(
            select(DedupeExclusion.event_id).where(
                DedupeExclusion.event_id.in_(ids), DedupeExclusion.fingerprint == fp
            )
        ).scalars()
    )
    listing = Listing(ev.title, ev.start_time, ev.location, ev.url)
    for cand in candidates:
        if cand.id in excluded:
            continue
        # A source's own second listing is usually another session the same day — only fold
        # when it starts at the same minute (one event listed under two titles).
        same_source = cand.id in own or cand.source_id == source.id
        if same_source and not _same_start_slot(ev.start_time, cand.start_time):
            continue
        if match_reason(listing, Listing(cand.title, _aware(cand.start_time), cand.location, cand.url)):
            return cand
    return None


def _apply_scraped_events(
    db: Session,
    source: EventSource,
    scraped_events: list[ScrapedEvent],
    effective_lookahead_days: int,
    status_code: int = 200,
    etag: str | None = None,
    last_mod: str | None = None,
    content: str = "",
) -> tuple[int, int]:
    """Filter, dedupe, and persist a scraper's results, then update the
    source's cache record and last-fetch bookkeeping. Shared by every fetch
    path (browser-rendered, ICS/HTTP, and the Bright Data API path) -- each
    just produces `scraped_events` differently before calling this.

    Each event keeps one sighting per source it was seen on (U33); an event
    is only marked cancelled once every source has stopped listing it.
    """
    global_keyword_include, global_keyword_exclude = settings_service.get_global_keywords(db)

    # Events are labelled with the category assigned to their source (admin-
    # managed list), not whatever the scraper itself guesses -- a source like
    # Eventbrite carries all sorts of events, so trusting its own "Eventbrite"
    # label would just turn the source name into a bogus pseudo-category.
    category_row = db.execute(select(Category).where(Category.key == source.category)).scalar_one_or_none()
    source_category_label = category_row.label if category_row else "General"
    # ...unless the scraper is sure of a single event's category (see
    # ScrapedEvent.category_key), e.g. a district committee's own meeting.
    # A title can also make it certain (a loppemarked is a flea market wherever
    # it's listed -- see auto_category.py).
    for ev in scraped_events:
        if not ev.category_key:
            ev.category_key = category_key_for_title(ev.title)
    override_labels = {
        c.key: c.label
        for c in db.execute(
            select(Category).where(Category.key.in_({ev.category_key for ev in scraped_events if ev.category_key}))
        ).scalars()
    }

    # Apply the lookahead limit (normally the 7-day Agenda horizon; wider for
    # an on-demand deep search -- see max_lookahead_days above). A show's
    # further dates are kept further out (REPEAT_HORIZON_DAYS).
    now = datetime.now(timezone.utc)
    max_lookahead = now + timedelta(days=effective_lookahead_days)
    repeat_horizon = now + timedelta(days=max(REPEAT_HORIZON_DAYS, effective_lookahead_days))
    finished_before = now - timespan.STARTED_GRACE
    new_count = 0
    valid_count = 0
    seen_fingerprints: set[str] = set()

    # Snapshot this source's live sightings of events still on *within this
    # same window* so we can tell which ones dropped out of the listing
    # (likely cancelled/removed) once we're done. Bounding by max_lookahead
    # here matters: without it, a routine 7-day sync would see far-future
    # events (e.g. ones found by an on-demand deep search past that window)
    # as "missing" from its own narrower scrape and wrongly drop them.
    # Only meaningful if the scrape actually returned something -- an empty/
    # broken scrape must never be treated as "everything cancelled".
    previously_open: dict[str, EventSighting] = {}
    if scraped_events:
        previously_open = {
            sighting.fingerprint: sighting
            for sighting in db.execute(
                select(EventSighting)
                .join(Event, Event.id == EventSighting.event_id)
                .where(
                    EventSighting.source_id == source.id,
                    EventSighting.dropped_at.is_(None),
                    Event.is_cancelled == False,  # noqa: E712
                    Event.start_time <= max_lookahead,
                    or_(Event.start_time >= now, Event.end_time >= now),
                )
            ).scalars()
        }

    # In-memory caches of what this same call already handled -- the session
    # is autoflush=False, and a scraper can return the same event twice in
    # one pass (some sites render a hidden duplicate card, e.g. Eventbrite,
    # Madbillet), which must not insert it twice.
    pending_by_fingerprint: dict[str, Event] = {}
    sightings_by_event: dict[int, EventSighting] = {}

    for ev, is_further_date in _expand_dates(scraped_events):
        # Still on (a run until its end, a one-off for 3 hours after it
        # starts) and inside the window.
        if (ev.end_time or ev.start_time) < finished_before and ev.start_time < finished_before:
            continue
        if ev.start_time > (repeat_horizon if is_further_date else max_lookahead):
            continue

        # Keywords check -- source-level filter, then the global one on top
        if not _passes_keywords(ev.title, ev.description, ev.location, source.keywords_include, source.keywords_exclude):
            continue
        if not _passes_keywords(ev.title, ev.description, ev.location, global_keyword_include, global_keyword_exclude):
            continue

        if not is_further_date:
            valid_count += 1
        event_category_label = override_labels.get(ev.category_key or "", source_category_label)
        fp = compute_event_fingerprint(source.user_id, ev.title, ev.start_time, ev.location)
        seen_fingerprints.add(fp)

        event = pending_by_fingerprint.get(fp) or _find_match(db, source, ev, fp)
        if event is None:
            # Same scrape, different fingerprint: e.g. Jungle listing "Søndagsbrød" and
            # "Søndagsbrød: Floor" for one NABO morning — match against what we just added.
            listing = Listing(ev.title, ev.start_time, ev.location, ev.url)
            for pending in pending_by_fingerprint.values():
                if not _same_start_slot(ev.start_time, pending.start_time):
                    continue
                if match_reason(
                    listing,
                    Listing(pending.title, _aware(pending.start_time), pending.location, pending.url),
                ):
                    event = pending
                    break
        if event is not None:
            # Update mutable fields without disturbing user favourite/calendar state
            _merge_into(event, ev, event.source_id == source.id, event_category_label)
            # An older same-source twin left over from before this rule still has this fp.
            twin = db.execute(
                select(Event).where(
                    Event.user_id == source.user_id,
                    Event.fingerprint == fp,
                    Event.id != event.id,
                )
            ).scalar_one_or_none()
            if twin is not None:
                _fold_event_into(db, event, twin)
                sightings_by_event.pop(twin.id, None)
        else:
            event = Event(
                user_id=source.user_id,
                source_id=source.id,
                fingerprint=fp,
                title=ev.title,
                description=ev.description,
                start_time=ev.start_time,
                end_time=ev.end_time,
                location=ev.location,
                cost=ev.cost,
                category=event_category_label,
                url=ev.url,
                image_url=ev.image_url,
                is_favourited=False,
                calendar_synced=False,
            )
            db.add(event)
            db.flush()
            new_count += 1

        sighting = sightings_by_event.get(event.id) or db.execute(
            select(EventSighting).where(EventSighting.event_id == event.id, EventSighting.source_id == source.id)
        ).scalar_one_or_none()
        if sighting is None:
            sighting = EventSighting(event_id=event.id, source_id=source.id, first_seen=now)
            db.add(sighting)
        sighting.fingerprint = fp
        sighting.url = ev.url or ""
        sighting.title = ev.title
        sighting.start_time = ev.start_time
        sighting.location = ev.location or ""
        sighting.cost = ev.cost or ""
        sighting.last_seen = now
        sighting.dropped_at = None
        db.flush()
        sightings_by_event[event.id] = sighting
        pending_by_fingerprint[fp] = event

    # Sightings this scrape didn't see again: dropped. The event itself is
    # only cancelled once no source lists it any more.
    # A show whose card says "2 other dates" but whose own page wasn't read
    # this time (page limit, cache expired, fetch failed) still has those
    # dates: not seeing them now says nothing about them.
    schedule_unread = {fold(ev.title) for ev in scraped_events if ev.has_more_dates and not ev.more_dates}
    dropped_event_ids = set()
    for fp, sighting in previously_open.items():
        if fold(sighting.title) in schedule_unread:
            continue
        if fp not in seen_fingerprints and sighting.event_id not in sightings_by_event:
            sighting.dropped_at = now
            dropped_event_ids.add(sighting.event_id)
    if dropped_event_ids:
        db.flush()
        still_listed = set(
            db.execute(
                select(EventSighting.event_id).where(
                    EventSighting.event_id.in_(dropped_event_ids), EventSighting.dropped_at.is_(None)
                )
            ).scalars()
        )
        for event_id in dropped_event_ids - still_listed:
            stale_event = db.get(Event, event_id)
            if stale_event is not None:
                stale_event.is_cancelled = True

    # Update cache record
    cached_fetch = db.get(SourceFetch, source.url)
    if not cached_fetch:
        cached_fetch = SourceFetch(url=source.url)
        db.add(cached_fetch)
    cached_fetch.etag = etag
    cached_fetch.last_modified = last_mod
    cached_fetch.status_code = status_code
    cached_fetch.fetched_at = utcnow()
    cached_fetch.item_count = valid_count
    cached_fetch.body_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    source.last_item_count = valid_count
    source.last_error = None
    db.commit()

    return new_count, valid_count


def backfill_sightings(db: Session) -> int:
    """One sighting per event from before U33, from its own source and link."""
    missing = db.execute(
        select(Event)
        .outerjoin(EventSighting, EventSighting.event_id == Event.id)
        .where(Event.source_id.is_not(None), EventSighting.id.is_(None))
    ).scalars().all()
    for event in missing:
        seen = event.created_at or utcnow()
        db.add(
            EventSighting(
                event_id=event.id,
                source_id=event.source_id,
                fingerprint=event.fingerprint,
                url=event.url or "",
                title=event.title,
                start_time=event.start_time,
                location=event.location or "",
                cost=event.cost or "",
                first_seen=seen,
                last_seen=seen,
                dropped_at=utcnow() if event.is_cancelled else None,
            )
        )
    if missing:
        db.commit()
    return len(missing)


def purge_expired_events(db: Session) -> int:
    """Purge past events older than 24 hours to keep the database lean (a run
    counts from its end, so an exhibition stays while it's on)."""
    from app.models import EventDiscovery, EventSocialLink, EventUpdate

    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    expired = and_(func.coalesce(Event.end_time, Event.start_time) < cutoff, Event.is_favourited == False)  # noqa: E712
    expired_ids = select(Event.id).where(expired).scalar_subquery()
    # Rows that reference events.id (FKs enforced) -- clear them first or the delete fails.
    db.execute(delete(EventSocialLink).where(EventSocialLink.event_id.in_(expired_ids)))
    db.execute(delete(EventUpdate).where(EventUpdate.event_id.in_(expired_ids)))
    db.execute(delete(EventSighting).where(EventSighting.event_id.in_(expired_ids)))
    db.execute(
        update(EventDiscovery).where(EventDiscovery.event_id.in_(expired_ids)).values(event_id=None)
    )
    result = db.execute(delete(Event).where(expired))
    db.commit()
    return result.rowcount


def sync_all_sources(user_id: int | None = None) -> None:
    """Run full sync for all enabled sources (or a specific user's sources)."""
    if not _lock.acquire(blocking=False):
        return

    state.running = True
    state.last_started_at = utcnow()
    state.last_error = None
    state.progress = "Starting sync..."
    state.last_message = "Syncing..."
    _reset_stop()
    total_new = 0
    synced = 0

    try:
        with SessionLocal() as db:
            query = select(EventSource).where(EventSource.enabled == True)
            if user_id is not None:
                query = query.where(EventSource.user_id == user_id)
            sources = list(db.execute(query).scalars())
            total = len(sources)

            # One shared browser for the whole batch instead of a fresh
            # Chromium launch per source; only started if something in this
            # batch actually needs it (ICS/webcal sources don't).
            needs_browser = any(not is_ics_source(s) for s in sources)
            with browser_session() if needs_browser else nullcontext() as browser:
                for idx, src in enumerate(sources, 1):
                    if _stop_requested.is_set():
                        break
                    state.progress = f"Syncing {idx}/{total}: {src.name}..."
                    try:
                        new_cnt, _ = fetch_and_extract_source(db, src, browser=browser)
                        total_new += new_cnt
                    except Exception as e:
                        logger.exception("Error syncing source %s: %s", src.id, e)
                    synced = idx

            purge_expired_events(db)

        # New venues get their map coordinates in the background (F18).
        from app.services import places

        places.look_up_pending_background()
        state.last_new_events = total_new
        state.last_finished_at = utcnow()
        if _stop_requested.is_set() and synced < total:
            state.progress = f"Stopped after {synced}/{total} sources: {total_new} new events found."
            state.last_message = "Stopped"
        else:
            state.progress = f"Complete: {total_new} new events found."
            state.last_message = "Idle"
    except Exception as e:
        state.last_error = str(e)
        state.last_message = "Error"
        logger.exception("Sync all error: %s", e)
    finally:
        state.running = False
        _reset_stop()
        _lock.release()


def trigger_sync_background(user_id: int | None = None) -> bool:
    """Trigger background sync thread if not already running."""
    if state.running:
        return False
    thread = Thread(target=sync_all_sources, args=(user_id,), daemon=True)
    thread.start()
    return True


def _sync_single_source_thread(source_id: int, user_id: int) -> None:
    if not _lock.acquire(blocking=False):
        return

    state.running = True
    state.last_started_at = utcnow()
    state.last_error = None
    state.last_message = "Syncing..."
    _reset_stop()

    try:
        with SessionLocal() as db:
            src = db.get(EventSource, source_id)
            if not src or src.user_id != user_id:
                state.progress = "Source not found."
                return
            state.progress = f"Syncing '{src.name}'..."
            new_cnt, total = fetch_and_extract_source(db, src)
            state.last_new_events = new_cnt
            state.progress = f"'{src.name}' synced: {new_cnt} new, {total} total upcoming."
        state.last_finished_at = utcnow()
        state.last_message = "Idle"
    except Exception as e:
        state.last_error = str(e)
        state.last_message = "Error"
        logger.exception("Single-source sync error for source %s: %s", source_id, e)
    finally:
        # One source is a single unit: a stop request just lets it finish.
        state.running = False
        _reset_stop()
        _lock.release()


def trigger_single_source_sync_background(source_id: int, user_id: int) -> bool:
    """Sync one source in the background instead of blocking the request --
    some sources (e.g. Facebook via Bright Data's async fallback) can take
    minutes, and the Sources page has no way to show progress on a fetch
    that's blocking its own request/response cycle. Reuses the same shared
    `state`/lock as the "sync all" button, so the topbar's status pill (and
    its existing polling in app.js) picks this up automatically -- and a
    second sync genuinely can't run concurrently with this one anyway.
    """
    if state.running:
        return False
    thread = Thread(target=_sync_single_source_thread, args=(source_id, user_id), daemon=True)
    thread.start()
    return True
