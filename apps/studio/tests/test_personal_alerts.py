"""Studio personal alerts: publish event is household; bell + Settings card."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes
from app.services import notify as notify_service
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SECRET = "studio-test-secret"


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


def test_site_published_is_household(sent):
    assert notify_service.emit_site_published(title="Star Snake", kind="game", project_id="abc") is True
    event = sent[0]
    assert event["id"] == "studio.site_published"
    assert event["audience"] == "household"
    assert not event.get("user")


# -- pages -----------------------------------------------------------------------


def _client(monkeypatch, secret: str) -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: secret)
    monkeypatch.setattr(
        routes, "bell_context", lambda user, **kw: {"show": bool(user) and kw.get("enabled", True), "dot": True, "url": "/notifications"}
    )
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    c = _client(monkeypatch, SECRET)
    c.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=OWNER, username="jo", display_name="Jo", is_admin=True,
            apps=["studio"], session_id="s", phone_alerts=True,
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


def test_standalone_has_no_bell_or_card(monkeypatch):
    html = _client(monkeypatch, "").get("/settings?tab=notifications").text
    assert "alerts-bell" not in html
    assert "Manage personal alerts" not in html
