from __future__ import annotations

import hashlib
import logging
import re
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from app.config import env
from app.db import SessionLocal
from app.models import Category, Event, EventSource, SourceFetch, utcnow
from app.services.dedupe import compute_event_fingerprint
from app.services.scrapers.base import ScrapedEvent
from app.services.scrapers.bandsintown import is_listing_url as is_bandsintown_listing_url
from app.services.scrapers.brightdata_facebook import BrightDataError, fetch_events as fetch_brightdata_facebook_events
from app.services.scrapers.bandsintown import with_date_window as with_bandsintown_date_window
from app.services.scrapers.browser_fetch import browser_session, fetch_rendered_html
from app.services.scrapers.brugbyen import BrugbyenExtractor
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


state = IngestState()


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
    if "cphpost.dk" in url_lower:
        return CphPostExtractor()
    if "ra.co" in url_lower:
        return ResidentAdvisorExtractor()
    if "brugbyen.kk.dk" in url_lower:
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
    is_brugbyen_listing = "brugbyen.kk.dk" in url_lower and is_brugbyen_listing_url(url)
    is_facebook = "facebook.com" in url_lower

    # Facebook's own event search requires a login our browser-based scraper
    # can't provide (an unauthenticated fetch gets an empty page shell), so
    # it's routed through Bright Data's hosted scraper instead of the normal
    # browser-render/HTTP paths below -- see brightdata_facebook.py.
    if is_facebook:
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
    return _apply_scraped_events(db, source, scraped_events, effective_lookahead_days, status_code, etag, last_mod, content)


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
    """
    global_keyword_include, global_keyword_exclude = settings_service.get_global_keywords(db)

    # Events are labelled with the category assigned to their source (admin-
    # managed list), not whatever the scraper itself guesses -- a source like
    # Eventbrite carries all sorts of events, so trusting its own "Eventbrite"
    # label would just turn the source name into a bogus pseudo-category.
    category_row = db.execute(select(Category).where(Category.key == source.category)).scalar_one_or_none()
    source_category_label = category_row.label if category_row else "General"

    # Apply the lookahead limit (normally the 7-day Agenda horizon; wider for
    # an on-demand deep search -- see max_lookahead_days above)
    now = datetime.now(timezone.utc)
    max_lookahead = now + timedelta(days=effective_lookahead_days)
    new_count = 0
    valid_count = 0
    seen_fingerprints: set[str] = set()

    # Snapshot this source's currently-open events *within this same window*
    # so we can tell which ones dropped out of the listing (likely
    # cancelled/removed) once we're done. Bounding by max_lookahead here
    # matters: without it, a routine 7-day sync would see far-future events
    # (e.g. ones found by an on-demand deep search past that window) as
    # "missing" from its own narrower scrape and wrongly mark them cancelled.
    # Only meaningful if the scrape actually returned something -- an empty/
    # broken scrape must never be treated as "everything cancelled".
    previously_open = {}
    if scraped_events:
        previously_open = {
            e.fingerprint: e
            for e in db.execute(
                select(Event).where(
                    Event.source_id == source.id,
                    Event.is_cancelled == False,
                    Event.start_time >= now,
                    Event.start_time <= max_lookahead,
                )
            ).scalars()
        }

    # In-memory cache of fingerprints already handled in this same call --
    # the session is autoflush=False, so a fresh SELECT won't see an Event
    # we've only db.add()'d earlier in this same loop. Without this, a
    # scraper returning the same event twice in one pass (some sites render
    # a hidden duplicate card, e.g. Eventbrite, Madbillet) inserts it twice
    # and trips the (user_id, fingerprint) unique constraint on commit.
    pending_by_fingerprint: dict[str, Event] = dict(previously_open)

    for ev in scraped_events:
        # Check date window: now <= event_time <= now + 7 days
        # Add 3 hours grace period for events happening right now today
        if ev.start_time < now - timedelta(hours=3) or ev.start_time > max_lookahead:
            continue

        # Keywords check -- source-level filter, then the global one on top
        if not _passes_keywords(ev.title, ev.description, ev.location, source.keywords_include, source.keywords_exclude):
            continue
        if not _passes_keywords(ev.title, ev.description, ev.location, global_keyword_include, global_keyword_exclude):
            continue

        valid_count += 1
        fp = compute_event_fingerprint(source.user_id, ev.title, ev.start_time, ev.location)
        seen_fingerprints.add(fp)

        existing = pending_by_fingerprint.get(fp)
        if existing is None:
            existing = db.execute(
                select(Event).where(Event.user_id == source.user_id, Event.fingerprint == fp)
            ).scalar_one_or_none()

        if existing:
            # Update mutable fields without disturbing user favourite/calendar state
            if ev.cost and ev.cost != "Free / Unspecified":
                existing.cost = ev.cost
            if ev.location and ev.location != "Unspecified":
                existing.location = ev.location
            if ev.description and len(ev.description) > len(existing.description):
                existing.description = ev.description
            if existing.is_cancelled:
                existing.is_cancelled = False
            existing.category = source_category_label
        else:
            new_ev = Event(
                user_id=source.user_id,
                source_id=source.id,
                fingerprint=fp,
                title=ev.title,
                description=ev.description,
                start_time=ev.start_time,
                end_time=ev.end_time,
                location=ev.location,
                cost=ev.cost,
                category=source_category_label,
                url=ev.url,
                image_url=ev.image_url,
                is_favourited=False,
                calendar_synced=False,
            )
            db.add(new_ev)
            new_count += 1
            existing = new_ev

        pending_by_fingerprint[fp] = existing

    for fp, stale_event in previously_open.items():
        if fp not in seen_fingerprints:
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


def purge_expired_events(db: Session) -> int:
    """Purge past events older than 24 hours to keep the database lean."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    result = db.execute(delete(Event).where(Event.start_time < cutoff, Event.is_favourited == False))
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
    total_new = 0

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
                    state.progress = f"Syncing {idx}/{total}: {src.name}..."
                    try:
                        new_cnt, _ = fetch_and_extract_source(db, src, browser=browser)
                        total_new += new_cnt
                    except Exception as e:
                        logger.exception("Error syncing source %s: %s", src.id, e)

            purge_expired_events(db)

        state.last_new_events = total_new
        state.last_finished_at = utcnow()
        state.progress = f"Complete: {total_new} new events found."
        state.last_message = "Idle"
    except Exception as e:
        state.last_error = str(e)
        state.last_message = "Error"
        logger.exception("Sync all error: %s", e)
    finally:
        state.running = False
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
        state.running = False
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
