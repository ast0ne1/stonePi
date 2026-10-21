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
    assert b'id="catalog-sources"' in res.data
    assert b'<h2 class="section-label">Catalog</h2>' in res.data
    assert b"Kultunaut Copenhagen" in res.data

    # 7. Settings view
    res = client.get("/settings")
    assert res.status_code == 200
    assert b"data-settings-hub-list" in res.data
    assert b'data-settings-panel="general"' in res.data
    # Palette moved to the portal; General points there instead of an in-app picker.
    assert "Settings → General".encode() in res.data
    assert b"avatar menu" in res.data


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


def test_brightdata_limit_shows_and_saves():
    from app.services import brightdata

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        token = create_session_token(admin.id, admin.username, admin.role)
    client.set_cookie("eventtrakr_session", token)

    res = client.get("/settings?tab=providers")
    assert res.status_code == 200
    assert b"Monthly limit (records, all apps)" in res.data
    assert b'value="5000"' in res.data

    res = client.post("/settings/integrations/limit", data={"brightdata_limit": "3000"})
    assert res.status_code == 302
    assert brightdata.usage()["limit"] == 3000


def test_agenda_view_hides_filter_rows_and_their_filters():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        db.query(Event).filter_by(user_id=admin.id, fingerprint="test-fp-agenda-view").delete()
        db.add(Event(user_id=admin.id, fingerprint="test-fp-agenda-view", title="Paid Jazz Night",
                     start_time=now + timedelta(days=1), location="Copenhagen", cost="DKK 150", category="Music"))
        db.commit()
        token = create_session_token(admin.id, admin.username, admin.role)
    client.set_cookie("eventtrakr_session", token)

    html = client.get("/agenda").get_data(as_text=True)
    assert 'data-filter-key="category"' in html and "<span>Free</span>" in html
    assert 'id="agenda-view"' in client.get("/settings?tab=general").get_data(as_text=True)

    # Categories off, Free on: the row stays with only the Free chip, no category sheet.
    assert client.post("/settings/agenda-view", data={"show_free": "1", "show_source": "1"}).status_code == 302
    html = client.get("/agenda?category=Nope").get_data(as_text=True)
    assert "<span>Free</span>" in html and 'data-sheet="filters"' not in html
    assert "Paid Jazz Night" in html  # a hidden category filter no longer filters

    # Both off: the whole row goes, and ?free=1 is ignored.
    client.post("/settings/agenda-view", data={"show_source": "1"})
    html = client.get("/agenda?free=1").get_data(as_text=True)
    assert 'data-filter-key="category"' not in html and "Paid Jazz Night" in html

    client.post("/settings/agenda-view", data={"show_category": "1", "show_free": "1", "show_source": "1"})


def test_price_pill_shows_amount_only_when_paid():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        for fp, title, cost in (("test-fp-pill-paid", "Pill Paid", "DKK 150"), ("test-fp-pill-free", "Pill Free", "Free"),
                                ("test-fp-pill-none", "Pill None", "Unspecified")):
            db.query(Event).filter_by(user_id=admin.id, fingerprint=fp).delete()
            db.add(Event(user_id=admin.id, fingerprint=fp, title=title, start_time=now + timedelta(days=1),
                         location="Copenhagen", cost=cost, category="Music"))
        db.commit()
        token = create_session_token(admin.id, admin.username, admin.role)
    client.set_cookie("eventtrakr_session", token)
    html = client.get("/agenda").get_data(as_text=True)
    assert 'cost-pill cost-paid" title="Price: DKK 150"' in html and '<span aria-hidden="true">DKK 150</span>' in html
    assert 'cost-pill cost-free is-icon-only" title="Free"' in html
    assert 'cost-pill cost-unspecified is-icon-only" title="Price not listed"' in html
