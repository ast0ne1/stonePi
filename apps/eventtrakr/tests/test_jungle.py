"""Jungle (jungle.am) events come from SvelteKit's __data.json, not the HTML."""

import json
from datetime import datetime, timedelta, timezone

from app.models import EventSource
from app.services.ingest import get_scraper_for_source
from app.services.scrapers.jungle import JungleExtractor, data_url, decode, is_jungle_url

# Same shape as the live feed: page node with a promised "shelves" value,
# then the shelves streamed as a chunk; one event sits on two shelves.
EVENT = {
    "id": 3, "title": 4, "slug": 5, "description": 6, "event_date": 7, "end_date": 8,
    "custom_address": 9, "host": 10, "ticket_price": 11, "currency": 12, "status": 13,
    "displayDate": 14, "entryKey": 15, "metadata": 16, "occurrence_label": 18,
}
CHUNK = [
    [1, 2],
    {"id": 19, "title": 20, "items": 23},
    {"id": 21, "title": 20, "items": 23},
    "e1", "Mandagsmad: Oktober", "mandagsmad-oktober",
    "<p>Hjemmelavet <strong>mad</strong></p><p>105 kr.</p>",
    "2026-10-05T15:00:00+00:00", "2026-10-05T18:00:00+00:00",
    "Ægirsgade 46B, Copenhagen", "NABO", 105, "DKK", "published",
    "2026-10-12T15:00:00+00:00", "e1-occ2",
    {"poster_url": 17}, "https://images.example/poster.webp", "Grøn sæsonkarry",
    "events-all", "All events", "events-get-tickets",
    EVENT,
    [22],
]
FEED = "\n".join([
    json.dumps({"type": "data", "nodes": [{"type": "data", "data": [{"shelves": 1}, ["Promise", 1]]}]}),
    json.dumps({"type": "chunk", "id": 1, "data": CHUNK}),
])


def test_data_url_and_detection():
    assert data_url("https://jungle.am/events") == "https://jungle.am/events/__data.json"
    assert data_url("https://jungle.am/events/") == "https://jungle.am/events/__data.json"
    assert is_jungle_url("https://www.jungle.am/events") and not is_jungle_url("https://notjungle.am/x")
    assert isinstance(get_scraper_for_source(EventSource(url="https://jungle.am/events", source_type="supported")), JungleExtractor)


def test_decode_resolves_references():
    roots = decode(FEED)
    shelves = roots[1]
    assert shelves[0]["items"][0]["title"] == "Mandagsmad: Oktober"
    assert shelves[0]["items"][0] is shelves[1]["items"][0]


def test_jungle_extractor_reads_events_once():
    events = JungleExtractor().extract(FEED, "https://jungle.am/events")
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Mandagsmad: Oktober"
    assert ev.start_time == datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
    # end_date belongs to the first date: shifted onto this occurrence.
    assert ev.end_time - ev.start_time == timedelta(hours=3)
    assert ev.location == "NABO · Ægirsgade 46B, Copenhagen"
    assert ev.cost == "105 DKK"
    assert ev.url == "https://jungle.am/events/mandagsmad-oktober"
    assert ev.image_url == "https://images.example/poster.webp"
    assert ev.description.startswith("Grøn sæsonkarry\n\nHjemmelavet mad")


def test_jungle_extractor_ignores_garbage():
    assert JungleExtractor().extract("<html>not json</html>", "https://jungle.am/events") == []
