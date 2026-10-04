"""Auth calls made on the person's behalf: a revoked session goes to sign-in, a
restarting Auth gets a self-retrying page — never Auth's "Not signed in" as a banner."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import collector, routes, services
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, PlatformUser

CSRF = "csrf-xyz"
APPS = [{"id": "dashboard", "name": "Dashboard", "enabled": True, "capabilities": []}]


def _signed_out(*_a, **_k):
    raise services.AuthAPIError("Not signed in", 401)


def _restarting(*_a, **_k):
    raise services.AuthAPIError(services.AUTH_RESTARTING_MESSAGE, 503, unreachable=True)


@pytest.fixture
def client(monkeypatch):
    admin = PlatformUser(user_id="p-admin", username="adam", display_name="Adam", is_admin=True,
                         apps=["dashboard"], session_id="s")
    monkeypatch.setattr(services, "current_user", lambda cookies: admin)
    monkeypatch.setattr(services, "catalog_apps", lambda **kw: APPS)
    monkeypatch.setattr(routes, "_alerts_bell", lambda request, user: {"show": False, "dot": False})
    monkeypatch.setattr(routes, "_factory_admin", lambda cookies, user: False)
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(CSRF_COOKIE, CSRF)
    c.cookies.set(COOKIE_NAME, "still-signed-but-revoked")
    return c


# --- services.auth_exchange classifies failures -------------------------------------------

def test_auth_exchange_unreachable_is_restarting(monkeypatch):
    def boom(*_a, **_k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(services._AUTH_HTTP, "request", boom)
    with pytest.raises(services.AuthAPIError) as info:
        services.auth_request("GET", "/api/users", {})
    assert info.value.unavailable and not info.value.signed_out
    assert "restarting" in str(info.value)


def test_auth_exchange_401_is_signed_out(monkeypatch):
    monkeypatch.setattr(
        services._AUTH_HTTP, "request",
        lambda *a, **k: httpx.Response(401, json={"detail": "Not signed in"}),
    )
    with pytest.raises(services.AuthAPIError) as info:
        services.auth_request("GET", "/api/users", {})
    assert info.value.signed_out and not info.value.unavailable
    # Still a RuntimeError, so older `except RuntimeError` callers keep working.
    assert isinstance(info.value, RuntimeError)


# --- Users page ---------------------------------------------------------------------------

def test_users_page_revoked_session_redirects_to_login(client, monkeypatch):
    monkeypatch.setattr(services, "auth_request", _signed_out)
    resp = client.get("/users")
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "/login?next=" in location and "users" in location
    # The dead session cookie is dropped so Dashboard stops trusting it.
    assert any(COOKIE_NAME + "=" in h and ("Max-Age=0" in h or "expires=" in h.lower())
               for h in resp.headers.get_list("set-cookie"))


def test_users_page_auth_restarting_retries_instead_of_banner(client, monkeypatch):
    monkeypatch.setattr(services, "auth_request", _restarting)
    resp = client.get("/users")
    assert resp.status_code == 503
    assert "data-auth-wait" in resp.text
    assert "Auth is restarting" in resp.text
    assert "Not signed in" not in resp.text


def test_users_page_other_errors_still_banner(client, monkeypatch):
    def forbidden(*_a, **_k):
        raise services.AuthAPIError("Administrator only", 403)

    monkeypatch.setattr(services, "auth_request", forbidden)
    resp = client.get("/users")
    assert resp.status_code == 200
    assert "Administrator only" in resp.text


def test_users_update_json_revoked_session_gives_login_url(client, monkeypatch):
    monkeypatch.setattr(services, "auth_request", _signed_out)
    resp = client.post(
        "/users/p-jo",
        data={"csrf_token": CSRF, "action": "save", "display_name": "Jo", "enabled": "1"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["ok"] is False and body["signed_out"] is True
    assert "/login?next=" in body["login_url"]


def test_users_create_form_revoked_session_redirects_to_login(client, monkeypatch):
    monkeypatch.setattr(services, "auth_request", _signed_out)
    resp = client.post("/users", data={"csrf_token": CSRF, "username": "sam", "password": "password123"})
    assert resp.status_code == 303
    assert "/login?next=" in resp.headers["location"]
    assert "users" in resp.headers["location"]


# --- Services / Health pages ----------------------------------------------------------------

def _ready_snapshot():
    return {
        "ready": True,
        "cards": [{"id": "auth", "name": "Auth", "unit": "stonepi-auth", "unit_status": "active",
                   "health": {"ok": True, "status": 200}, "port": 8011, "path": "/auth", "url": "/auth/"}],
        "watch": {"level": "healthy", "apps": []},
    }


def test_services_page_revoked_session_redirects(client, monkeypatch):
    monkeypatch.setattr(services, "auth_request", _signed_out)
    monkeypatch.setattr(collector, "get_snapshot", _ready_snapshot)
    resp = client.get("/applications")
    assert resp.status_code == 303
    assert "/login?next=" in resp.headers["location"]


def test_overview_revoked_session_redirects(client, monkeypatch):
    monkeypatch.setattr(services, "auth_request", _signed_out)
    monkeypatch.setattr(collector, "get_snapshot", _ready_snapshot)
    resp = client.get("/overview")
    assert resp.status_code == 303
    assert "/login?next=" in resp.headers["location"]


def test_password_change_revoked_session_redirects(client, monkeypatch):
    def exchange(*_a, **_k):
        raise services.AuthAPIError("Not signed in", 401)

    monkeypatch.setattr(services, "auth_exchange", exchange)
    resp = client.post(
        "/settings/password",
        data={"csrf_token": CSRF, "current_password": "a", "new_password": "bbbbbbbb", "new_password_confirm": "bbbbbbbb"},
    )
    assert resp.status_code == 303
    assert "/login?next=" in resp.headers["location"]
