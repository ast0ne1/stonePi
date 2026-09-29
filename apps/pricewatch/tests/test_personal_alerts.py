"""PriceWatch personal alerts: strike events carry the owner; bell + Settings card."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes
from app.services import ntfy
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
WATCH = {"id": 3, "product_name": "Headphones", "target_price": 900, "currency": "DKK", "user_key": OWNER}
OFFER = {"product_price": 850, "retailer": "Shop", "product_url": "https://shop.example/p"}


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


def test_strike_is_personal_to_watch_owner(sent):
    assert ntfy.notify_strike(WATCH, OFFER) is True
    event = sent[0]
    assert event["id"] == "pricewatch.target_reached"
    assert event["audience"] == "personal"
    assert event["user"] == OWNER


def test_price_drop_is_personal(sent):
    ntfy.notify_strike(WATCH, OFFER, kind="price_drop")
    assert sent[0]["id"] == "pricewatch.price_drop"
    assert sent[0]["user"] == OWNER


@pytest.mark.parametrize("owner", ["local", "", None, "12"])
def test_strike_without_platform_owner_is_not_sent(sent, owner):
    assert ntfy.notify_strike({**WATCH, "user_key": owner}, OFFER) is False
    assert sent == []


# -- pages -----------------------------------------------------------------------

SECRET = "pw-test-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import db
    from app import config as cfg

    for mod in (db, cfg):
        monkeypatch.setattr(mod, "DB_PATH", tmp_path / "t.sqlite", raising=False)
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path, raising=False)
    db.init_db() if hasattr(db, "init_db") else None
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
            apps=["pricewatch"], session_id="s", phone_alerts=True,
        ),
    )
    return c


def test_settings_notifications_tab_uses_shared_card(client):
    html = client.get("/settings?tab=notifications").text
    assert "Manage personal alerts" in html
    assert "Significant price drop" in html
    assert "alerts#choose" in html  # admin extras


def test_top_bar_has_bell_before_sign_out(client):
    html = client.get("/settings?tab=general").text
    bell = html.find('class="icon-btn alerts-bell')
    sign_out = html.find('class="icon-btn sign-out"')
    assert 0 < bell < sign_out
    assert "alerts-bell-dot" in html
