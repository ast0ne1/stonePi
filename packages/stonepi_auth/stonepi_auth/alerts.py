"""Personal alerts helpers shared by every StonePi app.

- ``phone_alerts_allowed``: may this person use personal alerts (session only).
- ``add_shared_templates``: expose ``stonepi/alerts.html`` (bell + Settings card)
  to any Jinja environment — Starlette ``templates.env`` or Flask ``app.jinja_env``.
- ``bell_context`` / ``notifications_card_context``: state for those macros.
- ``auth_user_id`` / ``session_user_id``: the owner of a personal event, always a
  platform Auth user id (never a local app id or ``"local"``).

Notify's roster (Auth ``/api/internal/people``) stays authoritative for
delivery; these are the fast answers for rendering pages.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

SHARED_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_NOT_OWNERS = frozenset({"", "local", "none", "null", "anonymous"})


def phone_alerts_allowed(user: Any) -> bool:
    if user is None:
        return False
    if getattr(user, "is_admin", False):
        return True
    flag = getattr(user, "phone_alerts", None)
    if flag is not None:
        return bool(flag)
    # Cookie issued before the platform permission: fall back to NewsCast's old capability.
    has_capability = getattr(user, "has_capability", None)
    return bool(has_capability and has_capability("newscast", "can_use_ntfy"))


# -- Event owners --------------------------------------------------------------


def auth_user_id(value: Any) -> str | None:
    """Normalise a personal event's owner to an Auth user id, or None.

    Rejects local placeholders (``"local"``) and bare integers, which are
    always local app user ids — personal events must never carry those.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None
    text = str(value).strip()
    if text.lower() in _NOT_OWNERS or text.isdigit():
        return None
    return text[:64]


def session_user_id(cookies: Mapping[str, str] | None, secret: str) -> str | None:
    """Auth user id of the signed-in person (apps without a local user table)."""
    from .session import COOKIE_NAME, decode_session

    user = decode_session((cookies or {}).get(COOKIE_NAME), secret) if secret else None
    return auth_user_id(user.user_id) if user else None


# -- Shared templates ----------------------------------------------------------


def add_shared_templates(jinja_env: Any) -> Any:
    """Let ``jinja_env`` load ``stonepi/*.html`` from this package (idempotent).

    App templates keep priority; shared ones are the fallback.
    """
    from jinja2 import ChoiceLoader, FileSystemLoader

    shared = str(SHARED_TEMPLATES_DIR)
    loader = jinja_env.loader
    if isinstance(loader, ChoiceLoader):
        for sub in loader.loaders:
            if isinstance(sub, FileSystemLoader) and shared in sub.searchpath:
                return jinja_env
        loader.loaders = [*loader.loaders, FileSystemLoader(shared)]
    else:
        jinja_env.loader = ChoiceLoader([loader, FileSystemLoader(shared)] if loader else [FileSystemLoader(shared)])
    return jinja_env


def notifications_url(home_url: str) -> str:
    return f"{str(home_url or '').rstrip('/')}/notifications"


def notify_page_url(home_url: str, page: str = "") -> str:
    """A Notify page (``alerts``, ``alerts#choose``, ``displays``) for a portal home URL.

    Behind nginx Notify lives under its path (``/notify/``); in local dev
    (loopback home with a port, no nginx) it has its own port instead.
    """
    from urllib.parse import urlsplit

    from .catalog import APP_CATALOG

    notify = next((a for a in APP_CATALOG if a.get("id") == "notify"), {})
    page = str(page or "").lstrip("/")
    home = str(home_url or "").rstrip("/")
    parts = urlsplit(home)
    if parts.hostname in {"127.0.0.1", "localhost"} and parts.port and notify.get("port"):
        return f"{parts.scheme or 'http'}://{parts.hostname}:{notify['port']}/{page}"
    path = str(notify.get("path") or "/notify/").rstrip("/")
    return f"{home}{path}/{page}"


def notify_settings_url(home_url: str, tab: str = "") -> str:
    """Old Notify Settings link (Notify now redirects it to the page for ``tab``)."""
    return notify_page_url(home_url, "settings" + (f"?tab={tab}" if tab else ""))


def bell_context(
    user: Any,
    *,
    session_cookie: str | None,
    home_url: str,
    active: bool = False,
    enabled: bool = True,
) -> dict[str, Any]:
    """State for ``alerts_bell``. Hidden when signed out or not under the platform."""
    if not enabled or user is None:
        return {"show": False, "dot": False, "url": "", "active": False}
    allowed = phone_alerts_allowed(user)
    status = {"show": allowed, "dot": False}
    try:
        from stonepi_contracts import personal_alerts_status

        from .session import COOKIE_NAME

        status = personal_alerts_status(
            getattr(user, "user_id", ""),
            cookie_name=COOKIE_NAME,
            session_cookie=session_cookie,
            session_allowed=allowed,
        )
    except Exception:  # noqa: BLE001 — the bell must never break a page
        pass
    return {**status, "url": notifications_url(home_url), "active": bool(active)}


def notifications_card_context(
    app_id: str,
    user: Any,
    *,
    home_url: str,
    blurb: str | None = None,
) -> dict[str, Any] | None:
    """State for ``notifications_card`` (an app's Settings -> Notifications tab).

    None when signed out / not under the platform, so standalone apps show nothing.
    """
    if user is None:
        return None
    labels: list[str] = []
    name = app_id
    try:
        from stonepi_contracts import app_label, events_for_app

        labels = [ev.label for ev in events_for_app(app_id)]
        name = app_label(app_id)
    except Exception:  # noqa: BLE001
        pass
    if blurb is None:
        blurb = f"{name} can send phone alerts about:" if labels else f"{name} doesn't send phone alerts yet."
    is_admin = bool(getattr(user, "is_admin", False))
    return {
        "blurb": blurb,
        "events": labels,
        "manage_url": notifications_url(home_url) if phone_alerts_allowed(user) else "",
        "is_admin": is_admin,
        "destinations_url": notify_page_url(home_url, "alerts") if is_admin else "",
        "approvals_url": notify_page_url(home_url, "alerts#choose") if is_admin else "",
    }
