"""Adding links from elsewhere (pinboard-canvas.md step 4): /add, the manifest's share
target, and Settings -> Board's bookmarklet and Shortcut."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import links, people, routes, store
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
SECRET = "pin-secret"


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")
    monkeypatch.setattr(links, "fetch_preview_later", lambda card_id: None)


def _client(monkeypatch, *, signed_in=True, apps=("pinboard",)) -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    monkeypatch.setattr(routes, "notifications_card_context", lambda *a, **kw: None)
    monkeypatch.setattr(people, "household_people", lambda s: [])
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    if signed_in:
        c.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=SECRET, user_id=JO, username="jo", display_name="Jo", is_admin=False,
                apps=list(apps), session_id="s", permissions={"pinboard": {}},
            ),
        )
    return c


def test_add_opens_add_link_filled_in(monkeypatch):
    page = _client(monkeypatch).get("/add", params={"url": "https://example.com/r", "title": "  Great   recipe ", "text": ""}).text
    assert 'data-sheet="add-link" data-open-on-load' in page
    assert 'value="https://example.com/r"' in page
    assert 'name="title" value="Great recipe"' in page


def test_android_text_only_share_finds_the_address(monkeypatch):
    page = _client(monkeypatch).get("/add", params={"text": "Look at this - https://example.com/x?y=1"}).text
    assert 'value="https://example.com/x?y=1"' in page
    assert ">Look at this</textarea>" in page


def test_plain_board_does_not_open_the_form(monkeypatch):
    assert "data-open-on-load" not in _client(monkeypatch).get("/").text


def test_signed_out_comes_back_to_the_shared_link(monkeypatch):
    res = _client(monkeypatch, signed_in=False).get("/add", params={"url": "https://example.com/r"}, follow_redirects=False)
    assert res.status_code == 303
    assert "add%3Furl%3Dhttps" in res.headers["location"] or "add?url=https" in res.headers["location"]


def test_no_access_page_still_renders(monkeypatch):
    res = _client(monkeypatch, apps=()).get("/add", params={"url": "https://example.com/r"})
    assert res.status_code == 403 and "No access to Pinboard." in res.text


def test_shared_title_kept_until_the_preview_finds_one():
    card = store.add_card("link", {"url": "https://example.com/r", "title": "From the share sheet"})
    assert store.card_view(store.get_card(card["id"]))["title"] == "From the share sheet"
    store.set_link_preview(card["id"], {"title": "From the page", "preview": "ok"})
    assert store.get_card(card["id"])["data"]["title"] == "From the page"


def test_manifest_has_a_share_target(monkeypatch):
    data = _client(monkeypatch, signed_in=False).get("/manifest.webmanifest").json()
    assert data["start_url"] == "./" and data["scope"] == "./"
    assert data["share_target"] == {
        "action": "./add",
        "method": "GET",
        "params": {"title": "title", "text": "text", "url": "url"},
    }
    assert any(icon["sizes"] == "512x512" for icon in data["icons"])


def test_settings_board_explains_bookmarklet_and_shortcut(monkeypatch):
    page = _client(monkeypatch).get("/settings", params={"tab": "board"}).text
    for text in ("data-bookmarklet", "data-shortcut-url", "Show in Share Sheet", "URL Encode", "Open URLs", 'data-add-url href="/add"'):
        assert text in page
