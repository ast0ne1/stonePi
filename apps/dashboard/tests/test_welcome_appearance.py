"""Settings → General → Appearance (F6): welcome message, Home greeting, App colours removed."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes, services
from stonepi_auth.session import CSRF_COOKIE, PlatformUser

CSRF = "csrf-xyz"


@pytest.fixture
def env(monkeypatch):
    calls: list[tuple] = []
    state = {"me": {"launcher_order": [], "welcome": {"mode": "time", "text": ""}}}

    def fake_auth(method, path, cookies, json_body=None):
        calls.append((method, path, json_body))
        if path == "/api/me/welcome":
            state["me"]["welcome"] = services.normalize_welcome(json_body)
            return {"ok": True, "welcome": state["me"]["welcome"]}
        if path == "/api/me" and method == "PATCH":
            state["me"]["display_name"] = json_body["display_name"]
            return state["me"]
        if path == "/api/me":
            return state["me"]
        return {}

    member = PlatformUser(user_id="p-jo", username="jo", display_name="Jo Bloggs", is_admin=False,
                          apps=["dashboard", "newscast"], session_id="s")
    monkeypatch.setattr(services, "current_user", lambda cookies: member)
    monkeypatch.setattr(services, "auth_request", fake_auth)
    monkeypatch.setattr(routes, "_alerts_bell", lambda request, user: {"show": False, "dot": False})
    monkeypatch.setattr(routes, "_factory_admin", lambda cookies, user: False)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(CSRF_COOKIE, CSRF)
    return {"client": client, "calls": calls, "state": state}


def test_normalize_welcome_default_options_and_cap():
    assert services.normalize_welcome(None) == {"mode": "time", "text": ""}
    assert services.normalize_welcome({"mode": "nope"})["mode"] == "time"
    for mode in ("time", "hello", "none"):
        assert services.normalize_welcome({"mode": mode})["mode"] == mode
    assert services.normalize_welcome({"mode": "custom", "text": "y" * 99})["text"] == "y" * 60
    assert services.normalize_welcome({"mode": "custom", "text": "  "})["mode"] == "time"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(0, "Good morning"), (11, "Good morning"), (12, "Good afternoon"), (17, "Good afternoon"), (18, "Good evening"), (23, "Good evening")],
)
def test_time_based_greeting_hours(hour, expected):
    assert routes._greeting_for_hour(hour) == expected


def test_save_welcome_puts_to_auth(env):
    resp = env["client"].post(
        "/settings/welcome", data={"csrf_token": CSRF, "welcome_mode": "custom", "welcome_text": "Hi {name}"}
    )
    assert resp.status_code == 303 and "msg=Welcome" in resp.headers["location"]
    assert env["calls"][-1] == ("PUT", "/api/me/welcome", {"mode": "custom", "text": "Hi {name}"})


def test_custom_without_text_is_refused(env):
    resp = env["client"].post("/settings/welcome", data={"csrf_token": CSRF, "welcome_mode": "custom", "welcome_text": " "})
    assert resp.status_code == 303 and "err=" in resp.headers["location"]
    assert not any(path == "/api/me/welcome" for _m, path, _b in env["calls"])


def test_expired_form_does_not_save(env):
    resp = env["client"].post("/settings/welcome", data={"csrf_token": "stale", "welcome_mode": "hello"})
    assert "err=Form+expired" in resp.headers["location"]
    assert not any(path == "/api/me/welcome" for _m, path, _b in env["calls"])


def test_home_greets_by_account_alias(env):
    env["state"]["me"]["welcome"] = {"mode": "custom", "text": "Welcome home, {name}"}
    # No alias from Auth: the session's display name, in full (never the username).
    html = env["client"].get("/").text
    assert "Welcome home, Jo Bloggs" in html and "Welcome home, jo" not in html
    # The live alias from Auth wins (renamed after signing in).
    env["state"]["me"]["display_name"] = "Jojo"
    html = env["client"].get("/").text
    assert "Welcome home, Jojo" in html
    env["state"]["me"]["welcome"] = {"mode": "hello", "text": ""}
    assert "Hello, Jojo" in env["client"].get("/").text
    assert "Your apps" in html
    env["state"]["me"]["welcome"] = {"mode": "none", "text": ""}
    html = env["client"].get("/").text
    assert 'class="home-greeting"' not in html and "Your apps" in html


def test_app_colours_and_palettes_are_gone(env):
    assert env["client"].post("/settings/app-colours", data={"csrf_token": CSRF}).status_code in (404, 405)
    html = env["client"].get("/settings?tab=general&panel=appearance").text
    assert "App colours" not in html and "data-palette-set" not in html
    assert 'data-sp-theme="system"' in html and 'name="welcome_mode"' in html


def test_welcome_name_fallbacks():
    from types import SimpleNamespace

    assert services.welcome_name(SimpleNamespace(display_name="Adam Stone", username="admin")) == "Adam Stone"
    assert services.welcome_name(SimpleNamespace(display_name="", username="admin")) == "admin"
    assert services.welcome_name(SimpleNamespace(display_name="Admin", username="admin"), "Adam") == "Adam"


def test_welcome_form_saves_alias_and_home_uses_it(env):
    resp = env["client"].post(
        "/settings/welcome", data={"csrf_token": CSRF, "welcome_mode": "hello", "display_name": "  Adam "}
    )
    assert resp.status_code == 303 and "msg=Welcome" in resp.headers["location"]
    assert ("PATCH", "/api/me", {"display_name": "Adam"}) in env["calls"]
    assert "Hello, Adam" in env["client"].get("/").text
    html = env["client"].get("/settings?tab=general&panel=appearance").text
    assert 'name="display_name"' in html and 'value="Adam"' in html
