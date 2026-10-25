"""StonePi's health for agents: the same Watch roll-up Notify and Dashboard use, cached."""

from __future__ import annotations

import threading
import time
from typing import Any

from app.config import DATA_DIR

CACHE_S = 20
_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "watch": None}


def _health_url(item: dict) -> str:
    return f"http://127.0.0.1:{item['port']}{item.get('health') or '/healthz'}"


def watch() -> dict[str, Any]:
    """Watch roll-up (level, summary, reasons, apps, disk, backup); cached for CACHE_S."""
    with _lock:
        if _cache["watch"] is not None and time.monotonic() - _cache["at"] < CACHE_S:
            return _cache["watch"]
    try:
        import stonepi_watch
        from stonepi_auth import APP_CATALOG
    except ImportError:
        result = {"level": "unknown", "summary": "Status unavailable", "reasons": [], "apps": []}
    else:
        result = stonepi_watch.evaluate(
            health_url_for=_health_url,
            backup_info=stonepi_watch.read_backup_info,
            data_dir=DATA_DIR,
            catalog=list(APP_CATALOG),
        )
    with _lock:
        _cache.update(at=time.monotonic(), watch=result)
    return result


def get_status_payload() -> dict[str, Any]:
    """Compact, speech-friendly; no personal data (muse.md §7.1)."""
    w = watch()
    down = [str(a.get("n") or a.get("id")) for a in w.get("apps") or [] if a.get("enabled", True) and not a.get("running")]
    age = w.get("backup_age_days")
    return {
        "level": w.get("level", "unknown"),
        "summary": w.get("summary", ""),
        "needs_attention": list(w.get("reasons") or [])[:5],
        "services_down": down[:8],
        "services_total": len(w.get("apps") or []),
        "disk_used_pct": w.get("disk_pct"),
        "last_backup_days_ago": round(age, 1) if isinstance(age, (int, float)) else None,
    }
