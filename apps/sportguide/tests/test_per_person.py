"""SportGuide per person (Phase 8): Teams to Watch, timezone, lead time and alerts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes
from app.services import notify as notify_service
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"  # first admin
JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
SECRET = "sg-per-person-secret"
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


def _listing(external_id: str, title: str, minutes: int = 20) -> dict:
    return {
        "external_id": external_id,
        "sport": "football",
        "league": "Premier League",
        "title": title,
        "starts_at": (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channels": [],
        "source_url": "",
    }


# -- alerts ----------------------------------------------------------------------


def test_each_person_is_alerted_only_for_their_own_teams(sent):
    db.set_watched_teams(ADAM, ["Arsenal"])
    db.set_watched_teams(JO, ["Denmark"])
    db.replace_source_listings(
        "wheresthematch",
        [_listing("a1", "Arsenal vs Chelsea"), _listing("d1", "Denmark vs Sweden"), _listing("x1", "Spurs vs Villa")],
    )
    totals = notify_service.check_all_watching(now=NOW)
    assert totals["people"] == 2 and totals["notified"] == 2
    by_user = {e["user"]: e["title"] for e in sent}
    assert by_user == {ADAM: "Arsenal vs Chelsea", JO: "Denmark vs Sweden"}
    assert all(e["audience"] == "personal" for e in sent)


def test_same_match_alerts_two_watchers_separately(sent):
    db.set_watched_teams(ADAM, ["Arsenal"])
    db.set_watched_teams(JO, ["Chelsea"])
    db.replace_source_listings("wheresthematch", [_listing("a1", "Arsenal vs Chelsea")])
    notify_service.check_all_watching(now=NOW)
    assert sorted(e["user"] for e in sent) == sorted([ADAM, JO])
    # Distinct dedupe keys, so Notify doesn't treat Jo's alert as a repeat of Adam's.
    assert len({e["dedupe_key"] for e in sent}) == 2
    notify_service.check_all_watching(now=NOW)
    assert len(sent) == 2  # each person only once


def test_per_person_lead_time(sent):
    db.set_watched_teams(ADAM, ["Arsenal"])
    db.set_watched_teams(JO, ["Arsenal"])
    db.set_approaching_lead_minutes(ADAM, 60)
    db.set_approaching_lead_minutes(JO, 15)
    db.replace_source_listings("wheresthematch", [_listing("a1", "Arsenal vs Chelsea", minutes=40)])
    notify_service.check_all_watching(now=NOW)
    assert [e["user"] for e in sent] == [ADAM]


def test_unmoved_shared_list_still_alerts_household(sent):
    db.set_watched_teams(db.LOCAL_KEY, ["Arsenal"])
    db.replace_source_listings("wheresthematch", [_listing("a1", "Arsenal vs Chelsea")])
    notify_service.check_all_watching(now=NOW)
    assert sent[0]["audience"] == "household" and not sent[0].get("user")


def test_unknown_key_sends_nothing(sent):
    db.set_watched_teams("42", ["Arsenal"])  # a stray local id, never a platform owner
    db.replace_source_listings("wheresthematch", [_listing("a1", "Arsenal vs Chelsea")])
    assert notify_service.check_all_watching(now=NOW)["notified"] == 0
    assert sent == []


# -- one-time move of shared prefs to the first admin ------------------------------


def test_move_local_prefs_to_first_admin_once():
    db.set_watched_teams(db.LOCAL_KEY, ["Arsenal", "Denmark"])
    db.set_approaching_lead_minutes(db.LOCAL_KEY, 45)
    db.set_pref(db.LOCAL_KEY, "timezone", "Europe/Copenhagen")
    db.set_pref(db.LOCAL_KEY, "city", "Copenhagen")

    assert db.move_local_prefs_to(ADAM) is True
    assert db.get_watched_teams(ADAM) == ["Arsenal", "Denmark"]
    assert db.approaching_lead_minutes(ADAM) == 45
    assert db.user_timezone(ADAM) == "Europe/Copenhagen"
    assert db.get_pref(ADAM, "city") == "Copenhagen"
    # Shared list is gone (no duplicate household alerts); household timezone stays.
    assert db.get_watched_teams(db.LOCAL_KEY) == []
    assert db.household_timezone() == "Europe/Copenhagen"
    # Only once: a later admin doesn't get it.
    assert db.move_local_prefs_to(JO) is False
    assert db.get_watched_teams(JO) == []


def test_move_keeps_admins_own_values():
    db.set_watched_teams(db.LOCAL_KEY, ["Arsenal"])
    db.set_watched_teams(ADAM, ["Liverpool"])
    db.move_local_prefs_to(ADAM)
    assert db.get_watched_teams(ADAM) == ["Liverpool"]


def test_new_person_defaults_to_household_timezone():
    db.set_pref(db.LOCAL_KEY, "timezone", "Europe/London")
    assert db.user_timezone(JO) == "Europe/London"


def test_display_widget_uses_everyones_teams():
    db.set_watched_teams(ADAM, ["Arsenal"])
    db.set_watched_teams(JO, ["arsenal", "Denmark"])
    assert sorted(t.casefold() for t in db.all_watched_teams()) == ["arsenal", "denmark"]


# -- pages -----------------------------------------------------------------------


def _client(monkeypatch, uid: str, *, is_admin: bool) -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    c.cookies.set(
        COOKIE_NAME,
        encode_session(secret=SECRET, user_id=uid, username=uid[:4], display_name=uid[:4], is_admin=is_admin,
                       apps=["sportguide"], session_id="s", phone_alerts=True),
    )
    return c


def _teams_box(html: str) -> str:
    """Watched team names shown in the Teams to Watch list, one per line."""
    names = re.findall(r'<span class="watch-team-name">(.*?)(?: <span class="watch-team-sport">.*?</span>)?</span>', html)
    return "\n".join(names)


def test_first_admin_page_view_moves_shared_teams(monkeypatch):
    db.set_watched_teams(db.LOCAL_KEY, ["Arsenal"])
    adam = _client(monkeypatch, ADAM, is_admin=True)
    assert _teams_box(adam.get("/settings?tab=watch").text).strip() == "Arsenal"
    assert db.get_watched_teams(db.LOCAL_KEY) == []


def test_member_page_view_does_not_take_shared_teams(monkeypatch):
    db.set_watched_teams(db.LOCAL_KEY, ["Arsenal"])
    jo = _client(monkeypatch, JO, is_admin=False)
    assert _teams_box(jo.get("/settings?tab=watch").text).strip() == ""
    assert db.get_watched_teams(db.LOCAL_KEY) == ["Arsenal"]


def test_saving_teams_and_lead_time_is_per_person(monkeypatch):
    db.replace_source_listings("wheresthematch", [_listing("a1", "Arsenal vs Chelsea"), _listing("d1", "Denmark vs Sweden")])
    adam = _client(monkeypatch, ADAM, is_admin=True)
    jo = _client(monkeypatch, JO, is_admin=False)
    jo.post("/settings/watch/add", data={"team": "denmark", "csrf_token": "csrf"})
    jo.post("/settings/notifications", data={"notify_approaching_minutes": "15", "csrf_token": "csrf"})
    adam.post("/settings/watch/add", data={"team": "arsenal", "csrf_token": "csrf"})
    assert db.get_watched_teams(JO) == ["Denmark"]
    assert db.get_watched_teams(ADAM) == ["Arsenal"]
    assert db.approaching_lead_minutes(JO) == 15
    assert db.approaching_lead_minutes(ADAM) == db.DEFAULT_APPROACHING_MINUTES
    assert _teams_box(jo.get("/settings?tab=watch").text).strip() == "Denmark"


def test_member_timezone_does_not_change_household(monkeypatch):
    db.set_pref(db.LOCAL_KEY, "timezone", "Europe/London")
    jo = _client(monkeypatch, JO, is_admin=False)
    from app.config import COMMON_TIMEZONES

    other = next(tz for tz in COMMON_TIMEZONES if tz != "Europe/London")
    jo.post("/settings/general", data={"city": "", "timezone": other, "csrf_token": "csrf"})
    assert db.user_timezone(JO) == other
    assert db.household_timezone() == "Europe/London"
