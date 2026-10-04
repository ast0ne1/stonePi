"""Health page live updates: /api/overview/watch + collector early refresh (rate-limited)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import collector, routes, services
from stonepi_auth.session import CSRF_COOKIE, PlatformUser

CSRF = "csrf-xyz"


def _card(app_id, ok=True, unit="active"):
    return {"id": app_id, "name": app_id.title(), "unit": f"stonepi-{app_id}", "unit_status": unit,
            "health": {"ok": ok, "status": 200 if ok else 0}, "port": 8000, "path": f"/{app_id}", "url": f"/{app_id}/"}


def _row(app_id, ok=True, unit="active", level="healthy"):
    return {"id": app_id, "n": app_id.title(), "enabled": True, "running": ok, "unit": unit,
            "health_ok": ok, "level": level}


@pytest.fixture
def env(monkeypatch):
    admin = PlatformUser(user_id="p-admin", username="adam", display_name="Adam", is_admin=True,
                         apps=["dashboard"], session_id="s")
    refreshes: list[float] = []
    state = {"snap": {"ready": True, "cards": [], "watch": {}}, "disabled": []}
    monkeypatch.setattr(services, "current_user", lambda cookies: admin)
    monkeypatch.setattr(services, "auth_request", lambda *a, **k: {"disabled": state["disabled"]})
    monkeypatch.setattr(collector, "get_snapshot", lambda: state["snap"])
    monkeypatch.setattr(collector, "request_refresh", lambda window=0.0: refreshes.append(window))
    monkeypatch.setattr(routes, "_disabled_cache", {"at": -1e9, "ids": frozenset()})
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(CSRF_COOKIE, CSRF)
    return {"client": client, "state": state, "refreshes": refreshes}


def test_poll_reports_attention_and_nudges_collector(env):
    env["state"]["snap"] = {
        "ready": True,
        "cards": [_card("auth"), _card("newscast", ok=False, unit="activating")],
        "watch": {"level": "attention", "summary": "NewsCast is not running", "checked_at": "2026-10-03T10:00:00",
                  "apps": [_row("auth"), _row("newscast", ok=False, unit="activating", level="attention")]},
    }
    resp = env["client"].get("/api/overview/watch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] == "attention" and data["settled"] is False and data["transitional"] is True
    assert data["healthy_count"] == 1 and data["total"] == 2
    assert {c["id"]: c["unit_status"] for c in data["cards"]} == {"auth": "active", "newscast": "activating"}
    assert env["refreshes"] == [0.0]
    assert resp.headers["cache-control"] == "no-store"


def test_poll_all_clear_is_settled_and_leaves_collector_alone(env):
    env["state"]["snap"] = {
        "ready": True,
        "cards": [_card("auth")],
        "watch": {"level": "healthy", "summary": "", "apps": [_row("auth")]},
    }
    data = env["client"].get("/api/overview/watch").json()
    assert data["settled"] is True and data["level"] == "healthy"
    assert env["refreshes"] == []


def test_poll_applies_hidden_apps(env):
    env["state"]["disabled"] = ["newscast"]
    env["state"]["snap"] = {
        "ready": True,
        "cards": [_card("auth"), _card("newscast", ok=False, unit="inactive")],
        "watch": {"level": "attention", "summary": "NewsCast is not running", "backup_status": "ok",
                  "backup_age_days": 0.5, "disk_pct": 20,
                  "apps": [_row("auth"), _row("newscast", ok=False, unit="inactive", level="attention")]},
    }
    data = env["client"].get("/api/overview/watch").json()
    assert data["level"] == "healthy"
    assert next(c for c in data["cards"] if c["id"] == "newscast")["enabled"] is False


def test_poll_revoked_session_gives_login_url(env, monkeypatch):
    def signed_out(*_a, **_k):
        raise services.AuthAPIError("Not signed in", 401)

    monkeypatch.setattr(services, "auth_request", signed_out)
    resp = env["client"].get("/api/overview/watch")
    assert resp.status_code == 401
    assert "/login?next=" in resp.json()["login_url"]


def test_overview_page_has_live_region(env, monkeypatch):
    monkeypatch.setattr(routes, "_alerts_bell", lambda request, user: {"show": False, "dot": False})
    monkeypatch.setattr(routes, "_factory_admin", lambda cookies, user: False)
    monkeypatch.setattr(routes, "services_platform_version", lambda: "0.1.8.1")
    env["state"]["snap"] = {
        "ready": True,
        "cards": [_card("auth"), _card("newscast", ok=False, unit="inactive")],
        "watch": {"level": "attention", "summary": "NewsCast is not running",
                  "apps": [_row("auth"), _row("newscast", ok=False, unit="inactive", level="attention")]},
        "backup": {"status": "ok"},
        "destinations": {"ntfy": {"configured": False}, "trmnl": {"configured": False}},
    }
    html = env["client"].get("/overview").text
    assert "data-health-live" in html and 'data-health-settled="false"' in html
    assert 'data-health-card="newscast"' in html
    assert "data-health-label" in html


def test_service_action_triggers_fast_refresh(env, monkeypatch):
    card = {**_card("newscast"), "unit": "stonepi-newscast"}
    monkeypatch.setattr(services, "application_cards", lambda cookies=None: [card])
    monkeypatch.setattr(services, "control_unit", lambda unit, action: (True, "ok"))
    resp = env["client"].post("/applications/newscast/restart", data={"csrf_token": CSRF})
    assert resp.status_code == 303
    assert env["refreshes"] == [30.0]


# --- collector scheduling -----------------------------------------------------------------

@pytest.fixture
def fresh_state(monkeypatch):
    monkeypatch.setattr(collector, "_cards_state", {"last": 1000.0, "early": False, "fast_until": -1e9})
    monkeypatch.setattr(collector, "_snapshot", {"watch": {"apps": [_row("auth")]}})


def test_cards_due_normally_after_full_interval(fresh_state):
    assert collector._cards_due_at(1001.0) == 1000.0 + collector.CARDS_INTERVAL


def test_request_refresh_is_rate_limited_to_fast_interval(fresh_state):
    collector.request_refresh()
    # Due early, but never sooner than FAST_CARDS_INTERVAL after the last pass.
    assert collector._cards_due_at(1001.0) == 1000.0 + collector.FAST_CARDS_INTERVAL
    assert collector._wake.is_set()
    collector._wake.clear()


def test_refresh_window_keeps_fast_cadence(fresh_state, monkeypatch):
    monkeypatch.setattr(collector.time, "monotonic", lambda: 1000.0)
    collector.request_refresh(window=30)
    collector._cards_state["early"] = False  # one pass has run
    assert collector._cards_due_at(1010.0) == 1000.0 + collector.FAST_CARDS_INTERVAL
    assert collector._cards_due_at(1040.0) == 1000.0 + collector.CARDS_INTERVAL
    collector._wake.clear()


def test_transitional_unit_keeps_fast_cadence(fresh_state, monkeypatch):
    monkeypatch.setattr(collector, "_snapshot", {"watch": {"apps": [_row("newscast", ok=False, unit="activating")]}})
    assert collector._cards_due_at(1001.0) == 1000.0 + collector.FAST_CARDS_INTERVAL
    # A plainly stopped app doesn't keep the loop busy on its own.
    monkeypatch.setattr(collector, "_snapshot", {"watch": {"apps": [_row("newscast", ok=False, unit="inactive")]}})
    assert collector._cards_due_at(1001.0) == 1000.0 + collector.CARDS_INTERVAL
