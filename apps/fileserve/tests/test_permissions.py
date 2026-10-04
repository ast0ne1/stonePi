"""FileServe publishing capabilities and the page-auth bypass (StonePi and solo mode)."""

from __future__ import annotations

from base64 import b64encode
from io import BytesIO

import pytest

from app import main
from app.services import capabilities
from stonepi_auth.session import COOKIE_NAME, encode_session

SECRET = "fs-perm-secret"
ADMIN_ID = "11111111-1111-4111-8111-111111111111"
SAM_ID = "22222222-2222-4222-8222-222222222222"
KIM_ID = "33333333-3333-4333-8333-333333333333"
JSON = {"Accept": "application/json", "X-Requested-With": "fetch"}


def _make_client(tmp_path, monkeypatch, *, secret: str):
    hosted = tmp_path / "hosted"
    hosted.mkdir()
    monkeypatch.setattr("app.services.pages.HOSTED_DIR", hosted)
    monkeypatch.setattr("app.services.users.HOSTED_DIR", hosted)
    monkeypatch.setattr("app.auth.env.stonepi_session_secret", secret)
    monkeypatch.setattr("app.main.env.stonepi_session_secret", secret)
    monkeypatch.setattr("app.auth.env.stonepi_prefix", "")
    monkeypatch.setattr("app.main.env.stonepi_prefix", "")
    monkeypatch.setattr("app.auth._platform_session_secret", lambda: secret)
    monkeypatch.setattr(main, "bell_context", lambda user, **kw: {"show": False, "dot": False, "url": ""})
    flask_app = main.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "DATABASE_URL": f"sqlite:///{(tmp_path / 'perm.db').as_posix()}",
        }
    )
    return flask_app.test_client()


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


@pytest.fixture
def client(tmp_path, monkeypatch, sent):
    return _make_client(tmp_path, monkeypatch, secret=SECRET)


def _as(client, *, user_id, username, admin=False, fileserve=None, studio=None, apps=None):
    perms = {}
    if fileserve is not None:
        perms["fileserve"] = fileserve
    if studio is not None:
        perms["studio"] = studio
    client.set_cookie(
        COOKIE_NAME,
        encode_session(
            secret=SECRET,
            user_id=user_id,
            username=username,
            display_name=username.title(),
            is_admin=admin,
            apps=apps if apps is not None else ["fileserve", "studio"],
            session_id=f"s-{username}",
            permissions=perms,
        ),
    )


def _as_admin(client, **kw):
    _as(client, user_id=ADMIN_ID, username="ada", admin=True, **kw)


def _as_sam(client, **caps):
    fileserve = {capabilities.PUBLISH_PAGES: True, capabilities.PUBLISH_UNPROTECTED: False}
    studio = {"can_publish": caps.pop("studio_publish", False)}
    fileserve.update(caps)
    _as(client, user_id=SAM_ID, username="sam", fileserve=fileserve, studio=studio)


def _as_kim(client):
    _as(client, user_id=KIM_ID, username="kim", fileserve={capabilities.PUBLISH_PAGES: True})


def _add(client, slug, *, protect=False, label=None):
    data = {"label": label or slug.title(), "slug": slug, "file": (BytesIO(f"<html>{slug}</html>".encode()), "a.html")}
    if protect:
        data.update({"protect": "1", "page_username": "guest", "page_password": "secret1"})
    return client.post("/admin/add", data=data, content_type="multipart/form-data", headers=JSON)


def _basic(user, password):
    return {"Authorization": "Basic " + b64encode(f"{user}:{password}".encode()).decode()}


def _page_id(client, slug):
    from app import db as database
    from app.models import Page

    db = database.SessionLocal()
    try:
        return db.query(Page).filter(Page.slug == slug).one().id
    finally:
        db.close()


# -- can(cap) ---------------------------------------------------------------------


def test_user_can_reads_grant_then_catalog_default():
    from app.models import User

    member = User(username="sam", role="user")
    member._capabilities = {}  # older cookie: no FileServe keys yet
    assert member.can(capabilities.PUBLISH_PAGES) is True
    assert member.can(capabilities.PUBLISH_UNPROTECTED) is False
    member._capabilities = {capabilities.PUBLISH_PAGES: False, capabilities.PUBLISH_UNPROTECTED: True}
    assert member.can(capabilities.PUBLISH_PAGES) is False
    assert member.can(capabilities.PUBLISH_UNPROTECTED) is True
    admin = User(username="ada", role="admin")
    admin._capabilities = {capabilities.PUBLISH_PAGES: False}
    assert admin.can(capabilities.PUBLISH_PAGES) is True
    # Solo FileServe: no switches, local accounts publish as before.
    assert User(username="alex", role="user").can(capabilities.PUBLISH_UNPROTECTED) is True


# -- 1/2: publishing ---------------------------------------------------------------


def test_member_without_publish_pages_cannot_add_edit_or_enable(client):
    _as_sam(client, can_publish_unprotected=True)
    assert _add(client, "first").status_code == 200
    page_id = _page_id(client, "first")
    assert client.post(f"/admin/toggle/{page_id}", headers=JSON).status_code == 200  # now off

    _as_sam(client, can_publish_pages=False, can_publish_unprotected=True)
    denied = _add(client, "second", protect=True)
    assert denied.status_code == 403
    assert "Publish pages" in denied.get_json()["message"]
    edit = client.post(
        f"/admin/edit/{page_id}", data={"label": "First", "slug": "first"}, headers=JSON
    )
    assert edit.status_code == 403
    assert client.post(f"/admin/toggle/{page_id}", headers=JSON).status_code == 403
    # Delete still works: removing your own page isn't publishing.
    assert client.post(f"/admin/delete/{page_id}", headers=JSON).status_code == 200

    html = client.get("/admin/add").get_data(as_text=True)
    assert capabilities.ASK_PUBLISH.replace("'", "&#39;") in html
    assert "publish-fieldset\" disabled" in html
    listing = client.get("/admin").get_data(as_text=True)
    assert "<span>Add page</span>" not in listing
    assert capabilities.ASK_PUBLISH.replace("'", "&#39;") in listing


def test_fetch_proxy_needs_fileserve_access_and_publish_pages(client, monkeypatch):
    monkeypatch.setattr("app.main.fetch_proxy.fetch_url", lambda url: (b"<html>ok</html>", "text/html"))
    _as(client, user_id=KIM_ID, username="kim", apps=["studio"])  # signed in, no FileServe
    for path in ("/admin/add/fetch", "/admin/tools/fetch"):
        assert client.get(f"{path}?url=https://example.com/", headers=JSON).status_code == 401
    _as_sam(client, can_publish_pages=False)
    for path in ("/admin/add/fetch", "/admin/tools/fetch"):
        assert client.get(f"{path}?url=https://example.com/", headers=JSON).status_code == 403
    _as_sam(client)
    ok = client.get("/admin/add/fetch?url=https://example.com/")
    assert ok.status_code == 200
    assert ok.data == b"<html>ok</html>"


def test_member_without_unprotected_must_set_password(client):
    _as_sam(client)
    open_page = _add(client, "open")
    assert open_page.status_code == 403
    assert "Publish without a password" in open_page.get_json()["message"]
    assert _add(client, "locked", protect=True).status_code == 200

    page_id = _page_id(client, "locked")
    # Can't drop protection from an existing page.
    removed = client.post(
        f"/admin/edit/{page_id}", data={"label": "Locked", "slug": "locked"}, headers=JSON
    )
    assert removed.status_code == 403
    kept = client.post(
        f"/admin/edit/{page_id}",
        data={"label": "Locked again", "slug": "locked", "protect": "1", "page_username": "guest"},
        headers=JSON,
    )
    assert kept.status_code == 200

    html = client.get("/admin/add").get_data(as_text=True)
    assert "data-protect-required" in html
    assert '<input type="hidden" name="protect" value="1" />' in html
    assert "Required for your account." in html


def test_existing_open_member_page_is_left_alone(client):
    _as_sam(client, can_publish_unprotected=True)
    assert _add(client, "legacy").status_code == 200
    page_id = _page_id(client, "legacy")
    assert client.post(f"/admin/toggle/{page_id}", headers=JSON).status_code == 200  # off

    _as_sam(client)  # "Publish without a password" withdrawn; the page stays as it is
    relabel = client.post(f"/admin/edit/{page_id}", data={"label": "Legacy 2", "slug": "legacy"}, headers=JSON)
    assert relabel.status_code == 200
    replace = client.post(
        f"/admin/edit/{page_id}",
        data={"label": "Legacy 2", "slug": "legacy", "file": (BytesIO(b"<html>new</html>"), "b.html")},
        content_type="multipart/form-data",
        headers=JSON,
    )
    assert replace.status_code == 403
    # Turning an open page back on needs a password first.
    assert client.post(f"/admin/toggle/{page_id}", headers=JSON).status_code == 403


def test_member_with_unprotected_and_admin_publish_openly(client):
    _as_sam(client, can_publish_unprotected=True)
    assert _add(client, "sam-open").status_code == 200
    _as_admin(client)
    assert _add(client, "admin-open").status_code == 200
    assert client.get("/admin-open").status_code == 200
    html = client.get("/admin/add").get_data(as_text=True)
    assert "data-protect-required" not in html
    assert "publish-fieldset\" disabled" not in html


def test_studio_publish_needs_both_capabilities(client):
    def publish(**extra):
        data = {"title": "Studio", "slug": "studio-out", "file": (BytesIO(b"<html>s</html>"), "s.html"), **extra}
        return client.post("/api/studio/publish", data=data, content_type="multipart/form-data", headers=JSON)

    _as_sam(client, can_publish_unprotected=True)
    no_studio = publish()
    assert no_studio.status_code == 403
    assert "Studio" in no_studio.get_json()["message"]

    _as_sam(client, studio_publish=True, can_publish_pages=False)
    assert publish().status_code == 403

    _as_sam(client, studio_publish=True)
    assert publish().status_code == 403  # needs a password
    ok = publish(protected="1", username="guest", password="secret1")
    assert ok.status_code == 200
    assert ok.get_json()["public_path"] == "/u/sam/studio-out"

    _as_admin(client, studio={})
    admin = publish(slug="admin-studio")
    assert admin.status_code == 200


# -- 3: page Basic auth / disabled bypass ------------------------------------------


def test_only_owner_or_fileserve_admin_bypasses_page_auth(client):
    _as_sam(client)
    assert _add(client, "secret", protect=True).status_code == 200
    _as_sam(client, can_publish_unprotected=True)
    assert _add(client, "hidden").status_code == 200
    hidden_id = _page_id(client, "hidden")
    assert client.post(f"/admin/toggle/{hidden_id}", headers=JSON).status_code == 200

    # Owner previews both.
    assert client.get("/u/sam/secret").status_code == 200
    assert client.get("/u/sam/hidden").status_code == 200

    # Another member is a normal visitor.
    _as_kim(client)
    challenged = client.get("/u/sam/secret")
    assert challenged.status_code == 401
    assert "Basic" in challenged.headers["WWW-Authenticate"]
    assert client.get("/u/sam/secret", headers=_basic("guest", "secret1")).status_code == 200
    assert client.get("/u/sam/hidden").status_code == 404

    # Platform user without FileServe access gets no bypass either.
    _as(client, user_id=KIM_ID, username="kim", apps=["studio"])
    assert client.get("/u/sam/secret").status_code == 401
    _as(client, user_id=ADMIN_ID, username="ada", admin=True, apps=["studio"])
    assert client.get("/u/sam/secret").status_code == 401
    assert client.get("/u/sam/hidden").status_code == 404

    # Admin with FileServe access can.
    _as_admin(client)
    assert client.get("/u/sam/secret").status_code == 200
    assert client.get("/u/sam/hidden").status_code == 200

    client.delete_cookie(COOKIE_NAME)
    assert client.get("/u/sam/secret").status_code == 401
    assert client.get("/u/sam/hidden").status_code == 404


# -- 5: household notification -----------------------------------------------------


def test_household_notification_only_for_admin_pages(client, sent):
    _as_sam(client, can_publish_unprotected=True)
    assert _add(client, "member-page").status_code == 200
    assert sent == []
    _as_admin(client)
    assert _add(client, "admin-page").status_code == 200
    assert len(sent) == 1
    assert sent[0]["url"] == "/files/admin-page"


# -- solo mode ---------------------------------------------------------------------


def test_solo_mode_unchanged_and_members_cannot_bypass(tmp_path, monkeypatch, sent):
    solo = _make_client(tmp_path, monkeypatch, secret="")
    solo.post("/login", data={"username": "admin", "password": "admin"})
    solo.post("/admin/settings/users", data={"username": "alex", "password": "alexpass"})
    assert _add(solo, "solo-open").status_code == 200
    assert _add(solo, "solo-locked", protect=True).status_code == 200
    off_id = _page_id(solo, "solo-open")
    assert solo.post(f"/admin/toggle/{off_id}", headers=JSON).status_code == 200
    assert solo.get("/solo-open").status_code == 200  # owner/admin preview
    assert solo.get("/solo-locked").status_code == 200
    html = solo.get("/admin/add").get_data(as_text=True)
    assert "data-protect-required" not in html

    solo.post("/logout")
    solo.post("/login", data={"username": "alex", "password": "alexpass"})
    assert _add(solo, "alex-open").status_code == 200  # local accounts publish openly
    assert solo.get("/solo-open").status_code == 404
    assert solo.get("/solo-locked").status_code == 401
    # Solo household alerts: the local admin's pages only.
    assert [event["url"] for event in sent] == ["/files/solo-open", "/files/solo-locked"]


def test_member_with_no_stored_grant_gets_catalog_defaults(client):
    _as(client, user_id=SAM_ID, username="sam", fileserve={})
    assert _add(client, "default-open").status_code == 403
    assert _add(client, "default-locked", protect=True).status_code == 200
