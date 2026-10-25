"""Ripple Phase 2: grants, posting, editing, deleting, the feed and settings."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SAM = "0b1d3c5e-1111-4a2b-9c3d-5e6f7a8b9c0d"
SECRET = "ripple-secret"


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)


def _client(monkeypatch, *, secret=SECRET, uid=JO, name="Jo", is_admin=False, granted=True, signed_in=True) -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: secret)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    if secret and signed_in:
        c.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=secret, user_id=uid, username=name.lower(), display_name=name, is_admin=is_admin,
                apps=["ripple"] if granted else [], session_id="s", permissions={},
            ),
        )
    return c


def _post(c: TestClient, body: str, *, show: bool = True, csrf: str = "csrf"):
    data = {"body": body, "csrf_token": csrf}
    if show:
        data["show_on_display"] = "1"
    return c.post("/posts", data=data, follow_redirects=False)


def _all_posts() -> list[dict]:
    return db.list_posts(1, 100)[0]


# -- access ----------------------------------------------------------------------------


def test_signed_out_goes_to_sign_in(monkeypatch):
    c = _client(monkeypatch, signed_in=False)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and "login" in r.headers["location"]
    r = _post(c, "Hi")
    assert r.status_code == 303 and "login" in r.headers["location"]
    assert _all_posts() == []


def test_without_the_grant_nothing_is_shown_or_saved(monkeypatch):
    _post(_client(monkeypatch, uid=ADAM, name="Adam"), "Private family news")
    c = _client(monkeypatch, uid=SAM, name="Sam", granted=False)
    for path in ("/", "/new", "/settings", "/posts/1/edit"):
        r = c.get(path)
        assert r.status_code == 403, path
        assert "Private family news" not in r.text
        assert "No access to Ripple" in r.text
    assert _post(c, "Sneaky").status_code == 403
    assert c.post("/posts/1/delete", data={"csrf_token": "csrf"}).status_code == 403
    assert [p["body"] for p in _all_posts()] == ["Private family news"]


def test_admin_without_the_grant_still_gets_in(monkeypatch):
    c = _client(monkeypatch, uid=ADAM, name="Adam", is_admin=True, granted=False)
    assert c.get("/").status_code == 200


# -- posting ---------------------------------------------------------------------------


def test_member_posts_and_sees_it_in_the_feed(monkeypatch):
    c = _client(monkeypatch)
    r = _post(c, "  Walk along the harbour.\r\nCold but clear.  ")
    assert r.status_code == 303 and r.headers["location"] == "/?msg=Posted"
    post = _all_posts()[0]
    assert post["body"] == "Walk along the harbour.\nCold but clear."
    assert post["author_id"] == JO and post["author_name"] == "Jo" and post["show_on_display"] is True
    html = c.get("/").text
    assert "Walk along the harbour." in html and "<strong>Jo</strong>" in html
    assert "Not on the family display" not in html


def test_display_switch_off_is_saved_and_shown(monkeypatch):
    c = _client(monkeypatch)
    _post(c, "Just for the feed", show=False)
    assert _all_posts()[0]["show_on_display"] is False
    assert "Not on the family display" in c.get("/").text


def test_new_post_form_has_the_display_switch_on(monkeypatch):
    html = _client(monkeypatch).get("/new").text
    assert 'name="show_on_display" value="1" checked' in html


@pytest.mark.parametrize("body, err", [("   ", "Write%20something"), ("x" * (db.BODY_MAX + 1), "up%20to")])
def test_empty_or_too_long_posts_are_refused(monkeypatch, body, err):
    r = _post(_client(monkeypatch), body)
    assert r.status_code == 303 and r.headers["location"].startswith("/new?err=")
    assert err in r.headers["location"]
    assert _all_posts() == []


def test_expired_form_is_refused(monkeypatch):
    r = _post(_client(monkeypatch), "Hi", csrf="wrong")
    assert "err=" in r.headers["location"]
    assert _all_posts() == []


def test_post_text_is_escaped(monkeypatch):
    c = _client(monkeypatch)
    _post(c, "<script>alert(1)</script>")
    html = c.get("/").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


# -- editing and deleting --------------------------------------------------------------


def test_author_edits_own_post(monkeypatch):
    c = _client(monkeypatch)
    _post(c, "First go")
    pid = _all_posts()[0]["id"]
    assert "First go" in c.get(f"/posts/{pid}/edit").text
    r = c.post(f"/posts/{pid}", data={"body": "Second go", "csrf_token": "csrf"}, follow_redirects=False)
    assert r.headers["location"] == "/?msg=Saved"
    post = db.get_post(pid)
    assert post["body"] == "Second go" and post["show_on_display"] is False


def test_member_cannot_change_someone_elses_post(monkeypatch):
    _post(_client(monkeypatch, uid=ADAM, name="Adam"), "Adam's news")
    pid = _all_posts()[0]["id"]
    jo = _client(monkeypatch)
    html = jo.get("/").text
    assert f"/posts/{pid}/edit" not in html and f"/posts/{pid}/delete" not in html
    assert "err=" in jo.get(f"/posts/{pid}/edit", follow_redirects=False).headers["location"]
    r = jo.post(f"/posts/{pid}", data={"body": "Hijacked", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = jo.post(f"/posts/{pid}/delete", data={"csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert db.get_post(pid)["body"] == "Adam's news"


def test_admin_can_edit_and_delete_any_post(monkeypatch):
    _post(_client(monkeypatch), "Jo's news")
    pid = _all_posts()[0]["id"]
    admin = _client(monkeypatch, uid=ADAM, name="Adam", is_admin=True)
    assert f"/posts/{pid}/edit" in admin.get("/").text
    admin.post(f"/posts/{pid}", data={"body": "Tidied", "show_on_display": "1", "csrf_token": "csrf"})
    assert db.get_post(pid)["body"] == "Tidied"
    assert db.get_post(pid)["author_name"] == "Jo"  # the author stays the author
    admin.post(f"/posts/{pid}/delete", data={"csrf_token": "csrf"})
    assert db.get_post(pid) is None


def test_deleted_post_leaves_the_feed_but_stays_in_the_database(monkeypatch):
    c = _client(monkeypatch)
    _post(c, "Gone soon")
    pid = _all_posts()[0]["id"]
    r = c.post(f"/posts/{pid}/delete", data={"csrf_token": "csrf"}, follow_redirects=False)
    assert r.headers["location"] == "/?msg=Deleted"
    assert "Gone soon" not in c.get("/").text
    assert c.get(f"/posts/{pid}/edit", follow_redirects=False).status_code == 303
    with db.connect() as conn:
        assert conn.execute("SELECT deleted_at FROM posts WHERE id = ?", (pid,)).fetchone()[0]


# -- feed ------------------------------------------------------------------------------


def test_feed_is_newest_first_and_paged(monkeypatch):
    c = _client(monkeypatch)
    for n in range(routes.PER_PAGE + 1):
        _post(c, f"Post number {n:02d}")
    first = c.get("/").text
    assert first.index("Post number 20") < first.index("Post number 19")
    assert "Post number 00" not in first and 'href="/?page=2"' in first
    second = c.get("/?page=2").text
    assert "Post number 00" in second and 'href="/?page=1"' in second


def test_empty_feed_invites_a_first_post(monkeypatch):
    html = _client(monkeypatch).get("/").text
    assert "No posts yet" in html and 'href="/new"' in html


# -- settings and standalone -----------------------------------------------------------


def test_only_admins_rename_the_board(monkeypatch):
    jo = _client(monkeypatch)
    assert 'action="/settings/board"' not in jo.get("/settings?tab=board").text
    r = jo.post("/settings/board", data={"board_name": "Mine", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and db.board_name() == db.DEFAULT_BOARD_NAME
    admin = _client(monkeypatch, uid=ADAM, name="Adam", is_admin=True)
    admin.post("/settings/board", data={"board_name": "  The   Stones ", "csrf_token": "csrf"})
    assert db.board_name() == "The Stones"
    assert "The Stones" in jo.get("/").text


def test_standalone_posts_as_you(monkeypatch):
    c = _client(monkeypatch, secret="")
    _post(c, "Solo")
    post = _all_posts()[0]
    assert (post["author_id"], post["author_name"]) == routes.LOCAL_AUTHOR
    assert f"/posts/{post['id']}/edit" in c.get("/").text


def test_schema_is_migrated_to_the_latest_version(tmp_path):
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    raw = sqlite3.connect(db.db_path())
    try:
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        raw.close()
    assert {"posts", "settings"} <= names


def test_healthz(monkeypatch):
    assert _client(monkeypatch).get("/healthz").json() == {"ok": True, "service": "ripple"}
