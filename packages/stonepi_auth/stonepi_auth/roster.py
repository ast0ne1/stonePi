"""Live per-app access from Auth's roster, for code that has no session cookie.

A user's capabilities normally come from the signed session cookie
(:meth:`stonepi_auth.session.PlatformUser.has_capability`), which lives for 14
days. Background work (scheduled scrapes, alerts, public pages) has no cookie
and would never see a revoke. :class:`Roster` pulls Auth's signed internal
roster (``GET /api/internal/people``, see :mod:`stonepi_auth.internal`) at most
once per ``ttl`` seconds and answers "may this person still use my app / this
capability?".

Auth's roster lists **enabled** users only, without names, each with
``is_admin`` and ``permissions`` (``{app_id: {capability: bool}}`` for the apps
they are granted, catalog defaults already applied). From it:

* a person missing from the roster is disabled or deleted: ``has_app`` False,
  no capabilities;
* a person without ``app_id`` in their permissions has no access to the app:
  ``has_app`` False, no capabilities;
* admins always have the app and every capability;
* a capability Auth didn't send (Auth older than this app's catalog) takes the
  local catalog default.

Fallback semantics (read this before using it)
----------------------------------------------
:meth:`Roster.access` returns ``None`` when the answer is **unknown**: no
session secret, Auth unreachable (or too old to send ``permissions``) and no
good roster has been fetched in the last ``max_stale`` seconds. While Auth is
down the last good roster keeps answering (up to ``max_stale``, a day by
default). Nothing here ever raises into callers; failures are logged.

Callers decide what ``None`` means. Recommended: on ``None`` keep the previous
behaviour for that person (e.g. the session-cookie or locally mirrored
capabilities) so an Auth outage never blocks anyone, but when the roster
positively says no (``has_app`` False or ``can(cap)`` False) stop the work.

Usage::

    from stonepi_auth.roster import get_roster

    access = get_roster("pricewatch").access(user.auth_user_id)
    if access is not None and not access.can("can_track_prices"):
        skip()  # revoked, disabled or deleted in Auth
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("stonepi_auth.roster")

ROSTER_PATH = "/api/internal/people"
DEFAULT_AUTH_URL = "http://127.0.0.1:8011"
DEFAULT_TTL_SECONDS = 5 * 60
RETRY_AFTER_FAILURE_SECONDS = 30
MAX_STALE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class MemberAccess:
    """One person's access to one app, as Auth's roster last said.

    ``capabilities`` covers every capability the catalog (or Auth) knows for
    the app, with defaults applied; all True for admins, all False without
    access. ``enabled`` is False when the person is missing from the roster
    (disabled or deleted).
    """

    user_id: str
    app_id: str
    enabled: bool
    is_admin: bool
    has_app: bool
    capabilities: dict[str, bool] = field(default_factory=dict)

    def can(self, cap: str) -> bool:
        """True when this person may use the app and holds ``cap`` (admins: always)."""
        if not self.has_app:
            return False
        if self.is_admin:
            return True
        return bool(self.capabilities.get(cap, False))


def catalog_defaults(app_id: str) -> dict[str, bool]:
    """``{capability: default}`` for ``app_id`` from the shared catalog."""
    try:
        from .catalog import canonical_app_id, capabilities_for

        items = capabilities_for(canonical_app_id(app_id))
    except Exception:  # noqa: BLE001
        return {}
    return {str(item["id"]): bool(item.get("default", False)) for item in items if item.get("id")}


def member_access(
    row: Mapping[str, Any] | None,
    app_id: str,
    *,
    user_id: str = "",
    defaults: Mapping[str, bool] | None = None,
) -> MemberAccess:
    """Resolve one roster row (or ``None`` = not on the roster) for ``app_id``."""
    known = dict(catalog_defaults(app_id) if defaults is None else defaults)
    if row is None:
        return MemberAccess(
            user_id=str(user_id), app_id=app_id, enabled=False, is_admin=False,
            has_app=False, capabilities={cap: False for cap in known},
        )
    uid = str(row.get("id") or user_id)
    perms = row.get("permissions") if isinstance(row.get("permissions"), Mapping) else {}
    granted = perms.get(app_id)
    for cap in granted or {}:
        known.setdefault(str(cap), False)
    if bool(row.get("is_admin")):
        return MemberAccess(
            user_id=uid, app_id=app_id, enabled=True, is_admin=True,
            has_app=True, capabilities={cap: True for cap in known},
        )
    if app_id not in perms:
        return MemberAccess(
            user_id=uid, app_id=app_id, enabled=True, is_admin=False,
            has_app=False, capabilities={cap: False for cap in known},
        )
    stored = granted if isinstance(granted, Mapping) else {}
    caps = {cap: bool(stored.get(cap, default)) for cap, default in known.items()}
    return MemberAccess(
        user_id=uid, app_id=app_id, enabled=True, is_admin=False, has_app=True, capabilities=caps,
    )


def parse_people(rows: Any) -> dict[str, dict] | None:
    """Validate Auth's ``people`` list into ``{id: row}``; None when unusable.

    An empty roster (never true for a working Auth) or one from before per-app
    permissions were included tells us nothing safe, so it counts as a failure.
    """
    if not isinstance(rows, list):
        return None
    people = {str(row["id"]): row for row in rows if isinstance(row, dict) and str(row.get("id") or "").strip()}
    if not people or not all("permissions" in row for row in people.values()):
        return None
    return people


def _default_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if str(vaulted or "").strip():
            return str(vaulted).strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("STONEPI_SESSION_SECRET", "").strip()


def _internal_base(configured: str | None) -> str:
    """Loopback base for service calls. A browser path such as ``/auth`` is not one."""
    for candidate in (configured, os.environ.get("STONEPI_AUTH_URL")):
        value = str(candidate or "").strip().rstrip("/")
        if value.startswith(("http://", "https://")):
            return value
    return DEFAULT_AUTH_URL


class Roster:
    """Cached view of Auth's roster for one app. Thread-safe; never raises.

    ``auth_url``: Auth's internal base URL (default ``STONEPI_AUTH_URL`` when it
    is an http(s) URL, else ``http://127.0.0.1:8011``).
    ``secret_getter``: returns the platform session secret (default: Vault, then
    ``STONEPI_SESSION_SECRET``). An empty secret means "not on StonePi SSO":
    nothing is fetched and :meth:`access` returns None.
    ``ttl``: seconds a good roster is trusted before the next pull. After a
    failed pull the next attempt waits ``min(ttl, 30)`` seconds.
    ``max_stale``: how long the last good roster keeps answering while Auth is
    down; after that :meth:`access` returns None.
    """

    def __init__(
        self,
        app_id: str,
        auth_url: str | None = None,
        secret_getter: Callable[[], str] | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
        *,
        max_stale: float = MAX_STALE_SECONDS,
        timeout: float = 2.0,
    ) -> None:
        self.app_id = str(app_id)
        self.auth_url = auth_url
        self.secret_getter = secret_getter or _default_secret
        self.ttl = float(ttl)
        self.max_stale = float(max_stale)
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._fetching = threading.Lock()
        self._people: dict[str, dict] | None = None
        self._good_at = 0.0  # monotonic time of the last good pull
        self._attempt_at = 0.0  # monotonic time of the last attempt
        self._last_ok = False

    # -- fetching -------------------------------------------------------------

    def _fetch(self) -> dict[str, dict] | None:
        from . import internal  # looked up per call so tests can patch get_internal_json

        try:
            secret = str(self.secret_getter() or "").strip()
        except Exception:  # noqa: BLE001
            logger.warning("Roster %s: couldn't read the session secret", self.app_id, exc_info=True)
            return None
        if not secret:
            return None
        body = internal.get_internal_json(_internal_base(self.auth_url), secret, ROSTER_PATH, timeout=self.timeout)
        if not isinstance(body, dict):
            logger.info("Roster %s: Auth roster unavailable", self.app_id)
            return None
        people = parse_people(body.get("people"))
        if people is None:
            logger.warning("Roster %s: Auth roster empty or without permissions (old Auth?)", self.app_id)
        return people

    def _due(self, now: float) -> bool:
        if not self._attempt_at:
            return True
        wait = self.ttl if self._last_ok else min(self.ttl, RETRY_AFTER_FAILURE_SECONDS)
        return now - self._attempt_at >= wait

    def refresh(self, force: bool = False) -> bool:
        """Pull the roster if the cache is due (or ``force``).

        True only when a good roster was fetched by this call; False when the
        cache was still fresh, another thread is already pulling, or the pull
        failed (the last good roster is kept).
        """
        try:
            now = time.monotonic()
            with self._lock:
                if not force and not self._due(now):
                    return False
            if not self._fetching.acquire(blocking=False):
                return False
            try:
                with self._lock:
                    if not force and not self._due(time.monotonic()):
                        return False
                    self._attempt_at = time.monotonic()
                people = self._fetch()
                with self._lock:
                    self._last_ok = people is not None
                    if people is not None:
                        self._people = people
                        self._good_at = time.monotonic()
                return people is not None
            finally:
                self._fetching.release()
        except Exception:  # noqa: BLE001
            logger.exception("Roster %s: refresh failed", self.app_id)
            return False

    # -- answers --------------------------------------------------------------

    def _current(self) -> dict[str, dict] | None:
        with self._lock:
            if self._people is None:
                return None
            if time.monotonic() - self._good_at > self.max_stale:
                return None
            return self._people

    def members(self, *, refresh: bool = True) -> dict[str, MemberAccess] | None:
        """Everyone on the roster resolved for this app, or None when unknown.

        People missing from the roster (disabled/deleted) are not listed; use
        :meth:`access` for a specific id to get their "no access" answer.
        """
        try:
            if refresh:
                self.refresh()
            people = self._current()
            if people is None:
                return None
            defaults = catalog_defaults(self.app_id)
            return {uid: member_access(row, self.app_id, defaults=defaults) for uid, row in people.items()}
        except Exception:  # noqa: BLE001
            logger.exception("Roster %s: members failed", self.app_id)
            return None

    def access(self, auth_user_id: Any, *, refresh: bool = True) -> MemberAccess | None:
        """This person's access to the app, or None when unknown (see module docs).

        Refreshes the cache first when it is due (``refresh=False`` to skip).
        """
        try:
            if refresh:
                self.refresh()
            people = self._current()
            if people is None:
                return None
            uid = str(auth_user_id or "").strip()
            return member_access(people.get(uid) if uid else None, self.app_id, user_id=uid)
        except Exception:  # noqa: BLE001
            logger.exception("Roster %s: access failed", self.app_id)
            return None

    def access_many(self, auth_user_ids: Iterable[Any], *, refresh: bool = True) -> dict[str, MemberAccess] | None:
        """:meth:`access` for several ids with one cache check; None when unknown."""
        try:
            if refresh:
                self.refresh()
            people = self._current()
            if people is None:
                return None
            defaults = catalog_defaults(self.app_id)
            out: dict[str, MemberAccess] = {}
            for raw in auth_user_ids:
                uid = str(raw or "").strip()
                if uid:
                    out[uid] = member_access(people.get(uid), self.app_id, user_id=uid, defaults=defaults)
            return out
        except Exception:  # noqa: BLE001
            logger.exception("Roster %s: access_many failed", self.app_id)
            return None

    def age_seconds(self) -> float | None:
        """Seconds since the last good pull, or None if there never was one."""
        with self._lock:
            return None if self._people is None else time.monotonic() - self._good_at

    def clear(self) -> None:
        """Forget the cached roster (tests, or after the secret changes)."""
        with self._lock:
            self._people = None
            self._good_at = 0.0
            self._attempt_at = 0.0
            self._last_ok = False


_registry: dict[str, Roster] = {}
_registry_lock = threading.Lock()


def get_roster(
    app_id: str,
    *,
    auth_url: str | None = None,
    secret_getter: Callable[[], str] | None = None,
    ttl: float = DEFAULT_TTL_SECONDS,
    max_stale: float = MAX_STALE_SECONDS,
) -> Roster:
    """The process-wide :class:`Roster` for ``app_id``.

    Options apply when the roster is first created; later calls return the
    same instance (and ignore their options).
    """
    with _registry_lock:
        roster = _registry.get(app_id)
        if roster is None:
            roster = Roster(app_id, auth_url, secret_getter, ttl, max_stale=max_stale)
            _registry[app_id] = roster
        return roster


def reset_rosters() -> None:
    """Drop every process-wide roster (tests)."""
    with _registry_lock:
        _registry.clear()


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_STALE_SECONDS",
    "ROSTER_PATH",
    "MemberAccess",
    "Roster",
    "catalog_defaults",
    "get_roster",
    "member_access",
    "parse_people",
    "reset_rosters",
]
