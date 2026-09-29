"""Pinboard phone alerts: reminder due (daily check) and new notice.

- ``pinboard.reminder_due``: once, on the morning of the due date at the
  household reminder time. Personal to the assignee when assigned to a
  household member; household when unassigned or assigned by old free text.
- ``pinboard.notice_posted``: household, when a notice is created.

Both start "not approved" in Notify and people opt in, so they're quiet by default.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from app import store
from app.config import env

logger = logging.getLogger("pinboard.alerts")

TICK_SECONDS = 120
_stop = threading.Event()
_thread: threading.Thread | None = None


def _pinboard_url(path: str) -> str:
    prefix = (env.stonepi_prefix or ("/pinboard" if env.routing == "path" else "")).rstrip("/")
    return f"{env.public_origin.rstrip('/')}{prefix}{path}"


def _emit(**fields) -> bool:
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        return emit_event(EventEnvelope(source="pinboard", **fields))
    except Exception:  # noqa: BLE001
        logger.debug("pinboard emit failed", exc_info=True)
        return False


def emit_reminder_due(reminder: dict) -> bool:
    from stonepi_auth.alerts import auth_user_id

    owner = auth_user_id(reminder.get("assignee_user"))
    text = str(reminder.get("text") or "Reminder").strip()
    summary = "Due today"
    if not owner and reminder.get("assignee"):
        summary = f"Due today · {str(reminder['assignee']).strip()}"
    due = str(reminder.get("due") or "")[:10]
    return _emit(
        id="pinboard.reminder_due",
        title=f"Reminder: {text}"[:200],
        summary=summary,
        severity="info",
        audience="personal" if owner else "household",
        user=owner,
        dedupe_key=f"pinboard:reminder:{reminder.get('id')}:{due}",
        url=_pinboard_url("/reminders"),
        data={"reminder_id": reminder.get("id"), "due": due},
    )


def emit_notice_posted(notice: dict, *, by: str = "") -> bool:
    text = str(notice.get("text") or "").strip()
    return _emit(
        id="pinboard.notice_posted",
        title="New notice on Pinboard",
        summary=(f"{by}: {text}" if by else text)[:120],
        severity="info",
        audience="household",
        dedupe_key=f"pinboard:notice:{notice.get('id')}",
        url=_pinboard_url("/notices"),
        data={"notice_id": notice.get("id")},
    )


def check_due_reminders(now: datetime | None = None) -> int:
    """Send today's due reminders once the reminder time has passed. Returns count sent.

    A reminder is recorded as sent only when Notify accepted it, so a Notify
    outage retries on the next tick; after a restart nothing repeats.
    """
    local = now or datetime.now().astimezone()
    hh, mm = (int(x) for x in store.reminder_time().split(":"))
    if (local.hour, local.minute) < (hh, mm):
        return 0
    sent = 0
    for reminder in store.reminders_due_for_alert(local.date()):
        if emit_reminder_due(reminder):
            store.mark_reminder_alerted(str(reminder.get("id")), str(reminder.get("due") or ""))
            sent += 1
    return sent


def _loop() -> None:
    if not _stop.wait(20):
        _tick()
    while not _stop.wait(TICK_SECONDS):
        _tick()


def _tick() -> None:
    try:
        sent = check_due_reminders()
        if sent:
            logger.info("Sent %s reminder alert(s)", sent)
    except Exception:
        logger.exception("Reminder check failed")


def start_scheduler() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="pinboard-reminders")
    _thread.start()
    logger.info("Pinboard reminder check started (every %ss)", TICK_SECONDS)


def stop_scheduler() -> None:
    _stop.set()
