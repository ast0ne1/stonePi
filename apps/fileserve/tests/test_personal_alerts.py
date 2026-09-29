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
    page = SimpleNamespace(id=4, slug="trip", title="Trip plan")
    assert pages_svc.emit_publication_created(page) is True
    event = sent[0]
    assert event["id"] == "fileserve.publication_created"
    assert event["audience"] == "household"
    assert not event.get("user")


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
    monkeypatch.setattr(
        main, "bell_context", lambda user, **kw: {"show": bool(user) and kw.get("enabled", True), "dot": True, "url": "/notifications"}
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


def test_top_bar_has_bell_before_sign_out(client):
    html = client.get("/admin/settings?tab=device").get_data(as_text=True)
    bell = html.find('class="icon-btn alerts-bell')
    sign_out = html.find('class="icon-btn sign-out"')
    assert 0 < bell < sign_out
    assert "alerts-bell-dot" in html


def test_standalone_has_no_bell_or_card(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, secret="")
    c.post("/login", data={"username": "admin", "password": "admin"})
    html = c.get("/admin/settings?tab=notifications").get_data(as_text=True)
    assert "icon-btn sign-out" in html
    assert "alerts-bell" not in html
    assert "Manage personal alerts" not in html
