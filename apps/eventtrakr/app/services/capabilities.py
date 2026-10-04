"""Per-person EventTrakr permissions (StonePi capabilities).

Admins have everything. Members get what Dashboard → Users grants them; a
capability their grant has never stored falls back to the catalog default
(everything on except Instagram & Facebook), so existing members keep working
after upgrade without an admin visiting Users.

Requests read the live platform cookie. Background jobs (social polling,
Facebook sources, public pages) have no session, so each visit mirrors the
person's capabilities onto ``users.capabilities``; :func:`refresh_from_auth`
also pulls the Auth roster now and then so a revoke takes effect even for
someone who never opens EventTrakr again.
"""

from __future__ import annotations

import json
import logging

from app.config import env

logger = logging.getLogger("eventtrakr.capabilities")

APP_ID = "eventtrakr"
MANAGE_SOURCES = "can_manage_sources"
USE_SOCIAL = "can_use_social"
SHARE_AGENDA = "can_share_agenda"
SYNC_CALENDAR = "can_sync_calendar"

# Used when the installed stonepi_auth predates EventTrakr's capabilities.
_FALLBACK = {
    MANAGE_SOURCES: ("Manage sources", True),
    USE_SOCIAL: ("Instagram & Facebook", False),
    SHARE_AGENDA: ("Share agenda publicly", True),
    SYNC_CALENDAR: ("Google Calendar sync", True),
}

ROSTER_TTL_SECONDS = 5 * 60


def _catalog() -> dict[str, tuple[str, bool]]:
    try:
        from stonepi_auth import capabilities_for

        items = capabilities_for(APP_ID)
    except Exception:  # noqa: BLE001
        items = []
    out = {
        str(item["id"]): (str(item.get("label") or item["id"]), bool(item.get("default", False)))
        for item in items
        if item.get("id") in _FALLBACK
    }
    return out or dict(_FALLBACK)


def defaults() -> dict[str, bool]:
    return {cap: default for cap, (_, default) in _catalog().items()}


def label(cap: str) -> str:
    return _catalog().get(cap, (cap, False))[0]


def denied_message(cap: str) -> str:
    return f"Ask an admin to allow “{label(cap)}” for you in StonePi → Users."


def resolve(stored: dict | None, *, is_admin: bool) -> dict[str, bool]:
    """Full capability map: admins get all; members get stored values over defaults."""
    if is_admin:
        return {cap: True for cap in defaults()}
    stored = stored if isinstance(stored, dict) else {}
    return {cap: bool(stored.get(cap, default)) for cap, default in defaults().items()}


def from_platform(platform) -> dict[str, bool]:
    """Capabilities from the StonePi session cookie (keys missing from older cookies use defaults)."""
    perms = (getattr(platform, "permissions", None) or {}).get(APP_ID)
    return resolve(perms, is_admin=bool(getattr(platform, "is_admin", False)))


def parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def encode(caps: dict[str, bool]) -> str:
    return json.dumps({k: bool(v) for k, v in sorted(caps.items())}, separators=(",", ":"))


def for_user(user) -> dict[str, bool]:
    """Mirrored capabilities for a local ``User`` row (background jobs, public pages).

    The mirror is already resolved (an admin's is all on), so it wins over the
    local role: the roster can revoke a former admin who never comes back.
    Rows never mirrored (solo EventTrakr, or not seen since upgrade) use the role.
    """
    if user is None:
        return {cap: False for cap in defaults()}
    stored = parse(getattr(user, "capabilities", None))
    if stored is not None:
        return resolve(stored, is_admin=False)
    return resolve(None, is_admin=(user.role == "admin"))


def user_can(user, cap: str) -> bool:
    return bool(for_user(user).get(cap))


def user_ids_with(db, cap: str, user_ids) -> set[int]:
    """Which of ``user_ids`` currently hold ``cap`` (one query)."""
    from sqlalchemy import select

    from app.models import User

    ids = {int(i) for i in user_ids if i is not None}
    if not ids:
        return set()
    rows = db.execute(select(User).where(User.id.in_(ids))).scalars()
    return {row.id for row in rows if user_can(row, cap)}


# -- Auth roster ---------------------------------------------------------------------
# The fetching, caching and per-person resolution live in the shared
# ``stonepi_auth.roster`` helper; EventTrakr mirrors each fresh roster onto its
# local ``users.capabilities`` so background jobs keep working off the mirror
# (and keep the last known state) while Auth is down.


def _session_secret() -> str:
    from app.services import auth

    return auth._platform_session_secret()


def _roster():
    from stonepi_auth.roster import get_roster

    return get_roster(
        APP_ID,
        auth_url=(env.stonepi_auth_url or "").strip() or None,
        secret_getter=lambda: _session_secret(),
        ttl=ROSTER_TTL_SECONDS,
    )


def _caps_for(access) -> dict[str, bool]:
    """EventTrakr capability map for one roster answer (``MemberAccess``)."""
    if not access.has_app:
        return {cap: False for cap in defaults()}
    if access.is_admin:
        return resolve(None, is_admin=True)
    return resolve(access.capabilities, is_admin=False)


def _mirror(db, access_for) -> int:
    from sqlalchemy import select

    from app.models import User

    changed = 0
    for user in db.execute(select(User).where(User.auth_user_id.is_not(None))).scalars():
        access = access_for(str(user.auth_user_id))
        if access is None:  # unknown: keep the mirror as it is
            continue
        encoded = encode(_caps_for(access))
        if user.capabilities != encoded:
            user.capabilities = encoded
            changed += 1
    if changed:
        db.commit()
    return changed


def apply_roster(db, people: list[dict]) -> int:
    """Mirror Auth's roster rows onto local users. Returns how many rows changed.

    Someone missing from the roster (disabled or deleted) or without EventTrakr
    access gets no capabilities, so their paid polling stops. Local roles are
    left alone (they follow the person's next visit).
    """
    from stonepi_auth.roster import catalog_defaults, member_access

    by_id = {str(row["id"]): row for row in people if isinstance(row, dict) and row.get("id")}
    known = catalog_defaults(APP_ID)
    return _mirror(db, lambda uid: member_access(by_id.get(uid), APP_ID, user_id=uid, defaults=known))


def refresh_from_auth(db, *, force: bool = False) -> bool:
    """Pull the Auth roster (at most every few minutes) and mirror it.

    True when a fresh roster was mirrored; False when the cache was still
    fresh, StonePi SSO is off, or Auth is unavailable (the mirror is kept).
    """
    try:
        roster = _roster()
    except Exception:  # noqa: BLE001 (stonepi_auth too old for the shared roster)
        logger.debug("Shared Auth roster unavailable", exc_info=True)
        return False
    if not roster.refresh(force=force):
        return False
    try:
        _mirror(db, lambda uid: roster.access(uid, refresh=False))
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Couldn't mirror the Auth roster")
        return False
    return True

