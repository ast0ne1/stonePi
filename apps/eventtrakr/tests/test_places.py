"""Map coordinates for event locations (F18 §1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Event, Place
from app.services import places


def _event(location: str) -> None:
    with SessionLocal() as db:
        db.add(Event(
            user_id=1, fingerprint=uuid4().hex, title="x", location=location,
            start_time=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        db.commit()


def test_is_nowhere():
    assert places.is_nowhere("Unspecified")
    assert places.is_nowhere("Online")
    assert places.is_nowhere("København")
    assert places.is_nowhere("Copenhagen, Denmark")
    assert not places.is_nowhere("Union")


def test_attempts_try_address_then_venue():
    assert places._attempts("Kulturhuset Brønshøj, Brønshøjvej 17") == [
        "Kulturhuset Brønshøj, Brønshøjvej 17", "Brønshøjvej 17", "Kulturhuset Brønshøj",
    ]
    assert places._attempts("Union") == ["Union"]


def test_look_up_pending_finds_queues_and_skips(monkeypatch):
    init_db()
    venue, address, nowhere = f"Venue {uuid4().hex[:6]}", f"Gade {uuid4().hex[:6]} 4", "Online"
    _event(venue)
    _event(f"Nowhere Hall {uuid4().hex[:6]}, {address}")
    _event(nowhere)
    queries = []

    def fake_search(client, query, box):
        queries.append(query)
        if query == venue or query == address:
            return [{"lat": "55.68", "lon": "12.57", "display_name": query}]
        return []

    monkeypatch.setattr(places, "_search", fake_search)
    monkeypatch.setattr(places, "_city_box", lambda db, client: [12.4, 55.6, 12.7, 55.75])
    places.look_up_pending()
    with SessionLocal() as db:
        by_key = {p.key: p for p in db.execute(select(Place)).scalars()}
    assert by_key[places.place_key(venue)].status == "found"
    assert by_key[places.place_key(venue)].lat == 55.68
    split = next(p for k, p in by_key.items() if k.startswith("nowhere hall"))
    assert split.status == "found"  # found by its address part
    assert by_key[places.place_key(nowhere)].status == "skipped"
    assert nowhere not in queries
    # Looked up once: a second run asks nothing new.
    queries.clear()
    places.look_up_pending()
    assert queries == []


def test_no_home_area_means_no_lookups(monkeypatch):
    init_db()
    _event(f"Somewhere {uuid4().hex[:6]}")
    asked = []
    monkeypatch.setattr(places, "_search", lambda client, query, box: asked.append((query, box)) or [])
    monkeypatch.setattr(places, "_city_box", lambda db, client: None)
    places.look_up_pending()
    assert asked == []  # waits rather than searching the whole world


def test_home_area_has_a_margin(monkeypatch):
    init_db()
    with SessionLocal() as db:
        from app.models import Setting

        row = db.get(Setting, "map_area:" + places.place_key(places.env.default_location))
        if row:
            db.delete(row)
            db.commit()
        monkeypatch.setattr(places, "_search", lambda client, q, box: [{"boundingbox": ["55.61", "55.73", "12.45", "12.65"]}])
        box = places._city_box(db, None)
    assert box[0] < 12.45 and box[2] > 12.65 and box[1] < 55.61 and box[3] > 55.73
