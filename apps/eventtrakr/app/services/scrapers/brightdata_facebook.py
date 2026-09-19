from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from app.services.scrapers.base import ScrapedEvent

logger = logging.getLogger("eventtrakr.scrapers.brightdata_facebook")

# Facebook's own event search requires a logged-in session that our
# browser-based scraper can't provide (an unauthenticated fetch returns an
# empty page shell -- see the "Facebook Events" catalog entry this replaces).
# Bright Data's hosted "Facebook Events - discover by URL" scraper handles
# that authentication/anti-bot layer on their end instead: you hand it a
# facebook.com/events/search/?q=... URL and get back structured event data.
#
# The same dataset_id serves two different modes: the default "collect a
# specific event" mode (input must be a single event's own URL) and
# "discover" mode, which is what a search/listing URL needs -- that requires
# the extra type=discover_new&discover_by=url query params (confirmed from
# the account dashboard's own discovery example; the public docs page for
# this exact endpoint omits them, which is what led this integration astray
# initially). Sending a search URL without those params gets accepted by the
# API but silently produces no usable data, since it's then being treated as
# a malformed single-event request.
#
# POST /datasets/v3/scrape tries to run synchronously and return the JSON
# array directly in the response body -- but only within Bright Data's ~1
# minute synchronous window. A job that takes longer (as this one routinely
# does) instead gets HTTP 202 back with a snapshot_id, and falls back to the
# same async trigger/progress/snapshot pattern their other dataset APIs use.
# Both paths have to be handled; the docs' own example only shows the fast
# (200) case.
# https://docs.brightdata.com/api-reference/scrapers/social-media-apis/facebook-events-discover-by-url

API_BASE = "https://api.brightdata.com/datasets/v3"
FACEBOOK_EVENTS_DATASET_ID = "gd_m14sd0to1jz48ppm51"
REQUEST_TIMEOUT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 3.0
# The fetch runs in a background thread (see ingest.trigger_single_source_
# sync_background), not on the request/response cycle, so there's no UX
# cost to a generous budget here -- better to wait out a genuinely slow job
# than time out one that would have succeeded a little later.
MAX_WAIT_SECONDS = 300.0


class BrightDataError(Exception):
    pass


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_cost(tickets_obj) -> str:
    if not isinstance(tickets_obj, dict):
        return "Free / Unspecified"
    min_price = tickets_obj.get("min_price")
    if min_price is None:
        return "Free / Unspecified"
    currency = (tickets_obj.get("currency") or "").strip()
    max_price = tickets_obj.get("max_price")
    if max_price and max_price != min_price:
        return f"{currency} {min_price}-{max_price}".strip()
    return f"{currency} {min_price}".strip()


def _map_record(rec: dict, fallback_url: str) -> ScrapedEvent | None:
    """Map one raw Bright Data Facebook record to a ScrapedEvent. Shared by
    both discovery (many records per call) and the single-event lookup (one
    record) -- same response shape either way, confirmed from a real capture
    rather than the public docs example (see module docstring above)."""
    title = rec.get("title") or rec.get("name")
    start_time = _parse_dt(rec.get("event_start_time") or rec.get("event_date"))
    if not title or not start_time:
        return None
    end_time = _parse_dt(rec.get("event_end_time") or rec.get("event_end_date"))

    location_obj = rec.get("location")
    if isinstance(location_obj, dict):
        location = location_obj.get("address")
    elif isinstance(location_obj, str):
        location = location_obj
    else:
        location = None
    location = location or rec.get("full_address") or "Unspecified"

    description = rec.get("unformatted_description_text")
    if not description:
        desc_obj = rec.get("description")
        if isinstance(desc_obj, dict):
            description = desc_obj.get("text")
        elif isinstance(desc_obj, str):
            description = desc_obj
    description = description or ""

    tickets_obj = rec.get("tickets")
    ticket_url = tickets_obj.get("url") if isinstance(tickets_obj, dict) else None

    return ScrapedEvent(
        title=str(title).strip(),
        description=str(description).strip()[:1000],
        start_time=start_time,
        end_time=end_time,
        location=str(location)[:250],
        cost=_format_cost(tickets_obj)[:90],
        url=rec.get("url") or ticket_url or fallback_url,
        image_url=rec.get("main_image_downloadable") or rec.get("main_image"),
        category="Facebook",
    )


def fetch_single_event(api_key: str, event_url: str) -> ScrapedEvent | None:
    """Look up one specific Facebook event by its own URL (the default
    "collect" mode -- no discover_new/discover_by params, unlike the search
    discovery flow in fetch_events()). Used by the "Add Event" page's
    "Fetch Details" button. Returns None if Bright Data found nothing
    parseable for that URL; raises BrightDataError on a request failure."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            resp = client.post(
                f"{API_BASE}/scrape",
                params={
                    "dataset_id": FACEBOOK_EVENTS_DATASET_ID,
                    "notify": "false",
                    "include_errors": "true",
                },
                headers=headers,
                json={"input": [{"url": event_url}], "limit_per_input": None},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise BrightDataError(f"Bright Data scrape failed: HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise BrightDataError(f"Bright Data scrape request failed: {e}") from e

        if resp.status_code == 202:
            snapshot_id = resp.json().get("snapshot_id")
            if not snapshot_id:
                raise BrightDataError("Bright Data returned 202 without a snapshot_id")
            records = _wait_for_snapshot(client, headers, snapshot_id)
        else:
            records = resp.json()

    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return None
    return _map_record(records[0], event_url)


def _wait_for_snapshot(client: httpx.Client, headers: dict, snapshot_id: str) -> list:
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        progress_resp = client.get(f"{API_BASE}/progress/{snapshot_id}", headers=headers)
        progress_resp.raise_for_status()
        status = progress_resp.json().get("status")
        if status == "ready":
            break
        if status in ("failed", "canceled"):
            raise BrightDataError(f"Bright Data job {status}")
        if time.monotonic() > deadline:
            raise BrightDataError("Timed out waiting for Bright Data snapshot")
        time.sleep(POLL_INTERVAL_SECONDS)

    data_resp = client.get(f"{API_BASE}/snapshot/{snapshot_id}", params={"format": "json"}, headers=headers)
    data_resp.raise_for_status()
    return data_resp.json()


def fetch_events(api_key: str, search_url: str) -> list[ScrapedEvent]:
    """Run a Bright Data scrape job for a Facebook events search URL and
    return the results as ScrapedEvents.

    Raises BrightDataError on an invalid key or a failed request -- callers
    should catch this the same way they'd catch a failed page render, and
    surface source.last_error instead of crashing the sync.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            resp = client.post(
                f"{API_BASE}/scrape",
                params={
                    "dataset_id": FACEBOOK_EVENTS_DATASET_ID,
                    "notify": "false",
                    "include_errors": "true",
                    "type": "discover_new",
                    "discover_by": "url",
                },
                headers=headers,
                json={"input": [{"url": search_url}], "limit_per_input": None},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise BrightDataError(f"Bright Data scrape failed: HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise BrightDataError(f"Bright Data scrape request failed: {e}") from e

        if resp.status_code == 202:
            snapshot_id = resp.json().get("snapshot_id")
            if not snapshot_id:
                raise BrightDataError("Bright Data returned 202 without a snapshot_id")
            records = _wait_for_snapshot(client, headers, snapshot_id)
        else:
            records = resp.json()

    raw_count = len(records) if isinstance(records, list) else 0
    logger.info("Bright Data Facebook: received %d raw record(s) for %s", raw_count, search_url)

    events: list[ScrapedEvent] = []
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict):
            continue
        mapped = _map_record(rec, search_url)
        if mapped:
            events.append(mapped)

    if raw_count and not events:
        sample = records[0] if isinstance(records[0], dict) else {}
        logger.warning(
            "Bright Data Facebook: %d raw record(s) but 0 parsed -- check field names. Sample record: %s",
            raw_count,
            sample,
        )

    return events
