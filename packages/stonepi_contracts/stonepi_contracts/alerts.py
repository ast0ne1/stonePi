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

# Not /notifications: the pre-rename nginx sent that to Notify with a permanent (308)
# redirect, which browsers keep replaying from cache without asking the Pi.
NOTIFICATIONS_PATH = "/me/notifications"
STATUS_TTL_SECONDS = 60
# A hidden bell is re-checked sooner, so an admin turning phone alerts on shows up
# across every app (each its own process, each its own cache) within seconds.
HIDDEN_TTL_SECONDS = 10
# A failed lookup (Notify down / not answering) is retried after this long,
# so a Notify problem never costs every page view a network call.
FAILURE_TTL_SECONDS = 15
# A slow or failed lookup keeps showing the last good answer for this long, so a busy
# Notify (e.g. just restarted) doesn't flash "couldn't check" at people.
STALE_OK_SECONDS = 15 * 60
# With nothing cached yet (first page after a restart) wait a little longer.
FIRST_LOOKUP_TIMEOUT = 2.5

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


def _ask_notify(
    cookie_name: str,
    session: str,
    base_url: str | None,
    timeout: float,
    *,
    path: str = "/api/me/subscription",
) -> dict[str, Any] | None:
    if not session:
        return None
    url = f"{(base_url or notify_base_url()).rstrip('/')}{path}"
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
        if hit and now() - hit[0] < (STATUS_TTL_SECONDS if hit[1].get("show") else HIDDEN_TTL_SECONDS):
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
    uid = str(user_id or "").strip()
    with _lock:
        _cache.pop(uid, None)
        _failures.pop(uid, None)
        for key in [k for k in _app_cache if k[0] == uid]:
            _app_cache.pop(key, None)
            _app_failures.pop(key, None)


# -- One app's bell panel ------------------------------------------------------

_app_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_app_failures: dict[tuple[str, str], float] = {}


def app_alerts_status(
    user_id: str,
    app_id: str,
    *,
    cookie_name: str,
    session_cookie: str | None,
    session_allowed: bool,
    base_url: str | None = None,
    timeout: float = 1.0,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """State for one app's header bell and its panel (Notify ``/api/me/apps/{app}``).

    ``{"show", "dot", "reachable", "allowed", "available", "enabled", "needs_setup",
    "events", "last", "seen_at"}``.
    ``dot`` means that app sent you an alert since you last opened its panel.
    Cached per person and app for a minute; the panel also remembers its own
    last opening in the browser, so a cached dot never outlives a look.
    """
    uid = str(user_id or "").strip()
    app = str(app_id or "").strip()
    empty: dict[str, Any] = {
        "show": False, "dot": False, "reachable": False, "allowed": False, "available": False,
        "enabled": False, "needs_setup": False, "events": [], "last": None, "seen_at": None,
    }
    if not uid or not app:
        return empty
    key = (uid, app)
    fallback = {**empty, "show": bool(session_allowed), "allowed": bool(session_allowed)}

    def stale_or_fallback() -> dict[str, Any]:
        # Last good answer (still marked reachable) beats "couldn't check" for a while.
        hit = _app_cache.get(key)
        if hit and now() - hit[0] < STALE_OK_SECONDS:
            return dict(hit[1])
        return fallback

    with _lock:
        hit = _app_cache.get(key)
        if hit and now() - hit[0] < (STATUS_TTL_SECONDS if hit[1].get("show") else HIDDEN_TTL_SECONDS):
            return dict(hit[1])
        failed_at = _app_failures.get(key)
        if failed_at is not None and now() - failed_at < FAILURE_TTL_SECONDS:
            return stale_or_fallback()
        first = hit is None
    wait = max(timeout, FIRST_LOOKUP_TIMEOUT) if first else timeout
    body = _ask_notify(cookie_name, session_cookie or "", base_url, wait, path=f"/api/me/apps/{app}")
    if body is None:
        with _lock:
            _app_failures[key] = now()
            return stale_or_fallback()
    status = app_status_from_body(body)
    with _lock:
        _app_failures.pop(key, None)
        _app_cache[key] = (now(), status)
        if len(_app_cache) > 512:
            for old in sorted(_app_cache, key=lambda k: _app_cache[k][0])[:128]:
                _app_cache.pop(old, None)
    return dict(status)


def app_status_from_body(body: dict[str, Any]) -> dict[str, Any]:
    """Bell state from Notify's ``/api/me/apps/{app}`` body (Notify itself builds the body in-process)."""
    show = bool(body.get("allowed") and body.get("available"))
    return {
        "show": show,
        "dot": bool(show and body.get("unseen")),
        "reachable": True,
        "allowed": bool(body.get("allowed")),
        "available": bool(body.get("available")),
        "enabled": bool(body.get("enabled")),
        "needs_setup": bool(body.get("needs_setup")),
        "events": [e for e in body.get("events") or [] if isinstance(e, dict)],
        "last": body.get("last") if isinstance(body.get("last"), dict) else None,
        "recent": [r for r in body.get("recent") or [] if isinstance(r, dict)],
        "seen_at": body.get("seen_at") or None,
    }
