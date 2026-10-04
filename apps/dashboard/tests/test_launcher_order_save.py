"""Home -> reorder: fetch saves get honest JSON (a failed save must never look saved)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes, services
from stonepi_auth.session import CSRF_COOKIE, PlatformUser

CSRF = "csrf-xyz"
FETCH = {"Accept": "application/json", "X-Requested-With": "fetch"}


@pytest.fixture
def env(monkeypatch):
    calls: list[tuple] = []
    state = {"fail": None, "me": {"launcher_order": []}}

    def fake_auth(method, path, cookies, json_body=None):
        calls.append((method, path, json_body))
        if state["fail"] is not None:
            raise state["fail"]
        if path == "/api/me/launcher-order":
            return {"launcher_order": json_body["order"]}
        if path == "/api/me":
            return state["me"]
        return {}

    member = PlatformUser(user_id="p-jo", username="jo", display_name="Jo", is_admin=False,
                          apps=["dashboard", "newscast", "fileserve"], session_id="s")
    monkeypatch.setattr(services, "current_user", lambda cookies: member)
    monkeypatch.setattr(services, "auth_request", fake_auth)
    monkeypatch.setattr(routes, "_alerts_bell", lambda request, user: {"show": False, "dot": False})
    monkeypatch.setattr(routes, "_factory_admin", lambda cookies, user: False)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(CSRF_COOKIE, CSRF)
    return {"client": client, "calls": calls, "state": state}


def test_fetch_save_returns_json_ok(env):
    resp = env["client"].post("/launcher-order", data={"csrf_token": CSRF, "order": "fileserve,newscast"}, headers=FETCH)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "order": ["fileserve", "newscast"], "message": "App order saved"}
    assert env["calls"][-1] == ("PUT", "/api/me/launcher-order", {"order": ["fileserve", "newscast"]})


def test_fetch_expired_form_is_an_error_not_a_redirect(env):
    resp = env["client"].post("/launcher-order", data={"csrf_token": "stale", "order": "newscast"}, headers=FETCH)
    assert resp.status_code == 403
    assert resp.json()["ok"] is False
    assert not env["calls"]


def test_fetch_auth_error_is_reported(env):
    env["state"]["fail"] = services.AuthAPIError("database is locked", 500)
    resp = env["client"].post("/launcher-order", data={"csrf_token": CSRF, "order": "newscast"}, headers=FETCH)
    assert resp.status_code == 500
    assert resp.json() == {"ok": False, "error": "database is locked"}


def test_fetch_auth_restarting_is_503(env):
    env["state"]["fail"] = services.AuthAPIError(services.AUTH_RESTARTING_MESSAGE, 503, unreachable=True)
    resp = env["client"].post("/launcher-order", data={"csrf_token": CSRF, "order": "newscast"}, headers=FETCH)
    assert resp.status_code == 503
    assert resp.json()["ok"] is False and resp.json()["retry"] is True


def test_fetch_revoked_session_gives_login_url(env):
    env["state"]["fail"] = services.AuthAPIError("Not signed in", 401)
    resp = env["client"].post("/launcher-order", data={"csrf_token": CSRF, "order": "newscast"}, headers=FETCH)
    assert resp.status_code == 401
    assert resp.json()["signed_out"] is True
    assert "/login?next=" in resp.json()["login_url"]


def test_fetch_no_session_is_401_json(env, monkeypatch):
    monkeypatch.setattr(services, "current_user", lambda cookies: None)
    resp = env["client"].post("/launcher-order", data={"csrf_token": CSRF, "order": "newscast"}, headers=FETCH)
    assert resp.status_code == 401
    assert resp.json()["ok"] is False


def test_plain_form_post_keeps_redirect(env):
    env["state"]["fail"] = services.AuthAPIError("database is locked", 500)
    resp = env["client"].post("/launcher-order", data={"csrf_token": CSRF, "order": "newscast"})
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/?err=")


def test_home_read_only_while_auth_restarting(env):
    env["state"]["fail"] = services.AuthAPIError(services.AUTH_RESTARTING_MESSAGE, 503, unreachable=True)
    resp = env["client"].get("/")
    assert resp.status_code == 200
    assert "data-launcher-readonly" in resp.text
    assert "data-launcher-edit-toggle" not in resp.text


def test_home_reorderable_when_auth_answers(env):
    resp = env["client"].get("/")
    assert resp.status_code == 200
    assert "data-launcher-readonly" not in resp.text
    assert "data-launcher-edit-toggle" in resp.text
    assert "data-launcher-status" in resp.text


def test_home_revoked_session_goes_to_login(env):
    env["state"]["fail"] = services.AuthAPIError("Not signed in", 401)
    resp = env["client"].get("/")
    assert resp.status_code == 303
    assert "/login?next=" in resp.headers["location"]
