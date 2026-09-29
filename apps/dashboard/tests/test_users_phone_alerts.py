"""Dashboard -> Users: the platform Phone alerts toggle."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes, services
from stonepi_auth.session import CSRF_COOKIE, PlatformUser

CSRF = "csrf-xyz"
APPS = [
    {"id": "dashboard", "name": "Dashboard", "enabled": True, "capabilities": []},
    {"id": "newscast", "name": "NewsCast", "enabled": True,
     "capabilities": [{"id": "can_add_custom_sources", "label": "Add custom feeds"}]},
]
PEOPLE = [
    {"id": "p-admin", "username": "adam", "display_name": "Adam", "enabled": True, "is_admin": True,
     "phone_alerts": True, "apps": ["dashboard", "newscast"], "permissions": {}},
    {"id": "p-jo", "username": "jo", "display_name": "Jo", "enabled": True, "is_admin": False,
     "phone_alerts": False, "apps": ["dashboard"], "permissions": {}},
]


@pytest.fixture
def env(monkeypatch):
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
    client = TestClient(app)
    client.cookies.set(CSRF_COOKIE, CSRF)
    return {"client": client, "calls": calls}


def _toggle(html: str, person_id: str) -> str:
    form = html.split(f'data-user-form="{person_id}"', 1)[1]
    return form.split('name="phone_alerts"', 1)[1].split("/>", 1)[0]


def test_users_page_shows_phone_alerts_toggle(env):
    html = env["client"].get("/users").text
    assert "Phone alerts" in html
    assert "checked" in _toggle(html, "p-admin")
    assert "checked" not in _toggle(html, "p-jo")


def test_update_sends_phone_alerts(env):
    form = {"csrf_token": CSRF, "action": "save", "display_name": "Jo", "enabled": "1", "is_admin": "0",
            "apps": "dashboard", "phone_alerts": "1"}
    env["client"].post("/users/p-jo", data=form, headers={"Accept": "application/json"})
    method, path, body = env["calls"][-1]
    assert (method, path) == ("PATCH", "/api/users/p-jo")
    assert body["phone_alerts"] is True
    form.pop("phone_alerts")
    env["client"].post("/users/p-jo", data=form, headers={"Accept": "application/json"})
    assert env["calls"][-1][2]["phone_alerts"] is False


def test_create_sends_phone_alerts(env):
    form = {"csrf_token": CSRF, "username": "sam", "password": "password123", "is_admin": "0",
            "apps": "dashboard", "phone_alerts": "1"}
    env["client"].post("/users", data=form, headers={"Accept": "application/json"})
    method, path, body = env["calls"][-1]
    assert (method, path) == ("POST", "/api/users")
    assert body["phone_alerts"] is True
