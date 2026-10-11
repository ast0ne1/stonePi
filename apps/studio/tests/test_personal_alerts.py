"""Studio personal alerts: publish event is household; bell + Settings card."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes
from app.services import notify as notify_service
from stonepi_auth.header import header_context
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
        routes,
        "bell_context",
        lambda user, **kw: {
            "show": bool(user) and kw.get("enabled", True),
            "dot": True,
            "url": "/notifications",
            "panel": None,
            "header": header_context(kw["app_id"], user, home_url="http://stonepi.test"),
        },
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


def test_shared_header_order_and_bottom_bar(client):
    html = client.get("/settings?tab=about").text
    bell = html.find('class="icon-btn sp-bell has-dot"')
    switcher = html.find('aria-label="Switch app"')
    avatar = html.find('class="sp-trigger sp-avatar"')
    assert 0 < bell < switcher < avatar
    assert "Log out" in html
    assert 'name="csrf_token"' in html[avatar:]
    assert "sign-out" not in html and "alerts-bell" not in html
    nav = html[html.index('<nav class="nav"'):]
    nav = nav[: nav.index("</nav>")]
    assert re.findall(r"<span>([^<]+)</span>", nav) == ["Projects", "Settings"]


def test_first_visit_lands_on_create_then_projects_once_you_have_one(client):
    from app import store

    first = client.get("/", follow_redirects=False)
    assert first.status_code == 200 and "What shall we make today?" in first.text
    assert "&larr; Projects" not in first.text  # nothing to go back to yet
    nav = first.text[first.text.index('<nav class="nav"'):]
    assert 'class="is-active"' in nav[: nav.index("<span>Projects</span>")]  # Create counts as Projects

    store.create_project("Dragon game", kind="game", owner=OWNER, owner_name="Jo")
    back = client.get("/", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"].endswith("/projects")
    created = client.get("/new").text
    assert "What shall we make today?" in created and 'href="/projects"' in created


def test_create_is_a_floating_button_on_projects_only(client):
    projects = client.get("/projects").text
    assert 'class="sp-fab" href="/new"' in projects
    assert 'aria-label="Create"' in projects
    assert "project-new" not in projects  # the "Start something new" tile is gone: one create control
    assert 'class="sp-fab"' not in client.get("/settings?tab=about").text
    assert 'class="sp-fab"' not in client.get("/new").text


def test_standalone_has_no_bell_or_card(monkeypatch):
    html = _client(monkeypatch, "").get("/settings?tab=notifications").text
    assert "sp-bell" not in html
    assert "Manage personal alerts" not in html
