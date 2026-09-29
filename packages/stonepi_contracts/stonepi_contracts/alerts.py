"""Top-bar bell state for personal alerts (Dashboard and every app).

``personal_alerts_status`` asks Notify (loopback, as the signed-in person) whether
the person may use personal alerts and whether they still need setting up.
Answers are cached per person for a minute so page renders stay fast; if Notify
can't be reached the bell falls back to the session and shows no dot.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from .emit import notify_base_url

logger = logging.getLogger("stonepi.contracts.alerts")

NOTIFICATIONS_PATH = "/notifications"
STATUS_TTL_SECONDS = 60
# A failed lookup (Notify down / not answering) is retried after this long,
# so a Notify problem never costs every page view a network call.
FAILURE_TTL_SECONDS = 15

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, bool]]] = {}
_failures: dict[str, float] = {}
# One pooled client per process: creating an httpx client costs ~250 ms on Windows.
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=2.0, follow_redirects=False)
        return _client


def notifications_url(home_url: str) -> str:
    """Dashboard Notifications page for a portal home URL (``stonepi_home_url``)."""
    return f"{str(home_url or '').rstrip('/')}{NOTIFICATIONS_PATH}"


def _ask_notify(cookie_name: str, session: str, base_url: str | None, timeout: float) -> dict[str, Any] | None:
    if not session:
        return None
    url = f"{(base_url or notify_base_url()).rstrip('/')}/api/me/subscription"
    try:
        response = _http().get(url, cookies={cookie_name: session}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.debug("alerts status unavailable: %s", exc)
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


def personal_alerts_status(
    user_id: str,
    *,
    cookie_name: str,
    session_cookie: str | None,
    session_allowed: bool,
    base_url: str | None = None,
    timeout: float = 1.0,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, bool]:
    """``{"show": bool, "dot": bool}`` for the bell.

    ``cookie_name``/``session_cookie`` are the platform session cookie
    (``stonepi_auth.session.COOKIE_NAME`` and its value), forwarded to Notify.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return {"show": False, "dot": False}
    fallback = {"show": bool(session_allowed), "dot": False}
    with _lock:
        hit = _cache.get(uid)
        if hit and now() - hit[0] < STATUS_TTL_SECONDS:
            return dict(hit[1])
        failed_at = _failures.get(uid)
        if failed_at is not None and now() - failed_at < FAILURE_TTL_SECONDS:
            return fallback
    body = _ask_notify(cookie_name, session_cookie or "", base_url, timeout)
    if body is None:
        with _lock:
            _failures[uid] = now()
        return fallback
    # Hidden until the household has phone alerts on: before that it only leads to "not on yet".
    show = bool(body.get("allowed") and body.get("available"))
    status = {"show": show, "dot": bool(show and body.get("needs_setup"))}
    with _lock:
        _failures.pop(uid, None)
        _cache[uid] = (now(), status)
        if len(_cache) > 256:
            for key in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
                _cache.pop(key, None)
    return dict(status)


def forget_alerts_status(user_id: str) -> None:
    """Drop a cached answer (e.g. after the person changes their alerts)."""
    with _lock:
        _cache.pop(str(user_id or "").strip(), None)
        _failures.pop(str(user_id or "").strip(), None)
