"""Phone alerts admin page (/alerts), History, About, and the admin summary API.

One page for the whole household setup: connect ntfy, approve alerts, choose
who can use them, and set up your own phone. A checklist at the top shows
what's done; once everything is, the page leads with a summary instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import people
from app.routes import APP_BLURB, _approval_view, _guard, _page_ctx, templates
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request, set_csrf_cookie
from stonepi_auth.http import request_is_https

router = APIRouter()
logger = logging.getLogger("notify.alerts")

ON = {"1", "on", "true"}
NTFY_SH = "https://ntfy.sh"


def _back(path: str = "/alerts", *, msg: str = "", err: str = "", anchor: str = "") -> RedirectResponse:
    query = f"?err={quote(err)}" if err else (f"?msg={quote(msg)}" if msg else "")
    return RedirectResponse(f"{path}{query}{('#' + anchor) if anchor else ''}", status_code=303)


def _ago(stamp: str) -> str:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    minutes = int((datetime.now(timezone.utc) - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def _last_delivery() -> dict[str, Any] | None:
    """Newest phone-alert delivery attempt (not TRMNL, not 'alerts off')."""
    import stonepi_notify

    for item in stonepi_notify.load_history(limit=50):
        if item.get("channel") != "ntfy" or str(item.get("message") or "").startswith("ntfy disabled"):
            continue
        deliveries = item.get("deliveries")
        sent = sum(1 for d in deliveries or [] if d.get("ok")) if deliveries is not None else None
        return {
            "ok": bool(item.get("ok")),
            "title": item.get("title") or "",
            "message": item.get("message") or "",
            "ago": _ago(item.get("at") or ""),
            "sent": sent,
            "total": len(deliveries) if deliveries is not None else None,
        }
    return None


def alerts_overview(user, cookies: dict[str, str]) -> dict[str, Any]:
    """Everything the checklist, summary and Dashboard status line need."""
    import stonepi_notify
    from stonepi_contracts import AUDIENCE_ADMIN, EVENT_CATALOG

    ntfy = stonepi_notify.load_destinations().get("ntfy") or {}
    prefs = stonepi_notify.load_prefs()
    decided = prefs.get("events") or {}
    approved = sum(1 for ev in EVENT_CATALOG if stonepi_notify.normalize_approval(decided.get(ev.id, False))["approved"])
    needs_review = sum(1 for ev in EVENT_CATALOG if ev.id not in decided)
    admin_only_open = [ev.label for ev in EVENT_CATALOG if ev.audience == AUDIENCE_ADMIN and ev.id not in decided]

    users = people.auth_users(cookies)
    household = None
    if users is not None:
        household = [
            {
                "id": u["id"],
                "name": u.get("display_name") or u.get("username") or "Someone",
                "is_admin": bool(u.get("is_admin")),
                "phone_alerts": bool(u.get("is_admin") or u.get("phone_alerts")),
            }
            for u in users
            if u.get("enabled", True)
        ]
        household.sort(key=lambda p: (not p["is_admin"], p["name"].casefold()))
    members = [p for p in household or [] if not p["is_admin"]]
    members_on = sum(1 for p in members if p["phone_alerts"])

    sub = stonepi_notify.get_subscription(getattr(user, "user_id", "")) if user else None
    last = _last_delivery()
    enabled = bool(ntfy.get("enabled"))
    steps = {
        "connect": enabled,
        "choose": approved > 0,
        # Done once someone besides the admins can use alerts, or when there's nobody else.
        "people": household is not None and (members_on > 0 or not members),
        "phone": bool(sub and sub.get("enabled") and sub.get("topic")),
    }
    return {
        "enabled": enabled,
        "server": str(ntfy.get("server") or NTFY_SH),
        "own_server": str(ntfy.get("server") or NTFY_SH).rstrip("/") != NTFY_SH,
        "household_topic": str(ntfy.get("household_topic") or ""),
        "default_priority": ntfy.get("default_priority") or 3,
        "token_set": bool(stonepi_notify.vault_ntfy_token()),
        "approved": approved,
        "total": len(EVENT_CATALOG),
        "needs_review": needs_review,
        "admin_only_open": admin_only_open,
        "household": household,
        "members": len(members),
        "members_on": members_on,
        "people_on": sum(1 for p in household or [] if p["phone_alerts"]),
        "phone_ready": steps["phone"],
        "last": last,
        "steps": steps,
        "steps_done": sum(steps.values()),
        "all_set": all(steps.values()),
        # Only real problems: nothing can be sent, or the last send failed.
        "needs_attention": enabled and (approved == 0 or bool(last and not last["ok"])),
    }


def _admin(request: Request, path: str, active: str):
    return _guard(request, path=path, active=active, template="denied.html")


def _render(request: Request, template: str, active: str, user, extra: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(request, template, _page_ctx(request, active=active, user=user, csrf=csrf, extra=extra))
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


# -- pages -----------------------------------------------------------------------------------


@router.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request):
    import stonepi_notify

    user, denied = _admin(request, "/alerts", "alerts")
    if denied is not None:
        return denied
    prefs = stonepi_notify.load_prefs()
    approval_groups, needs_review_other, needs_review_count = _approval_view(prefs)
    return _render(
        request,
        "alerts.html",
        "alerts",
        user,
        {
            "overview": alerts_overview(user, dict(request.cookies)),
            "approval_groups": approval_groups,
            "needs_review_other": needs_review_other,
            "needs_review_count": needs_review_count,
            "outputs_required": bool(prefs.get("outputs_required")),
        },
    )


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    import stonepi_notify

    user, denied = _admin(request, "/history", "history")
    if denied is not None:
        return denied
    return _render(request, "history.html", "history", user, {"history": stonepi_notify.load_history(limit=60)})


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    from stonepi_contracts import WIDGET_CATALOG

    user, denied = _admin(request, "/about", "about")
    if denied is not None:
        return denied
    return _render(request, "about.html", "about", user, {"app_blurb": APP_BLURB, "widget_catalog_count": len(WIDGET_CATALOG)})


# -- actions ---------------------------------------------------------------------------------


async def _form(request: Request, path: str, active: str):
    user, denied = _admin(request, path, active)
    if denied is not None:
        return None, None, denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return None, None, _back(err="Form expired. Try again.")
    return user, form, None


@router.post("/alerts/connect")
async def alerts_connect(request: Request):
    import stonepi_notify

    user, form, bail = await _form(request, "/alerts", "alerts")
    if bail is not None:
        return bail
    own = str(form.get("server_choice") or "ntfysh") == "own"
    server = str(form.get("server") or "").strip() if own else NTFY_SH
    if own and not server.startswith(("https://", "http://")):
        return _back(err="Enter your ntfy server address, e.g. https://ntfy.example.com.")
    token = str(form.get("token") or "").strip()
    if own and token and not _save_token(token):
        return _back(err=TOKEN_NOT_SAVED)
    stonepi_notify.save_destinations({"ntfy": {"enabled": True, "server": server}})
    return _back(msg="Phone alerts are on. Next, choose which alerts are allowed.", anchor="choose")


@router.post("/alerts/connection")
async def alerts_connection(request: Request):
    import stonepi_notify

    user, form, bail = await _form(request, "/alerts", "alerts")
    if bail is not None:
        return bail
    if str(form.get("action") or "") == "off":
        stonepi_notify.save_destinations({"ntfy": {"enabled": False}})
        return _back(msg="Phone alerts are off for the household.")
    token = str(form.get("token") or "").strip()
    if token and not _save_token(token):
        return _back(err=TOKEN_NOT_SAVED, anchor="advanced")
    stonepi_notify.save_destinations(
        {
            "ntfy": {
                "server": str(form.get("server") or NTFY_SH).strip() or NTFY_SH,
                "household_topic": str(form.get("household_topic") or "").strip(),
                "default_priority": str(form.get("default_priority") or "3"),
            }
        }
    )
    stonepi_notify.save_prefs({"outputs_required": str(form.get("outputs_required") or "") in ON})
    return _back(msg="Saved.", anchor="advanced")


TOKEN_NOT_SAVED = "The ntfy access token could not be saved to the Vault; check the Vault in Dashboard → Settings."


def _save_token(token: str) -> bool:
    """Store the ntfy token in the Vault. False (and logged) if it could not be written."""
    try:
        from stonepi_vault import set_secret

        set_secret("STONEPI_NTFY_TOKEN", token)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("could not save STONEPI_NTFY_TOKEN to the vault", exc_info=True)
        return False


@router.post("/alerts/approvals")
async def alerts_approvals(request: Request):
    import stonepi_notify

    user, form, bail = await _form(request, "/alerts", "alerts")
    if bail is not None:
        return bail
    events = dict(stonepi_notify.load_prefs().get("events") or {})
    # Every event shown on the form posts event_known_<id>; unticked boxes mean no.
    for key in form.keys():
        if key.startswith("event_known_"):
            eid = key[len("event_known_") :]
            events[eid] = {
                "approved": str(form.get(f"approve_{eid}") or "") in ON,
                "admin_only": str(form.get(f"admin_only_{eid}") or "") in ON,
                "urgent": str(form.get(f"urgent_{eid}") or "") in ON,
            }
    stonepi_notify.save_prefs({"events": events})
    return _back(msg="Alerts saved.", anchor="choose")


@router.post("/alerts/approve-recommended")
async def alerts_approve_recommended(request: Request):
    """Approve every household and personal alert; leave admin-only ones for the admin."""
    import stonepi_notify
    from stonepi_contracts import AUDIENCE_ADMIN, EVENT_CATALOG

    user, form, bail = await _form(request, "/alerts", "alerts")
    if bail is not None:
        return bail
    events = dict(stonepi_notify.load_prefs().get("events") or {})
    approved, left = 0, []
    for ev in EVENT_CATALOG:
        current = stonepi_notify.normalize_approval(events.get(ev.id, False))
        if ev.audience == AUDIENCE_ADMIN:
            if not current["approved"]:
                left.append(ev.label)
            continue
        if not current["approved"]:
            approved += 1
        events[ev.id] = {**current, "approved": True}
    stonepi_notify.save_prefs({"events": events})
    msg = f"Approved {approved} alert{'' if approved == 1 else 's'}"
    if left:
        msg += f" · {', '.join(left)} left for you to decide"
    return _back(msg=msg, anchor="choose")


@router.post("/alerts/people/{user_id}")
async def alerts_person(request: Request, user_id: str):
    user, form, bail = await _form(request, "/alerts", "alerts")
    if bail is not None:
        return bail
    ok, message = people.set_phone_alerts(dict(request.cookies), user_id, str(form.get("phone_alerts") or "") in ON)
    if not ok:
        return _back(err=f"Could not change phone alerts: {message}", anchor="people")
    return _back(msg="Saved.", anchor="people")


@router.post("/alerts/test")
async def alerts_test(request: Request):
    """Test to your own phone if it's set up, else the household channel."""
    import stonepi_notify

    user, form, bail = await _form(request, "/alerts", "alerts")
    if bail is not None:
        return bail
    sub = stonepi_notify.get_subscription(getattr(user, "user_id", "")) if user else None
    household = str((stonepi_notify.load_destinations().get("ntfy") or {}).get("household_topic") or "")
    if sub and sub.get("enabled") and sub.get("topic"):
        topic, where = sub["topic"], "your phone"
    elif household:
        topic, where = household, "the household channel"
    else:
        return _back(err="Set up your phone first (step 4), or add a household channel under Advanced.")
    result = stonepi_notify.deliver_ntfy(
        title="StonePi", body="Phone alerts are working.", tags="white_check_mark",
        source="notify", event_id="notify.test", topic=topic,
    )
    if not result.get("ok"):
        return _back(err=f"Test failed: {str(result.get('message') or '')[:120]}")
    return _back(msg=f"Test sent to {where}.")


# -- Dashboard status line ------------------------------------------------------------------


@router.get("/api/admin/summary")
def admin_summary(request: Request):
    """Admin-only status for Dashboard → Settings (session auth)."""
    import stonepi_display

    user, denied = _admin(request, "/alerts", "alerts")
    if denied is not None or user is None:
        return JSONResponse({"ok": False}, status_code=403)
    displays = stonepi_display.load_displays()
    pushing = len(stonepi_display.pushable_displays())
    o = alerts_overview(user, dict(request.cookies))
    return {
        "ok": True,
        "displays": {"count": len(displays), "pushing": pushing},
        "alerts": {
            "enabled": o["enabled"],
            "approved": o["approved"],
            "total": o["total"],
            "people_on": o["people_on"],
            "all_set": o["all_set"],
            "needs_attention": o["needs_attention"],
        },
    }
