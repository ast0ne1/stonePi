"""Cross-source merging (U33), runs and further dates (F18 §3a) in ingest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Category, DedupeExclusion, Event, EventSighting, EventSource, User
from app.services import ingest
from app.services.dedupe import Listing, compute_event_fingerprint, match_reason
from app.services.scrapers.base import ScrapedEvent


@pytest.fixture()
def user_and_sources():
    init_db()
    with SessionLocal() as db:
        user = User(username=f"merge-{uuid4().hex[:8]}", role="user")
        db.add(user)
        db.flush()
        a = EventSource(user_id=user.id, name="Kultunaut", url=f"https://a.example/{uuid4().hex}", category="culture")
        b = EventSource(user_id=user.id, name="BrugByen", url=f"https://b.example/{uuid4().hex}", category="community")
        db.add_all([a, b])
        db.commit()
        return user.id, a.id, b.id


def _soon(days: int = 2, hour: int = 20) -> datetime:
    day = datetime.now(timezone.utc) + timedelta(days=days)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def _apply(source_id: int, events: list[ScrapedEvent], days: int = 7):
    with SessionLocal() as db:
        src = db.get(EventSource, source_id)
        return ingest._apply_scraped_events(db, src, events, days)


def _events(user_id: int) -> list[Event]:
    with SessionLocal() as db:
        return list(db.execute(select(Event).where(Event.user_id == user_id).order_by(Event.start_time)).scalars())


def _sightings(event_id: int) -> list[EventSighting]:
    with SessionLocal() as db:
        return list(db.execute(select(EventSighting).where(EventSighting.event_id == event_id)).scalars())


def test_same_event_from_two_sources_is_one_event_with_two_sightings(user_and_sources):
    uid, a, b = user_and_sources
    start = _soon()
    _apply(a, [ScrapedEvent("Fran De Pian: Nostalgiología", "", start, location="Union", url="https://a.example/fran")])
    _apply(b, [
        ScrapedEvent(
            "Fran De Pian – Nostalgiologia", "A longer description from the second source", start + timedelta(minutes=30),
            location="Union, Nørrebrogade 1", cost="DKK 120", url="https://b.example/fran",
        )
    ])
    (event,) = _events(uid)
    assert event.title == "Fran De Pian: Nostalgiología"  # the first source's
    assert event.url == "https://a.example/fran"
    assert event.cost == "DKK 120"  # a real price fills in
    assert event.location == "Union, Nørrebrogade 1"  # an address beats a bare name
    assert event.description.startswith("A longer description")
    assert event.category == "Culture"  # the primary source's, not the second one's
    assert {s.source_id for s in _sightings(event.id)} == {a, b}


def test_dropped_by_one_source_is_not_cancelled_while_another_lists_it(user_and_sources):
    uid, a, b = user_and_sources
    start = _soon()
    shared = ScrapedEvent("Copenhagen Cottage Market", "", start, location="Højbro Plads")
    other = ScrapedEvent("Something else", "", start, location="Elsewhere")
    _apply(a, [shared, other])
    _apply(b, [shared])
    _apply(a, [other])  # a stops listing the market
    market = next(e for e in _events(uid) if e.title == "Copenhagen Cottage Market")
    assert market.is_cancelled is False
    _apply(b, [ScrapedEvent("Unrelated", "", start, location="Somewhere")])  # now b drops it too
    market = next(e for e in _events(uid) if e.title == "Copenhagen Cottage Market")
    assert market.is_cancelled is True


def test_a_sources_own_two_listings_stay_two_events(user_and_sources):
    uid, a, _ = user_and_sources
    start = _soon()
    _apply(a, [
        ScrapedEvent("Jazz Night", "", start, location="Jazzhouse"),
        ScrapedEvent("Jazz Night!", "", start + timedelta(minutes=10), location="Jazzhouse"),
    ])
    # Same fingerprint after normalising -> one; a near-identical second card from
    # the same source is never fuzzy-merged into its sibling.
    assert len(_events(uid)) == 1
    _apply(a, [
        ScrapedEvent("Jazz Night", "", start, location="Jazzhouse"),
        ScrapedEvent("Jazz Night late show", "", start + timedelta(minutes=10), location="Jazzhouse"),
    ])
    assert len(_events(uid)) == 2


def test_same_source_same_minute_title_variants_fold_together(user_and_sources):
    """Jungle (etc.) listing 'Søndagsbrød' and 'Søndagsbrød: Floor' at 08:00 NABO is one event."""
    uid, a, _ = user_and_sources
    start = _soon(hour=8)
    _apply(a, [
        ScrapedEvent("Søndagsbrød", "Short", start, location="NABO · NABO , Nørrebro", url="https://a.example/1"),
        ScrapedEvent(
            "Søndagsbrød: Floor", "Longer description from the fuller card", start,
            location="NABO · Ægirsgade 46B, Copenhagen", url="https://a.example/2",
        ),
    ])
    (event,) = _events(uid)
    assert event.title == "Søndagsbrød"
    assert "Ægirsgade" in event.location  # the address fills in
    assert event.description.startswith("Longer description")
    # A refresh that still returns both titles collapses any leftover twin too.
    _apply(a, [
        ScrapedEvent("Søndagsbrød: Floor", "Longer description from the fuller card", start,
                     location="NABO · Ægirsgade 46B, Copenhagen", url="https://a.example/2"),
        ScrapedEvent("Søndagsbrød", "Short", start, location="NABO · NABO , Nørrebro", url="https://a.example/1"),
    ])
    assert len(_events(uid)) == 1


def test_split_pair_is_not_merged_again(user_and_sources):
    uid, a, b = user_and_sources
    start = _soon()
    _apply(a, [ScrapedEvent("Repair Café", "", start, location="Valby Kulturhus")])
    (event,) = _events(uid)
    listing = ScrapedEvent("Repair Cafe", "", start, location="Valby Kulturhus, Valgårdsvej 4")
    fp = compute_event_fingerprint(uid, listing.title, listing.start_time, listing.location)
    with SessionLocal() as db:
        db.add(DedupeExclusion(user_id=uid, event_id=event.id, fingerprint=fp))
        db.commit()
    _apply(b, [listing])
    assert len(_events(uid)) == 2


def test_a_run_already_under_way_is_kept(user_and_sources):
    uid, a, _ = user_and_sources
    started = datetime.now(timezone.utc) - timedelta(days=3)
    _apply(a, [ScrapedEvent("Exhibition", "", started, end_time=started + timedelta(days=30), location="Nikolaj")])
    (event,) = _events(uid)
    assert event.end_time is not None
    # ...and it's on the Agenda's "still on" list.
    from app.services import timespan

    with SessionLocal() as db:
        found = db.execute(select(Event.id).where(Event.user_id == uid, timespan.still_on(datetime.now(timezone.utc)))).scalars().all()
    assert found == [event.id]


def test_further_dates_are_stored_past_the_window(user_and_sources):
    uid, a, _ = user_and_sources
    first = _soon(days=2, hour=19)
    later = [(first + timedelta(days=21), None), (first + timedelta(days=200), None)]
    new, found = _apply(a, [ScrapedEvent("Hamlet", "", first, location="Det Kongelige Teater", more_dates=later)])
    starts = [e.start_time.date() for e in _events(uid)]
    # The 3-weeks-out date is kept (beyond the 7-day window); 200 days out is past REPEAT_HORIZON_DAYS.
    assert starts == [first.date(), (first + timedelta(days=21)).date()]
    assert (new, found) == (2, 1)


def test_match_reason_keeps_generic_titles_apart():
    t = datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc)
    assert match_reason(Listing("Lokaludvalgsmøde", t, "Fishtank"), Listing("Lokaludvalgsmøde", t, "Beboerhuset")) is None
    assert match_reason(Listing("Lokaludvalgsmøde", t, "Unspecified"), Listing("Lokaludvalgsmøde", t, "")) is None
    assert match_reason(Listing("Jazz i Haven", t, "Unspecified"), Listing("Jazz i Haven", t, "Kongens Have"))
    assert match_reason(Listing("Jazz Night", t, "Jazzhouse"), Listing("Jazz Night", t + timedelta(hours=4), "Jazzhouse")) is None


def test_match_reason_merges_short_title_inside_longer_at_same_venue():
    """'Søndagsbrød' at NABO and 'Søndagsbrød: Floor' at NABO + street are one event."""
    t = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    assert match_reason(
        Listing("Søndagsbrød: Floor", t, "NABO · Ægirsgade 46B, Copenhagen"),
        Listing("Søndagsbrød", t, "NABO · NABO, Nørrebro"),
    ) == "same title and place"
    # A short one-word title still needs a real place match — "Jazz" alone is too thin.
    assert match_reason(Listing("Jazz: Late", t, "Jazzhouse"), Listing("Jazz", t, "Jazzhouse")) is None


def test_flea_markets_get_their_own_category_from_any_source(user_and_sources):
    from app.services.auto_category import category_key_for_title

    for title in ("Loppemarked i Valby", "Stort loppetorv", "Kræmmermarked på Israels Plads", "Vintage flea market", "Flea-market Sunday"):
        assert category_key_for_title(title) == "flea-markets", title
    for title in ("Repair Café", "Byttemarked", "Galoppen"):
        assert category_key_for_title(title) is None, title

    uid, a, _ = user_and_sources
    _apply(a, [
        ScrapedEvent("Loppemarked på Halmtorvet", "", _soon(), location="Halmtorvet"),
        ScrapedEvent("Jazz Night", "", _soon(), location="Jazzhouse"),
    ])
    assert {e.title: e.category for e in _events(uid)} == {
        "Loppemarked på Halmtorvet": "Flea markets",
        "Jazz Night": "Culture",
    }


def test_overnight_event_is_not_a_run_and_belongs_to_its_start_day(user_and_sources):
    from app.services import timespan

    uid, a, _ = user_and_sources
    start = _soon(days=2, hour=22)
    _apply(a, [ScrapedEvent("Club night", "", start, end_time=start + timedelta(hours=6), location="Culture Box")])
    (event,) = _events(uid)
    assert timespan.is_run(event) is False
    next_day = datetime(start.year, start.month, start.day, tzinfo=timezone.utc) + timedelta(days=1)
    with SessionLocal() as db:
        on_next_day = db.execute(
            select(Event.id).where(Event.user_id == uid, timespan.overlaps(next_day, next_day + timedelta(days=1)))
        ).scalars().all()
    assert on_next_day == []


def test_further_dates_survive_a_sync_that_didnt_read_the_schedule(user_and_sources):
    uid, a, _ = user_and_sources
    first = _soon(days=1, hour=19)
    second = first + timedelta(days=3)
    card = dict(title="Hamlet", description="", location="Det Kongelige Teater", has_more_dates=True)
    _apply(a, [ScrapedEvent(start_time=first, more_dates=[(second, None)], **card)])
    assert len(_events(uid)) == 2
    _apply(a, [ScrapedEvent(start_time=first, **card)])  # page not read this time
    assert [e.is_cancelled for e in _events(uid)] == [False, False]


def test_merged_location_does_not_flip_flop(user_and_sources):
    uid, a, b = user_and_sources
    start = _soon()
    _apply(a, [ScrapedEvent("Fran De Pian", "", start, location="Union")])
    _apply(b, [ScrapedEvent("Fran De Pian", "", start, location="Union, Nørrebrogade 1")])
    _apply(a, [ScrapedEvent("Fran De Pian", "", start, location="Union")])
    (event,) = _events(uid)
    assert event.location == "Union, Nørrebrogade 1"


def test_agenda_source_filter_includes_merged_listings(user_and_sources):
    from app.routes.ui import _listed_by

    uid, a, b = user_and_sources
    start = _soon()
    _apply(a, [ScrapedEvent("Copenhagen Cottage Market", "", start, location="Højbro Plads")])
    _apply(b, [ScrapedEvent("Copenhagen Cottage Market", "", start, location="Højbro Plads")])
    with SessionLocal() as db:
        ids = db.execute(select(Event.id).where(Event.user_id == uid, _listed_by(b))).scalars().all()
    assert ids == [e.id for e in _events(uid)]


def test_loppen_venue_is_not_a_flea_market():
    from app.services.auto_category import category_key_for_title

    assert category_key_for_title("Koncert på Loppen: The Band") is None
    assert category_key_for_title("Loppen presents: Night") is None
    assert category_key_for_title("Loppemarked ved Loppen") == "flea-markets"
