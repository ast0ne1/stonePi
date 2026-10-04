"""Bright Data limits: Instagram allowance, polling hours, usage estimate, reset day."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app.db import SessionLocal
from app.models import Setting, User
from app.services import brightdata
from app.services.auth import create_session_token
from app.services.social import config as social_config
from app.services.social.providers import brightdata_instagram as ig


@pytest.fixture
def restore_settings():
    """These tests share the app database; put every setting back as it was."""
    with SessionLocal() as db:
        before = {row.key: row.value for row in db.query(Setting).all()}
    yield
    with SessionLocal() as db:
        for row in db.query(Setting).all():
            if row.key not in before:
                db.delete(row)
            elif row.value != before[row.key]:
                row.value = before[row.key]
        db.commit()


def _admin_client():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        client.set_cookie("eventtrakr_session", create_session_token(admin.id, admin.username, admin.role))
    return client


def test_polling_hours_window():
    day = ("08:00", "23:00")
    assert social_config.within_active_hours(None, datetime(2026, 10, 3, 3, 0))
    assert social_config.within_active_hours(day, datetime(2026, 10, 3, 8, 0))
    assert not social_config.within_active_hours(day, datetime(2026, 10, 3, 23, 0))
    assert not social_config.within_active_hours(day, datetime(2026, 10, 3, 2, 30))
    night = ("22:00", "02:00")  # crosses midnight
    assert social_config.within_active_hours(night, datetime(2026, 10, 3, 1, 0))
    assert not social_config.within_active_hours(night, datetime(2026, 10, 3, 12, 0))
    assert social_config.active_minutes_per_day(day) == 900
    assert social_config.active_minutes_per_day(night) == 240


def test_set_active_hours_validates_and_clears(restore_settings):
    with SessionLocal() as db:
        social_config.set_active_hours(db, "08:00", "23:00")
        assert social_config.active_hours(db) == ("08:00", "23:00")
        with pytest.raises(ValueError):
            social_config.set_active_hours(db, "8am", "23:00")
        social_config.set_active_hours(db, "", "")
        assert social_config.active_hours(db) is None


def test_estimate_counts_accounts_hours_and_posts():
    assert social_config.checks_per_day({"mode": "interval", "interval_minutes": 60}, 900) == 15
    assert social_config.checks_per_day({"mode": "weekly", "days": ["mon", "thu"], "times": ["09:00"]}, 900) == 2 / 7
    estimate = social_config.estimate_from(
        [None, {"mode": "interval", "interval_minutes": 180}],  # global hourly + one every 3 hours
        global_minutes=60,
        active_minutes=900,  # 08:00-23:00
        posts=5,
        period_days=30,
    )
    assert estimate == {"accounts": 2, "checks": 600, "posts_per_check": 5, "worst_case": 3000}


def test_instagram_allowance_defaults_once_and_blocks_calls():
    brightdata.ensure_instagram_allowance()
    assert brightdata.usage()["use_limits"][brightdata.INSTAGRAM_USE]["limit"] == brightdata.DEFAULT_INSTAGRAM_ALLOWANCE
    brightdata.set_instagram_allowance(5)
    client = MagicMock()
    with patch("app.services.social.providers.brightdata_instagram.httpx.Client", return_value=client):
        with pytest.raises(brightdata.BrightDataBudgetError):
            ig.fetch_recent_posts("k", "venue_a", num_of_posts=10, paced=False)
    client.post.assert_not_called()
    brightdata.set_instagram_allowance(None)  # blank: shares the whole limit
    brightdata.ensure_instagram_allowance()  # the choice sticks
    assert brightdata.INSTAGRAM_USE not in brightdata.usage()["use_limits"]


def test_settings_save_reset_day_hours_and_allowance(restore_settings):
    client = _admin_client()
    html = client.get("/settings?tab=providers").get_data(as_text=True)
    assert "Resets on day of the month" in html and "Instagram allowance" in html and "Polling hours" in html

    client.post("/settings/integrations/limit", data={"brightdata_limit": "5000", "brightdata_reset_day": "15"})
    assert brightdata.usage()["reset_day"] == 15

    client.post(
        "/settings/discovery",
        data={"social_poll_minutes": "120", "social_posts_per_check": "5", "social_active_start": "08:00",
              "social_active_end": "22:00", "social_allowance": "1500"},
    )
    assert brightdata.usage()["use_limits"][brightdata.INSTAGRAM_USE]["limit"] == 1500
    with SessionLocal() as db:
        assert social_config.active_hours(db) == ("08:00", "22:00")
        assert social_config.poll_minutes(db) == 120
