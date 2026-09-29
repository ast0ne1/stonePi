"""Settings page: Reader firmware, Keep papers on reader, and Contents page controls."""

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
from app.models import Base, User
from app.routers import ui
from app.services import passwords, reader_config, settings
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "0b8e4f0e-6a57-4d0f-9d7e-2f4c1b6a9d11"
SECRET = "nc-test-secret"


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(
        auth_user_id=OWNER, username="adam", password=passwords.hash_password("test"), role="admin"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
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
    return client, db, user


def test_reader_tab_shows_firmware_and_keep_papers(ctx):
    client, db, user = ctx
    reader_config.remember_detected_firmware(db, user.id, "crossink", "1.6.0")
    html = client.get("/settings?tab=reader").text
    assert 'name="reader_firmware"' in html
    for value in ("auto", "crosspoint", "crossink"):
        assert f'value="{value}"' in html
    assert "Detected: <strong>CrossInk 1.6.0</strong>" in html
    assert 'name="reader_keep_days"' in html
    assert "CrossPoint</strong> or <strong>CrossInk" in html


def test_publication_tab_replaces_cover_first_with_contents_page(ctx):
    client, _db, _user = ctx
    html = client.get("/settings?tab=publication").text
    assert "epub_cover_first" not in html
    assert 'name="epub_contents_detail"' in html
    assert 'name="epub_contents_limit"' in html
    assert '<option value="sources"' in html
    assert "Categories and sources only" in html


def test_saving_reader_and_contents_settings(ctx):
    client, db, user = ctx
    page = client.get("/settings?tab=reader").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page)
    form = {
        "settings_tab": "reader",
        "reader_device": "xteink",
        "reader_host": "crosspoint.local",
        "reader_firmware": "crossink",
        "reader_keep_days": "7",
    }
    if token:
        form["csrf_token"] = token.group(1)
    response = client.post("/settings", data=form)
    assert response.status_code in {200, 303}, response.text[:400]
    db.expire_all()
    assert reader_config.reader_firmware(db, user.id) == "crossink"
    assert reader_config.reader_keep_days(db, user.id) == 7

    publication = {
        **form,
        "settings_tab": "publication",
        "epub_contents_detail": "auto",
        "epub_contents_limit": "25",
        "epub_x3_screen": "1",
        "epub_chapters_by_source": "1",
        "epub_omit_article_links": "1",
        "epub_toc_outline_numbers": "1",
    }
    response = client.post("/settings", data=publication)
    assert response.status_code in {200, 303}, response.text[:400]
    db.expire_all()
    assert settings.epub_contents_detail(db) == "auto"
    assert settings.epub_contents_limit(db) == 25

    response = client.post("/settings", data={**publication, "epub_contents_detail": "sources"})
    assert response.status_code in {200, 303}, response.text[:400]
    db.expire_all()
    assert settings.epub_contents_detail(db) == "sources"
