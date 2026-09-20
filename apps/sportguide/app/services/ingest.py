from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app import db
from app.collectors import ausportguide, wheresthematch
from app.timeutil import needs_daily_refresh

logger = logging.getLogger("sportguide.ingest")

_refresh_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "last_error": None, "last_ok_at": None}


def refresh_status() -> dict[str, Any]:
    return {
        "running": bool(_state["running"]),
        "last_error": _state.get("last_error"),
        "last_ok_at": _state.get("last_ok_at") or db.get_meta("last_refresh_at"),
        "daily": True,
    }


def maybe_daily_refresh(*, tz_name: str | None = None, async_: bool = False) -> dict[str, Any] | None:
    """Run a full refresh at most once per local day after the configured hour."""
    last = db.get_meta("last_refresh_at")
    tz = tz_name or db.get_pref("local", "timezone", "Australia/Melbourne")
    if not needs_daily_refresh(last, tz):
        return None
    if async_:
        refresh_async()
        return {"ok": True, "started": True}
    return refresh_all()


def refresh_all() -> dict[str, Any]:
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "error": "Refresh already running"}
    _state["running"] = True
    _state["last_error"] = None
    results: dict[str, Any] = {"ok": True, "sources": {}}
    try:
        collectors = (
            ("ausportguide", ausportguide.fetch_listings),
            ("wheresthematch", wheresthematch.fetch_listings),
        )
        for source_id, fetch in collectors:
            try:
                rows = fetch()
                count = db.replace_source_listings(source_id, [r.as_dict() for r in rows])
                results["sources"][source_id] = {"ok": True, "count": count}
            except Exception as exc:
                logger.exception("collector %s failed", source_id)
                db.update_source_status(source_id, ok=False, error=str(exc), count=0)
                results["sources"][source_id] = {"ok": False, "error": str(exc)}
                results["ok"] = False
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.set_meta("last_refresh_at", now)
        _state["last_ok_at"] = now
        if not results["ok"]:
            _state["last_error"] = "One or more sources failed"
        return results
    except Exception as exc:
        logger.exception("refresh failed")
        _state["last_error"] = str(exc)
        return {"ok": False, "error": str(exc)}
    finally:
        _state["running"] = False
        _refresh_lock.release()


def refresh_async() -> None:
    if _state["running"]:
        return
    threading.Thread(target=refresh_all, daemon=True, name="sportguide-refresh").start()
