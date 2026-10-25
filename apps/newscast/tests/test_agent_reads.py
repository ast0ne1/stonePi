"""AgentLink reads (Pebble): signed internal endpoint, the user's own Briefing and search."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from stonepi_auth.internal import sign_internal_v2

from app.db import get_db
from app.models import Base, Feed, Story, User
from app.routers import agent

SECRET = "agent-test-secret"
PATH = "/api/internal/agent"
ME, OTHER = str(uuid.uuid4()), str(uuid.uuid4())


def _client(monkeypatch):
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, future=True)
    db: Session = make()
    me = User(username="me", role="user", auth_user_id=ME)
    other = User(username="other", role="user", auth_user_id=OTHER)
    db.add_all([me, other])
    db.commit()
    now = datetime.now(timezone.utc)
    for user, prefix in ((me, "Mine"), (other, "Theirs")):
        db.add(Feed(user_id=user.id, name="BBC World", url=f"https://example.com/{prefix}.rss", enabled=True, type="rss", category="news"))
        for i in range(3):
            db.add(Story(
                user_id=user.id, title=f"{prefix} headline {i} about Mars", summary=f"<p>{prefix} summary {i}.</p>",
                source_name="BBC World", canonical_url=f"https://example.com/{prefix}/{i}",
                published_at=now - timedelta(minutes=10 * (i + 1)), content_hash=f"{prefix}{i}", cluster_key=f"{prefix}{i}", importance=5,
            ))
    db.add(Feed(user_id=me.id, name="Sky Sports", url="https://example.com/sport.rss", enabled=True, type="rss", category="sport"))
    for i in range(2):
        db.add(Story(
            user_id=me.id, title=f"Mine sport {i}: derby result", summary="<p>Goals.</p>", source_name="Sky Sports",
            canonical_url=f"https://example.com/sport/{i}", published_at=now - timedelta(minutes=5 + i),
            content_hash=f"sport{i}", cluster_key=f"sport{i}", importance=5,
        ))
    db.commit()

    def _get_db():
        yield db

    monkeypatch.setattr(agent, "_platform_session_secret", lambda: SECRET)
    app = FastAPI()
    app.include_router(agent.router)
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


def _post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post(PATH, content=body, headers={"Content-Type": "application/json", **sign_internal_v2(secret, "POST", PATH, body)})


def test_unsigned_is_404(monkeypatch):
    client = _client(monkeypatch)
    assert client.post(PATH, json={"tool": "get_news_briefing", "user": ME}).status_code == 404


def test_briefing_is_the_users_own(monkeypatch):
    client = _client(monkeypatch)
    payload = _post(client, {"tool": "get_news_briefing", "user": ME}).json()["payload"]
    titles = [h["title"] for h in payload["headlines"]]
    assert titles and all(t.startswith("Mine") for t in titles)
    first = next(h for h in payload["headlines"] if h["source"] == "BBC World")
    assert first["summary"] == "Mine summary 0." and first["category"] == "World News"
    assert payload["paper_ready"] is False and payload["story_count"] == 5


def test_search_is_the_users_own(monkeypatch):
    client = _client(monkeypatch)
    payload = _post(client, {"tool": "search_news", "user": ME, "params": {"query": "Mars"}}).json()["payload"]
    assert payload["total"] == 3 and all(s["title"].startswith("Mine") for s in payload["stories"])


def test_unknown_user_and_tool(monkeypatch):
    client = _client(monkeypatch)
    assert _post(client, {"tool": "get_news_briefing", "user": str(uuid.uuid4())}).json()["payload"]["headlines"] == []
    assert _post(client, {"tool": "add_feed", "user": ME}).status_code == 400


def test_briefing_by_category(monkeypatch):
    client = _client(monkeypatch)
    payload = _post(client, {"tool": "get_news_briefing", "user": ME}).json()["payload"]
    assert sorted((c["name"], c["stories"]) for c in payload["categories"]) == [("Sport", 2), ("World News", 3)]
    for asked in ("sport", "Sports", "SPORT"):
        sport = _post(client, {"tool": "get_news_briefing", "user": ME, "params": {"category": asked}}).json()["payload"]
        assert sport["category"] == "Sport" and sport["category_story_count"] == 2
        assert [h["source"] for h in sport["headlines"]] == ["Sky Sports", "Sky Sports"]
    world = _post(client, {"tool": "get_news_briefing", "user": ME, "params": {"category": "world"}}).json()["payload"]
    assert world["category"] == "World News" and world["category_story_count"] == 3
    missing = _post(client, {"tool": "get_news_briefing", "user": ME, "params": {"category": "fashion"}}).json()["payload"]
    assert missing["category_not_found"] == "fashion" and len(missing["headlines"]) == 5


def test_search_by_category(monkeypatch):
    client = _client(monkeypatch)
    payload = _post(client, {"tool": "search_news", "user": ME, "params": {"query": "derby", "category": "sport"}}).json()["payload"]
    assert payload["total"] == 2 and payload["category"] == "Sport"
    payload = _post(client, {"tool": "search_news", "user": ME, "params": {"query": "Mars", "category": "sport"}}).json()["payload"]
    assert payload["total"] == 0
