from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import EventSource
from app.services import ingest, settings as settings_service
from app.services.ingest import is_ics_source
from app.services.scrapers.browser_fetch import browser_session
from app.services.settings import WEEKDAY_CODES

logger = logging.getLogger("eventtrakr.schedule")
_scheduler: BackgroundScheduler | None = None

# Fixed background tick; the actual per-source cadence is decided by
# source_interval_minutes() / the global schedule below, so this only needs to
# be fine-grained enough to service the shortest allowed custom interval and
# to notice a "specific time of day" slot soon after it passes.
TICK_MINUTES = 5


def source_interval_minutes(source: EventSource, global_minutes: int) -> int:
    if source.schedule_mode == "custom" and source.interval_minutes and source.interval_minutes > 0:
        return max(15, source.interval_minutes)
    return max(15, global_minutes)


def _last_weekly_trigger(days: list[str], times: list[str], now_local: datetime) -> datetime | None:
    """Most recent (day, time) slot that has already passed, scanning back up
    to a week so a missed slot (e.g. app was offline) is still picked up."""
    candidates: list[datetime] = []
    for offset in range(8):
        day = now_local - timedelta(days=offset)
        if WEEKDAY_CODES[day.weekday()] not in days:
            continue
        for raw_time in times:
            try:
                hh, mm = (int(part) for part in raw_time.split(":"))
            except ValueError:
                continue
            candidate = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now_local:
                candidates.append(candidate)
    return max(candidates) if candidates else None


def _is_due(source: EventSource, global_schedule: dict, now_utc: datetime, now_local: datetime) -> bool:
    if source.schedule_mode == "custom":
        interval = source_interval_minutes(source, global_schedule.get("interval_minutes", 60))
        if not source.last_fetched_at:
            return True
        elapsed_minutes = (now_utc - source.last_fetched_at).total_seconds() / 60
        return elapsed_minutes >= interval

    if global_schedule.get("mode") == "weekly":
        last_trigger = _last_weekly_trigger(global_schedule["days"], global_schedule["times"], now_local)
        if last_trigger is None:
            return False
        if not source.last_fetched_at:
            return True
        return source.last_fetched_at.astimezone(now_local.tzinfo) < last_trigger

    if not source.last_fetched_at:
        return True
    interval = max(15, global_schedule.get("interval_minutes", 60))
    elapsed_minutes = (now_utc - source.last_fetched_at).total_seconds() / 60
    return elapsed_minutes >= interval


def _scheduled_tick() -> None:
    """Runs every TICK_MINUTES. Syncs only sources that are due, based on
    their effective schedule (global default, or their own custom interval)."""
    if ingest.state.running:
        return

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()
    try:
        with SessionLocal() as db:
            global_schedule = settings_service.get_global_schedule(db, env.default_sync_interval_minutes)
            sources = list(db.execute(select(EventSource).where(EventSource.enabled == True)).scalars())
            due_sources = [s for s in sources if _is_due(s, global_schedule, now_utc, now_local)]
            if not due_sources:
                return

            logger.info("Scheduled tick: syncing %d/%d due sources", len(due_sources), len(sources))
            needs_browser = any(not is_ics_source(s) for s in due_sources)
            with browser_session() if needs_browser else nullcontext() as browser:
                for src in due_sources:
                    try:
                        ingest.fetch_and_extract_source(db, src, browser=browser)
                    except Exception:
                        logger.exception("Scheduled sync failed for source %s", src.id)

            ingest.purge_expired_events(db)
    except Exception:
        logger.exception("Scheduled tick failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        func=_scheduled_tick,
        trigger="interval",
        minutes=TICK_MINUTES,
        id="periodic_sync",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started, checking for due sources every %d minutes", TICK_MINUTES)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
