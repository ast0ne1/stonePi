"""Live access checks for work that runs without a session cookie.

Scheduled checks and strike alerts act for a watch's owner long after they
signed in, so they ask Auth's roster (via :mod:`stonepi_auth.roster`) whether
the owner may still use PriceWatch and the capability involved. Watches store
the owner's Auth user id in ``user_key``; ``"local"`` watches (standalone or
dev runs) have no platform owner and are never blocked.

``None`` from the roster means "unknown" (no SSO secret, Auth down too long):
then everything carries on as before. Work stops only when the roster
positively says no.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from app.config import env

logger = logging.getLogger("pricewatch.access")

APP_ID = "pricewatch"
CAN_MANAGE_WATCHES = "can_manage_watches"
CAN_MANAGE_SOURCES = "can_manage_sources"
CAN_USE_ALERTS = "can_use_alerts"


def session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if str(vaulted or "").strip():
            return str(vaulted).strip()
    except Exception:
        pass
    return (env.session_secret or "").strip()


def _roster():
    from stonepi_auth.roster import get_roster

    return get_roster(APP_ID, auth_url=env.auth_url, secret_getter=session_secret)


def _owner_id(user_key: Any) -> str | None:
    from stonepi_auth.alerts import auth_user_id

    return auth_user_id(user_key)


def owner_access(user_key: Any):
    """The roster's ``MemberAccess`` for a watch owner; None when unknown or local."""
    owner = _owner_id(user_key)
    if owner is None:
        return None
    try:
        return _roster().access(owner)
    except Exception:  # noqa: BLE001 (stonepi_auth too old for the shared roster)
        logger.debug("Auth roster unavailable", exc_info=True)
        return None


def denied_reason(access, capability: str) -> str | None:
    """Why the roster says no to ``capability``, or None when allowed/unknown."""
    if access is None:
        return None
    if not access.has_app:
        return "owner no longer has PriceWatch access"
    if not access.can(capability):
        return f"owner lacks {capability}"
    return None


def owner_can(user_key: Any, capability: str) -> bool:
    """False only when the roster positively says the owner can't (unknown → True)."""
    return denied_reason(owner_access(user_key), capability) is None


def filter_watches(watches: Iterable[dict[str, Any]], capability: str = CAN_MANAGE_WATCHES) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Split watches into (allowed, [(skipped, reason)]) by their owners' roster access.

    One roster lookup for every owner. Local watches and an unknown roster keep
    every watch.
    """
    watches = list(watches)
    owners = {w.get("user_key"): _owner_id(w.get("user_key")) for w in watches}
    ids = {o for o in owners.values() if o}
    accesses = None
    if ids:
        try:
            accesses = _roster().access_many(ids)
        except Exception:  # noqa: BLE001
            logger.debug("Auth roster unavailable", exc_info=True)
            accesses = None
    if not accesses:
        return watches, []
    allowed: list[dict] = []
    skipped: list[tuple[dict, str]] = []
    for w in watches:
        owner = owners.get(w.get("user_key"))
        reason = denied_reason(accesses.get(owner), capability) if owner else None
        if reason:
            skipped.append((w, reason))
        else:
            allowed.append(w)
    return allowed, skipped
