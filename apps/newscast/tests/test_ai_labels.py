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
from tests.ai_helpers import platform_ai, use_ai

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


def _ai_on(monkeypatch, db: Session, **choices) -> None:
    """Ollama set up in the platform, and NewsCast's master switch plus the given choices on."""
    platform_ai(monkeypatch, "ok", provider="ollama", model="llama3.2")
    use_ai(db, **({"summaries": True} | choices))


def _feed_row(html: str, name: str) -> str:
    start = html.index(f'<h3 class="feed-row-title">{name}</h3>')
    return html[start : html.index("</summary>", start)]


def _visible_tags(fragment: str) -> list[str]:
    """AI tags shown on load (hidden ones are there only so the page can reveal them live)."""
    return [tag for tag in re.findall(r'<span class="chip chip-ai"[^>]*>', fragment) if " hidden" not in tag]


def test_ai_off_has_no_tags_but_explains(ctx):
    client, _db = ctx
    llm_tab = client.get("/settings?tab=llm").text
    assert "No AI is set up on this StonePi yet" in llm_tab
    assert 'name="ai_enabled" value="1" data-ai-master  disabled' in llm_tab
    assert AI_TAG not in llm_tab
    sources = client.get("/sources?tab=feeds").text
    assert AI_TAG not in _feed_row(sources, "Summary Feed")
    assert "picked on this device" in sources
    briefing = client.get("/").text
    assert "AI summary" not in briefing and ">Summary<" in briefing


def test_platform_key_alone_does_not_turn_ai_on(ctx, monkeypatch):
    client, _db = ctx
    platform_ai(monkeypatch, "ok")  # e.g. an OpenAI key added for Studio
    llm_tab = client.get("/settings?tab=llm").text
    assert "No AI is set up" not in llm_tab
    assert 'data-ai-master  disabled' not in llm_tab and "checked" not in llm_tab.split('name="ai_enabled"')[1][:60]
    assert AI_TAG not in _feed_row(client.get("/sources?tab=feeds").text, "Summary Feed")


def test_master_switch_and_choices_save(ctx, monkeypatch):
    client, db = ctx
    platform_ai(monkeypatch, "ok")
    form = {"settings_tab": "llm", "ai_enabled": "1", "ai_summaries": "ai", "translate_provider": "google",
            "ai_importance": "rules"}
    monkeypatch.setattr("app.services.catchup.start_catchup", lambda: True)
    assert client.post("/settings", data=form).status_code in {200, 303}
    db.expire_all()
    from app.services.ai import ai_settings

    ai = ai_settings(db)
    assert ai.enabled and ai.summaries and not ai.translate and not ai.importance
    form.pop("ai_enabled")
    client.post("/settings", data=form)
    db.expire_all()
    assert not ai_settings(db).enabled and not ai_settings(db).summaries


def test_ai_on_tags_only_settings_that_use_it(ctx, monkeypatch):
    client, db = ctx
    _ai_on(monkeypatch, db)
    llm_tab = client.get("/settings?tab=llm").text
    assert AI_TAG in llm_tab
    assert "story text stays on your network" in llm_tab
    sources = client.get("/sources?tab=feeds").text
    assert _visible_tags(_feed_row(sources, "Summary Feed"))
    assert not _visible_tags(_feed_row(sources, "Full Feed"))  # Full article, no translation
    # ...but it carries a hidden tag with live rules, so switching to Summarise shows it before Save.
    assert 'data-ai-rules="summarize:1;"' in _feed_row(sources, "Full Feed")


def test_cards_follow_each_story_not_the_settings(ctx, monkeypatch):
    client, db = ctx
    story = db.query(Story).one()
    story.ai_record = '{"summary":{"by":"ai","model":"Gemini 2.5 Flash-Lite","model_id":"gemini/x"},"translate":{"by":"fallback","reason":"unavailable"}}'
    db.commit()
    briefing = client.get("/").text  # AI is off now, but this story was made with it
    assert "AI summary" in briefing and "Gemini 2.5 Flash-Lite" in briefing
    assert ">Translated<" in briefing
    story.ai_record = '{"summary":{"by":"fallback","reason":"unavailable"}}'
    db.commit()
    assert "Summary · AI unavailable" in client.get("/").text


def test_translation_tag_needs_ai_translation(ctx, monkeypatch):
    client, db = ctx
    feed = db.query(Feed).filter(Feed.name == "Full Feed").one()
    feed.translate = True
    feed.translate_provider = "global"
    db.commit()
    assert AI_TAG not in _feed_row(client.get("/sources?tab=feeds").text, "Full Feed")
    _ai_on(monkeypatch, db, summaries=False, translate=True)
    assert _visible_tags(_feed_row(client.get("/sources?tab=feeds").text, "Full Feed"))
    feed.translate_provider = "google"  # the source opts out
    db.commit()
    assert not _visible_tags(_feed_row(client.get("/sources?tab=feeds").text, "Full Feed"))


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
