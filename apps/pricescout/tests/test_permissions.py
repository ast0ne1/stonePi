"""PriceScout permissions: Refresh needs Manage sources; madspild uses one household postcode."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes
from app.collectors import registry
from app.services import ingest
from stonepi_auth.csrf import CSRF_COOKIE
from stonepi_auth.session import COOKIE_NAME, encode_session

SECRET = "ps-perm-secret"
ADMIN = "11111111-1111-4111-8111-111111111111"
MEMBER = "22222222-2222-4222-8222-222222222222"
CSRF = "csrf-test-token"


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ps.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "CACHE_DIR", tmp_path / "cache")
    db.init_db()


@pytest.fixture
def app_env(monkeypatch, fresh_db):
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "_salling_token", lambda: "")
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    monkeypatch.setattr(routes, "notifications_card_context", lambda *a, **kw: None)

    runs: list[str] = []

    class _SyncThread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(routes.threading, "Thread", _SyncThread)
    monkeypatch.setattr(routes.ingest, "refresh_all", lambda **kw: runs.append(db.household_zip()) or {"ok": True})
    app = FastAPI()
    app.include_router(routes.router)
    return app, runs


def _client(app, *, user_id: str, admin: bool, perms: dict | None = None) -> TestClient:
    c = TestClient(app)
    c.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=user_id, username=user_id[:4], display_name="U", is_admin=admin,
            apps=["pricescout"], session_id="s", permissions=perms or {},
        ),
    )
    c.cookies.set(CSRF_COOKIE, CSRF)
    return c


def _refresh(c: TestClient):
    return c.post("/sources/refresh", data={"csrf_token": CSRF, "next": "offers"}, follow_redirects=False)


def test_member_without_capability_cannot_refresh(app_env):
    app, runs = app_env
    c = _client(app, user_id=MEMBER, admin=False)
    assert 'action="/sources/refresh"' not in c.get("/").text
    assert 'action="/sources/refresh"' not in c.get("/sources").text
    r = _refresh(c)
    assert r.status_code == 403
    assert runs == []


def test_stale_can_use_alerts_grant_is_harmless(app_env):
    app, runs = app_env
    c = _client(app, user_id=MEMBER, admin=False, perms={"pricescout": {"can_use_alerts": True}})
    assert c.get("/").status_code == 200
    assert 'action="/sources/refresh"' not in c.get("/").text
    assert _refresh(c).status_code == 403
    assert runs == []


def test_member_with_capability_can_refresh(app_env):
    app, runs = app_env
    c = _client(app, user_id=MEMBER, admin=False, perms={"pricescout": {"can_manage_sources": True}})
    assert 'action="/sources/refresh"' in c.get("/").text
    r = _refresh(c)
    assert r.status_code == 303
    assert "Refresh%20started" in r.headers["location"]
    assert len(runs) == 1


def test_admin_can_refresh(app_env):
    app, runs = app_env
    c = _client(app, user_id=ADMIN, admin=True)
    assert 'action="/sources/refresh"' in c.get("/").text
    assert _refresh(c).status_code == 303
    assert len(runs) == 1


def test_refresh_uses_household_postcode_not_clickers(app_env):
    app, runs = app_env
    db.set_household_zip("2100")
    db.set_pref(MEMBER, "zip", "8000")
    db.set_pref(ADMIN, "zip", "5000")
    member = _client(app, user_id=MEMBER, admin=False, perms={"pricescout": {"can_manage_sources": True}})
    admin = _client(app, user_id=ADMIN, admin=True)
    _refresh(member)
    _refresh(admin)
    assert runs == ["2100", "2100"]


def test_ingest_foodwaste_uses_household_postcode(fresh_db, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(registry, "tjek_collectors", lambda: [])
    monkeypatch.setattr(registry, "foodwaste_collectors", lambda zip_code: seen.append(zip_code) or [])
    db.set_household_zip("2100")
    db.set_pref("someone", "zip", "9000")
    ingest.refresh_all(force_mock=False)
    assert seen == ["2100"]


def test_household_postcode_is_admin_only(app_env):
    app, _runs = app_env
    member = _client(app, user_id=MEMBER, admin=False, perms={"pricescout": {"can_manage_sources": True}})
    html = member.get("/settings?tab=general&panel=preferences").text
    assert 'action="/settings/household"' not in html
    assert "Only an admin can change it" in html
    r = member.post("/settings/household", data={"csrf_token": CSRF, "household_zip": "8000"}, follow_redirects=False)
    assert r.status_code == 403
    assert db.household_zip() == ""

    admin = _client(app, user_id=ADMIN, admin=True)
    assert 'action="/settings/household"' in admin.get("/settings?tab=general&panel=preferences").text
    r = admin.post("/settings/household", data={"csrf_token": CSRF, "household_zip": "2100"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.household_zip() == "2100"
    bad = admin.post("/settings/household", data={"csrf_token": CSRF, "household_zip": "abc"}, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    assert db.household_zip() == "2100"


def test_member_general_save_does_not_touch_household(app_env):
    app, _runs = app_env
    db.set_household_zip("2100")
    member = _client(app, user_id=MEMBER, admin=False)
    member.post("/settings/general", data={"csrf_token": CSRF, "currency": "EUR", "zip": "8000"}, follow_redirects=False)
    assert db.household_zip() == "2100"
    assert db.get_pref(MEMBER, "currency") == "EUR"


def test_household_postcode_migrates_from_first_admin(app_env):
    app, _runs = app_env
    db.set_pref(MEMBER, "zip", "8000")
    member = _client(app, user_id=MEMBER, admin=False)
    member.get("/settings?tab=general&panel=preferences")
    assert db.household_zip() == ""  # members never seed it
    db.set_pref(ADMIN, "zip", "5000")
    admin = _client(app, user_id=ADMIN, admin=True)
    admin.get("/settings")
    assert db.household_zip() == "5000"
    db.set_pref(ADMIN, "zip", "6000")
    admin.get("/settings")
    assert db.household_zip() == "5000"  # one-time only


def test_household_postcode_migrates_from_solo_value(fresh_db):
    db.set_pref("local", "zip", "2300")
    db.init_db()
    assert db.household_zip() == "2300"
