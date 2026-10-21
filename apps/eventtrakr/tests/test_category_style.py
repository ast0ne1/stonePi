from datetime import datetime, timedelta, timezone

from app import create_app
from app.db import SessionLocal
from app.models import Category, Event, User
from app.services.auth import create_session_token
from app.services.category_style import category_style


def test_builtin_categories_and_aliases_get_their_own_icon_and_tint():
    assert category_style("Music", chosen={}) == {"icon": "music", "tint": "amethyst"}
    assert category_style("Food & Drink", chosen={}) == {"icon": "cup", "tint": "rust"}
    assert category_style("Art", chosen={}) == category_style("Arts", chosen={}) == {"icon": "palette", "tint": "moss"}


def test_custom_categories_get_a_tag_and_a_stable_tint():
    first = category_style("Wellness", chosen={})
    assert first["icon"] == "tag" and first == category_style("wellness ", chosen={})
    assert category_style(None, chosen={}) == category_style("", chosen={})


def test_chosen_colour_wins_and_keeps_the_icon():
    assert category_style("Music", chosen={"music": "rust"}) == {"icon": "music", "tint": "rust"}
    assert category_style("Art", chosen={"arts": "sky"})["tint"] == "sky"  # alias follows its category


def test_admin_picks_a_category_colour_and_pills_follow():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        db.query(Event).filter_by(user_id=admin.id, fingerprint="test-fp-cat-colour").delete()
        db.add(Event(user_id=admin.id, fingerprint="test-fp-cat-colour", title="Colour gig", location="Copenhagen",
                     start_time=datetime.now(timezone.utc) + timedelta(days=1), cost="Free", category="Music"))
        db.commit()
        token = create_session_token(admin.id, admin.username, admin.role)
    client.set_cookie("eventtrakr_session", token)

    html = client.get("/settings?tab=categories").get_data(as_text=True)
    assert 'action="/settings/categories/music/color"' in html and 'value="ember"' in html

    assert client.post("/settings/categories/music/color", data={"color": "rust"}).status_code == 302
    assert "badge category tint-rust" in client.get("/agenda").get_data(as_text=True)
    assert client.post("/settings/categories/music/color", data={"color": "neon"}).status_code == 302
    with SessionLocal() as db:
        assert db.query(Category).filter_by(key="music").one().color == "rust"  # unknown colour ignored

    client.post("/settings/categories/music/color", data={"color": ""})  # back to Auto
    assert "badge category tint-amethyst" in client.get("/agenda").get_data(as_text=True)
