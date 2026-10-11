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


# Same path as stonepi_contracts.alerts.NOTIFICATIONS_PATH (see the note there).
NOTIFICATIONS_PATH = "/me/notifications"


def notifications_url(home_url: str) -> str:
    return f"{str(home_url or '').rstrip('/')}{NOTIFICATIONS_PATH}"


def notify_page_url(home_url: str, page: str = "") -> str:
    """A Notify page (``alerts``, ``alerts#choose``, ``displays``) for a portal home URL.

    Behind nginx Notify lives under its path (``/notify/``); in local dev
    (loopback home with a port, no nginx) it has its own port instead.
    """
    from .header import app_page_url

    return app_page_url(home_url, "notify", page)


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
    app_id: str | None = None,
) -> dict[str, Any]:
    """State for ``alerts_bell`` (Dashboard) or the shared app header (``app_id`` set).

    The bell is hidden when signed out or not under the platform. With
    ``app_id`` the state also carries that app's bell panel (``panel``), the
    app switcher and the avatar menu (``header``) for ``stonepi/header.html``.
    """
    state: dict[str, Any] = {"show": False, "dot": False, "url": "", "active": False}
    if app_id:
        from .header import header_context

        # Standalone (no platform): no switcher or platform account, just the app's own Log out.
        state["header"] = header_context(app_id, user if enabled else None, home_url=home_url if enabled else "")
        state["panel"] = None
    if not enabled or user is None:
        return state
    allowed = phone_alerts_allowed(user)
    status: dict[str, Any] = {"show": allowed, "dot": False}
    try:
        from .session import COOKIE_NAME

        if app_id:
            from stonepi_contracts import app_alerts_status

            status = app_alerts_status(
                getattr(user, "user_id", ""),
                app_id,
                cookie_name=COOKIE_NAME,
                session_cookie=session_cookie,
                session_allowed=allowed,
            )
        else:
            from stonepi_contracts import personal_alerts_status

            status = personal_alerts_status(
                getattr(user, "user_id", ""),
                cookie_name=COOKIE_NAME,
                session_cookie=session_cookie,
                session_allowed=allowed,
            )
    except Exception:  # noqa: BLE001 — the bell must never break a page
        pass
    state.update(show=bool(status.get("show")), dot=bool(status.get("dot")))
    state.update(url=notifications_url(home_url), active=bool(active))
    if app_id:
        # The bell itself only shows once an admin has phone alerts on (for the household
        # and this person); with nothing relevant set up, its panel says so and links to setup.
        state["panel"] = _panel(app_id, status, home_url, is_admin=bool(getattr(user, "is_admin", False)))
    return state


def _panel(app_id: str, status: dict[str, Any], home_url: str, *, is_admin: bool = False) -> dict[str, Any]:
    """The bell panel: this app's alerts for you, the last one it sent, and a way to Preferences.

    ``state`` picks what the panel leads with:
    ``ready`` (some of this app's alerts are on), ``none_on`` (none of them are),
    ``no_events`` (the app sends none you can have), ``needs_setup`` (personal alerts
    never set up), ``household_off`` (phone alerts not on for the household),
    ``not_allowed`` (not allowed for this account), ``unreachable`` (Notify down).
    """
    every_app = app_id == "dashboard"  # the Dashboard's bell covers all apps
    try:
        from stonepi_contracts import EVENT_CATALOG, app_label, events_for_app

        name = "StonePi" if every_app else app_label(app_id)
        offered = {ev.id: ev.label for ev in (EVENT_CATALOG if every_app else events_for_app(app_id))}
    except Exception:  # noqa: BLE001
        name, offered = app_id, {}
    events = status.get("events") or []
    reachable = bool(status.get("reachable"))
    if not reachable:
        # Notify not answering: still list what the app can send, without ticks.
        events = [{"id": k, "label": v, "ticked": None} for k, v in offered.items()]
    enabled = bool(status.get("enabled"))
    ticked = enabled and any(e.get("ticked") for e in events)
    if not reachable:
        # Says nothing about whether alerts work: only that this panel couldn't check.
        state, message = "unreachable", "Couldn't load your alerts just now. Refresh the page in a moment to see them."
    elif not status.get("allowed"):
        state, message = "not_allowed", "Phone alerts aren't turned on for your account. A household admin can allow them in StonePi → Users."
    elif not status.get("available"):
        state, message = "household_off", (
            "Phone alerts aren't set up for the household yet. Turn them on in Notify first."
            if is_admin
            else "Phone alerts aren't set up for the household yet. Ask a household admin to turn them on."
        )
    elif status.get("needs_setup"):
        state, message = "needs_setup", (
            "You haven't set up phone alerts yet. Set them up to hear from your apps on your phone."
            if every_app
            else f"You haven't set up phone alerts yet. Set them up to hear from {name} on your phone."
        )
    elif not events:
        state, message = "no_events", f"{name} has no alerts you can turn on yet."
    elif not enabled:
        state, message = "none_on", (
            "Your phone alerts are switched off, so your apps can't reach you."
            if every_app
            else f"Your phone alerts are switched off, so {name} can't reach you."
        )
    elif not ticked:
        state, message = "none_on", (
            "None of your alerts are on yet. Choose which apps can reach your phone."
            if every_app
            else f"None of {name}'s alerts are on for you yet."
        )
    else:
        state, message = "ready", ""
    last = status.get("last")
    button = {
        "needs_setup": "Set up phone alerts",
        "none_on": "Choose alerts",
        "household_off": "Set up phone alerts" if is_admin else "Notification preferences",
    }.get(state, "Preferences")
    button_url = notifications_url(home_url)
    if state == "household_off" and is_admin:
        button_url = notify_page_url(home_url, "alerts")
    return {
        "app_name": name,
        "state": state,
        "message": message,
        "reachable": reachable,
        "enabled": enabled,
        "needs_setup": state == "needs_setup",
        # The alert list only helps once alerts can reach you (or to show what's on offer).
        "events": _panel_events(events, state, every_app),
        "every_app": every_app,
        # Dashboard overview: what's on, per app, and the latest alerts from any app.
        "on_by_app": _on_by_app(events) if every_app and state == "ready" else [],
        "recent": [
            {**_last_view(r), "app_label": r.get("app_label") or r.get("app") or "", "app": r.get("app") or "", "at": r.get("at") or ""}
            for r in (status.get("recent") or [])
        ] if every_app else [],
        "title": "Your alerts" if every_app else f"{name} alerts",
        "last": _last_view(last) if last else None,
        "show_last": state == "ready" or bool(last),
        "last_at": (last or {}).get("at") or "",
        "seen_url": notify_page_url(home_url, f"api/me/apps/{app_id}/seen"),
        # Clear hides what the panel lists, for this person only (Notify keeps its history).
        "clear_url": notify_page_url(home_url, f"api/me/apps/{app_id}/clear"),
        "preferences_url": button_url,
        "button": button,
    }


def _panel_events(events: list[dict[str, Any]], state: str, every_app: bool) -> list[dict[str, Any]]:
    """The alert list a panel shows: an app's own alerts with ticks; on the Dashboard only the ones on."""
    if state not in {"ready", "none_on", "unreachable"}:
        return []
    if every_app:
        return []  # the Dashboard groups what's on by app instead (``on_by_app``)
    return events


def _on_by_app(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``[{"app": "PriceWatch", "labels": ["Target price reached", ...]}]`` for the alerts that are on."""
    groups: dict[str, list[str]] = {}
    for e in events:
        if e.get("ticked"):
            groups.setdefault(str(e.get("app_label") or e.get("app") or ""), []).append(str(e.get("label") or ""))
    return [{"app": app, "labels": labels} for app, labels in groups.items()]


def _last_view(last: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone

    when = ""
    try:
        at = datetime.fromisoformat(str(last.get("at") or ""))
        seconds = int((datetime.now(timezone.utc) - at).total_seconds())
        if seconds < 60:
            when = "just now"
        elif seconds < 3600:
            when = f"{seconds // 60} min ago"
        elif seconds < 86400:
            hours = seconds // 3600
            when = f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = seconds // 86400
            when = "yesterday" if days == 1 else (f"{days} days ago" if days < 7 else at.astimezone().strftime("%d %b"))
    except ValueError:
        pass
    if last.get("ok"):
        delivery = "Sent to your phone, silently (quiet hours)" if last.get("silent") else "Sent to your phone"
    else:
        delivery = "Didn't reach your phone" + (f": {last['message']}" if last.get("message") else "")
    return {"title": last.get("title") or "", "when": when, "ok": bool(last.get("ok")), "delivery": delivery}


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
