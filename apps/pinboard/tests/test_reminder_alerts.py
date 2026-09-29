"""Pinboard alerts (Phase 8b): assignee picker, daily reminder check, reminder time, notices."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import alerts, people, routes, store
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
HOUSEHOLD = [{"id": ADAM, "name": "Adam"}, {"id": JO, "name": "Jo"}]
SECRET = "pin-secret"
TODAY = date(2026, 9, 27)


def _at(hh: int, mm: int = 0, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm).astimezone()


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


# -- store -------------------------------------------------------------------------


def test_reminder_keeps_assignee_name_and_user():
    item = store.add_reminder("Bins out", due=TODAY.isoformat(), assignee="Jo", assignee_user=JO)
    saved = store.list_items()["reminders"][0]
    assert saved["assignee"] == "Jo" and saved["assignee_user"] == JO and saved["id"] == item["id"]


def test_reminder_time_default_and_validation():
    assert store.reminder_time() == "08:00"
    assert store.set_reminder_time("7:30") == "07:30"
    assert store.reminder_time() == "07:30"
    with pytest.raises(ValueError):
        store.set_reminder_time("25:00")


def test_other_data_survives_saves():
    store.set_reminder_time("09:15")
    store.add_notice("Hello")
    store.add_reminder("Call plumber")
    assert store.reminder_time() == "09:15"


# -- daily check -----------------------------------------------------------------------


def test_nothing_before_reminder_time(sent):
    store.add_reminder("Bins out", due=TODAY.isoformat(), assignee_user=JO)
    assert alerts.check_due_reminders(_at(7, 59)) == 0
    assert sent == []


def test_assigned_reminder_is_personal_once(sent):
    store.add_reminder("Bins out", due=TODAY.isoformat(), assignee="Jo", assignee_user=JO)
    assert alerts.check_due_reminders(_at(8, 0)) == 1
    event = sent[0]
    assert event["id"] == "pinboard.reminder_due"
    assert event["audience"] == "personal" and event["user"] == JO
    assert event["title"] == "Reminder: Bins out"
    assert alerts.check_due_reminders(_at(8, 2)) == 0
    assert len(sent) == 1


def test_no_repeat_after_restart(sent):
    store.add_reminder("Bins out", due=TODAY.isoformat(), assignee_user=JO)
    alerts.check_due_reminders(_at(8, 0))
    # A restart re-reads the file from disk; the sent record lives there.
    assert store.reminders_due_for_alert(TODAY) == []
    assert alerts.check_due_reminders(_at(11, 0)) == 0
    assert len(sent) == 1


def test_late_start_still_sends_same_day(sent):
    store.add_reminder("Bins out", due=TODAY.isoformat())
    assert alerts.check_due_reminders(_at(14, 30)) == 1


def test_unassigned_and_free_text_are_household(sent):
    store.add_reminder("Water plants", due=TODAY.isoformat())
    store.add_reminder("Fix gate", due=TODAY.isoformat(), assignee="Grandad")  # old free text
    alerts.check_due_reminders(_at(9))
    assert {e["audience"] for e in sent} == {"household"}
    assert all(not e.get("user") for e in sent)
    assert any(e["summary"] == "Due today · Grandad" for e in sent)


def test_only_due_today(sent):
    store.add_reminder("Tomorrow", due=(TODAY + timedelta(days=1)).isoformat())
    store.add_reminder("Yesterday", due=(TODAY - timedelta(days=1)).isoformat())  # no overdue alerts (v1)
    assert alerts.check_due_reminders(_at(9)) == 0


def test_custom_reminder_time(sent):
    store.set_reminder_time("18:30")
    store.add_reminder("Bins out", due=TODAY.isoformat())
    assert alerts.check_due_reminders(_at(9)) == 0
    assert alerts.check_due_reminders(_at(18, 30)) == 1


def test_notify_down_retries_next_tick(monkeypatch):
    import stonepi_contracts

    outcomes = [False, True]
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: outcomes.pop(0))
    store.add_reminder("Bins out", due=TODAY.isoformat())
    assert alerts.check_due_reminders(_at(8)) == 0
    assert alerts.check_due_reminders(_at(8, 2)) == 1


def test_deleted_reminder_record_is_dropped(sent):
    item = store.add_reminder("Bins out", due=TODAY.isoformat())
    alerts.check_due_reminders(_at(8))
    store.delete_item("reminder", item["id"])
    assert store.list_items()["alerts_sent"] == {}


def test_bad_owner_falls_back_to_household(sent):
    store.add_reminder("Bins out", due=TODAY.isoformat(), assignee_user="local")
    alerts.check_due_reminders(_at(8))
    assert sent[0]["audience"] == "household"


# -- pages -----------------------------------------------------------------------------


def _client(monkeypatch, *, secret=SECRET, is_admin=True, uid=ADAM, name="Adam") -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: secret)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    monkeypatch.setattr(people, "household_people", lambda s: list(HOUSEHOLD) if s else [])
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    if secret:
        c.cookies.set(
            COOKIE_NAME,
            encode_session(secret=secret, user_id=uid, username=name.lower(), display_name=name,
                           is_admin=is_admin, apps=["pinboard"], session_id="s", phone_alerts=True),
        )
    return c


def test_reminder_form_offers_household_members(monkeypatch):
    html = _client(monkeypatch).get("/reminders").text
    assert '<select name="assignee_user">' in html
    assert f'<option value="{JO}">Jo</option>' in html
    assert "Anyone (household)" in html


def test_reminder_form_falls_back_to_text_standalone(monkeypatch):
    html = _client(monkeypatch, secret="").get("/reminders").text
    assert '<input name="assignee"' in html and "assignee_user" not in html


def test_picking_a_member_stores_name_and_id(monkeypatch):
    c = _client(monkeypatch)
    c.post("/reminder", data={"text": "Bins out", "due": TODAY.isoformat(), "assignee_user": JO, "csrf_token": "csrf"})
    rem = store.list_items()["reminders"][0]
    assert rem["assignee"] == "Jo" and rem["assignee_user"] == JO


def test_unknown_member_id_is_not_trusted(monkeypatch):
    c = _client(monkeypatch)
    c.post("/reminder", data={"text": "x", "assignee_user": "not-a-member", "assignee": "", "csrf_token": "csrf"})
    rem = store.list_items()["reminders"][0]
    assert rem["assignee_user"] == ""


def test_api_reminder_accepts_assignee_user(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/reminder", json={"text": "Buy milk", "assignee_user": JO}, headers={"X-StonePi-CSRF": "csrf"})
    assert r.json()["ok"] is True
    rem = store.list_items()["reminders"][0]
    assert rem["assignee_user"] == JO and rem["assignee"] == "Jo"
    c.post("/api/reminder", json={"text": "Household thing"}, headers={"X-StonePi-CSRF": "csrf"})
    assert store.list_items()["reminders"][0]["assignee_user"] == ""


def test_new_notice_alerts_household(monkeypatch, sent):
    c = _client(monkeypatch)
    c.post("/notice", data={"text": "Dinner at 7", "csrf_token": "csrf"})
    event = sent[0]
    assert event["id"] == "pinboard.notice_posted" and event["audience"] == "household"
    assert event["summary"] == "Adam: Dinner at 7"


def test_standalone_notice_sends_nothing(monkeypatch, sent):
    _client(monkeypatch, secret="").post("/notice", data={"text": "Hi", "csrf_token": "csrf"})
    assert sent == []


def test_admin_sets_reminder_time(monkeypatch):
    c = _client(monkeypatch)
    assert 'name="reminder_time" value="08:00"' in c.get("/settings?tab=notifications").text
    r = c.post("/settings/reminder-time", data={"reminder_time": "07:15", "csrf_token": "csrf"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert store.reminder_time() == "07:15"
    bad = c.post("/settings/reminder-time", data={"reminder_time": "nope", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]


def test_member_sees_reminder_time_read_only(monkeypatch):
    c = _client(monkeypatch, is_admin=False, uid=JO, name="Jo")
    html = c.get("/settings?tab=notifications").text
    assert 'name="reminder_time"' not in html and "set by an admin" in html
    r = c.post("/settings/reminder-time", data={"reminder_time": "06:00", "csrf_token": "csrf"}, follow_redirects=False)
    assert r.status_code == 303 and store.reminder_time() == "08:00"
