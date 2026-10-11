"""AI tags only where AI is actually on; hints explain where AI can be used either way."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import env
from app.db import get_db
from app.models import Base, Feed, Story, User
from app.routers import ui
from app.services import passwords, settings
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
SECRET = "nc-test-secret"
AI_TAG = 'class="chip chip-ai"'


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(auth_user_id=OWNER, username="adam", password=passwords.hash_password("test"), role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add_all(
        [
            Feed(user_id=user.id, name="Summary Feed", url="https://a.example/rss", summarize=True),
            Feed(user_id=user.id, name="Full Feed", url="https://b.example/rss", summarize=False),
            Story(
                user_id=user.id,
                title="A story",
                summary="Text",
                source_name="Summary Feed",
                canonical_url="https://a.example/1",
                published_at=datetime.now(timezone.utc),
                content_hash="h1",
                cluster_key="k1",
                importance=5,
                favourited=True,  # favourites skip the category mix, so the card always renders
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    monkeypatch.setattr(env, "stonepi_session_secret", SECRET)
    monkeypatch.setattr(ui, "bell_context", lambda user, **kw: {"show": False, "dot": False, "url": ""})
    app = FastAPI()
    app.include_router(ui.router)

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
    return client, db


def _ai_on(db: Session) -> None:
    settings.set_value(db, "llm_provider", "ollama")
    settings.set_value(db, "ollama_model", "llama3.2")
    db.commit()


def _feed_row(html: str, name: str) -> str:
    start = html.index(f'<h3 class="feed-row-title">{name}</h3>')
    return html[start : html.index("</summary>", start)]


def _visible_tags(fragment: str) -> list[str]:
    """AI tags shown on load (hidden ones are there only so the page can reveal them live)."""
    return [tag for tag in re.findall(r'<span class="chip chip-ai"[^>]*>', fragment) if " hidden" not in tag]


def test_ai_off_has_no_tags_but_explains(ctx):
    client, _db = ctx
    llm_tab = client.get("/settings?tab=llm").text
    assert "AI is off." in llm_tab
    assert AI_TAG not in llm_tab
    sources = client.get("/sources?tab=feeds").text
    assert AI_TAG not in _feed_row(sources, "Summary Feed")
    assert "because AI is off" in sources
    briefing = client.get("/").text
    assert "AI summary" not in briefing and ">Excerpt<" in briefing


def test_ai_on_tags_only_settings_that_use_it(ctx):
    client, db = ctx
    _ai_on(db)
    llm_tab = client.get("/settings?tab=llm").text
    assert "AI is on" in llm_tab and AI_TAG in llm_tab
    assert "story text stays on your network" in llm_tab
    sources = client.get("/sources?tab=feeds").text
    assert _visible_tags(_feed_row(sources, "Summary Feed"))
    assert not _visible_tags(_feed_row(sources, "Full Feed"))  # Full article, no translation
    # ...but it carries a hidden tag with live rules, so switching to Summarise shows it before Save.
    assert 'data-ai-rules="summarize:1;translate:llm"' in _feed_row(sources, "Full Feed")
    briefing = client.get("/").text
    assert "AI summary" in briefing


def test_translation_tag_needs_ai_and_ai_provider(ctx):
    client, db = ctx
    feed = db.query(Feed).filter(Feed.name == "Full Feed").one()
    feed.translate = True
    feed.translate_provider = "llm"
    db.commit()
    assert AI_TAG not in _feed_row(client.get("/sources?tab=feeds").text, "Full Feed")
    _ai_on(db)
    assert _visible_tags(_feed_row(client.get("/sources?tab=feeds").text, "Full Feed"))


def test_edit_source_changes_category(ctx):
    client, db = ctx
    feed = db.query(Feed).filter(Feed.name == "Summary Feed").one()
    page = client.get("/sources?tab=feeds").text
    assert 'name="category"' in page[page.index(f'id="feed-{feed.id}"') :]
    form = {"schedule_mode": "global", "summarize": "1", "translate": "off", "feed_type": "rss",
            "rss_url": feed.url, "category": "technology"}
    assert client.post(f"/feeds/{feed.id}/schedule", data=form).status_code in {200, 303}
    db.refresh(feed)
    assert feed.category == "technology"
    form["category"] = "not-a-category"  # unknown keys are ignored, not saved
    client.post(f"/feeds/{feed.id}/schedule", data=form)
    db.refresh(feed)
    assert feed.category == "technology"
