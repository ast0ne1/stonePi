from __future__ import annotations

import logging
import threading
from typing import Any

from app import db
from app.collectors import registry
from app.config import env

logger = logging.getLogger("pricescout.ingest")

_refresh_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "last_error": None}


def refresh_status() -> dict[str, Any]:
    return dict(_state)


def refresh_all(*, zip_code: str = "", force_mock: bool | None = None) -> dict[str, Any]:
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "error": "Refresh already running"}
    _state["running"] = True
    _state["last_error"] = None
    try:
        use_mock = env.mock if force_mock is None else force_mock
        if use_mock:
            db.seed_mock_if_empty()
            return {"ok": True, "mode": "mock"}

        sources = {s["id"]: s for s in db.list_sources()}
        results: dict[str, Any] = {"ok": True, "mode": "live", "sources": {}}

        for collector in registry.tjek_collectors():
            src = sources.get(collector.id) or {}
            if not src.get("enabled", 1):
                results["sources"][collector.id] = {"skipped": True}
                continue
            try:
                rows = collector.fetch_offers()
                count = db.replace_source_offers(collector.id, [r.as_dict() for r in rows])
                results["sources"][collector.id] = {"ok": True, "count": count}
            except Exception as exc:
                logger.exception("collector %s failed", collector.id)
                db.update_source_status(collector.id, ok=False, error=str(exc))
                results["sources"][collector.id] = {"ok": False, "error": str(exc)}
                # Keep going; seed mock only if DB still empty after all
                results["ok"] = False

        for collector in registry.foodwaste_collectors(zip_code):
            src = sources.get(collector.id) or {}
            if not src.get("enabled", 0):
                continue
            try:
                rows = collector.fetch_offers()
                count = db.replace_source_offers(collector.id, [r.as_dict() for r in rows])
                results["sources"][collector.id] = {"ok": True, "count": count}
            except Exception as exc:
                logger.exception("foodwaste %s failed", collector.id)
                db.update_source_status(collector.id, ok=False, error=str(exc))
                results["sources"][collector.id] = {"ok": False, "error": str(exc)}

        # Keep last good DB rows; seed only if still empty
        if not db.query_offers(limit=1):
            db.seed_mock_if_empty()
            results["fallback"] = "mock"
        else:
            try:
                results["recategorized"] = db.recategorize_products()
            except Exception:
                logger.exception("recategorize after refresh failed")
        return results
    except Exception as exc:
        _state["last_error"] = str(exc)
        logger.exception("refresh failed")
        db.seed_mock_if_empty()
        return {"ok": False, "error": str(exc), "fallback": "mock"}
    finally:
        _state["running"] = False
        _refresh_lock.release()
