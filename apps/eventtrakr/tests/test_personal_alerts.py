"""EventTrakr personal alerts: events go to the owner's Auth id; bell + Settings card."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db import SessionLocal, init_db
from app.models import Event, User
from app.services import notify as notify_service
from app.services import settings as settings_service
from app.services.social import config as social_config

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


@pytest.fixture
def sent(monkeypatch):
    events: list = []
    monkeypatch.setattr("stonepi_contracts.emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


@pytest.fixture
def people():
    """One platform-linked local user and one local-only user (standalone)."""
    init_db()
    auth_id = str(uuid4())
    tag = uuid4().hex[:8]
    with SessionLocal() as db:
        linked = User(username=f"alerts-linked-{tag}", password="", role="user", auth_user_id=auth_id)
        local = User(username=f"alerts-local-{tag}", password="", role="user")
        db.add_all([linked, local])
        db.commit()
        ids = {"linked": linked.id, "local": local.id, "auth": auth_id}
    yield ids
    with SessionLocal() as db:
        db.query(Event).filter(Event.user_id.in_([ids["linked"], ids["local"]])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["linked"], ids["local"]])).delete(synchronize_session=False)
        db.commit()


def _assert_no_local_ids(event: dict, *local_ids: int) -> None:
    assert event["user"] not in {"local", ""}
    assert not str(event["user"]).isdigit()
    for key in ("user_id", "local_user_id", "account_user_id"):
        assert key not in event["data"]
    assert all(v not in local_ids for v in event["data"].values() if isinstance(v, int))


def test_owner_auth_id_resolves_linked_users_only(people):
    with SessionLocal() as db:
        assert notify_service.owner_auth_id(db, people["linked"]) == people["auth"]
        assert notify_service.owner_auth_id(db, people["local"]) is None
        assert notify_service.owner_auth_id(db, None) is None


def test_event_approaching_is_personal_to_favourite_owner(sent, people):
    now = datetime(2031, 3, 4, 12, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        settings_service.set_value(db, "notify_approaching_minutes", "30")
        settings_service.set_value(db, notify_service.SENT_KEY, "{}")
        for who in ("linked", "local"):
            db.add(
                Event(
                    user_id=people[who],
                    fingerprint=f"alerts-{who}-{uuid4().hex[:8]}",
                    title=f"Alerts {who} gig",
                    description="",
                    start_time=now + timedelta(minutes=15),
                    location="Vega",
                    cost="Free",
                    category="Music",
                    url="",
                    is_favourited=True,
                )
            )
        db.commit()
        notify_service.check_approaching_favourites(db, now=now)
        settings_service.set_value(db, notify_service.SENT_KEY, "{}")

    mine = [e for e in sent if e["title"].startswith("Alerts ")]
    assert [e["title"] for e in mine] == ["Alerts linked gig"]  # local-only owner is never sent
    event = mine[0]
    assert event["id"] == "eventtrakr.event_approaching"
    assert event["audience"] == "personal"
    assert event["user"] == people["auth"]
    _assert_no_local_ids(event, people["linked"], people["local"])


@pytest.mark.parametrize(
    ("kind", "event_id"),
    [("discovered", "eventtrakr.social_discovered"), ("updated", "eventtrakr.social_event_updated")],
)
def test_social_events_are_personal_to_account_owner(sent, people, kind, event_id):
    result = {"notify": True, "notify_kind": kind, "title": "DJ Anna", "event_id": 7, "update_kind": "time"}
    with SessionLocal() as db:
        social_config.set_notify_enabled(db, True)
        assert notify_service.notify_from_process_result(
            db, result, username="theglobe", account_user_id=people["linked"]
        ) is True
    event = sent[0]
    assert event["id"] == event_id
    assert event["audience"] == "personal"
    assert event["user"] == people["auth"]
    _assert_no_local_ids(event, people["linked"], people["local"])


@pytest.mark.parametrize("owner_key", ["local", None])
def test_social_event_without_platform_owner_is_not_sent(sent, people, owner_key):
    result = {"notify": True, "notify_kind": "discovered", "title": "DJ Anna", "event_id": 7}
    with SessionLocal() as db:
        social_config.set_notify_enabled(db, True)
        owner = people[owner_key] if owner_key else None
        assert notify_service.notify_from_process_result(db, result, account_user_id=owner) is False
    assert sent == []


@pytest.mark.parametrize("user", ["local", "", None, "12", 12])
def test_emitters_reject_local_owners(sent, user):
    assert notify_service.emit_event_approaching(user=user, title="T", summary="S", event_key="1") is False
    assert notify_service.emit_social_discovered(user=user, title="T", summary="S", discovery_key="1") is False
    assert notify_service.emit_social_event_updated(user=user, title="T", summary="S", update_key="1") is False
    assert sent == []


# -- pages -----------------------------------------------------------------------

SECRET = "et-test-secret"


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from app import create_app
    from stonepi_auth.session import COOKIE_NAME, encode_session

    monkeypatch.setattr("app.services.auth._platform_session_secret", lambda: SECRET)
    monkeypatch.setattr("app.config.env.stonepi_session_secret", SECRET)
    from stonepi_auth.header import header_context

    monkeypatch.setattr(
        app_module,
        "bell_context",
        lambda user, **kw: {
            "show": bool(user), "dot": True, "url": "/notifications", "panel": None,
            "header": header_context(kw["app_id"], user, home_url="http://stonepi.test"),
        },
    )
    auth_id = str(uuid4())
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    c = flask_app.test_client()
    c.set_cookie(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=auth_id, username=f"jo-{auth_id[:8]}", display_name="Jo", is_admin=True,
            apps=["eventtrakr"], session_id="s", phone_alerts=True,
        ),
    )
    yield c
    with SessionLocal() as db:
        db.query(User).filter(User.auth_user_id == auth_id).delete(synchronize_session=False)
        db.commit()


def test_settings_notifications_tab_uses_shared_card(client):
    html = client.get("/settings?tab=notifications").get_data(as_text=True)
    assert "Manage personal alerts" in html
    assert "Favourite event starting" in html
    assert "alerts#choose" in html  # admin extras live in the card
    assert 'name="notify_approaching_minutes"' in html  # lead time stays below the card
    assert html.find("Manage personal alerts") < html.find('name="notify_approaching_minutes"')


def test_shared_header_order_and_bottom_bar(client):
    html = client.get("/settings?tab=general").get_data(as_text=True)
    search = html.find('aria-label="Search"')
    refresh = html.find("data-sync-btn")
    bell = html.find('class="icon-btn sp-bell has-dot"')
    switcher = html.find('aria-label="Switch app"')
    avatar = html.find('class="sp-trigger sp-avatar"')
    assert 0 < search < refresh < bell < switcher < avatar
    assert "Log out" in html and 'class="icon-btn sign-out"' not in html
    assert 'href="http://stonepi.test/"' in html  # Dashboard, top of the switcher
    nav = html[html.find('<nav class="nav"'):]
    nav = nav[: nav.find("</nav>")]
    assert [label for label in ("Home", "Agenda", "Faves", "Search", "Add", "Find", "Review", "Sources", "Settings") if f"<span>{label}</span>" in nav] == [
        "Agenda", "Faves", "Review", "Settings",
    ]


def test_add_is_a_floating_button_on_agenda_only(client):
    assert 'class="sp-fab" href="/add-event"' in client.get("/agenda").get_data(as_text=True)
    assert 'class="sp-fab"' not in client.get("/settings").get_data(as_text=True)


def test_settings_hub_opens_on_sources(client):
    html = client.get("/settings").get_data(as_text=True)
    assert html.find('href="/sources"') < html.find('href="/settings?tab=privacy"')
    assert 'href="/sources/social"' in html


def test_review_badge_counts_open_items_and_drops_on_reject(client):
    """Writes to the shared EventTrakr database like the rest of this suite, so it removes its rows."""
    import re

    from app.models import EventDiscovery, SocialAccount, SocialPost, utcnow

    client.get("/agenda")  # creates the signed-in person's local user
    with SessionLocal() as db:
        local = db.query(User).order_by(User.id.desc()).first()
        account = SocialAccount(user_id=local.id, platform="instagram", username=f"v{uuid4().hex[:8]}",
                                created_at=utcnow(), updated_at=utcnow())
        db.add(account)
        db.flush()
        account_id = account.id
        ids = []
        for i in range(3):
            post = SocialPost(social_account_id=account.id, external_post_id=uuid4().hex, retrieved_at=utcnow())
            db.add(post)
            db.flush()
            disc = EventDiscovery(user_id=local.id, social_post_id=post.id, social_account_id=account.id, title=f"Gig {i}")
            db.add(disc)
            db.flush()
            ids.append(disc.id)
        db.commit()

    def badge(html: str) -> int:
        found = re.search(r'class="nav-badge"[^>]*>(\d+)<', html)
        return int(found.group(1)) if found else 0

    try:
        before = badge(client.get("/agenda").get_data(as_text=True))
        assert before >= 3
        page = client.post(f"/discoveries/{ids[0]}/ignore", follow_redirects=True).get_data(as_text=True)
        assert badge(page) == before - 1
    finally:
        with SessionLocal() as db:
            db.query(EventDiscovery).filter(EventDiscovery.id.in_(ids)).delete(synchronize_session=False)
            db.query(SocialPost).filter(SocialPost.social_account_id == account_id).delete(synchronize_session=False)
            db.query(SocialAccount).filter(SocialAccount.id == account_id).delete(synchronize_session=False)
            db.commit()
