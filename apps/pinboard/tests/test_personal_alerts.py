"""Pinboard personal alerts: bell + Settings -> Notifications card (no emitters until Phase 8b)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes, store
from stonepi_auth.header import header_context
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
        routes,
        "bell_context",
        lambda user, **kw: {
            "show": bool(user),
            "dot": True,
            "url": "/notifications",
            "panel": None,
            "header": header_context(kw["app_id"], user, home_url="http://stonepi.test"),
        },
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
def test_shared_header_order_and_bottom_bar(client, path):
    html = client.get(path).text
    bell = html.find('class="icon-btn sp-bell has-dot"')
    switcher = html.find('aria-label="Switch app"')
    avatar = html.find('class="sp-trigger sp-avatar"')
    assert 0 < bell < switcher < avatar
    assert "Log out" in html
    assert 'class="icon-btn sign-out"' not in html
    assert "alerts-bell" not in html
    nav = html[html.find('<nav class="nav"'):]
    nav = nav[: nav.find("</nav>")]
    labels = [part.split("</span>")[0] for part in nav.split("<span>")[1:]]
    assert labels == ["Board", "Notices", "Reminders", "Settings"]


def test_board_plus_opens_add_to_board(client):
    html = client.get("/").text
    assert 'class="sp-fab" href="/reminders?new=1" aria-label="Add" title="Add" data-open-sheet="add-pick"' in html
    assert 'data-sheet="add-pick"' in html
    # The old red Add button in the page header is gone: one add control per page.
    assert html.count('data-open-sheet="add-pick"') == 1


def test_new_notice_is_a_floating_button_on_notices(client):
    html = client.get("/notices").text
    assert 'class="sp-fab" href="/notices?new=1#add-notice"' in html
    assert 'aria-label="New notice"' in html


@pytest.mark.parametrize("path", ["/reminders", "/settings?tab=about"])
def test_no_floating_button_off_board_and_notices(client, path):
    assert 'class="sp-fab"' not in client.get(path).text


def test_standalone_has_no_bell_or_card(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    html = _app().get("/settings?tab=notifications").text
    assert "Manage personal alerts" not in html
    assert "sp-bell" not in html
