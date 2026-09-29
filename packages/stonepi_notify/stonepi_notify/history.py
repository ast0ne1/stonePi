from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_data_dir: Path | None = None
_lock = threading.Lock()
MAX_HISTORY = 100


def configure_history(data_dir: Path) -> None:
    global _data_dir
    _data_dir = Path(data_dir)


def _path() -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_notify not configured")
    return _data_dir / "history.json"


def load_history(*, limit: int = 50) -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("items") or []
        if not isinstance(items, list):
            return []
        return list(items)[: max(1, min(limit, MAX_HISTORY))]
    except (OSError, json.JSONDecodeError):
        return []


def append_history(
    *,
    channel: str,
    ok: bool,
    title: str,
    message: str = "",
    source: str = "",
    event_id: str = "",
    deliveries: list[dict[str, Any]] | None = None,
) -> None:
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "channel": str(channel or "").strip() or "unknown",
        "ok": bool(ok),
        "title": str(title or "")[:200],
        "message": str(message or "")[:400],
        "source": str(source or "")[:80],
        "event_id": str(event_id or "")[:120],
    }
    if deliveries is not None:
        # Per-recipient results; topics must already be redacted by the caller.
        entry["deliveries"] = [dict(d) for d in deliveries][:50]
    with _lock:
        items = load_history(limit=MAX_HISTORY)
        items.insert(0, entry)
        items = items[:MAX_HISTORY]
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
