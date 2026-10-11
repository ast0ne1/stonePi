from datetime import datetime, timedelta, timezone

from app.services.schedule import (
    feed_interval_minutes,
    feed_is_due,
    normalize_optional_clock,
    paper_prep_clock,
    within_active_hours,
)
from app.services.settings import format_interval_short


class FakeFeed:
    def __init__(self, schedule_mode="global", interval_minutes=None, last_fetched_at=None):
        self.schedule_mode = schedule_mode
        self.interval_minutes = interval_minutes
        self.last_fetched_at = last_fetched_at


def test_format_interval_uses_hours_over_one_hour():
    assert format_interval_short(15) == "15 min"
    assert format_interval_short(60) == "1 hr"
    assert format_interval_short(120) == "2 hrs"
    assert format_interval_short(1440) == "24 hrs"


def test_custom_interval_wins():
    feed = FakeFeed(schedule_mode="custom", interval_minutes=15)
    assert feed_interval_minutes(feed, 60) == 15


def test_global_interval_used_by_default():
    feed = FakeFeed()
    assert feed_interval_minutes(feed, 60) == 60


def test_feed_due_when_never_fetched():
    assert feed_is_due(FakeFeed(), 60)


def test_feed_not_due_inside_window():
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    feed = FakeFeed(last_fetched_at=now - timedelta(minutes=10))
    assert not feed_is_due(feed, 60, now=now)


def test_custom_feed_due_after_its_interval():
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    feed = FakeFeed(
        schedule_mode="custom",
        interval_minutes=15,
        last_fetched_at=now - timedelta(minutes=16),
    )
    assert feed_is_due(feed, 60, now=now)


def test_normalize_optional_clock():
    assert normalize_optional_clock("") == ""
    assert normalize_optional_clock("9:00") == "09:00"
    assert normalize_optional_clock("12:30") == "12:30"
    assert normalize_optional_clock("25:00") == ""


def test_within_active_hours_blank_is_all_day():
    noon = datetime(2026, 9, 14, 12, 0)
    assert within_active_hours("", "", now=noon)
    assert within_active_hours("09:00", "", now=noon)
    assert within_active_hours("", "12:00", now=noon)


def test_within_active_hours_same_day_window():
    assert within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 9, 0))
    assert within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 11, 59))
    assert not within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 12, 0))
    assert not within_active_hours("09:00", "12:00", now=datetime(2026, 9, 14, 8, 59))


def test_within_active_hours_overnight():
    assert within_active_hours("22:00", "06:00", now=datetime(2026, 9, 14, 23, 0))
    assert within_active_hours("22:00", "06:00", now=datetime(2026, 9, 14, 5, 0))
    assert not within_active_hours("22:00", "06:00", now=datetime(2026, 9, 14, 12, 0))


def test_global_feed_respects_active_hours():
    utc_now = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    feed = FakeFeed(last_fetched_at=utc_now - timedelta(hours=2))
    assert feed_is_due(
        feed,
        60,
        now=utc_now,
        active_start="09:00",
        active_end="12:00",
        local_now=datetime(2026, 9, 14, 10, 0),
    )
    assert not feed_is_due(
        feed,
        60,
        now=utc_now,
        active_start="09:00",
        active_end="12:00",
        local_now=datetime(2026, 9, 14, 13, 0),
    )


def test_custom_feed_ignores_active_hours():
    utc_now = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    feed = FakeFeed(
        schedule_mode="custom",
        interval_minutes=15,
        last_fetched_at=utc_now - timedelta(minutes=20),
    )
    assert feed_is_due(
        feed,
        60,
        now=utc_now,
        active_start="09:00",
        active_end="12:00",
        local_now=datetime(2026, 9, 14, 3, 0),
    )


def _fetched(local_naive: datetime) -> datetime:
    """Stored like the app stores it: UTC, from a local wall-clock time."""
    return local_naive.astimezone().astimezone(timezone.utc)


def test_paper_prep_clock_is_thirty_minutes_before_publish():
    assert paper_prep_clock("06:30") == "06:00"
    assert paper_prep_clock("00:10") == "00:00"  # never wraps to the previous day
    assert paper_prep_clock("") == ""


def test_paper_prep_fetches_before_publish_despite_daily_interval():
    # 2026-10-10: once-a-day refresh last ran 07:02 yesterday, paper at 06:30.
    feed = FakeFeed(last_fetched_at=_fetched(datetime(2026, 10, 9, 7, 2)))
    before = datetime(2026, 10, 10, 5, 57)
    at_prep = datetime(2026, 10, 10, 6, 2)
    assert not feed_is_due(feed, 1440, _fetched(before), local_now=before, prep_at="06:00")
    assert feed_is_due(feed, 1440, _fetched(at_prep), local_now=at_prep, prep_at="06:00")


def test_paper_prep_skips_sources_already_fetched_today():
    feed = FakeFeed(last_fetched_at=_fetched(datetime(2026, 10, 10, 5, 2)))
    assert not feed_is_due(feed, 1440, local_now=datetime(2026, 10, 10, 6, 2), prep_at="06:00")


def test_paper_prep_covers_custom_sources_and_ignores_active_hours():
    feed = FakeFeed(schedule_mode="custom", interval_minutes=1440, last_fetched_at=_fetched(datetime(2026, 10, 9, 9, 0)))
    assert feed_is_due(
        feed, 60, local_now=datetime(2026, 10, 10, 6, 2), prep_at="06:00", active_start="09:00", active_end="17:00"
    )


def test_manual_refresh_yesterday_does_not_delay_the_paper():
    # Refresh tapped at 09:00 yesterday restarts the 24 hours, but prep still runs at 06:00.
    feed = FakeFeed(last_fetched_at=_fetched(datetime(2026, 10, 9, 9, 0)))
    assert feed_is_due(feed, 1440, local_now=datetime(2026, 10, 10, 6, 2), prep_at="06:00")


def test_daily_refresh_time_anchors_once_a_day():
    feed = FakeFeed(last_fetched_at=_fetched(datetime(2026, 10, 9, 5, 4)))
    assert not feed_is_due(feed, 1440, local_now=datetime(2026, 10, 10, 4, 57), daily_at="05:00")
    assert feed_is_due(feed, 1440, local_now=datetime(2026, 10, 10, 5, 2), daily_at="05:00")
    # Already fetched after today's 05:00: not due again until tomorrow, even 24h+ later in the day.
    feed.last_fetched_at = _fetched(datetime(2026, 10, 10, 5, 2))
    assert not feed_is_due(feed, 1440, local_now=datetime(2026, 10, 10, 23, 0), daily_at="05:00")


def test_daily_refresh_time_does_not_drift_after_a_late_fetch():
    # Yesterday's fetch ran late (09:00); today still refreshes at 05:00, not 09:00.
    feed = FakeFeed(last_fetched_at=_fetched(datetime(2026, 10, 9, 9, 0)))
    assert feed_is_due(feed, 1440, local_now=datetime(2026, 10, 10, 5, 2), daily_at="05:00")


def test_daily_refresh_time_only_applies_to_once_a_day():
    feed = FakeFeed(last_fetched_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    assert not feed_is_due(feed, 60, daily_at="05:00")
