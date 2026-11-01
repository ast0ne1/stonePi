"""U34 holidays and U36 "good time to call" on the family display, and their settings."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import calls, db, holidays, local, notify
from tests.test_local import CPH, SYD, _client

UK = {"name": "London", "label": "London, England, United Kingdom", "lat": 51.5085, "lon": -0.1257,
      "tz": "Europe/London", "country": "GB"}
DK_ROWS = [
    {"date": "2026-12-24", "localName": "Juleaftensdag", "name": "Christmas Eve", "global": True, "counties": None},
    {"date": "2026-12-25", "localName": "Juledag", "name": "Christmas Day", "global": True, "counties": None},
]
GB_ROWS = [
    {"date": "2026-12-25", "localName": "Christmas Day", "name": "Christmas Day", "global": True, "counties": None},
    {"date": "2026-11-30", "localName": "St Andrew's Day", "name": "St. Andrew's Day", "global": False, "counties": ["GB-SCT"]},
    {"date": "2026-12-28", "localName": "Boxing Day", "name": "Boxing Day", "global": True, "counties": None},
]
# 20 Dec 2026, noon UTC.
DEC_20 = datetime(2026, 12, 20, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(notify, "display_changed", lambda secret: None)


def _fetches(monkeypatch, data):
    asked = []

    def fake(country, year):
        asked.append((country, year))
        if isinstance(data, Exception):
            raise data
        return holidays._clean(data.get(country, []))

    monkeypatch.setattr(holidays, "_fetch", fake)
    return asked


# -- U34 holidays ---------------------------------------------------------------------------


def test_fetched_once_a_day_per_country_and_kept_when_a_fetch_fails(monkeypatch):
    import httpx

    local.set_place("here", {**CPH, "country": "DK"})
    local.set_place("family", UK)
    asked = _fetches(monkeypatch, {"DK": DK_ROWS, "GB": GB_ROWS})
    assert holidays.refresh(DEC_20) is True
    # December: this year and next, for both countries.
    assert sorted(asked) == [("DK", 2026), ("DK", 2027), ("GB", 2026), ("GB", 2027)]
    holidays.refresh(DEC_20.replace(hour=20))
    assert len(asked) == 4  # not again the same day
    _fetches(monkeypatch, httpx.ConnectError("down"))
    holidays.refresh(DEC_20.replace(day=22), force=True)
    assert holidays._stored("GB", 2026)[0]  # the last good copy stays


def test_board_lines_merge_both_places_and_respect_regions(monkeypatch):
    local.set_place("here", {**CPH, "country": "DK"})
    local.set_place("family", UK)
    _fetches(monkeypatch, {"DK": DK_ROWS, "GB": GB_ROWS})
    holidays.refresh(DEC_20)
    assert holidays.lines(DEC_20) == []  # off until switched on
    holidays.save(True, 14, {"family": ""})
    assert holidays.lines(DEC_20) == [
        "Christmas Eve · Thu 24 Dec · Copenhagen",
        "Christmas Day · Fri 25 Dec · both",
        "Boxing Day · Mon 28 Dec · London",
    ]
    # St Andrew's Day is Scotland's only: shown with the GB-SCT region, nationwide ones always.
    nov = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    assert holidays.lines(nov) == []
    holidays.save(True, 14, {"family": "GB-SCT"})
    assert holidays.lines(nov) == ["St. Andrew's Day · Mon 30 Nov · London"]
    assert holidays.regions("GB") == ["GB-SCT"]


def test_a_place_saved_without_a_country_gets_one(monkeypatch):
    local.set_place("here", CPH)
    monkeypatch.setattr(local, "search_places", lambda q: [{**CPH, "country": "DK"}])
    _fetches(monkeypatch, {"DK": DK_ROWS})
    holidays.refresh(DEC_20)
    assert local.get_place("here")["country"] == "DK"


def test_holiday_settings(monkeypatch):
    c = _client(monkeypatch)
    local.set_place("family", UK)
    _fetches(monkeypatch, {"GB": GB_ROWS})
    holidays.refresh(DEC_20)
    page = c.get("/settings?tab=holidays").text
    assert "Public holidays" in page and 'name="region_family"' in page and "GB-SCT" in page
    assert "Holidays" in c.get("/settings").text and "Good time to call" in c.get("/settings").text
    r = c.post("/settings/holidays", data={"csrf_token": "csrf", "on": "1", "days": "30", "region_family": "gb-sct"},
               follow_redirects=False)
    assert r.status_code == 303 and "msg=Saved" in r.headers["location"]
    assert holidays.enabled() and holidays.days_ahead() == 30 and holidays.region("family") == "GB-SCT"
    bad = c.post("/settings/holidays", data={"csrf_token": "csrf", "days": "500"}, follow_redirects=False)
    assert "err=" in bad.headers["location"] or "error=" in bad.headers["location"]
    member = _client(monkeypatch, is_admin=False)
    assert "msg=Saved" not in member.post("/settings/holidays", data={"csrf_token": "csrf", "days": "5"},
                                          follow_redirects=False).headers.get("location", "")


# -- U36 good time to call ------------------------------------------------------------------


def _calls_on(work=True):
    local.set_place("here", CPH)
    local.set_place("family", SYD)
    assert calls.save(True, work, {"sleep_from": "23:00", "sleep_to": "07:00", "work_from": "09:00", "work_to": "17:00"})


def test_good_time_to_call_in_the_familys_time():
    _calls_on()
    # Sun 25 Oct 2026, 18:00 in Copenhagen = Mon 05:00 in Sydney: free until 23:00 ours = 09:00 theirs.
    assert calls.line(datetime(2026, 10, 25, 17, tzinfo=timezone.utc)) == "Good time to call · until 09:00 your time"
    # 01:00 Monday ours: asleep, back at 07:00 ours = 17:00 theirs.
    assert calls.line(datetime(2026, 10, 26, 0, tzinfo=timezone.utc)) == "We're asleep · back from 17:00 your time"
    # 10:00 Monday ours: at work until 17:00 ours = 03:00 Tuesday theirs (a day later for them).
    assert calls.line(datetime(2026, 10, 26, 9, tzinfo=timezone.utc)) == "We're at work · free from Tue 03:00 your time"
    # Saturday: no work.
    assert calls.line(datetime(2026, 10, 31, 9, tzinfo=timezone.utc)).startswith("Good time to call")


def test_calls_off_without_our_city_and_without_work():
    assert calls.line() is None
    _calls_on(work=False)
    assert calls.line(datetime(2026, 10, 26, 9, tzinfo=timezone.utc)).startswith("Good time to call · until")


def test_calls_settings_and_the_board(monkeypatch):
    c = _client(monkeypatch)
    local.set_place("here", CPH)
    page = c.get("/settings?tab=calls").text
    assert 'name="sleep_from"' in page and 'name="work_on"' in page
    bad = c.post("/settings/calls", data={"csrf_token": "csrf", "on": "1", "sleep_from": "7am", "sleep_to": "07:00",
                                          "work_from": "09:00", "work_to": "17:00"}, follow_redirects=False)
    assert not calls.enabled() and "msg=Saved" not in bad.headers["location"]
    ok = c.post("/settings/calls", data={"csrf_token": "csrf", "on": "1", "sleep_from": "22:30", "sleep_to": "06:30",
                                         "work_from": "08:00", "work_to": "16:00", "work_on": "1"}, follow_redirects=False)
    assert "msg=Saved" in ok.headers["location"] and calls.times()["sleep_from"] == "22:30" and calls.work_on()
    board = c.get("/display").text
    assert 'class="notes"' in board and ("Good time to call" in board or "asleep" in board or "at work" in board)


def test_country_lookup_retries_and_never_overwrites_a_new_city(monkeypatch):
    local.set_place("here", CPH)
    calls_made = []

    def down(q):
        calls_made.append(q)
        raise OSError("no network yet")

    monkeypatch.setattr(local, "search_places", down)
    holidays._ensure_country("here")
    holidays._ensure_country("here")  # within the hour: not again
    assert calls_made == ["Copenhagen"] and "country" not in local.get_place("here")
    db.set_setting("country_lookup_here", "0")  # an hour later
    # The admin picks Sydney while the search for Copenhagen runs: Sydney stays.
    def search_then_city_changes(q):
        local.set_place("here", SYD)
        return [{**CPH, "country": "DK"}]

    monkeypatch.setattr(local, "search_places", search_then_city_changes)
    holidays._ensure_country("here")
    assert local.get_place("here")["name"] == "Sydney" and "country" not in local.get_place("here")


def test_november_looks_into_next_year():
    assert holidays._years(datetime(2026, 11, 20, tzinfo=timezone.utc)) == [2026, 2027]
    assert holidays._years(datetime(2026, 6, 1, tzinfo=timezone.utc)) == [2026]
