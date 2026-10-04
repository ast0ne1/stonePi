"""Search: words in any order across fields, Unicode-aware matching, ranking, location handling."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db import SessionLocal, init_db
from app.models import Event, EventSource, User
from app.routes.ui import fold_text, run_search, search_tokens
from app.services.auth import create_session_token

NOW = datetime.now(timezone.utc)


@pytest.fixture
def member():
    """A local member with a few sources and events; removed afterwards."""
    init_db()
    with SessionLocal() as db:
        user = User(username=f"search-{uuid4().hex[:8]}", role="user", default_location="Copenhagen, Denmark")
        db.add(user)
        db.commit()
        uid = user.id
    yield uid
    with SessionLocal() as db:
        db.query(Event).filter(Event.user_id == uid).delete(synchronize_session=False)
        db.query(EventSource).filter(EventSource.user_id == uid).delete(synchronize_session=False)
        db.query(User).filter(User.id == uid).delete(synchronize_session=False)
        db.commit()


def _source(uid: int, name: str) -> int:
    with SessionLocal() as db:
        src = EventSource(user_id=uid, name=name, url=f"https://example.com/{uuid4().hex}")
        db.add(src)
        db.commit()
        return src.id


def _event(uid: int, title: str, *, days: float = 1, location: str = "Unspecified", description: str = "",
           category: str = "General", source_id: int | None = None) -> int:
    with SessionLocal() as db:
        ev = Event(user_id=uid, fingerprint=uuid4().hex, title=title, description=description,
                   start_time=NOW + timedelta(days=days), location=location, category=category,
                   source_id=source_id)
        db.add(ev)
        db.commit()
        return ev.id


def _search(uid: int, q: str = "", **kw) -> list[str]:
    with SessionLocal() as db:
        return [ev.title for ev in run_search(db, uid, tokens=search_tokens(q), **kw)]


def test_fold_and_tokens():
    assert fold_text("Østerbro") == "østerbro"
    assert fold_text("AARHUS") == "aarhus"
    assert fold_text("Café Ålborg") == "cafe alborg"
    assert search_tokens("  Jazz,  ØSTERBRO jazz ") == ["jazz", "østerbro"]
    assert search_tokens("a jazz") == ["jazz"]  # short words dropped when there are others
    assert search_tokens("a") == ["a"]
    assert search_tokens("@theglobe #quiz") == ["theglobe", "quiz"]
    assert len(search_tokens(" ".join(f"w{i}" for i in range(20)))) == 8


def test_words_in_any_order_across_fields(member):
    _event(member, "Jazz Night", location="Østerbro, København")
    _event(member, "Jazz Brunch", location="Aarhus")
    assert _search(member, "østerbro jazz") == ["Jazz Night"]
    assert _search(member, "night københavn") == ["Jazz Night"]
    assert _search(member, "jazz vesterbro") == []


def test_venue_and_category_match(member):
    _event(member, "Indie double bill", location="VEGA, Enghavevej 40")
    _event(member, "Wine tasting", category="Food & Drink")
    assert _search(member, "vega") == ["Indie double bill"]
    assert _search(member, "food tasting") == ["Wine tasting"]


def test_source_name_match(member):
    src = _source(member, "Kulturhuset Islands Brygge")
    _event(member, "Open mic", source_id=src)
    _event(member, "Open air cinema")
    assert _search(member, "islands brygge") == ["Open mic"]
    assert _search(member, "open kulturhuset") == ["Open mic"]


def test_danish_and_accent_case_insensitive(member):
    _event(member, "Loppemarked", location="Østerbro")
    _event(member, "Koncert i Aarhus")
    _event(member, "Brunch på Café Norden")
    assert _search(member, "øster") == ["Loppemarked"]
    assert _search(member, "ØSTERBRO") == ["Loppemarked"]
    assert _search(member, "AARHUS") == ["Koncert i Aarhus"]
    assert _search(member, "cafe") == ["Brunch på Café Norden"]
    assert _search(member, "PÅ café") == ["Brunch på Café Norden"]


def test_ranking_title_then_place_then_description(member):
    _event(member, "Milonga", description="An evening of tango", days=1)
    _event(member, "Social dance", location="Tango Hall", days=2)
    _event(member, "Tango late", days=4)
    _event(member, "Tango early", days=3)
    _event(member, "Old tango", days=-5)  # past: after upcoming ones in the same tier
    assert _search(member, "tango") == ["Tango early", "Tango late", "Old tango", "Social dance", "Milonga"]


def test_location_and_category_filters(member):
    _event(member, "Quiz", location="Nørrebro, København")
    _event(member, "Quiz", location="Aarhus C", category="Community")
    assert _search(member, "quiz", location="københavn") == ["Quiz"]
    assert len(_search(member, "quiz")) == 2
    assert _search(member, "", location="AARHUS") == ["Quiz"]
    assert _search(member, "quiz", category="Community") == ["Quiz"]


def test_search_page_does_not_apply_default_location_silently(member):
    from app import create_app

    _event(member, "Harbour quiz", location="Aarhus")
    _event(member, "City quiz", location="Copenhagen, Denmark")
    client = create_app().test_client()
    with SessionLocal() as db:
        user = db.get(User, member)
        client.set_cookie("eventtrakr_session", create_session_token(user.id, user.username, user.role))

    html = client.get("/search").get_data(as_text=True)
    assert 'name="location" class="form-control" value=""' in html  # not pre-filled
    assert 'data-fill-location="Copenhagen, Denmark"' in html  # offered as a chip instead

    html = client.get("/search?q=quiz&location=&category=all").get_data(as_text=True)
    assert "Harbour quiz" in html and "City quiz" in html

    html = client.get("/search?q=quiz&location=Copenhagen").get_data(as_text=True)
    assert "City quiz" in html and "Harbour quiz" not in html
    assert "data-location-filter" in html  # visible, removable

    # A category-only search counts as a search.
    html = client.get("/search?category=General").get_data(as_text=True)
    assert "search-results-meta" in html


def test_search_stays_fast_with_thousands_of_events(member):
    words = ["jazz", "quiz", "tango", "loppemarked", "koncert", "foredrag", "brunch", "film"]
    places = ["Østerbro", "Nørrebro", "Vesterbro", "Aarhus", "Amager", "Frederiksberg"]
    src = _source(member, "Big calendar")
    with SessionLocal() as db:
        db.add_all(
            Event(user_id=member, fingerprint=uuid4().hex, title=f"{words[i % 8].title()} nr. {i}",
                  description=("Lang beskrivelse med æøå og detaljer om arrangementet. " * 8) + words[(i + 3) % 8],
                  start_time=NOW + timedelta(hours=i), location=f"{places[i % 6]}, København",
                  category="General", source_id=src)
            for i in range(3000)
        )
        db.commit()
    started = time.perf_counter()
    found = _search(member, "øster jazz")
    elapsed = time.perf_counter() - started
    assert found and all("Jazz" in t for t in found)
    print(f"search over 3000 events: {elapsed * 1000:.0f} ms")
    assert elapsed < 2.0
