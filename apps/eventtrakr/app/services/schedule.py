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
    custom = settings_service.get_source_schedule(source, global_minutes)
    if custom and custom.get("mode") == "interval":
        return max(15, custom["interval_minutes"])
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


def _is_due_for_config(config: dict, last_fetched_at: datetime | None, now_utc: datetime, now_local: datetime) -> bool:
    if last_fetched_at is not None and last_fetched_at.tzinfo is None:
        # Stored as UTC; SQLite hands DateTime(timezone=True) back without tzinfo.
        last_fetched_at = last_fetched_at.replace(tzinfo=timezone.utc)
    if config.get("mode") == "weekly":
        last_trigger = _last_weekly_trigger(config["days"], config["times"], now_local)
        if last_trigger is None:
            return False
        if not last_fetched_at:
            return True
        return last_fetched_at.astimezone(now_local.tzinfo) < last_trigger

    if not last_fetched_at:
        return True
    interval = max(15, config.get("interval_minutes", 60))
    elapsed_minutes = (now_utc - last_fetched_at).total_seconds() / 60
    return elapsed_minutes >= interval


def is_due_for_config(config: dict, last_at: datetime | None, now_utc: datetime, now_local: datetime) -> bool:
    """Public wrapper — used by URL sources and Instagram social accounts."""
    return _is_due_for_config(config, last_at, now_utc, now_local)


def _is_due(source: EventSource, global_schedule: dict, now_utc: datetime, now_local: datetime) -> bool:
    custom = settings_service.get_source_schedule(source, global_schedule.get("interval_minutes", 60))
    if custom is not None:
        return _is_due_for_config(custom, source.last_fetched_at, now_utc, now_local)
    return _is_due_for_config(global_schedule, source.last_fetched_at, now_utc, now_local)

def _scheduled_tick() -> None:
    """Runs every TICK_MINUTES. Syncs due sources, social accounts, and approaching favourites."""
    if ingest.state.running:
        return

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()
    try:
        with SessionLocal() as db:
            from app.services import notify as notify_service

            try:
                notify_service.check_approaching_favourites(db, now=now_utc)
            except Exception:
                logger.exception("Approaching-favourites check failed")

            # Pick up capability changes for people who haven't opened EventTrakr
            # since (cached; a no-op without StonePi SSO or when Auth is down).
            try:
                from app.services import capabilities

                capabilities.refresh_from_auth(db)
            except Exception:
                logger.debug("Capability roster refresh failed", exc_info=True)

            # Social poll runs even when no URL sources are due; isolate errors
            # so a Bright Data failure cannot break source sync.
            try:
                from app.services.social import poll as social_poll
                from app.services.social import discover as social_discover

                if not social_poll.is_running():
                    social_result = social_poll.poll_due_accounts(db)
                    for account_result in social_result.get("results") or []:
                        username = account_result.get("username") or ""
                        account_user_id = account_result.get("user_id")
                        for item in account_result.get("results") or []:
                            try:
                                notify_service.notify_from_process_result(
                                    db, item, username=username, account_user_id=account_user_id
                                )
                            except Exception:
                                logger.debug("Social notify failed", exc_info=True)
                    try:
                        social_discover.expire_old_candidates(db, now=now_utc)
                    except Exception:
                        logger.debug("Discovery expiry failed", exc_info=True)
            except Exception:
                logger.exception("Social poll tick failed")

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
