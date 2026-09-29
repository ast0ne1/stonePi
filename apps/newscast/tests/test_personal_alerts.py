"""NewsCast personal alerts: paper events carry the owner's Auth id; bell + Settings card."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import env
from app.db import get_db
from app.models import Base, User
from app.routers import ui
from app.services import ntfy, passwords
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _user(db: Session, auth_user_id: str | None = OWNER, **kw) -> User:
    fields = {"username": "jo", "password": passwords.hash_password("jjjj"), "role": "admin", "can_use_ntfy": True}
    fields.update(kw)
    user = User(auth_user_id=auth_user_id, **fields)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events: list[dict] = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


@pytest.mark.parametrize(
    ("kind", "event_id"),
    [("publish", "newscast.publication_available"), ("push", "newscast.push_available")],
)
def test_paper_alerts_are_personal_to_owner(sent, kind, event_id):
    db = _session()
    user = _user(db)
    assert ntfy.notify(db, kind=kind, title="Home", body="Paper", user_id=user.id) is True
    event = sent[0]
    assert event["id"] == event_id
    assert event["audience"] == "personal"
    assert event["user"] == OWNER
    assert "user_id" not in (event.get("data") or {})
    assert str(user.id) not in event["dedupe_key"].split(":")


@pytest.mark.parametrize("auth_id", [None, "", "local", "12"])
def test_owner_without_auth_id_is_not_sent(sent, auth_id):
    db = _session()
    user = _user(db, auth_user_id=auth_id or None)
    assert ntfy.notify(db, kind="publish", title="Home", body="Paper", user_id=user.id) is False
    assert sent == []
    # Not marked as notified, so a later linked account still gets today's alert.
    assert ntfy._already_notified(db, "publish", ntfy._today(), user.id) is False


def test_single_user_publish_without_user_id_is_not_sent(sent):
    db = _session()
    assert ntfy.notify(db, kind="publish", title="Home", body="Paper") is False
    assert ntfy.notify(db, kind="publish", title="Home", body="Paper", user_id=999) is False
    assert sent == []


def test_old_local_permission_no_longer_decides(sent):
    """Phone alerts is a platform permission now; Notify drops events for people without it."""
    db = _session()
    user = _user(db, role="user", can_use_ntfy=False)
    assert ntfy.notify(db, kind="publish", title="Home", body="Paper", user_id=user.id) is True
    assert sent[0]["audience"] == "personal"


# -- pages -----------------------------------------------------------------------

SECRET = "nc-test-secret"


@pytest.fixture
def client(monkeypatch):
    db = _session()
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    monkeypatch.setattr(env, "stonepi_session_secret", SECRET)
    monkeypatch.setattr(
        ui, "bell_context", lambda user, **kw: {"show": bool(user), "dot": True, "url": "/notifications"}
    )
    app = FastAPI()
    app.include_router(ui.router)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=OWNER, username="jo", display_name="Jo", is_admin=True,
            apps=["newscast"], session_id="s", phone_alerts=True,
        ),
    )
    return c


def test_settings_notifications_tab_uses_shared_card(client):
    res = client.get("/settings?tab=notifications")
    assert res.status_code == 200
    html = res.text
    assert "Manage personal alerts" in html
    assert "alerts#choose" in html  # admin extras from the shared card
    assert "Household event prefs" not in html  # old duplicate admin links are gone
    assert 'name="ntfy_notify_on_publish"' not in html  # per-person choices live on the Notifications page


def test_top_bar_has_bell_before_sign_out(client):
    html = client.get("/settings?tab=device").text
    bell = html.find('class="icon-btn alerts-bell')
    sign_out = html.find('class="icon-btn sign-out"')
    assert 0 < bell < sign_out
    assert "alerts-bell-dot" in html
