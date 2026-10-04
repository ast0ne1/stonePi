"""Members vs household admins: what a NewsCast member may and may not do.

Router-level access only needs a NewsCast session (require_user); anything
household-wide (catalog packages, categories, global settings, refreshing every
source, other people's feeds) is admin work. Background refresh asks Auth's
roster before fetching a member's custom sources.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
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
from app.services import ingest, passwords, settings
from app.services import user_settings as user_settings_service
from app.services import users as users_service
from stonepi_auth.roster import reset_rosters
from stonepi_auth.session import COOKIE_NAME, encode_session

SECRET = "nc-test-secret"
ALICE = "a11ce000-0000-4000-8000-000000000001"  # admin
BOB = "b0b00000-0000-4000-8000-000000000002"  # member, no custom sources
CARA = "ca5a0000-0000-4000-8000-000000000003"  # member with custom sources + status


@pytest.fixture
def world(monkeypatch):
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    alice = User(auth_user_id=ALICE, username="alice", password=passwords.hash_password("a"), role="admin")
    bob = User(auth_user_id=BOB, username="bob", password=passwords.hash_password("b"), role="user")
    cara = User(auth_user_id=CARA, username="cara", password=passwords.hash_password("c"), role="user")
    db.add_all([alice, bob, cara])
    db.commit()
    alice_feed = Feed(user_id=alice.id, name="Alice feed", url="https://example.com/a.xml", enabled=True, type="rss")
    bob_feed = Feed(
        user_id=bob.id, catalog_id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/",
        rss_url="https://techcrunch.com/feed/", enabled=True, type="rss",
    )
    db.add_all([alice_feed, bob_feed])
    db.commit()
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    monkeypatch.setattr(env, "stonepi_session_secret", SECRET)
    monkeypatch.setattr(ui, "bell_context", lambda user, **kw: {"show": False, "dot": False, "url": ""})
    started: list = []
    monkeypatch.setattr(ui, "start_ingest", lambda **kw: started.append(kw) or {"ok": True, "message": "started"})
    monkeypatch.setattr(stories_router, "start_ingest", lambda **kw: started.append(kw) or {"ok": True})
    monkeypatch.setattr("app.services.favicon.capture_for_feed_async", lambda *_a, **_k: None)
    app = FastAPI()
    app.include_router(ui.router)
    app.include_router(stories_router.router)
    app.include_router(feeds_router.router)

    def override():
        yield db

    app.dependency_overrides[get_db] = override

    def client_for(auth_id: str, username: str, is_admin: bool, perms: dict | None = None) -> TestClient:
        client = TestClient(app, follow_redirects=False, headers={"Accept": "application/json"})
        client.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=SECRET, user_id=auth_id, username=username, display_name=username, is_admin=is_admin,
                apps=["newscast"], session_id=username, permissions={"newscast": perms or {}},
            ),
        )
        return client

    yield {
        "db": db, "started": started,
        "alice_id": alice.id, "bob_id": bob.id, "cara_id": cara.id,
        "alice_feed": alice_feed.id, "bob_feed": bob_feed.id,
        "alice": client_for(ALICE, "alice", True),
        "bob": client_for(BOB, "bob", False),
        "cara": client_for(CARA, "cara", False, {"can_add_custom_sources": True, "can_view_status": True}),
    }
    ingest.state.running = False
    ingest.state.manual = False
    ingest.state.started_by = None
    ingest.state.last_feed_stats = []


# -- auth dependencies + role mirroring ---------------------------------------------


def test_require_admin_rejects_members():
    from fastapi import HTTPException

    member = SimpleNamespace(state=SimpleNamespace(newscast_session=auth.SessionUser("bob", 2, "user")), cookies={})
    admin = SimpleNamespace(state=SimpleNamespace(newscast_session=auth.SessionUser("alice", 1, "admin")), cookies={})
    with pytest.raises(HTTPException) as exc:
        auth.require_admin(member, None)
    assert exc.value.status_code == 403
    assert auth.require_admin(admin, None).username == "alice"
    assert auth.require_user(member, None).username == "bob"


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_local_admin_role_follows_the_platform():
    db = _session()
    platform = SimpleNamespace(user_id="u-1", username="adam", display_name="", is_admin=True, permissions={})
    user = users_service.get_or_create_from_platform(db, platform)
    assert user.role == "admin" and user.can_add_custom_sources

    platform.is_admin = False  # demoted in StonePi
    user = users_service.get_or_create_from_platform(db, platform)
    assert user.role == "user"
    assert user.can_add_custom_sources is False and user.can_view_status is False


def test_linked_local_admin_is_demoted_when_platform_says_member():
    db = _session()
    db.add(User(username="adam", password="", role="admin", can_add_custom_sources=True))
    db.commit()
    platform = SimpleNamespace(user_id="u-2", username="adam", display_name="", is_admin=False, permissions={})
    user = users_service.get_or_create_from_platform(db, platform)
    assert user.auth_user_id == "u-2" and user.role == "user"


def test_platform_flags_read_through_has_capability():
    db = _session()
    calls: list = []

    def has_capability(app_id, cap):
        calls.append((app_id, cap))
        return cap == "can_view_status"

    platform = SimpleNamespace(
        user_id="u-3", username="mia", display_name="", is_admin=False, permissions={}, has_capability=has_capability,
    )
    user = users_service.get_or_create_from_platform(db, platform)
    assert user.can_view_status is True and user.can_add_custom_sources is False
    assert ("newscast", "can_add_custom_sources") in calls


def test_solo_admin_stays_admin():
    db = _session()
    admin = users_service.ensure_admin_user(db)
    assert admin.role == "admin"
    assert auth.resolve_login_identity(db, admin.username).role == "admin"


# -- JSON feeds API --------------------------------------------------------------


def test_feed_list_is_scoped_to_the_caller(world):
    bob_names = [feed["name"] for feed in world["bob"].get("/api/feeds").json()["feeds"]]
    assert bob_names == ["TechCrunch"]
    alice_names = {feed["name"] for feed in world["alice"].get("/api/feeds").json()["feeds"]}
    assert alice_names == {"Alice feed", "TechCrunch"}


def test_members_cannot_patch_or_delete_other_feeds(world):
    db = world["db"]
    response = world["bob"].patch(f"/api/feeds/{world['alice_feed']}", json={"name": "Hijacked"})
    assert response.status_code == 404
    assert world["bob"].delete(f"/api/feeds/{world['alice_feed']}").status_code == 404
    db.expire_all()
    assert db.get(Feed, world["alice_feed"]).name == "Alice feed"
    # Admins may act on any household feed.
    assert world["alice"].patch(f"/api/feeds/{world['bob_feed']}", json={"name": "TC"}).status_code == 200


def test_member_without_custom_sources_cannot_repoint_a_catalog_feed(world):
    bob, db = world["bob"], world["db"]
    response = bob.patch(f"/api/feeds/{world['bob_feed']}", json={"url": "http://evil.example/feed.xml"})
    assert response.status_code == 403
    response = bob.patch(f"/api/feeds/{world['bob_feed']}", json={"rss_url": "http://evil.example/feed.xml"})
    assert response.status_code == 403
    db.expire_all()
    assert db.get(Feed, world["bob_feed"]).url == "https://techcrunch.com/feed/"
    # Same URL, or other fields, are fine.
    assert bob.patch(f"/api/feeds/{world['bob_feed']}", json={"url": "https://techcrunch.com/feed/"}).status_code == 200
    assert bob.patch(f"/api/feeds/{world['bob_feed']}", json={"enabled": False}).status_code == 200


def test_member_with_custom_sources_may_repoint_own_feed(world):
    db = world["db"]
    feed = Feed(user_id=world["cara_id"], name="Cara", url="https://example.org/a.xml", type="rss", enabled=True)
    db.add(feed)
    db.commit()
    response = world["cara"].patch(f"/api/feeds/{feed.id}", json={"url": "https://example.org/b.xml"})
    assert response.status_code == 200, response.text


def test_schedule_form_blocks_url_change_without_custom_sources(world):
    bob, db = world["bob"], world["db"]
    form = {"feed_type": "rss", "rss_url": "http://evil.example/feed.xml", "homepage_url": ""}
    response = bob.post(f"/feeds/{world['bob_feed']}/schedule", data=form)
    assert response.status_code == 403
    db.expire_all()
    assert db.get(Feed, world["bob_feed"]).rss_url == "https://techcrunch.com/feed/"
    keep = {"feed_type": "rss", "rss_url": "https://techcrunch.com/feed/", "keyword_include": "ai"}
    assert bob.post(f"/feeds/{world['bob_feed']}/schedule", data=keep).status_code == 200


def test_recommended_delete_handles_duplicate_rows_and_only_the_callers(world):
    db = world["db"]
    # Bob has the catalog-id row plus a legacy row on the catalog URL (both match);
    # Alice has her own subscription.
    db.add(Feed(user_id=world["bob_id"], name="TC legacy", url="https://techcrunch.com/", type="webpage"))
    db.add(Feed(user_id=world["alice_id"], catalog_id="techcrunch", name="TC", url="https://techcrunch.com/feed/"))
    db.commit()
    response = world["bob"].delete("/api/feeds/recommended/techcrunch")
    assert response.status_code == 200 and response.json()["removed"] is True
    assert db.query(Feed).filter(Feed.user_id == world["bob_id"]).count() == 0
    assert db.query(Feed).filter(Feed.user_id == world["alice_id"], Feed.catalog_id == "techcrunch").count() == 1


# -- admin-only household routes --------------------------------------------------


def test_household_routes_are_admin_only(world, monkeypatch):
    bob = world["bob"]
    package = json.dumps({"id": "x", "name": "X", "sources": []}).encode()
    assert bob.post(
        "/catalog/packages/import", files={"file": ("p.json", io.BytesIO(package), "application/json")}
    ).status_code == 403
    assert bob.get("/catalog/packages/export?category=news").status_code == 403
    assert bob.post("/settings/categories", data={"label": "Sport"}).status_code == 403
    assert bob.post("/settings/categories/news/rename", data={"label": "Nope"}).status_code == 403
    assert bob.post("/settings/categories/news/delete").status_code == 403
    probed: list = []
    monkeypatch.setattr("app.services.summarize.list_ollama_models", lambda url: probed.append(url) or [])
    assert bob.get("/api/ollama/models?base_url=http://169.254.169.254/").status_code == 403
    assert probed == []
    assert world["alice"].get("/api/ollama/models?base_url=http://127.0.0.1:11434").status_code == 200
    assert world["alice"].post("/settings/categories", data={"label": "Sport"}).status_code in {200, 303}


# -- settings -----------------------------------------------------------------------


def test_member_settings_never_touch_household_keys(world):
    db = world["db"]
    settings.set_value(db, "keyword_include", "climate")
    settings.set_value(db, "briefing_limit", "30")
    settings.set_value(db, "translate_target_lang", "da")
    settings.set_value(db, "epub_x3_screen", "1")
    form = {
        "settings_tab": "publication",
        "keyword_include": "",
        "keyword_exclude": "everything",
        "briefing_limit": "10",
        "translate_target_lang": "en",
        "publication_include_saved": "1",
    }
    response = world["bob"].post("/settings", data=form)
    assert response.status_code == 200, response.text
    db.expire_all()
    assert settings.get_value(db, "keyword_include") == "climate"
    assert settings.get_value(db, "keyword_exclude") == ""
    assert settings.get_value(db, "briefing_limit") == "30"
    assert settings.get_value(db, "translate_target_lang") == "da"
    assert settings.get_value(db, "epub_x3_screen") == "1"
    assert user_settings_service.get_value(db, world["bob_id"], "publication_include_saved") == "1"
    # Household-only tabs fall back to the member's own General tab: nothing global is written.
    for tab in ("filters", "translation"):
        assert world["bob"].post("/settings", data={**form, "settings_tab": tab}).status_code == 200
    db.expire_all()
    assert settings.get_value(db, "keyword_include") == "climate"


def test_member_publication_tab_hides_household_fields(world):
    html = world["bob"].get("/settings?tab=publication", headers={"Accept": "text/html"}).text
    assert 'name="publication_include_saved"' in html
    for field in ("briefing_limit", "epub_x3_screen", "keyword_include", "translate_target_lang", "category_opds_"):
        assert f'name="{field}' not in html


def test_admin_save_only_writes_the_submitted_tab(world):
    db = world["db"]
    settings.set_value(db, "keyword_include", "climate")
    settings.set_value(db, "ollama_model", "llama3.2")
    settings.set_value(db, "instance_name", "Home")
    settings.set_value(db, "briefing_category_mix", "1")
    settings.set_value(db, "x3_device_id", "dev-1")
    alice = world["alice"]
    response = alice.post("/settings", data={"settings_tab": "reader", "reader_device": "xteink"})
    assert response.status_code == 200, response.text
    response = alice.post("/settings", data={"settings_tab": "device"})
    assert response.status_code == 200, response.text
    db.expire_all()
    assert settings.get_value(db, "keyword_include") == "climate"
    assert settings.get_value(db, "ollama_model") == "llama3.2"
    assert settings.get_value(db, "instance_name") == "Home"
    assert settings.get_value(db, "briefing_category_mix") == "1"
    assert settings.get_value(db, "x3_device_id") == "dev-1"
    response = alice.post("/settings", data={"settings_tab": "filters", "keyword_include": "energy"})
    assert response.status_code == 200
    db.expire_all()
    assert settings.get_value(db, "keyword_include") == "energy"


# -- refresh + status ------------------------------------------------------------------


def test_member_refresh_is_limited_to_own_sources(world):
    started = world["started"]
    world["bob"].post("/ingest")
    world["bob"].post("/api/ingest")
    assert started[0]["user_id"] == world["bob_id"] and started[0]["started_by"] == world["bob_id"]
    assert started[1]["user_id"] == world["bob_id"]
    started.clear()
    world["alice"].post("/ingest")
    world["alice"].post("/api/ingest")
    assert "user_id" not in started[0] and "user_id" not in started[1]


def test_members_only_stop_their_own_refresh(world):
    ingest.state.running = True
    ingest.state.manual = True
    ingest.state.started_by = world["alice_id"]
    assert world["bob"].post("/ingest/stop").json()["ok"] is False
    assert ingest.state.stop_requested is False
    assert world["bob"].get("/api/activity").json()["ingest"]["stoppable"] is False
    assert world["alice"].get("/api/activity").json()["ingest"]["stoppable"] is True
    ingest.state.started_by = world["bob_id"]
    assert world["bob"].post("/ingest/stop").json()["ok"] is True
    ingest.state.stop_requested = False
    ingest.state.started_by = world["cara_id"]
    assert world["alice"].post("/ingest/stop").json()["ok"] is True  # admins stop any manual run
    ingest.state.stop_requested = False


def test_ingest_status_shows_members_only_their_feeds(world):
    ingest.state.last_feed_stats = [
        {"feed_id": world["alice_feed"], "name": "Alice feed"},
        {"feed_id": world["bob_feed"], "name": "TechCrunch"},
    ]
    bob_rows = world["bob"].get("/api/ingest/status").json()["last_feed_stats"]
    assert [row["name"] for row in bob_rows] == ["TechCrunch"]
    assert len(world["cara"].get("/api/ingest/status").json()["last_feed_stats"]) == 2  # can_view_status
    assert len(world["alice"].get("/api/ingest/status").json()["last_feed_stats"]) == 2


def test_request_stop_owner_rules():
    ingest.state.running = True
    ingest.state.manual = True
    ingest.state.started_by = 5
    try:
        assert ingest.request_stop(6, is_admin=False)["ok"] is False
        assert ingest.request_stop(None, is_admin=False)["ok"] is False
        assert ingest.request_stop(5, is_admin=False)["ok"] is True
    finally:
        ingest.state.running = False
        ingest.state.manual = False
        ingest.state.started_by = None
        ingest.state.stop_requested = False


# -- background refresh vs Auth's roster -----------------------------------------------


@pytest.fixture
def roster_db(monkeypatch):
    reset_rosters()
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    monkeypatch.setattr("app.services.briefing.maybe_publish_daily_briefing", lambda _db: None)
    db = _session()
    keep = User(auth_user_id="keep", username="keep", password="", role="user")
    gone = User(auth_user_id="gone", username="gone", password="", role="user")
    nocap = User(auth_user_id="nocap", username="nocap", password="", role="user")
    db.add_all([keep, gone, nocap])
    db.commit()
    feeds = {
        "keep custom": Feed(user_id=keep.id, name="keep custom", url="https://k.example/f.xml", type="rss"),
        "gone custom": Feed(user_id=gone.id, name="gone custom", url="https://g.example/f.xml", type="rss"),
        "nocap custom": Feed(user_id=nocap.id, name="nocap custom", url="https://n.example/f.xml", type="rss"),
        "nocap catalog": Feed(
            user_id=nocap.id, catalog_id="techcrunch", name="nocap catalog", url="https://t.example/f.xml", type="rss",
        ),
    }
    for feed in feeds.values():
        feed.enabled = True
        feed.summarize = False
    db.add_all(feeds.values())
    db.commit()
    yield db
    reset_rosters()
    ingest.state.running = False


ROSTER = {
    "people": [
        {"id": "keep", "is_admin": False, "permissions": {"newscast": {"can_add_custom_sources": True}}},
        {"id": "nocap", "is_admin": False, "permissions": {"newscast": {"can_add_custom_sources": False}}},
        # "gone" is missing: disabled or deleted in Auth.
    ]
}


def _fetched(monkeypatch, db) -> list[str]:
    fetched: list[str] = []

    def fake_collect(feed, **_kw):
        fetched.append(feed.name)
        return []

    monkeypatch.setattr("app.services.ingest._collect_feed_items", fake_collect)
    ingest.run_ingest(db, force=True)
    return sorted(fetched)


def test_background_refresh_skips_revoked_custom_feeds(roster_db, monkeypatch):
    monkeypatch.setattr("stonepi_auth.internal.get_internal_json", lambda *a, **k: ROSTER)
    assert _fetched(monkeypatch, roster_db) == ["keep custom", "nocap catalog"]


def test_background_refresh_keeps_fetching_when_roster_unknown(roster_db, monkeypatch):
    monkeypatch.setattr("stonepi_auth.internal.get_internal_json", lambda *a, **k: None)  # Auth down
    assert _fetched(monkeypatch, roster_db) == ["gone custom", "keep custom", "nocap catalog", "nocap custom"]


def test_member_refresh_only_fetches_own_feeds(roster_db, monkeypatch):
    monkeypatch.setattr("stonepi_auth.internal.get_internal_json", lambda *a, **k: ROSTER)
    fetched: list[str] = []
    monkeypatch.setattr(
        "app.services.ingest._collect_feed_items", lambda feed, **_kw: fetched.append(feed.name) or []
    )
    keep = roster_db.query(User).filter(User.username == "keep").one()
    ingest.run_ingest(roster_db, force=True, manual=True, user_id=keep.id, started_by=keep.id)
    assert fetched == ["keep custom"]
