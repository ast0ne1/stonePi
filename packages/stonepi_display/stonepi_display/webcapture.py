"""Captures of the web pages, shared by every screen that shows them (F14).

Runs in Notify only (it owns the TRMNL schedule; the Car Thing service asks Notify).
Each capture is cached per page settings and size, so a TRMNL push and the Car Thing
asking for the same page within ``max_age`` share one Chromium run, and concurrent
asks for the same capture wait for the one already running instead of starting another.

A page served by a StonePi app that only opens with a one-off capture key (Ripple's
family display) gets one through the key provider Notify registers. The key goes in the
address with the panel's size and bit depth (``k``, ``w``, ``h``, ``scale``, ``bits``), so
the app lays itself out for that screen and prepares photos for its greys.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .webpages import page_css

MAX_ENTRIES = 12
KEYED_PARAMS = ("k", "w", "h", "scale", "bits")

# url -> a one-off key for an app page, None for any other page; raises when the app won't give one.
_key_provider: Callable[[str], str | None] | None = None


def set_key_provider(provider: Callable[[str], str | None] | None) -> None:
    global _key_provider
    _key_provider = provider


def capture_url(url: str, width: int, height: int, scale: float, bits: int) -> str:
    """The address to capture: ``url`` itself, or for an app page, with a fresh key and the panel."""
    key = _key_provider(url) if _key_provider else None
    if not key:
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in KEYED_PARAMS]
    query += [
        ("k", key),
        ("w", str(round(width / scale))),
        ("h", str(round(height / scale))),
        ("scale", f"{scale:g}"),
        ("bits", str(int(bits))),
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))

_lock = threading.Lock()
# One lock per capture in progress (and the callers waiting on it), with a count of its
# users; it's dropped when the last one leaves, so the dict only holds in-flight keys.
_key_locks: dict[str, list[Any]] = {}
_cache: dict[str, tuple[float, bytes]] = {}
_cache_urls: dict[str, str] = {}  # cache key -> the page address it captured (for forget)
_errors: dict[str, tuple[float, str]] = {}


def _key(page: dict[str, Any], width: int, height: int, scale: float, bits: int) -> str:
    settings = {k: page.get(k) for k in ("url", "preset", "hide", "css", "settle_s", "scale")}
    blob = json.dumps([settings, width, height, scale, bits], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def capture(
    page: dict[str, Any], width: int, height: int, *, device_scale: float = 1.0, max_age: float = 0.0, bits: int = 0
) -> bytes:
    """Colour PNG of ``page`` at ``width``×``height``; a capture younger than ``max_age`` s is reused.

    ``device_scale`` multiplies the page's own zoom (TRMNL X lays out at 1040×780 CSS px and
    draws at 1.8×). ``bits`` is the e-ink panel's depth (0 for a colour screen); only app pages
    use it. Raises ``stonepi_browser.CaptureError`` (or RuntimeError) on failure.
    """
    scale = float(page.get("scale") or 1.0) * float(device_scale or 1.0)
    key = _key(page, width, height, scale, bits)
    with _lock:
        entry = _key_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            return _capture_locked(page, key, width, height, scale, max_age, bits)
    finally:
        with _lock:
            entry[1] -= 1
            if entry[1] == 0 and _key_locks.get(key) is entry:
                del _key_locks[key]


def _capture_locked(
    page: dict[str, Any], key: str, width: int, height: int, scale: float, max_age: float, bits: int
) -> bytes:
    from stonepi_browser import capture_page

    with _lock:
        hit = _cache.get(key)
    if hit and max_age > 0 and time.monotonic() - hit[0] < max_age:
        return hit[1]
    try:
        url = capture_url(page["url"], width, height, scale, bits)
        png = capture_page(url, width, height, scale=scale, css=page_css(page), settle_s=float(page.get("settle_s") or 0))
    except Exception as exc:
        with _lock:
            _errors[page.get("id") or ""] = (time.time(), str(exc)[:200])
        raise
    with _lock:
        _cache[key] = (time.monotonic(), png)
        _cache_urls[key] = str(page.get("url") or "")
        _errors.pop(page.get("id") or "", None)
        while len(_cache) > MAX_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
            _cache_urls.pop(oldest, None)
    return png


def last_error(page_id: str) -> str:
    with _lock:
        hit = _errors.get(page_id)
    return hit[1] if hit else ""


def forget(urls: set[str]) -> None:
    """Drop cached captures of these addresses (their page changed); other pages keep theirs."""
    with _lock:
        for key in [k for k, url in _cache_urls.items() if url in urls]:
            _cache.pop(key, None)
            _cache_urls.pop(key, None)


def clear() -> None:
    with _lock:
        _cache.clear()
        _cache_urls.clear()
