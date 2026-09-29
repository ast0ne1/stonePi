"""Background tick for SportGuide approaching watched-match alerts."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("sportguide.schedule")

TICK_SECONDS = 5 * 60
_stop = threading.Event()
_thread: threading.Thread | None = None


def _tick() -> None:
    try:
        from app.services import ingest

        ingest.maybe_daily_refresh(async_=True)
    except Exception:
        logger.exception("Daily refresh check failed")
    try:
        from app.services import notify as notify_service

        result = notify_service.check_all_watching()
        if result.get("notified"):
            logger.info(
                "Approaching watched matches: notified=%s checked=%s people=%s",
                result.get("notified"),
                result.get("checked"),
                result.get("people"),
            )
    except Exception:
        logger.exception("Approaching-watched check failed")


def _loop() -> None:
    # First pass shortly after boot so restart doesn't wait a full tick.
    if not _stop.wait(15):
        _tick()
    while not _stop.wait(TICK_SECONDS):
        _tick()


def start_scheduler() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="sportguide-notify-tick")
    _thread.start()
    logger.info("SportGuide notify scheduler started (every %ss)", TICK_SECONDS)


def stop_scheduler() -> None:
    _stop.set()
