"""Shared app header: app switcher, avatar account menu and the bell's panel.

Every StonePi app renders the same cross-app controls on the right of its
top bar (``stonepi/header.html``)::

    [App logo + name]  …  [Search] [Primary action] [Bell] [App switcher] [Avatar]

The app supplies its logo, search page, primary action and bottom-bar tabs;
this module supplies everything that is the same everywhere. State comes from
``stonepi_auth.alerts.bell_context(..., app_id=...)`` so each app passes one dict.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from .catalog import APP_CATALOG, LAUNCHER_APP_IDS

# Dashboard Settings → General → Your password (Auth itself has no pages).
ACCOUNT_PATH = "/settings?tab=general#account-password"


def app_page_url(home_url: str, app_id: str, page: str = "") -> str:
    """Absolute browser URL of a page in another app, for a portal home URL.

    Behind nginx apps live under their path (``/news/``); in local dev
    (loopback home with a port, no nginx) each has its own port instead.
    Absolute on purpose: app HTML is prefix-rewritten, and a root path like
    ``/news/`` would otherwise be fine, but ``/`` (Dashboard) would not.
    """
    item = next((a for a in APP_CATALOG if a.get("id") == app_id), {})
    page = str(page or "").lstrip("/")
    home = str(home_url or "").rstrip("/")
    if app_id == "dashboard":
        return f"{home}/{page}"
    parts = urlsplit(home)
    if parts.hostname in {"127.0.0.1", "localhost"} and parts.port and item.get("port"):
        return f"{parts.scheme or 'http'}://{parts.hostname}:{item['port']}/{page}"
    path = str(item.get("path") or f"/{app_id}/").rstrip("/")
    return f"{home}{path}/{page}"


# The person's Home tile order, for the app switcher in every app. Auth keeps the real order;
# the Dashboard copies it into this cookie (shared by every app on the host, like
# ``stonepi-theme``) on Home and on save, and the header's script orders the switcher by it.
LAUNCHER_ORDER_COOKIE = "stonepi-launcher-order"


def launcher_order_cookie_value(order: list[str]) -> str:
    """Known app ids joined with dots (a cookie value needs no quoting that way)."""
    return ".".join(app_id for app_id in order if app_id in LAUNCHER_APP_IDS)


def launcher_order_from_cookie(value: str | None) -> list[str]:
    """The Home order read back from the cookie: its known apps first, then the rest in catalog order."""
    saved = [app_id for app_id in (value or "").split(".") if app_id in LAUNCHER_APP_IDS]
    saved = list(dict.fromkeys(saved))
    return saved + [app_id for app_id in LAUNCHER_APP_IDS if app_id not in saved]


def set_launcher_order_cookie(response: Any, order: list[str], *, secure: bool = False) -> None:
    """Remember the Home order for the switcher (readable by the page's script: not a secret)."""
    response.set_cookie(
        LAUNCHER_ORDER_COOKIE,
        launcher_order_cookie_value(order),
        max_age=365 * 24 * 3600,
        path="/",
        samesite="lax",
        secure=secure,
        httponly=False,
    )


def switcher_apps(user: Any, home_url: str, current: str) -> list[dict[str, Any]]:
    """The nine user apps in Dashboard order, filtered to what this person may open."""
    by_id = {a["id"]: a for a in APP_CATALOG}
    is_admin = bool(getattr(user, "is_admin", False))
    can_access = getattr(user, "can_access", None)
    out = []
    for app_id in LAUNCHER_APP_IDS:
        item = by_id.get(app_id)
        if item is None:
            continue
        if user is not None and not is_admin and callable(can_access) and not can_access(app_id) and app_id != current:
            continue
        out.append(
            {
                "id": app_id,
                "name": item.get("name") or app_id,
                "icon": item.get("icon") or app_id,
                "color": item.get("color") or "",
                "url": app_page_url(home_url, app_id),
                "current": app_id == current,
            }
        )
    return out


def _initials(name: str) -> str:
    words = [w for w in str(name or "").replace("_", " ").replace(".", " ").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[-1][:1]).upper()


def account_context(user: Any, home_url: str) -> dict[str, Any] | None:
    """Avatar menu identity; None when nobody is signed in to the platform."""
    if user is None:
        return None
    username = str(getattr(user, "username", "") or "")
    name = str(getattr(user, "display_name", "") or username)
    return {
        "name": name,
        "username": username,
        "initials": _initials(name),
        "role": "Admin" if getattr(user, "is_admin", False) else "Member",
        "manage_url": f"{str(home_url or '').rstrip('/')}{ACCOUNT_PATH}" if home_url else "",
    }


def header_context(app_id: str, user: Any, *, home_url: str) -> dict[str, Any]:
    """Switcher + avatar state (the bell's own state comes from ``bell_context``)."""
    home = str(home_url or "").rstrip("/")
    return {
        "app": app_id,
        "dashboard_url": app_page_url(home, "dashboard") if home else "",
        "apps": switcher_apps(user, home, app_id) if home else [],
        "account": account_context(user, home),
    }
