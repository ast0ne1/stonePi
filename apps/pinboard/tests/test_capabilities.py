"""Pinboard capabilities: Post notices and Assign reminders to others."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import people, routes, store
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
HOUSEHOLD = [{"id": ADAM, "name": "Adam"}, {"id": JO, "name": "Jo"}]
SECRET = "pin-secret"


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")


@pytest.fixture(autouse=True)
def no_alerts(monkeypatch):
    import stonepi_contracts

    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: True)


def _client(monkeypatch, *, secret=SECRET, is_admin=False, perms=None, uid=JO, name="Jo") -> TestClient:
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
            encode_session(
                secret=secret, user_id=uid, username=name.lower(), display_name=name, is_admin=is_admin,
                apps=["pinboard"], session_id="s", permissions={"pinboard": perms or {}},
            ),
        )
    return c


ALL = {"can_post_notices": True, "can_assign_others": True}


# -- notices ------------------------------------------------------------------------


def test_member_without_post_notices_is_refused(monkeypatch):
    c = _client(monkeypatch, perms={"can_post_notices": False, "can_assign_others": True})
    r = c.post("/notice", data={"text": "Hi", "csrf_token": "csrf"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert store.list_items()["notices"] == []
    html = c.get("/notices").text
    assert 'action="/notice"' not in html
    assert "Ask an admin to allow &#39;Post notices&#39; in StonePi → Users." in html
    assert 'href="/notices?new=1"' not in c.get("/").text


def test_member_with_post_notices_can_post(monkeypatch):
    c = _client(monkeypatch, perms=ALL)
    assert 'action="/notice"' in c.get("/notices").text
    c.post("/notice", data={"text": "Dinner at 7", "csrf_token": "csrf"})
    assert store.list_items()["notices"][0]["text"] == "Dinner at 7"


def test_admin_and_standalone_post_notices_without_grant(monkeypatch):
    _client(monkeypatch, is_admin=True, uid=ADAM, name="Adam").post("/notice", data={"text": "A", "csrf_token": "csrf"})
    _client(monkeypatch, secret="").post("/notice", data={"text": "B", "csrf_token": "csrf"})
    assert sorted(n["text"] for n in store.list_items()["notices"]) == ["A", "B"]


# -- reminders ----------------------------------------------------------------------


def test_member_without_assign_others_only_gets_me(monkeypatch):
    c = _client(monkeypatch, perms={"can_post_notices": True, "can_assign_others": False})
    html = c.get("/reminders").text
    assert f'<option value="{JO}">Me</option>' in html
    assert "Anyone (household)" not in html and f'<option value="{ADAM}">' not in html
    assert "Assign reminders to others" in html


def test_member_without_assign_others_cannot_assign_someone_else(monkeypatch):
    c = _client(monkeypatch, perms={"can_post_notices": True, "can_assign_others": False})
    r = c.post("/reminder", data={"text": "Bins", "assignee_user": ADAM, "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post("/reminder", data={"text": "Bins", "assignee": "Adam", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert store.list_items()["reminders"] == []


def test_member_without_assign_others_can_assign_self_and_unassigned_becomes_self(monkeypatch):
    c = _client(monkeypatch, perms={"can_post_notices": False, "can_assign_others": False})
    c.post("/reminder", data={"text": "Mine", "assignee_user": JO, "csrf_token": "csrf"})
    c.post("/reminder", data={"text": "Household?", "assignee_user": "", "csrf_token": "csrf"})
    rems = store.list_items()["reminders"]
    assert len(rems) == 2
    assert all(r["assignee_user"] == JO and r["assignee"] == "Jo" for r in rems)


def test_api_reminder_respects_assign_others(monkeypatch):
    c = _client(monkeypatch, perms={"can_post_notices": False, "can_assign_others": False})
    r = c.post("/api/reminder", json={"text": "x", "assignee_user": ADAM}, headers={"X-StonePi-CSRF": "csrf"})
    assert r.status_code == 403 and "Assign reminders to others" in r.json()["message"]
    # PriceScout's shopping-list send (no assignee) lands as the caller's own reminder.
    r = c.post("/api/reminder", json={"text": "Shopping list"}, headers={"X-StonePi-CSRF": "csrf"})
    assert r.json()["ok"] is True
    rem = store.list_items()["reminders"][0]
    assert rem["assignee_user"] == JO


def test_member_with_assign_others_and_admin_are_unrestricted(monkeypatch):
    c = _client(monkeypatch, perms=ALL)
    assert "Anyone (household)" in c.get("/reminders").text
    c.post("/reminder", data={"text": "For Adam", "assignee_user": ADAM, "csrf_token": "csrf"})
    c.post("/reminder", data={"text": "Household", "csrf_token": "csrf"})
    a = _client(monkeypatch, is_admin=True, uid=ADAM, name="Adam")
    a.post("/api/reminder", json={"text": "For Jo", "assignee_user": JO}, headers={"X-StonePi-CSRF": "csrf"})
    by_text = {r["text"]: r["assignee_user"] for r in store.list_items()["reminders"]}
    assert by_text == {"For Adam": ADAM, "Household": "", "For Jo": JO}


def test_cookie_from_before_the_capabilities_keeps_defaults(monkeypatch):
    """A member signed in before the upgrade has no entry for the new capabilities: defaults (on) apply."""
    c = _client(monkeypatch, perms={})
    r = c.post("/notice", data={"text": "Still allowed", "csrf_token": "csrf"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
