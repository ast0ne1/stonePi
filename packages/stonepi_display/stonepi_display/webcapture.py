"""Captures of the web pages, shared by every screen that shows them (F14).

Runs in Notify only (it owns the TRMNL schedule; the Car Thing service asks Notify).
Each capture is cached per page settings and size, so a TRMNL push and the Car Thing
asking for the same page within ``max_age`` share one Chromium run, and concurrent
asks for the same capture wait for the one already running instead of starting another.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from .webpages import page_css

MAX_ENTRIES = 12

_lock = threading.Lock()
# One lock per capture in progress (and the callers waiting on it), with a count of its
# users; it's dropped when the last one leaves, so the dict only holds in-flight keys.
_key_locks: dict[str, list[Any]] = {}
_cache: dict[str, tuple[float, bytes]] = {}
_errors: dict[str, tuple[float, str]] = {}


def _key(page: dict[str, Any], width: int, height: int, scale: float) -> str:
    settings = {k: page.get(k) for k in ("url", "preset", "hide", "css", "settle_s", "scale")}
    blob = json.dumps([settings, width, height, scale], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def capture(page: dict[str, Any], width: int, height: int, *, device_scale: float = 1.0, max_age: float = 0.0) -> bytes:
    """Colour PNG of ``page`` at ``width``×``height``; a capture younger than ``max_age`` s is reused.

    ``device_scale`` multiplies the page's own zoom (TRMNL X lays out at 1040×780 CSS px and
    draws at 1.8×). Raises ``stonepi_browser.CaptureError`` (or RuntimeError) on failure.
    """
    scale = float(page.get("scale") or 1.0) * float(device_scale or 1.0)
    key = _key(page, width, height, scale)
    with _lock:
        entry = _key_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            return _capture_locked(page, key, width, height, scale, max_age)
    finally:
        with _lock:
            entry[1] -= 1
            if entry[1] == 0 and _key_locks.get(key) is entry:
                del _key_locks[key]


def _capture_locked(page: dict[str, Any], key: str, width: int, height: int, scale: float, max_age: float) -> bytes:
    from stonepi_browser import capture_page

    with _lock:
        hit = _cache.get(key)
    if hit and max_age > 0 and time.monotonic() - hit[0] < max_age:
        return hit[1]
    try:
        png = capture_page(
            page["url"], width, height, scale=scale, css=page_css(page), settle_s=float(page.get("settle_s") or 0)
        )
    except Exception as exc:
        with _lock:
            _errors[page.get("id") or ""] = (time.time(), str(exc)[:200])
        raise
    with _lock:
        _cache[key] = (time.monotonic(), png)
        _errors.pop(page.get("id") or "", None)
        while len(_cache) > MAX_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
    return png


def last_error(page_id: str) -> str:
    with _lock:
        hit = _errors.get(page_id)
    return hit[1] if hit else ""


def clear() -> None:
    with _lock:
        _cache.clear()
