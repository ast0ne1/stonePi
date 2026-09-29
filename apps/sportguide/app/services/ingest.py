from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from app import db
from app.collectors import ausportguide, wheresthematch
from app.timeutil import needs_daily_refresh

logger = logging.getLogger("sportguide.ingest")

_refresh_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "last_error": None, "last_ok_at": None, "source_id": None}

COLLECTORS: dict[str, Callable[[], list]] = {
    "ausportguide": ausportguide.fetch_listings,
    "wheresthematch": wheresthematch.fetch_listings,
}


def refresh_status() -> dict[str, Any]:
    return {
        "running": bool(_state["running"]),
        "last_error": _state.get("last_error"),
        "last_ok_at": _state.get("last_ok_at") or db.get_meta("last_refresh_at"),
        "source_id": _state.get("source_id"),
        "daily": True,
    }


def maybe_daily_refresh(*, tz_name: str | None = None, async_: bool = False) -> dict[str, Any] | None:
    """Run a full refresh at most once per local day after the configured hour."""
    last = db.get_meta("last_refresh_at")
    tz = tz_name or db.household_timezone()
    if not needs_daily_refresh(last, tz):
        return None
    if async_:
        refresh_async()
        return {"ok": True, "started": True}
    return refresh_all()


def _run_collector(source_id: str) -> dict[str, Any]:
    fetch = COLLECTORS.get(source_id)
    if not fetch:
        return {"ok": False, "error": "Unknown source"}
    try:
        rows = fetch()
        count = db.replace_source_listings(source_id, [r.as_dict() for r in rows])
        return {"ok": True, "count": count}
    except Exception as exc:
        logger.exception("collector %s failed", source_id)
        db.update_source_status(source_id, ok=False, error=str(exc), count=0)
        return {"ok": False, "error": str(exc)}


def refresh_all() -> dict[str, Any]:
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "error": "Refresh already running"}
    _state["running"] = True
    _state["last_error"] = None
    _state["source_id"] = None
    results: dict[str, Any] = {"ok": True, "sources": {}}
    try:
        for source_id in COLLECTORS:
            result = _run_collector(source_id)
            results["sources"][source_id] = result
            if not result.get("ok"):
                results["ok"] = False
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.set_meta("last_refresh_at", now)
        _state["last_ok_at"] = now
        if not results["ok"]:
            _state["last_error"] = "One or more sources failed"
        try:
            from app.services import notify as notify_service

            notify_service.check_approaching_watched()
        except Exception:
            logger.exception("post-refresh approaching check failed")
        return results
    except Exception as exc:
        logger.exception("refresh failed")
        _state["last_error"] = str(exc)
        return {"ok": False, "error": str(exc)}
    finally:
        _state["running"] = False
        _state["source_id"] = None
        _refresh_lock.release()


def refresh_one(source_id: str) -> dict[str, Any]:
    if source_id not in COLLECTORS:
        return {"ok": False, "error": "Unknown source"}
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "error": "Refresh already running"}
    _state["running"] = True
    _state["last_error"] = None
    _state["source_id"] = source_id
    try:
        result = _run_collector(source_id)
        if not result.get("ok"):
            _state["last_error"] = str(result.get("error") or "Refresh failed")
        return result
    except Exception as exc:
        logger.exception("refresh one %s failed", source_id)
        _state["last_error"] = str(exc)
        return {"ok": False, "error": str(exc)}
    finally:
        _state["running"] = False
        _state["source_id"] = None
        _refresh_lock.release()


def refresh_async() -> None:
    if _state["running"]:
        return
    threading.Thread(target=refresh_all, daemon=True, name="sportguide-refresh").start()


def refresh_one_async(source_id: str) -> None:
    if source_id not in COLLECTORS:
        return
    if _state["running"]:
        return
    threading.Thread(
        target=refresh_one,
        args=(source_id,),
        daemon=True,
        name=f"sportguide-refresh-{source_id}",
    ).start()
