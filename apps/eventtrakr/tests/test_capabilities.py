"""Per-person EventTrakr capabilities: route gates, defaults, background jobs, public pages."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db import SessionLocal, init_db
from app.models import CalendarConnection, Event, EventSource, SocialAccount, User, utcnow
from app.services import capabilities

SECRET = "et-cap-secret"
ALL_OFF = {
    capabilities.MANAGE_SOURCES: False,
    capabilities.USE_SOCIAL: False,
    capabilities.SHARE_AGENDA: False,
    capabilities.SYNC_CALENDAR: False,
}
ALL_ON = {cap: True for cap in ALL_OFF}


@pytest.fixture
def make_client(monkeypatch):
    """``make_client(perms=..., is_admin=...)`` -> (client, local User id) signed in through StonePi SSO."""
    import app as app_module
    from app import create_app
    from stonepi_auth.session import COOKIE_NAME, encode_session

    monkeypatch.setattr("app.services.auth._platform_session_secret", lambda: SECRET)
    monkeypatch.setattr("app.config.env.stonepi_session_secret", SECRET)
    monkeypatch.setattr(app_module, "bell_context", lambda user, **kw: {"show": False, "dot": False, "url": ""})
    # Never reach a real Auth from tests.
    monkeypatch.setattr("stonepi_auth.internal.get_internal_json", lambda *a, **k: None)
    init_db()
    created: list[str] = []

    def factory(*, perms: dict | None = None, is_admin: bool = False):
        auth_id = str(uuid4())
        created.append(auth_id)
        flask_app = create_app()
        flask_app.config["TESTING"] = True
        client = flask_app.test_client()
        permissions = {"eventtrakr": perms} if perms is not None else {}
        client.set_cookie(
            COOKIE_NAME,
            encode_session(
                secret=SECRET, user_id=auth_id, username=f"cap-{auth_id[:8]}", display_name="Cap",
                is_admin=is_admin, apps=["eventtrakr"], session_id="s", permissions=permissions,
            ),
        )
        client.get("/settings?tab=general")  # first visit creates + mirrors the local user
        with SessionLocal() as db:
            local = db.query(User).filter(User.auth_user_id == auth_id).one()
            return client, local.id

    yield factory
    with SessionLocal() as db:
        ids = [u.id for u in db.query(User).filter(User.auth_user_id.in_(created)).all()]
        if ids:
            db.query(EventSource).filter(EventSource.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(SocialAccount).filter(SocialAccount.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(Event).filter(Event.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(CalendarConnection).filter(CalendarConnection.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def _flashes(client) -> list[str]:
    with client.session_transaction() as sess:
        return [message for _, message in sess.get("_flashes", [])]


def _source(user_id: int, url: str = "") -> int:
    with SessionLocal() as db:
        src = EventSource(user_id=user_id, name="Test source", url=url or f"https://example.com/{uuid4().hex}")
        db.add(src)
        db.commit()
        return src.id


def _account(user_id: int) -> int:
    with SessionLocal() as db:
        acc = SocialAccount(user_id=user_id, platform="instagram", username=f"acc{uuid4().hex[:8]}")
        db.add(acc)
        db.commit()
        return acc.id


# -- defaults ---------------------------------------------------------------------


def test_catalog_declares_eventtrakr_capabilities_with_defaults():
    from stonepi_auth import capabilities_for

    caps = {c["id"]: c for c in capabilities_for("eventtrakr")}
    assert set(caps) == set(ALL_OFF)
    assert {k: c["default"] for k, c in caps.items()} == {
        "can_manage_sources": True,
        "can_use_social": False,
        "can_share_agenda": True,
        "can_sync_calendar": True,
    }
    assert capabilities.defaults() == {k: c["default"] for k, c in caps.items()}


def test_resolve_uses_defaults_for_missing_keys_and_admin_gets_all():
    assert capabilities.resolve(None, is_admin=False) == capabilities.defaults()
    assert capabilities.resolve({}, is_admin=False)[capabilities.USE_SOCIAL] is False
    assert capabilities.resolve({"can_use_social": True}, is_admin=False)[capabilities.USE_SOCIAL] is True
    assert capabilities.resolve({"can_manage_sources": False}, is_admin=False)[capabilities.MANAGE_SOURCES] is False
    assert capabilities.resolve(ALL_OFF, is_admin=True) == ALL_ON


def test_existing_member_gets_defaults_without_admin_action(make_client):
    # A grant saved before EventTrakr had capabilities: empty dict (or a pre-upgrade cookie).
    client, uid = make_client(perms={})
    with SessionLocal() as db:
        mirrored = capabilities.for_user(db.get(User, uid))
    assert mirrored == {
        "can_manage_sources": True,
        "can_use_social": False,
        "can_share_agenda": True,
        "can_sync_calendar": True,
    }
    src_id = _source(uid)
    client.post(f"/sources/toggle/{src_id}")
    with SessionLocal() as db:
        assert db.get(EventSource, src_id).enabled is False  # manage sources: on
    acc_before = _count_accounts(uid)
    client.post("/sources/social/add", data={"username": "theglobe"})
    assert _count_accounts(uid) == acc_before  # social: off


def test_legacy_local_user_row_without_mirror_gets_defaults():
    init_db()
    with SessionLocal() as db:
        user = User(username=f"legacy-{uuid4().hex[:8]}", role="user", capabilities=None)
        db.add(user)
        db.commit()
        try:
            assert capabilities.for_user(user) == capabilities.defaults()
        finally:
            db.delete(user)
            db.commit()


def _count_accounts(uid: int) -> int:
    with SessionLocal() as db:
        return db.query(SocialAccount).filter(SocialAccount.user_id == uid).count()


# -- route gates ------------------------------------------------------------------

MANAGE_ROUTES = [
    ("toggle", lambda sid: f"/sources/toggle/{sid}", {}),
    ("category", lambda sid: f"/sources/{sid}/category", {"category": "music"}),
    ("schedule", lambda sid: f"/sources/{sid}/schedule", {"schedule_mode": "custom", "schedule_type": "interval", "interval_minutes": "180"}),
    ("delete", lambda sid: f"/sources/delete/{sid}", {}),
]


@pytest.mark.parametrize("name,url,form", MANAGE_ROUTES)
def test_manage_source_routes_refuse_member_without_capability(make_client, name, url, form):
    client, uid = make_client(perms={**ALL_ON, "can_manage_sources": False})
    src_id = _source(uid)
    res = client.post(url(src_id), data=form)
    assert res.status_code == 302 and res.headers["Location"].rstrip("/").endswith("/sources")
    assert any("Manage sources" in m for m in _flashes(client))
    with SessionLocal() as db:
        src = db.get(EventSource, src_id)
        assert src is not None and src.enabled is True and src.schedule_mode == "global"


@pytest.mark.parametrize("perms,is_admin", [({"can_manage_sources": True}, False), (ALL_OFF, True)])
def test_manage_source_routes_allow_capability_and_admin(make_client, perms, is_admin):
    client, uid = make_client(perms=perms, is_admin=is_admin)
    src_id = _source(uid)
    client.post(f"/sources/toggle/{src_id}")
    client.post(f"/sources/{src_id}/schedule", data={"schedule_mode": "custom", "schedule_type": "interval", "interval_minutes": "180"})
    with SessionLocal() as db:
        src = db.get(EventSource, src_id)
        assert src.enabled is False and src.schedule_mode == "custom"
    client.post(f"/sources/delete/{src_id}")
    with SessionLocal() as db:
        assert db.get(EventSource, src_id) is None


def test_add_source_and_catalog_subscribe_refused_without_capability(make_client, monkeypatch):
    from app.models import CatalogSource
    from app.services import ingest

    monkeypatch.setattr(ingest, "fetch_and_extract_source", lambda *a, **k: pytest.fail("must not fetch"))
    client, uid = make_client(perms={"can_manage_sources": False})
    client.post("/sources/add", data={"name": "X", "url": "https://example.com/x"})
    with SessionLocal() as db:
        cat = db.query(CatalogSource).first()
    client.post(f"/sources/catalog/subscribe/{cat.id}")
    with SessionLocal() as db:
        assert db.query(EventSource).filter(EventSource.user_id == uid).count() == 0


def test_facebook_source_needs_social_capability_too(make_client, monkeypatch):
    from app.services import ingest

    monkeypatch.setattr(ingest, "fetch_and_extract_source", lambda *a, **k: pytest.fail("must not fetch"))
    client, uid = make_client(perms={"can_manage_sources": True, "can_use_social": False})
    client.post("/sources/add", data={"name": "FB", "url": "https://www.facebook.com/events/search/?q=x"})
    assert any("Instagram & Facebook" in m for m in _flashes(client))
    with SessionLocal() as db:
        assert db.query(EventSource).filter(EventSource.user_id == uid).count() == 0


def test_social_routes_refuse_member_without_capability(make_client, monkeypatch):
    from app.services.social import poll as social_poll

    monkeypatch.setattr(social_poll, "poll_account", lambda *a, **k: pytest.fail("must not spend Bright Data"))
    client, uid = make_client(perms={})  # default: no social
    acc_id = _account(uid)
    for url, form in [
        ("/sources/social/add", {"username": "theglobe"}),
        (f"/sources/social/{acc_id}/toggle", {}),
        (f"/sources/social/{acc_id}/schedule", {"schedule_mode": "custom", "schedule_type": "interval", "interval_minutes": "180"}),
        (f"/sources/social/{acc_id}/check", {}),
        (f"/sources/social/{acc_id}/delete", {}),
    ]:
        res = client.post(url, data=form)
        assert res.status_code == 302 and res.headers["Location"].rstrip("/").endswith("/sources/social"), url
    res = client.post(f"/api/social/accounts/{acc_id}/check")
    assert res.status_code == 403 and "Instagram & Facebook" in res.get_json()["error"]
    res = client.post("/add-event/fetch-facebook", json={"url": "https://www.facebook.com/events/1"})
    assert res.status_code == 403
    with SessionLocal() as db:
        acc = db.get(SocialAccount, acc_id)
        assert acc is not None and acc.tracking_enabled is True
        assert db.query(SocialAccount).filter(SocialAccount.user_id == uid).count() == 1
    html = client.get("/sources/social").get_data(as_text=True)
    assert 'data-cap-hint="can_use_social"' in html
    assert 'action="/sources/social/add"' not in html


@pytest.mark.parametrize("perms,is_admin", [({"can_use_social": True}, False), (ALL_OFF, True)])
def test_social_routes_allow_capability_and_admin(make_client, perms, is_admin):
    client, uid = make_client(perms=perms, is_admin=is_admin)
    client.post("/sources/social/add", data={"username": "theglobe"})
    assert _count_accounts(uid) == 1
    html = client.get("/sources/social").get_data(as_text=True)
    assert 'action="/sources/social/add"' in html


def test_privacy_requires_share_capability(make_client):
    client, uid = make_client(perms={**ALL_ON, "can_share_agenda": False})
    res = client.post("/settings/privacy", data={"is_public": "1", "favourites_public": "1"})
    assert res.status_code == 302 and "tab=privacy" in res.headers["Location"]
    with SessionLocal() as db:
        user = db.get(User, uid)
        assert user.is_public is False and user.favourites_public is False
    html = client.get("/settings?tab=privacy").get_data(as_text=True)
    assert 'data-cap-hint="can_share_agenda"' in html
    assert 'action="/settings/privacy"' not in html

    client2, uid2 = make_client(perms={"can_share_agenda": True})
    client2.post("/settings/privacy", data={"is_public": "1"})
    with SessionLocal() as db:
        assert db.get(User, uid2).is_public is True


def test_google_calendar_requires_sync_capability(make_client):
    client, _ = make_client(perms={**ALL_ON, "can_sync_calendar": False})
    res = client.get("/calendar/google/connect")
    assert res.status_code == 302 and "tab=calendar" in res.headers["Location"]
    assert any("Google Calendar sync" in m for m in _flashes(client))
    html = client.get("/settings?tab=calendar").get_data(as_text=True)
    assert 'data-cap-hint="can_sync_calendar"' in html
    assert 'href="/calendar/google/connect"' not in html

    allowed, _ = make_client(perms={"can_sync_calendar": True})
    allowed.get("/calendar/google/connect")
    # Gets past the gate (here it stops at "credentials not configured" or goes to Google).
    assert not any("Google Calendar sync" in m for m in _flashes(allowed))


def test_favourite_skips_google_forward_without_sync_capability(make_client, monkeypatch):
    from app.routes import api as api_routes

    calls: list[int] = []
    monkeypatch.setattr(api_routes.calendar_sync, "forward_event_to_google", lambda db, uid, ev: calls.append(uid) or (True, ""))
    client, uid = make_client(perms={"can_sync_calendar": False})
    with SessionLocal() as db:
        ev = Event(user_id=uid, fingerprint=uuid4().hex, title="T", start_time=utcnow() + timedelta(days=1))
        db.add(ev)
        db.commit()
        ev_id = ev.id
    assert client.post(f"/api/events/{ev_id}/favourite").get_json()["favourited"] is True
    assert calls == []
    allowed, uid2 = make_client(perms={"can_sync_calendar": True})
    with SessionLocal() as db:
        ev = Event(user_id=uid2, fingerprint=uuid4().hex, title="T", start_time=utcnow() + timedelta(days=1))
        db.add(ev)
        db.commit()
        ev_id = ev.id
    allowed.post(f"/api/events/{ev_id}/favourite")
    assert calls == [uid2]


def test_sources_page_hides_controls_without_manage_capability(make_client):
    client, uid = make_client(perms={"can_manage_sources": False})
    _source(uid)
    html = client.get("/sources").get_data(as_text=True)
    assert 'data-cap-hint="can_manage_sources"' in html
    assert 'action="/sources/add"' not in html
    assert "/sources/catalog/subscribe/" not in html
    assert "/sources/toggle/" not in html

    allowed, uid2 = make_client(perms={"can_manage_sources": True})
    _source(uid2)
    html = allowed.get("/sources").get_data(as_text=True)
    assert 'action="/sources/add"' in html and "/sources/toggle/" in html


# -- background jobs and public pages -----------------------------------------------


def test_background_social_poll_skips_people_without_capability(make_client, monkeypatch):
    from app.services.social import config as social_config
    from app.services.social import poll as social_poll

    _, no_cap = make_client(perms={})
    _, with_cap = make_client(perms={"can_use_social": True})
    _, admin = make_client(perms={}, is_admin=True)
    accounts = {uid: _account(uid) for uid in (no_cap, with_cap, admin)}

    polled: list[int] = []
    monkeypatch.setattr(social_config, "instagram_enabled", lambda db: True)
    monkeypatch.setattr(social_poll, "_account_due", lambda *a, **k: True)
    monkeypatch.setattr(social_poll, "poll_account", lambda db, account, **k: polled.append(account.id) or {})
    with SessionLocal() as db:
        result = social_poll.poll_due_accounts(db)
    assert accounts[no_cap] not in polled
    assert accounts[with_cap] in polled
    assert accounts[admin] in polled
    assert result["skipped_no_permission"] >= 1


def test_roster_refresh_revokes_people_who_never_come_back(make_client):
    _, keeps = make_client(perms={"can_use_social": True})
    _, revoked = make_client(perms={"can_use_social": True})
    _, gone = make_client(perms={"can_use_social": True})
    with SessionLocal() as db:
        rows = {u.id: u for u in db.query(User).filter(User.id.in_([keeps, revoked, gone]))}
        people = [
            {"id": rows[keeps].auth_user_id, "is_admin": False,
             "permissions": {"eventtrakr": {**ALL_ON}}},
            {"id": rows[revoked].auth_user_id, "is_admin": False,
             "permissions": {"eventtrakr": {**ALL_ON, "can_use_social": False}}},
            # `gone` is disabled in Auth: not in the roster at all.
        ]
        # The shared test DB may hold other SSO-linked rows: keep them as they are.
        others = {
            u.id: (u.role, u.capabilities)
            for u in db.query(User).filter(User.auth_user_id.is_not(None), User.id.not_in(rows))
        }
        for uid in others:
            other = db.get(User, uid)
            people.append({"id": other.auth_user_id, "is_admin": other.role == "admin",
                           "permissions": {"eventtrakr": capabilities.for_user(other)}})
        try:
            capabilities.apply_roster(db, people)
            assert capabilities.user_can(db.get(User, keeps), capabilities.USE_SOCIAL) is True
            assert capabilities.user_can(db.get(User, revoked), capabilities.USE_SOCIAL) is False
            assert capabilities.user_can(db.get(User, revoked), capabilities.MANAGE_SOURCES) is True
            assert capabilities.for_user(db.get(User, gone)) == ALL_OFF
            assert db.get(User, gone).role == "user"  # roles are left to the next visit
        finally:
            for uid, (role, caps) in others.items():
                other = db.get(User, uid)
                other.role, other.capabilities = role, caps
            db.commit()


def test_mirror_revokes_a_former_admin_who_never_comes_back():
    user = User(username="x", role="admin", capabilities=capabilities.encode(ALL_OFF))
    assert capabilities.for_user(user) == ALL_OFF
    assert capabilities.for_user(User(username="y", role="admin", capabilities=None)) == ALL_ON


def test_refresh_from_auth_ignores_rosters_without_permissions(monkeypatch):
    monkeypatch.setattr(capabilities, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(
        "stonepi_auth.internal.get_internal_json",
        lambda base, secret, path, **kw: {"people": [{"id": "x", "is_admin": False, "phone_alerts": False}]},
    )
    with SessionLocal() as db:
        assert capabilities.refresh_from_auth(db, force=True) is False


def test_refresh_from_auth_mirrors_through_shared_roster(make_client, monkeypatch):
    _, revoked = make_client(perms={"can_use_social": True})
    with SessionLocal() as db:
        target = db.get(User, revoked).auth_user_id
        others = {
            u.id: (u.auth_user_id, u.role, u.capabilities)
            for u in db.query(User).filter(User.auth_user_id.is_not(None), User.id != revoked)
        }
    people = [{"id": target, "is_admin": False, "permissions": {"eventtrakr": {"can_use_social": False}}}]
    for auth_id, role, caps in others.values():
        people.append({"id": auth_id, "is_admin": role == "admin",
                       "permissions": {"eventtrakr": capabilities.parse(caps) or {}}})
    calls: list[str] = []

    def fake(base, secret, path, **kw):
        calls.append(path)
        return {"people": people}

    monkeypatch.setattr(capabilities, "_session_secret", lambda: SECRET)
    monkeypatch.setattr("stonepi_auth.internal.get_internal_json", fake)
    with SessionLocal() as db:
        try:
            assert capabilities.refresh_from_auth(db, force=True) is True
            assert calls == ["/api/internal/people"]
            user = db.get(User, revoked)
            assert capabilities.user_can(user, capabilities.USE_SOCIAL) is False
            assert capabilities.user_can(user, capabilities.MANAGE_SOURCES) is True  # catalog default
            # Cached: no second call inside the TTL.
            assert capabilities.refresh_from_auth(db) is False
            assert calls == ["/api/internal/people"]
            # Auth down: the mirror stays as it was.
            monkeypatch.setattr("stonepi_auth.internal.get_internal_json", lambda *a, **k: None)
            assert capabilities.refresh_from_auth(db, force=True) is False
            assert capabilities.user_can(db.get(User, revoked), capabilities.MANAGE_SOURCES) is True
        finally:
            for uid, (_, role, caps) in others.items():
                other = db.get(User, uid)
                other.role, other.capabilities = role, caps
            db.commit()


def test_scheduled_facebook_source_skipped_without_social_capability(make_client, monkeypatch):
    from app.services import ingest

    monkeypatch.setattr(ingest, "fetch_brightdata_facebook_events", lambda *a, **k: pytest.fail("must not spend Bright Data"))
    _, uid = make_client(perms={})
    src_id = _source(uid, url=f"https://www.facebook.com/events/search/?q={uuid4().hex}")
    with SessionLocal() as db:
        src = db.get(EventSource, src_id)
        assert ingest.fetch_and_extract_source(db, src) == (0, 0)
        assert "Instagram & Facebook" in (src.last_error or "")


def test_public_agenda_hidden_once_owner_loses_share_capability(make_client):
    from app import create_app

    _, uid = make_client(perms={"can_share_agenda": True})
    with SessionLocal() as db:
        user = db.get(User, uid)
        user.is_public = True
        user.favourites_public = True
        db.add(Event(user_id=uid, fingerprint=uuid4().hex, title="Visible gig",
                     start_time=datetime.now(timezone.utc) + timedelta(days=1), is_favourited=True))
        db.commit()
        username = user.username
    anon = create_app().test_client()
    res = anon.get(f"/u/{username}")
    assert res.status_code == 200 and "Visible gig" in res.get_data(as_text=True)
    assert anon.get(f"/u/{username}/favourites").status_code == 200

    with SessionLocal() as db:
        user = db.get(User, uid)
        user.capabilities = json.dumps({**ALL_ON, "can_share_agenda": False})
        db.commit()
    res = anon.get(f"/u/{username}")
    assert res.status_code == 403 and "Visible gig" not in res.get_data(as_text=True)
    assert anon.get(f"/u/{username}/favourites").status_code == 403


def test_calendar_feed_404_without_sync_capability(make_client, monkeypatch):
    from app import create_app

    monkeypatch.setattr("app.routes.calendar.is_public_exposure", lambda: False)
    _, uid = make_client(perms={"can_sync_calendar": False})
    with SessionLocal() as db:
        username = db.get(User, uid).username
    anon = create_app().test_client()
    assert anon.get(f"/calendar/feed/{username}.ics").status_code == 404
    with SessionLocal() as db:
        db.get(User, uid).capabilities = json.dumps(ALL_ON)
        db.commit()
    assert anon.get(f"/calendar/feed/{username}.ics").status_code == 200
