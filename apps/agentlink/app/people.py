"""Who is still allowed in: the enabled-user roster from Auth (signed internal call), cached.

AgentLink re-checks the bound user on every call (muse.md §7, policy evaluation), so
disabling someone in Dashboard cuts their Pebble off within a minute.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx
from stonepi_auth.internal import sign_internal

from app.config import env, session_secret

logger = logging.getLogger("agentlink.people")

PATH = "/api/internal/people"
TTL_S = 60
RETRY_AFTER_FAILURE_S = 15

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "people": None}
_client = httpx.Client(timeout=2.0, follow_redirects=False)


def _fetch() -> dict[str, dict] | None:
    secret = session_secret()
    if not secret:
        return None
    try:
        response = _client.get(f"{env.auth_url.rstrip('/')}{PATH}", headers=sign_internal(secret, "GET", PATH))
    except httpx.HTTPError as exc:
        logger.warning("Auth roster fetch failed: %s", exc)
        return None
    if response.status_code != 200:
        return None
    try:
        rows = response.json().get("people") or []
    except ValueError:
        return None
    return {str(r["id"]): r for r in rows if isinstance(r, dict) and str(r.get("id") or "").strip()}


def roster() -> dict[str, dict] | None:
    """{user_id: row} for enabled users, or None when Auth can't be asked."""
    now = time.monotonic()
    with _lock:
        age = now - _cache["at"]
        if _cache["at"] and ((_cache["people"] is not None and age < TTL_S) or (_cache["people"] is None and age < RETRY_AFTER_FAILURE_S)):
            return _cache["people"]
    people = _fetch()
    with _lock:
        _cache.update(at=now, people=people if people is not None else _cache["people"])
        return _cache["people"]


def user_enabled(user_id: str) -> bool | None:
    """True / False, or None when unknown (no secret in dev, Auth never answered)."""
    people = roster()
    if people is None:
        return None
    return str(user_id) in people


def user_apps(user_id: str) -> set[str] | None:
    """Apps the user is granted (Auth's roster ``permissions``, enabled apps only), or None
    when Auth can't be asked. Re-read on every tool call, so revoking an app in Dashboard
    cuts the user's agents off from it within a minute."""
    people = roster()
    if people is None:
        return None
    row = people.get(str(user_id))
    if row is None:
        return set()
    perms = row.get("permissions")
    granted = set(perms) if isinstance(perms, dict) else set()
    if row.get("is_admin"):
        # As everywhere on StonePi (stonepi_auth.roster.member_access), admins have every app
        # without a grant saved for it. A switched-off app just answers app_unavailable.
        granted |= _user_app_ids()
    return granted


def _user_app_ids() -> set[str]:
    try:
        from stonepi_auth import APP_CATALOG
    except ImportError:
        return set()
    return {str(item["id"]) for item in APP_CATALOG if item.get("group") != "system"}
