"""Every browser POST checks the CSRF token: form field csrf_token, or X-StonePi-CSRF on background posts."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import env
from app.db import get_db
from app.models import Base, Feed, User
from app.routers import feeds as feeds_router
from app.routers import stories as stories_router
from app.routers import ui
from app.services import passwords
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

OWNER = "5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
SECRET = "nc-test-secret"
TOKEN = "csrf-test-token"
FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(auth_user_id=OWNER, username="adam", password=passwords.hash_password("test"), role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    monkeypatch.setattr(env, "stonepi_session_secret", SECRET)
    monkeypatch.setattr(ui, "bell_context", lambda user, **kw: {"show": False, "dot": False, "url": ""})
    app = FastAPI()
    app.include_router(ui.router)
    auth.install_csrf_handler(app)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=OWNER, username="adam", display_name="Adam", is_admin=True,
            apps=["newscast"], session_id="s",
        ),
    )
    client.cookies.set(CSRF_COOKIE, TOKEN)
    return client, db, user


def _feed(db: Session, user: User) -> Feed:
    feed = Feed(user_id=user.id, name="Example", url="https://example.com/rss", type="rss", category="news")
    db.add(feed)
    db.commit()
    db.refresh(feed)
    return feed


def test_form_post_without_token_is_refused(ctx):
    client, db, user = ctx
    feed = _feed(db, user)
    response = client.post(
        f"/feeds/{feed.id}/delete",
        data={"next": "/sources?tab=feeds"},
        headers={"Referer": "http://testserver/sources?tab=feeds"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/sources?tab=feeds"
    db.expire_all()
    assert db.get(Feed, feed.id) is not None


def test_form_post_with_wrong_token_is_refused(ctx):
    client, db, user = ctx
    feed = _feed(db, user)
    client.post(f"/feeds/{feed.id}/delete", data={"csrf_token": "someone-elses"})
    db.expire_all()
    assert db.get(Feed, feed.id) is not None


def test_form_post_with_token_succeeds(ctx):
    client, db, user = ctx
    feed = _feed(db, user)
    response = client.post(f"/feeds/{feed.id}/delete", data={"csrf_token": TOKEN})
    assert response.status_code == 303
    db.expire_all()
    assert db.get(Feed, feed.id) is None


def test_fetch_post_without_token_gets_ok_false(ctx):
    client, db, user = ctx
    feed = _feed(db, user)
    response = client.post(f"/feeds/{feed.id}/toggle", headers=FETCH)
    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False and body["message"] == auth.CSRF_MESSAGE
    db.expire_all()
    assert db.get(Feed, feed.id).enabled is True


def test_fetch_post_with_header_succeeds(ctx):
    client, db, user = ctx
    feed = _feed(db, user)
    response = client.post(f"/feeds/{feed.id}/toggle", headers={**FETCH, "X-StonePi-CSRF": TOKEN})
    assert response.status_code == 200 and response.json()["ok"] is True
    db.expire_all()
    assert db.get(Feed, feed.id).enabled is False


def test_pages_issue_the_token_their_forms_send(ctx):
    client, _db, _user = ctx
    client.cookies.delete(CSRF_COOKIE)
    response = client.get("/settings?tab=users")
    issued = response.cookies.get(CSRF_COOKIE)
    assert issued
    assert f'name="csrf_token" value="{issued}"' in response.text


def test_every_cookie_signed_in_post_checks_csrf():
    """New POST routes on the browser routers get the check from the router, not by remembering it."""
    for router in (ui.router, feeds_router.router, stories_router.router):
        for route in router.routes:
            if not isinstance(route, APIRoute) or not (route.methods & {"POST", "PUT", "PATCH", "DELETE"}):
                continue
            calls = {dep.call for dep in route.dependant.dependencies}
            assert auth.require_csrf in calls, route.path
