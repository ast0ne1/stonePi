from datetime import datetime, timedelta, timezone
from app import create_app
from app.db import SessionLocal
from app.models import Event, EventSource, User
from app.services.auth import create_session_token


def test_app_routes():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    # 1. Unauthenticated redirect to login
    res = client.get("/")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]

    # 2. Login view renders
    res = client.get("/login")
    assert res.status_code == 200
    assert b"EventTrakr" in res.data
    assert b"Sign in" in res.data

    # 3. Simulate authenticated admin user
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        token = create_session_token(admin.id, admin.username, admin.role)

    client.set_cookie("eventtrakr_session", token)

    # 4. Authenticated Agenda view
    res = client.get("/agenda")
    assert res.status_code == 200
    assert b"7-Day Agenda" in res.data

    # 5. Search view
    res = client.get("/search")
    assert res.status_code == 200
    assert b"Search Events" in res.data

    # 6. Sources view (showing catalog and configured sources)
    res = client.get("/sources")
    assert res.status_code == 200
    assert b"Curated Catalog of Sources" in res.data
    assert b"Kultunaut Copenhagen" in res.data

    # 7. Settings view
    res = client.get("/settings")
    assert res.status_code == 200
    assert b"Appearance" in res.data
    assert b"Default" in res.data
    assert b"Ocean" in res.data
    assert b"Forest" in res.data
    assert b"Slate" in res.data


def test_favourite_api_and_public_agenda():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        admin.is_public = True  # Enable public overview

        # Clean up if already exists from prior run
        db.query(Event).filter_by(user_id=admin.id, fingerprint="test-fp-123456").delete()
        db.commit()

        # Create a test event for tomorrow
        ev = Event(
            user_id=admin.id,
            fingerprint="test-fp-123456",
            title="Design System Meetup",
            description="Talking about dark modes and palettes.",
            start_time=now + timedelta(days=1),
            location="Soho, London",
            cost="Free",
            category="Design",
            is_favourited=False,
        )
        db.add(ev)
        db.commit()
        ev_id = ev.id
        token = create_session_token(admin.id, admin.username, admin.role)

    client.set_cookie("eventtrakr_session", token)

    # Toggle favourite via API
    res = client.post(f"/api/events/{ev_id}/favourite")
    assert res.status_code == 200
    data = res.get_json()
    assert data["favourited"] is True

    # Check that it appears in /favourites
    res = client.get("/favourites")
    assert res.status_code == 200
    assert b"Design System Meetup" in res.data

    # Check public agenda /u/<username> without cookies
    public_client = app.test_client()
    res = public_client.get(f"/u/{admin.username}")
    assert res.status_code == 200
    assert b"Design System Meetup" in res.data
