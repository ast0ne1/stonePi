"""Pinboard personal alerts: bell + Settings -> Notifications card (no emitters until Phase 8b)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes, store
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SECRET = "pb-test-secret"


def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(
        routes, "bell_context", lambda user, **kw: {"show": bool(user), "dot": True, "url": "/notifications"}
    )
    c = _app()
    c.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=OWNER, username="jo", display_name="Jo", is_admin=True,
            apps=["pinboard"], session_id="s", phone_alerts=True,
        ),
    )
    return c


def test_settings_hub_lists_notifications(client):
    html = client.get("/settings").text
    assert 'href="/settings?tab=notifications"' in html
    assert 'href="/settings?tab=about"' in html


def test_settings_notifications_tab_uses_shared_card(client):
    html = client.get("/settings?tab=notifications").text
    assert "Manage personal alerts" in html
    assert "Reminder due" in html  # catalog events for Pinboard
    assert "alerts#choose" in html  # admin extras


@pytest.mark.parametrize("path", ["/", "/notices", "/reminders", "/settings?tab=about"])
def test_top_bar_has_bell_before_sign_out(client, path):
    html = client.get(path).text
    bell = html.find('class="icon-btn alerts-bell')
    sign_out = html.find('class="icon-btn sign-out"')
    assert 0 < bell < sign_out
    assert "alerts-bell-dot" in html


def test_standalone_has_no_bell_or_card(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    html = _app().get("/settings?tab=notifications").text
    assert "Manage personal alerts" not in html
    assert "alerts-bell" not in html
