"""PriceScout personal alerts: leaflet events are household; bell + Settings card."""

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


def test_publication_released_is_household(sent):
    assert notify_service.emit_publication_released(
        store_name="Netto", source_id="netto", catalog_id="cat-2", url="https://example/x", offer_count=3
    ) is True
    event = sent[0]
    assert event["id"] == "pricescout.publication_released"
    assert event["audience"] == "household"
    assert not event.get("user")


# -- pages -----------------------------------------------------------------------

SECRET = "ps-test-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ps.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "CACHE_DIR", tmp_path / "cache")
    db.init_db()
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
            apps=["pricescout"], session_id="s", phone_alerts=True,
        ),
    )
    return c


def test_settings_notifications_tab_uses_shared_card(client):
    html = client.get("/settings?tab=notifications").text
    assert "Manage personal alerts" in html
    assert "alerts#choose" in html  # admin extras


def test_top_bar_has_bell_before_sign_out(client):
    html = client.get("/settings?tab=about").text
    bell = html.find('class="icon-btn alerts-bell')
    sign_out = html.find('class="icon-btn sign-out"')
    assert 0 < bell < sign_out
    assert "alerts-bell-dot" in html


def test_standalone_has_no_bell_or_card(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ps.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "CACHE_DIR", tmp_path / "cache")
    db.init_db()
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    app = FastAPI()
    app.include_router(routes.router)
    html = TestClient(app).get("/settings?tab=notifications").text
    assert "Manage personal alerts" not in html
    assert "alerts-bell" not in html
