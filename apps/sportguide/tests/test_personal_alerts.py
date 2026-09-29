"""SportGuide personal alerts: bell + Settings card (per-person alerts: test_per_person.py)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes
from app.services import notify as notify_service
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
        routes, "bell_context", lambda user, **kw: {"show": bool(user), "dot": True, "url": "/notifications"}
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


def test_top_bar_has_bell_before_sign_out(client):
    html = client.get("/settings?tab=general").text
    bell = html.find('class="icon-btn alerts-bell')
    sign_out = html.find('class="icon-btn sign-out"')
    assert 0 < bell < sign_out
    assert "alerts-bell-dot" in html


def test_standalone_has_no_bell_or_card(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    app = FastAPI()
    app.include_router(routes.router)
    html = TestClient(app).get("/settings?tab=notifications").text
    assert "Manage personal alerts" not in html
    assert "alerts-bell" not in html
    assert 'name="notify_approaching_minutes"' in html
