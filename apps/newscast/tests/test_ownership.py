"""Per-item actions only ever touch the signed-in account's own stories, sources, and files.

Another household member's item is treated exactly like a missing one (404), and the
guard test fails if a new "act on item N" route is added without that check.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import env
from app.db import get_db
from app.models import Base, Feed, LibraryFile, Story, SyncTask, User
from app.routers import stories as stories_router
from app.routers import ui
from app.services import library, passwords
from app.services.saved import save_article
from stonepi_auth.session import COOKIE_NAME, encode_session

SECRET = "nc-test-secret"
ALICE = "a11ce000-0000-4000-8000-000000000001"
BOB = "b0b00000-0000-4000-8000-000000000002"

# Routes that take an id but are guarded another way (admin role, per-user query, or not a row id).
EXEMPT = {
    "/reader/queue/{task_id}/cancel",  # cancel_pending filters by the signed-in user
    "/catalog/{catalog_id}/add",  # catalog key, creates the caller's own feed
    "/catalog/{catalog_id}/remove",  # removes the caller's own feed by catalog key
    "/settings/users/{user_id}",  # admin only
    "/settings/users/{user_id}/permissions",  # admin only
    "/settings/users/{user_id}/delete",  # admin only
    "/settings/users/{user_id}/qr",  # admin only
    "/settings/categories/{key}/rename",  # household-wide categories
    "/settings/categories/{key}/delete",  # household-wide categories
}


@pytest.fixture
def world(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    alice = User(auth_user_id=ALICE, username="alice", password=passwords.hash_password("a"), role="admin")
    bob = User(auth_user_id=BOB, username="bob", password=passwords.hash_password("b"), role="user")
    db.add_all([alice, bob])
    db.commit()
    feed = Feed(user_id=alice.id, name="Alice feed", url="https://example.com/a.xml", enabled=True)
    story = Story(
        user_id=alice.id, title="Alice story", summary="s", source_name="Alice feed",
        canonical_url="https://example.com/a", content_hash="a", cluster_key="a",
    )
    saved = Story(
        user_id=alice.id, title="Alice long-read", summary="s", source_name="example.com",
        canonical_url="https://example.com/read", content_hash="r", cluster_key="r", saved=True,
    )
    (tmp_path / str(alice.id)).mkdir()
    (tmp_path / str(alice.id) / "book.epub").write_bytes(b"epub")
    book = LibraryFile(
        user_id=alice.id, title="Alice book", original_name="book.epub",
        stored_name=f"{alice.id}/book.epub", size=4,
    )
    db.add_all([feed, story, saved, book])
    db.commit()
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    monkeypatch.setattr(env, "stonepi_session_secret", SECRET)
    monkeypatch.setattr(ui, "bell_context", lambda user, **kw: {"show": False, "dot": False, "url": ""})
    started: list = []
    monkeypatch.setattr(ui, "start_ingest", lambda **kw: started.append(kw) or {"ok": True, "message": "started"})
    monkeypatch.setattr(stories_router, "start_ingest", lambda **kw: started.append(kw) or {"ok": True})
    app = FastAPI()
    app.include_router(ui.router)
    app.include_router(stories_router.router)

    def override():
        yield db

    app.dependency_overrides[get_db] = override

    def client_for(auth_id: str, username: str, is_admin: bool) -> TestClient:
        client = TestClient(app, follow_redirects=False, headers={"Accept": "application/json"})
        client.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=SECRET, user_id=auth_id, username=username, display_name=username, is_admin=is_admin,
                apps=["newscast"], session_id=username,
            ),
        )
        return client

    ids = {"feed_id": feed.id, "story_id": story.id, "file_id": book.id}
    return {
        "db": db, "app": app, "ids": ids, "saved_id": saved.id, "started": started,
        "alice": client_for(ALICE, "alice", True), "bob": client_for(BOB, "bob", False),
    }


def _snapshot(db: Session) -> tuple:
    db.expire_all()
    feed = db.query(Feed).one()
    return (
        feed.enabled, feed.muted_until, feed.schedule_mode, feed.keyword_include,
        tuple((s.id, bool(s.favourited), bool(s.saved)) for s in db.query(Story).order_by(Story.id)),
        db.query(LibraryFile).count(),
        db.query(SyncTask).count(),
    )


ACTIONS = [
    ("post", "/stories/{story_id}/favourite", {}),
    ("post", "/stories/{story_id}/longread", {}),
    ("post", "/saved/{saved_id}/delete", {}),
    ("post", "/library/{file_id}/push", {}),
    ("post", "/library/{file_id}/delete", {}),
    ("post", "/feeds/{feed_id}/schedule", {"schedule_mode": "custom", "keyword_include": "x"}),
    ("post", "/feeds/{feed_id}/mute", {}),
    ("post", "/feeds/{feed_id}/unmute", {}),
    ("post", "/feeds/{feed_id}/refresh", {}),
    ("post", "/feeds/{feed_id}/toggle", {}),
    ("post", "/feeds/{feed_id}/delete", {}),
    ("get", "/api/ingest/debug?feed_id={feed_id}", None),
    ("post", "/api/ingest?feed_id={feed_id}", None),
]


@pytest.mark.parametrize(("method", "path", "form"), ACTIONS)
def test_other_members_items_are_not_found(world, method, path, form):
    db = world["db"]
    before = _snapshot(db)
    url = path.format(**world["ids"], saved_id=world["saved_id"])
    response = getattr(world["bob"], method)(url, **({"data": form} if form is not None else {}))
    assert response.status_code == 404, (url, response.status_code, response.text[:200])
    assert _snapshot(db) == before
    assert world["started"] == []


def test_owner_can_still_act_on_own_items(world):
    alice, db, ids = world["alice"], world["db"], world["ids"]
    assert alice.post(f"/stories/{ids['story_id']}/favourite").json()["favourited"] is True
    assert alice.post(f"/feeds/{ids['feed_id']}/mute").status_code == 200
    assert alice.post(f"/library/{ids['file_id']}/push").status_code == 200
    assert db.query(SyncTask).count() == 1
    assert alice.post(f"/feeds/{ids['feed_id']}/toggle").json()["enabled"] is False


def test_api_stories_is_scoped_to_the_signed_in_account(world):
    titles = [story["title"] for story in world["bob"].get("/api/stories").json()["stories"]]
    assert "Alice story" not in titles


def test_every_item_route_is_covered(world):
    # A new "act on item N" route must either use the ownership check (and be listed in
    # ACTIONS) or be exempted here with a reason.
    covered = {re.sub(r"\?.*$", "", path).replace("{saved_id}", "{story_id}") for _m, path, _f in ACTIONS}
    item_routes = {
        route.path
        for route in world["app"].routes
        if re.search(r"\{[a-z_]+\}", getattr(route, "path", "")) and not route.path.startswith("/static")
    }
    unclassified = item_routes - covered - EXEMPT
    assert not unclassified, f"Add an ownership check + test, or exempt with a reason: {sorted(unclassified)}"


def test_save_article_uses_the_callers_copy(monkeypatch):
    monkeypatch.setattr("app.services.saved._http_get_html", lambda url: "<html><title>T</title></html>")
    monkeypatch.setattr(
        "app.services.saved.trafilatura.extract",
        lambda *_a, **_k: "Enough words to count as a full article for later reading on the e-ink device, twice over.",
    )
    monkeypatch.setattr(
        "app.services.saved.trafilatura.extract_metadata", lambda *_a, **_k: type("M", (), {"title": "T"})()
    )
    monkeypatch.setattr("app.services.favicon.ensure_favicon", lambda *_a, **_k: None)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    for uid in (1, 2):  # both accounts already have this story in their feeds
        db.add(Story(
            user_id=uid, title="Shared", summary="s", source_name="x", canonical_url="https://example.com/shared",
            content_hash=f"h{uid}", cluster_key=f"k{uid}",
        ))
    db.commit()

    saved = save_article(db, "https://example.com/shared", "7", "", user_id=2)

    assert saved.user_id == 2 and saved.saved is True
    first = db.query(Story).filter(Story.user_id == 1).one()
    assert first.saved is not True  # the other account's copy is untouched
    fresh = save_article(db, "https://example.com/new-read", "7", "", user_id=2)
    assert fresh.user_id == 2
