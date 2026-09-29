"""Cached household roster from Auth (signed internal call).

``load_people()`` returns ``{auth_user_id: {"is_admin", "phone_alerts"}}`` for
enabled users, or None when Auth has never answered (callers fall back to
subscription snapshots). While Auth is down the last good roster is kept for
a day so alerts such as a disk warning still route correctly.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app.config import env

logger = logging.getLogger("notify.people")

PEOPLE_PATH = "/api/internal/people"
TTL_SECONDS = 60
RETRY_AFTER_FAILURE_SECONDS = 15
STALE_OK_SECONDS = 24 * 60 * 60

_lock = threading.Lock()
_cache: dict[str, Any] = {"fetched_at": 0.0, "good_at": 0.0, "people": None}
# One pooled client: creating an httpx client costs ~250 ms on Windows.
_client = httpx.Client(timeout=2.0, follow_redirects=False)


def session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return env.session_secret.strip()


def _fetch() -> dict[str, dict[str, bool]] | None:
    from stonepi_auth.internal import sign_internal

    secret = session_secret()
    if not secret:
        return None
    url = f"{env.auth_url.rstrip('/')}{PEOPLE_PATH}"
    try:
        response = _client.get(url, headers=sign_internal(secret, "GET", PEOPLE_PATH))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auth roster fetch failed: %s", exc)
        return None
    if response.status_code != 200:
        logger.warning("Auth roster fetch returned HTTP %s", response.status_code)
        return None
    try:
        rows = response.json().get("people") or []
    except Exception:  # noqa: BLE001
        return None
    people: dict[str, dict[str, bool]] = {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            people[str(row["id"])] = {
                "is_admin": bool(row.get("is_admin")),
                "phone_alerts": bool(row.get("phone_alerts")),
            }
    return people


def load_people(*, force: bool = False) -> dict[str, dict[str, bool]] | None:
    now = time.monotonic()
    with _lock:
        if not force and _cache["fetched_at"]:
            age = now - _cache["fetched_at"]
            if _cache["people"] is not None and age < TTL_SECONDS:
                return _cache["people"]
            if _cache["people"] is None and age < RETRY_AFTER_FAILURE_SECONDS:
                return None  # Auth just failed; don't pay for another call on every request
        fresh = _fetch()
        _cache["fetched_at"] = now
        if fresh is not None:
            _cache["people"] = fresh
            _cache["good_at"] = now
            return fresh
        if _cache["people"] is not None and now - _cache["good_at"] < STALE_OK_SECONDS:
            return _cache["people"]
        return None


def reset_cache() -> None:
    with _lock:
        _cache.update({"fetched_at": 0.0, "good_at": 0.0, "people": None})


# -- Admin actions on Auth users, as the signed-in admin (their cookies + CSRF) ------------


def _admin_cookies(cookies: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE

    forward = {k: v for k, v in (cookies or {}).items() if k in {COOKIE_NAME, CSRF_COOKIE}}
    headers = {"X-StonePi-CSRF": forward[CSRF_COOKIE]} if forward.get(CSRF_COOKIE) else {}
    return forward, headers


def auth_users(cookies: dict[str, str]) -> list[dict[str, Any]] | None:
    """Household accounts from Auth (admin session), or None if Auth can't be asked."""
    forward, _ = _admin_cookies(cookies)
    try:
        response = _client.get(f"{env.auth_url.rstrip('/')}/api/users", cookies=forward)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auth users fetch failed: %s", exc)
        return None
    if response.status_code != 200:
        return None
    try:
        users = response.json().get("users") or []
    except Exception:  # noqa: BLE001
        return None
    return [u for u in users if isinstance(u, dict)]


def set_phone_alerts(cookies: dict[str, str], user_id: str, on: bool) -> tuple[bool, str]:
    """Switch one person's Phone alerts permission in Auth. ``(ok, message)``."""
    forward, headers = _admin_cookies(cookies)
    try:
        response = _client.patch(
            f"{env.auth_url.rstrip('/')}/api/users/{user_id}",
            cookies=forward,
            headers=headers,
            json={"phone_alerts": bool(on)},
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"Auth is not reachable ({type(exc).__name__})."
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or response.text
        except Exception:  # noqa: BLE001
            detail = response.text
        return False, str(detail)[:160]
    reset_cache()  # the delivery roster should see the change straight away
    return True, "Saved."
