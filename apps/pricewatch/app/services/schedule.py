from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.services import strike

logger = logging.getLogger("pricewatch.schedule")

TICK_MINUTES = 5
_scheduler: BackgroundScheduler | None = None
_running = False


def _scheduled_tick() -> None:
    global _running
    if _running:
        return
    _running = True
    try:
        summary = strike.check_due_watches()
        if summary.get("checked"):
            logger.info("Scheduled tick checked %s watches", summary["checked"])
    except Exception:
        logger.exception("Scheduled tick failed")
    finally:
        _running = False


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        func=_scheduled_tick,
        trigger="interval",
        minutes=TICK_MINUTES,
        id="pricewatch_tick",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started (tick every %s minutes)", TICK_MINUTES)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict:
    return {
        "running": bool(_scheduler and _scheduler.running),
        "tick_minutes": TICK_MINUTES,
        "busy": _running,
    }
