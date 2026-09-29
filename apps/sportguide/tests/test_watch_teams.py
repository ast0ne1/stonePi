"""Teams to Watch: pick from listings, normalised keys, legacy free-text migration."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes, teams
from app.services import notify as notify_service
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SECRET = "sg-watch-secret"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sport.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    c.cookies.set(
        COOKIE_NAME,
        encode_session(secret=SECRET, user_id=ADAM, username="adam", display_name="Adam", is_admin=True,
                       apps=["sportguide"], session_id="s", phone_alerts=True),
    )
    return c


def _row(eid: str, sport: str, title: str, minutes: int = 20) -> dict:
    return {
        "external_id": eid,
        "sport": sport,
        "league": "",
        "title": title,
        "starts_at": (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channels": [],
        "source_url": "",
    }


def _seed_listings() -> None:
    db.replace_source_listings(
        "ausportguide",
        [
            _row("r1", "rugby", "Sydney Roosters - Newcastle Knights Rugby League"),
            _row("c1", "cricket", "Odi: South Africa V Australia G3 | South Africa V Australia G3"),
            _row("f0", "afl", "Collingwood Magpies vs Carlton"),
        ],
    )
    db.replace_source_listings(
        "wheresthematch",
        [
            _row("f1", "football", "Arsenal vs Chelsea"),
            _row("f2", "football", "St Pauli vs Borussia Mönchengladbach"),
            _row("f3", "football", "FT vs ▶"),
        ],
    )


def _picker_options(html: str) -> dict[str, list[str]]:
    """Team suggestions by sport label, from the datalist values ("Arsenal · Football")."""
    box = re.search(r'<datalist id="watch-team-options">.*?</datalist>', html, re.S).group(0)
    out: dict[str, list[str]] = {}
    for value in re.findall(r'<option value="([^"]+)">', box):
        name, _, label = value.rpartition(" · ")
        out.setdefault(label, []).append(name)
    return out


# -- normalisation and title parsing ----------------------------------------------


def test_team_key_normalises_case_whitespace_and_accents():
    assert teams.team_key("  Borussia   MÖNCHENGLADBACH ") == "borussia monchengladbach"
    assert teams.team_key("St. Pauli") == "st pauli"


def test_teams_in_title_per_source_format():
    assert teams.teams_in_title("Arsenal vs Chelsea") == ["Arsenal", "Chelsea"]
    assert teams.teams_in_title("Sydney Roosters - Newcastle Knights Rugby League") == [
        "Sydney Roosters", "Newcastle Knights",
    ]
    assert teams.teams_in_title("Odi: South Africa V Australia G3 | South Africa V Australia G3") == [
        "South Africa", "Australia",
    ]
    assert teams.teams_in_title("FT vs ▶") == []


def test_normalised_matching():
    title = "Collingwood Magpies vs Carlton"
    assert notify_service.listing_matches_watched(title, ["  collingwood   MAGPIES "]) == "  collingwood   MAGPIES "
    assert notify_service.listing_matches_watched(title, ["Collingwood"]) == "Collingwood"  # whole words
    assert notify_service.listing_matches_watched(title, ["Carl"]) is None  # not inside a word
    assert notify_service.listing_matches_watched(title, ["Essendon"]) is None
    assert notify_service.listing_matches_watched("St Pauli vs Borussia Mönchengladbach", ["borussia monchengladbach"])


# -- picker, add, remove ------------------------------------------------------------


def test_picker_lists_standard_teams_and_listing_teams_by_sport(client):
    _seed_listings()
    options = _picker_options(client.get("/settings?tab=watch").text)
    assert list(options) == ["AFL", "Cricket", "Rugby", "Football"]
    assert {"Newcastle Knights", "Sydney Roosters", "Penrith Panthers"} <= set(options["Rugby"])
    assert "South Africa" in options["Cricket"]
    assert "Arsenal" in options["Football"] and "FT" not in options["Football"]
    # Listing-only teams still appear; catalog teams don't need a listing.
    assert "St Pauli" in options["Football"] and "Liverpool" in options["Football"]
    # "Collingwood Magpies" in listings is the catalog's Collingwood, not a second entry.
    assert "Collingwood" in options["AFL"] and "Collingwood Magpies" not in options["AFL"]
    assert 'name="teams_text"' not in client.get("/settings?tab=watch").text


def test_picker_offers_standard_teams_without_listings(client):
    options = _picker_options(client.get("/settings?tab=watch").text)
    assert "Arsenal" in options["Football"]
    assert "Collingwood" in options["AFL"]
    assert 'list="watch-team-options"' in client.get("/settings?tab=watch").text


def test_add_and_remove_teams(client):
    _seed_listings()
    r = client.post("/settings/watch/add", data={"team": "Arsenal ", "csrf_token": "csrf"}, follow_redirects=False)
    assert r.status_code == 303
    client.post("/settings/watch/add", data={"team": "sydney roosters", "csrf_token": "csrf"})
    assert db.get_watched_entries(ADAM) == [
        {"key": "arsenal", "name": "Arsenal", "sport": "football"},
        {"key": "sydney roosters", "name": "Sydney Roosters", "sport": "rugby"},
    ]
    # Watched teams leave the picker; adding again is a no-op.
    assert "Arsenal" not in _picker_options(client.get("/settings?tab=watch").text)["Football"]
    client.post("/settings/watch/add", data={"team": "ARSENAL", "csrf_token": "csrf"})
    assert db.get_watched_teams(ADAM) == ["Arsenal", "Sydney Roosters"]

    client.post("/settings/watch/remove", data={"team": "football:arsenal", "csrf_token": "csrf"})
    assert db.get_watched_teams(ADAM) == ["Sydney Roosters"]


def test_add_by_alias_and_picker_value(client):
    client.post("/settings/watch/add", data={"team": "Man Utd", "csrf_token": "csrf"})
    client.post("/settings/watch/add", data={"team": "Australia · Cricket", "csrf_token": "csrf"})
    client.post("/settings/watch/add", data={"team": "Australia · Rugby", "csrf_token": "csrf"})
    assert db.get_watched_entries(ADAM) == [
        {"key": "manchester united", "name": "Manchester United", "sport": "football"},
        {"key": "australia", "name": "Australia", "sport": "cricket"},
        {"key": "australia", "name": "Australia", "sport": "rugby"},
    ]


def test_ambiguous_name_asks_for_the_sport(client):
    r = client.post("/settings/watch/add", data={"team": "Australia", "csrf_token": "csrf"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and "more%20than%20one%20sport" in r.headers["location"]
    assert db.get_watched_entries(ADAM) == []


def test_matching_uses_sides_aliases_and_sport():
    melbourne = {"key": "melbourne", "name": "Melbourne", "sport": "afl"}
    assert teams.entry_matches("Melbourne vs Carlton", "afl", melbourne)
    assert not teams.entry_matches("North Melbourne vs Carlton", "afl", melbourne)
    united = {"key": "manchester united", "name": "Manchester United", "sport": "football"}
    assert teams.entry_matches("Man Utd vs Arsenal", "football", united)
    cricket_aus = {"key": "australia", "name": "Australia", "sport": "cricket"}
    assert not teams.entry_matches("Australia - New Zealand Rugby Union", "rugby", cricket_aus)
    assert teams.entry_matches("Odi: South Africa V Australia G3 | x", "cricket", cricket_aus)
    # Legacy entries (no sport) match any sport, nickname included.
    assert teams.entry_matches("Collingwood Magpies vs Carlton", "afl", {"key": "collingwood", "name": "Collingwood"})


def test_add_rejects_free_text_and_bad_csrf(client):
    _seed_listings()
    r = client.post("/settings/watch/add", data={"team": "Arsnal", "csrf_token": "csrf"}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    r = client.post("/settings/watch/add", data={"team": "arsenal", "csrf_token": "nope"}, follow_redirects=False)
    assert "Invalid" in r.headers["location"] or "error=" in r.headers["location"]
    assert db.get_watched_teams(ADAM) == []


# -- legacy migration ---------------------------------------------------------------


def test_legacy_csv_values_migrate_and_unmatched_still_show(client):
    _seed_listings()
    # Old free-text pref: a JSON list whose entries may hold commas.
    db.set_pref(ADAM, db.WATCHED_TEAMS_KEY, json.dumps(["Arsenal, collingwood", "  Hawthorn  ", "arsenal"]))
    assert db.get_watched_entries(ADAM) == [
        {"key": "arsenal", "name": "Arsenal"},
        {"key": "collingwood", "name": "collingwood"},
        {"key": "hawthorn", "name": "Hawthorn"},
    ]
    stored = json.loads(db.get_pref(ADAM, db.WATCHED_TEAMS_KEY))
    assert all(isinstance(e, dict) for e in stored)  # rewritten once in the new shape

    html = client.get("/settings?tab=watch").text
    rows = dict(re.findall(r'watch-team-name">([^<]+)</span>\s*(<span class="watch-team-hint">)?', html))
    # Hawthorn has no listing: still shown, with the hint. Legacy "collingwood" matches "Collingwood Magpies".
    assert rows == {"Arsenal": "", "collingwood": "", "Hawthorn": '<span class="watch-team-hint">'}


def test_bare_csv_string_migrates():
    db.set_pref(db.LOCAL_KEY, db.WATCHED_TEAMS_KEY, "Arsenal,Denmark\nFC København")
    assert db.get_watched_teams(db.LOCAL_KEY) == ["Arsenal", "Denmark", "FC København"]


# -- alerts and display API -----------------------------------------------------------


def test_alert_fires_for_watched_team(sent):
    _seed_listings()
    db.add_watched_team(db.LOCAL_KEY, "sydney roosters", "Sydney Roosters")
    totals = notify_service.check_all_watching(now=NOW)
    assert totals["notified"] == 1
    assert sent[0]["title"] == "Sydney Roosters - Newcastle Knights Rugby League"


def test_alert_fires_for_migrated_legacy_name(sent):
    _seed_listings()
    db.set_pref(db.LOCAL_KEY, db.WATCHED_TEAMS_KEY, json.dumps(["  COLLINGWOOD "]))
    assert notify_service.check_all_watching(now=NOW)["notified"] == 1
    assert sent[0]["title"] == "Collingwood Magpies vs Carlton"


def test_display_api_shape_unchanged(monkeypatch):
    monkeypatch.setattr(db, "household_timezone", lambda: "UTC")
    monkeypatch.setattr(routes, "window_utc", lambda tz: (
        NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), (NOW + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"), NOW,
    ))
    _seed_listings()
    db.add_watched_team(ADAM, "arsenal", "Arsenal")
    db.set_pref(db.LOCAL_KEY, db.WATCHED_TEAMS_KEY, json.dumps(["arsenal", "Hawthorn"]))
    app = FastAPI()
    app.include_router(routes.router)
    body = TestClient(app).get("/api/display").json()
    assert set(body) == {"ok", "on_now", "detail", "watched_teams", "next_watched"}
    assert body["watched_teams"] == ["Arsenal", "Hawthorn"]
    assert all(isinstance(t, str) for t in body["watched_teams"])
    assert set(body["next_watched"]) == {"title", "starts_at", "league", "local_time"}
    assert body["next_watched"]["title"] == "Arsenal vs Chelsea"
