"""SportGuide personal alerts: bell + Settings card (per-person alerts: test_per_person.py)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes
from app.services import notify as notify_service
from stonepi_auth.header import header_context
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


# -- pages -----------------------------------------------------------------------

SECRET = "sg-test-secret"


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sport.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()


@pytest.fixture
def client(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
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
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=OWNER, username="jo", display_name="Jo", is_admin=True,
            apps=["sportguide"], session_id="s", phone_alerts=True,
        ),
    )
    return c


def test_settings_notifications_tab_uses_shared_card_with_lead_time_below(client):
    html = client.get("/settings?tab=notifications").text
    assert "Manage personal alerts" in html
    assert "alerts#choose" in html  # admin extras
    card = html.find("Manage personal alerts")
    lead = html.find('name="notify_approaching_minutes"')
    assert 0 < card < lead


def test_shared_header_order_and_bottom_bar(client):
    html = client.get("/settings?tab=general").text
    refresh = html.find('class="btn btn-primary btn-refresh')
    bell = html.find('class="icon-btn sp-bell has-dot"')
    switcher = html.find('aria-label="Switch app"')
    avatar = html.find('class="sp-trigger sp-avatar"')
    assert 0 < refresh < bell < switcher < avatar
    assert "Log out" in html and 'class="icon-btn sign-out"' not in html
    assert 'aria-label="Search"' not in html  # nothing to search in SportGuide
    assert 'href="http://stonepi.test/"' in html  # Dashboard, top of the switcher
    nav = html[html.find('<nav class="nav"'):]
    nav = nav[: nav.find("</nav>")]
    assert [label for label in ("Home", "Now", "Sources", "Settings") if f"<span>{label}</span>" in nav] == [
        "Now", "Settings",
    ]


def test_settings_hub_opens_on_sources_and_sources_is_under_settings(client):
    html = client.get("/settings").text
    assert 0 < html.find('href="/sources"') < html.find('href="/settings?tab=general"')
    sources = client.get("/sources").text
    assert 'class="settings-back" href="/settings"' in sources
    nav = sources[sources.find('<nav class="nav"'):]
    assert 'href="/settings" class="is-active"' in nav[: nav.find("</nav>")]


def test_standalone_has_no_bell_or_card(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    app = FastAPI()
    app.include_router(routes.router)
    html = TestClient(app).get("/settings?tab=notifications").text
    assert "Manage personal alerts" not in html
    assert "sp-bell" not in html
    assert 'name="notify_approaching_minutes"' in html
