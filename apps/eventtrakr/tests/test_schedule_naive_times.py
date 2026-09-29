"""Last-fetched times read back from SQLite have no tzinfo; they are UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.schedule import is_due_for_config

CEST = timezone(timedelta(hours=2))
NOW_UTC = datetime(2026, 9, 28, 4, 40, tzinfo=timezone.utc)  # 06:40 CEST
NOW_LOCAL = NOW_UTC.astimezone(CEST)


def test_interval_with_naive_last_time_does_not_crash():
    config = {"mode": "interval", "interval_minutes": 60}
    thirty_min_ago = (NOW_UTC - timedelta(minutes=30)).replace(tzinfo=None)
    two_hours_ago = (NOW_UTC - timedelta(hours=2)).replace(tzinfo=None)
    assert is_due_for_config(config, thirty_min_ago, NOW_UTC, NOW_LOCAL) is False
    assert is_due_for_config(config, two_hours_ago, NOW_UTC, NOW_LOCAL) is True


def test_naive_and_aware_last_times_agree():
    config = {"mode": "interval", "interval_minutes": 60}
    last = NOW_UTC - timedelta(minutes=45)
    assert is_due_for_config(config, last, NOW_UTC, NOW_LOCAL) == is_due_for_config(
        config, last.replace(tzinfo=None), NOW_UTC, NOW_LOCAL
    )


def test_weekly_treats_naive_last_time_as_utc_not_local():
    # Trigger at 06:00 local (04:00 UTC). Fetched at 05:30 local = 03:30 UTC, i.e. before the trigger.
    from app.services.settings import WEEKDAY_CODES

    day = WEEKDAY_CODES[NOW_LOCAL.weekday()]
    config = {"mode": "weekly", "days": [day], "times": ["06:00"]}
    fetched_utc_naive = datetime(2026, 9, 28, 3, 30)
    assert is_due_for_config(config, fetched_utc_naive, NOW_UTC, NOW_LOCAL) is True
    # Fetched at 06:15 local = 04:15 UTC, after the trigger: not due again.
    assert is_due_for_config(config, datetime(2026, 9, 28, 4, 15), NOW_UTC, NOW_LOCAL) is False
