from datetime import datetime, timedelta, timezone

from app.models import Feed


def feed_interval_minutes(feed: Feed, global_minutes: int) -> int:
    if getattr(feed, "schedule_mode", "global") == "custom":
        custom = getattr(feed, "interval_minutes", None)
        if custom and custom > 0:
            return custom
    return max(1, global_minutes)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def normalize_optional_clock(value: str | None) -> str:
    raw = (value or "").strip().replace(".", ":")
    if not raw:
        return ""
    parts = raw.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return ""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def _minutes_since_midnight(value: str) -> int | None:
    clock = normalize_optional_clock(value)
    if not clock:
        return None
    hour, minute = (int(part) for part in clock.split(":"))
    return hour * 60 + minute


def within_active_hours(
    start: str | None,
    end: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True when local time is inside [start, end). Blank either side means all day."""
    start_m = _minutes_since_midnight(start or "")
    end_m = _minutes_since_midnight(end or "")
    if start_m is None or end_m is None:
        return True
    when = now or datetime.now()
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    now_m = when.hour * 60 + when.minute
    if start_m == end_m:
        return True
    if start_m < end_m:
        return start_m <= now_m < end_m
    return now_m >= start_m or now_m < end_m


def feed_uses_global_schedule(feed: Feed) -> bool:
    return getattr(feed, "schedule_mode", "global") != "custom"


# The paper only holds stories published today, so every source gets a fetch this
# long before publish time if it hasn't had one since midnight.
PAPER_PREP_LEAD_MINUTES = 30
DAILY_MINUTES = 1440


def paper_prep_clock(publish_at: str | None) -> str:
    publish_m = _minutes_since_midnight(publish_at or "")
    if publish_m is None:
        return ""
    prep_m = max(0, publish_m - PAPER_PREP_LEAD_MINUTES)
    return f"{prep_m // 60:02d}:{prep_m % 60:02d}"


def _local_naive(value: datetime) -> datetime:
    """Wall-clock time on this machine; naive values are taken as already local."""
    return value.astimezone().replace(tzinfo=None) if value.tzinfo is not None else value


def _today_at(clock: str, local_now: datetime) -> datetime | None:
    minutes = _minutes_since_midnight(clock)
    if minutes is None:
        return None
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=minutes)


def _missed_daily_clock(last_local: datetime | None, clock: str, local_now: datetime) -> bool:
    """True once today's clock time has passed and the last fetch was before it."""
    at = _today_at(clock, local_now)
    if at is None or local_now < at:
        return False
    return last_local is None or last_local < at


def _needs_paper_prep(last_local: datetime | None, prep_at: str, local_now: datetime) -> bool:
    """Past today's prep time with no fetch since midnight: the paper would have nothing new."""
    at = _today_at(prep_at, local_now)
    if at is None or local_now < at:
        return False
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return last_local is None or last_local < midnight


def feed_is_due(
    feed: Feed,
    global_minutes: int,
    now: datetime | None = None,
    *,
    active_start: str | None = None,
    active_end: str | None = None,
    local_now: datetime | None = None,
    daily_at: str | None = None,
    prep_at: str | None = None,
) -> bool:
    if daily_at or prep_at:
        local = _local_naive(local_now or now or datetime.now().astimezone())
        last = _aware(feed.last_fetched_at)
        last_local = _local_naive(last) if last is not None else None
        # Paper prep ignores active hours and custom intervals: the paper needs every source.
        if prep_at and _needs_paper_prep(last_local, prep_at, local):
            return True
        # "Once a day at HH:MM" instead of a rolling 24 hours that drifts later each day.
        if daily_at and feed_uses_global_schedule(feed) and global_minutes >= DAILY_MINUTES:
            return _missed_daily_clock(last_local, daily_at, local)
    if feed_uses_global_schedule(feed) and not within_active_hours(
        active_start,
        active_end,
        now=local_now,
    ):
        return False
    # A given local_now is the clock for the interval too (naive = this machine's local time).
    now = now or (local_now.astimezone().astimezone(timezone.utc) if local_now else datetime.now(timezone.utc))
    last = _aware(feed.last_fetched_at)
    if last is None:
        return True
    interval = timedelta(minutes=feed_interval_minutes(feed, global_minutes))
    return now - last >= interval


def feed_is_muted(feed: Feed, now: datetime | None = None) -> bool:
    until = _aware(getattr(feed, "muted_until", None))
    if until is None:
        return False
    when = now or datetime.now(timezone.utc)
    return when < until
