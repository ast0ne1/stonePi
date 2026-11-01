"""The map view's week API (F18)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app import create_app
from app.db import SessionLocal
from app.models import Event, Place, User
from app.routes import map as map_routes
from app.services import places
from app.services.auth import create_session_token


def _client():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with SessionLocal() as db:
        user = User(username=f"map-{uuid4().hex[:8]}", role="user")
        db.add(user)
        db.commit()
        client.set_cookie("eventtrakr_session", create_session_token(user.id, user.username, user.role))
        return client, user.id


def test_week_label_and_range():
    monday = date(2026, 10, 26)
    assert map_routes._week_label(monday) == "Wk 44 · 26 Oct – 1 Nov"
    today = date(2026, 10, 27)
    assert map_routes._week_start(None, today) == monday
    assert map_routes._week_start("2026-10-01", today) == monday  # no going back past this week
    assert map_routes._week_start("2027-06-01", today) == monday + timedelta(weeks=map_routes.MAX_WEEKS_AHEAD)


def test_map_week_returns_this_weeks_events_with_coordinates(monkeypatch):
    monkeypatch.setattr(places, "look_up_pending_background", lambda: False)
    client, uid = _client()
    venue = f"Union {uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(Place(key=places.place_key(venue), query=venue, lat=55.69, lon=12.55, status="found"))
        db.add(Event(user_id=uid, fingerprint=uuid4().hex, title="Gig", location=venue, start_time=now + timedelta(hours=2)))
        db.add(Event(user_id=uid, fingerprint=uuid4().hex, title="Somewhere", location="Online", start_time=now + timedelta(hours=3)))
        db.add(Event(
            user_id=uid, fingerprint=uuid4().hex, title="Exhibition", location=venue,
            start_time=now - timedelta(days=10), end_time=now + timedelta(days=20),
        ))
        db.add(Event(user_id=uid, fingerprint=uuid4().hex, title="Next month", location=venue, start_time=now + timedelta(days=40)))
        db.commit()

    assert client.get("/map").status_code == 200
    data = client.get("/api/map/week").get_json()
    assert data["week"]["is_current"] is True
    assert data["week"]["prev"] is None
    by_title = {e["title"]: e for e in data["events"]}
    assert set(by_title) == {"Gig", "Somewhere", "Exhibition"}
    assert by_title["Gig"]["lat"] == 55.69
    assert by_title["Somewhere"]["lat"] is None  # listed, not on the map
    assert by_title["Exhibition"]["is_run"] is True


def test_moving_a_pin_saves_it_for_the_place(monkeypatch):
    client, _ = _client()
    with SessionLocal() as db:
        place = Place(key=f"venue {uuid4().hex}", query="Venue", lat=1.0, lon=1.0, status="found")
        db.add(place)
        db.commit()
        place_id = place.id
    assert client.post(f"/api/map/places/{place_id}", json={"lat": 55.7, "lon": 12.6}).get_json() == {"saved": True}
    with SessionLocal() as db:
        place = db.get(Place, place_id)
        assert (place.lat, place.lon, place.status) == (55.7, 12.6, "manual")
