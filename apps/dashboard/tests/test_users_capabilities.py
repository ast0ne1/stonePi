"""Dashboard -> Users: catalog-driven capability toggles (EventTrakr) and their defaults."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes, services
from stonepi_auth import app_by_id
from stonepi_auth.session import CSRF_COOKIE, PlatformUser

CSRF = "csrf-xyz"
EVENTTRAKR = {**app_by_id("eventtrakr"), "enabled": True}
APPS = [{"id": "dashboard", "name": "Dashboard", "enabled": True, "capabilities": []}, EVENTTRAKR]
PEOPLE = [
    {"id": "p-admin", "username": "adam", "display_name": "Adam", "enabled": True, "is_admin": True,
     "phone_alerts": True, "apps": ["dashboard", "eventtrakr"], "permissions": {}},
    {"id": "p-jo", "username": "jo", "display_name": "Jo", "enabled": True, "is_admin": False,
     "phone_alerts": False, "apps": ["dashboard", "eventtrakr"],
     "permissions": {"eventtrakr": {"can_manage_sources": False, "can_use_social": True,
                                    "can_share_agenda": True, "can_sync_calendar": False}}},
]


@pytest.fixture
def client(monkeypatch):
    calls: list[tuple] = []

    def fake_auth(method, path, cookies, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET" and path == "/api/users":
            return {"users": PEOPLE}
        return {"ok": True}

    admin = PlatformUser(user_id="p-admin", username="adam", display_name="Adam", is_admin=True,
                         apps=["dashboard"], session_id="s")
    monkeypatch.setattr(services, "current_user", lambda cookies: admin)
    monkeypatch.setattr(services, "auth_request", fake_auth)
    monkeypatch.setattr(services, "catalog_apps", lambda **kw: APPS)
    monkeypatch.setattr(routes, "_alerts_bell", lambda request, user: {"show": False, "dot": False})
    monkeypatch.setattr(routes, "_factory_admin", lambda cookies, user: False)
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, CSRF)
    c.calls = calls
    return c


def _cap(html: str, cap: str) -> str:
    return html.split(f'name="perm_eventtrakr_{cap}"', 1)[1].split("/>", 1)[0]


def test_users_page_renders_eventtrakr_toggles(client):
    html = client.get("/users").text
    for label in ("Manage sources", "Instagram &amp; Facebook", "Share agenda publicly", "Google Calendar sync"):
        assert label in html


def test_existing_person_shows_saved_choices(client):
    html = client.get("/users").text
    jo = html.split('data-user-form="p-jo"', 1)[1]
    assert "checked" not in _cap(jo, "can_manage_sources")
    assert "checked" in _cap(jo, "can_use_social")
    assert "checked" not in _cap(jo, "can_sync_calendar")


def test_new_person_form_starts_from_catalog_defaults(client):
    html = client.get("/users").text
    create = html.split("data-create-form", 1)[1]
    assert "checked" in _cap(create, "can_manage_sources")
    assert "checked" not in _cap(create, "can_use_social")
    assert "checked" in _cap(create, "can_share_agenda")
    assert "checked" in _cap(create, "can_sync_calendar")


def test_saving_sends_every_eventtrakr_capability(client):
    form = {"csrf_token": CSRF, "action": "save", "display_name": "Jo", "enabled": "1", "is_admin": "0",
            "apps": ["dashboard", "eventtrakr"], "perm_eventtrakr_can_manage_sources": "1"}
    client.post("/users/p-jo", data=form, headers={"Accept": "application/json"})
    body = client.calls[-1][2]
    assert body["permissions"]["eventtrakr"] == {
        "can_manage_sources": True, "can_use_social": False, "can_share_agenda": False, "can_sync_calendar": False,
    }


def test_own_role_is_locked_and_no_self_disable_or_delete(client):
    html = client.get("/users").text
    me = html.split('data-user-detail="p-admin"', 1)[1].split("</article>", 1)[0]
    assert 'data-role-seg="user" disabled' in me and 'data-role-seg="admin" disabled' in me
    assert "data-delete-user" not in me and "data-toggle-enabled" not in me
    jo = html.split('data-user-detail="p-jo"', 1)[1].split("</article>", 1)[0]
    assert 'data-role-seg="user" disabled' not in jo and "data-delete-user" in jo


def test_save_without_role_field_never_demotes(client):
    # Your own page: the locked role switch isn't submitted.
    form = {"csrf_token": CSRF, "action": "save", "display_name": "Adam", "enabled": "1", "apps": ["dashboard"]}
    client.post("/users/p-admin", data=form, headers={"Accept": "application/json"})
    body = client.calls[-1][2]
    assert "is_admin" not in body
    assert body["enabled"] is True
