"""FileServe personal alerts: publication event is household; bell + Settings card."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main
from app.services import pages as pages_svc
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SECRET = "fs-test-secret"


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


def test_publication_created_is_household(sent):
    admin = SimpleNamespace(is_admin=True)
    page = SimpleNamespace(id=4, slug="trip", title="Trip plan", owner=admin, public_path="/trip")
    assert pages_svc.emit_publication_created(page) is True
    event = sent[0]
    assert event["id"] == "fileserve.publication_created"
    assert event["audience"] == "household"
    assert event["url"] == "/files/trip"
    assert not event.get("user")


def test_member_page_is_not_household_news(sent):
    member = SimpleNamespace(is_admin=False)
    page = SimpleNamespace(id=5, slug="notes", title="Notes", owner=member, public_path="/u/sam/notes")
    assert pages_svc.emit_publication_created(page) is False
    assert sent == []


# -- pages -----------------------------------------------------------------------


def _client(tmp_path, monkeypatch, *, secret: str):
    hosted = tmp_path / "hosted"
    hosted.mkdir()
    monkeypatch.setattr("app.services.pages.HOSTED_DIR", hosted)
    monkeypatch.setattr("app.services.users.HOSTED_DIR", hosted)
    monkeypatch.setattr("app.auth.env.stonepi_session_secret", secret)
    monkeypatch.setattr("app.main.env.stonepi_session_secret", secret)
    monkeypatch.setattr("app.auth.env.stonepi_prefix", "")
    monkeypatch.setattr("app.main.env.stonepi_prefix", "")
    monkeypatch.setattr("app.auth._platform_session_secret", lambda: secret)
    from stonepi_auth.header import header_context

    monkeypatch.setattr(
        main,
        "bell_context",
        lambda user, **kw: {
            "show": bool(user) and kw.get("enabled", True), "dot": True, "url": "/notifications", "panel": None,
            "header": header_context(kw["app_id"], user, home_url="http://stonepi.test"),
        },
    )
    flask_app = main.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "DATABASE_URL": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        }
    )
    return flask_app.test_client()


@pytest.fixture
def client(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, secret=SECRET)
    c.set_cookie(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=OWNER, username="jo", display_name="Jo", is_admin=True,
            apps=["fileserve"], session_id="s", phone_alerts=True,
        ),
    )
    return c


def test_settings_notifications_tab_uses_shared_card(client):
    html = client.get("/admin/settings?tab=notifications").get_data(as_text=True)
    assert "Manage personal alerts" in html
    assert "alerts#choose" in html  # admin extras


def test_shared_header_order_and_bottom_bar(client):
    html = client.get("/admin/settings?tab=device").get_data(as_text=True)
    bell = html.find('class="icon-btn sp-bell has-dot"')
    switcher = html.find('aria-label="Switch app"')
    avatar = html.find('class="sp-trigger sp-avatar"')
    assert 0 < bell < switcher < avatar
    assert "Log out" in html and 'class="icon-btn sign-out"' not in html
    assert 'aria-label="Search"' not in html  # FileServe has nothing to search
    assert 'href="http://stonepi.test/"' in html  # Dashboard, top of the switcher
    nav = html[html.find('<nav class="nav"'):]
    nav = nav[: nav.find("</nav>")]
    assert [label for label in ("Home", "Pages", "Add", "Settings") if f"<span>{label}</span>" in nav] == ["Pages", "Settings"]


def test_add_is_a_floating_button_on_pages_only(client):
    assert 'class="sp-fab" href="/admin/add"' in client.get("/admin").get_data(as_text=True)
    assert 'class="sp-fab"' not in client.get("/admin/settings?tab=device").get_data(as_text=True)
    assert 'class="sp-fab"' not in client.get("/admin/add").get_data(as_text=True)


def test_standalone_has_no_bell_or_card(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, secret="")
    c.post("/login", data={"username": "admin", "password": "admin"})
    html = c.get("/admin/settings?tab=notifications").get_data(as_text=True)
    assert 'class="sp-trigger sp-avatar"' in html and "Log out" in html  # avatar menu carries Log out
    assert 'class="icon-btn sp-bell' not in html
    assert "Manage personal alerts" not in html
