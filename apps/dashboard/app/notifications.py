"""Dashboard Notifications page — each person's own personal alerts.

Opened from the top-bar bell. Reads and writes the person's subscription
through Notify's ``/api/me/*`` API, forwarding their session; Notify only
ever touches the caller's own record.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import services
from app.routes import _html, _require_csrf, _user_or_login

router = APIRouter()

ON = {"1", "on", "true"}


def _qr_svg(url: str) -> str:
    try:
        import segno
    except ImportError:  # optional: page still works with the link alone
        return ""
    return segno.make(url, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff")


def _back(msg: str = "", err: str = "") -> RedirectResponse:
    query = ""
    if err:
        query = f"?err={quote(err, safe='')}"
    elif msg:
        query = f"?msg={quote(msg, safe='')}"
    return RedirectResponse(f"/notifications{query}", status_code=303)


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request):
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    status, state = services.notify_request("GET", "/api/me/subscription", dict(request.cookies))
    unreachable = status != 200
    if unreachable:
        state = {"allowed": False, "available": False, "groups": []}
    sub = state.get("subscription") or {}
    subscribe = state.get("subscribe") or {}
    enabled = bool(sub.get("enabled") and sub.get("topic"))
    return _html(
        request,
        "notifications.html",
        user,
        {
            "active": "notifications",
            "error": request.query_params.get("err") or (state.get("message") if unreachable else None),
            "message": request.query_params.get("msg") or None,
            "alerts": state,
            "alerts_unreachable": unreachable,
            "alerts_enabled": enabled,
            "subscription": sub,
            "subscribe": subscribe,
            "qr_svg": _qr_svg(subscribe["web_url"]) if enabled and subscribe.get("web_url") else "",
            "offered_ids": [e["id"] for g in state.get("groups") or [] for e in g.get("events") or []],
            "pi_clock": datetime.now().astimezone().strftime("%H:%M"),
            "notify_destinations_url": services.notify_page_url("alerts"),
            "notify_approvals_url": services.notify_page_url("alerts#choose"),
            **(
                {}
                if unreachable
                else {
                    "alerts_bell_state": {
                        "show": bool(state.get("allowed") and state.get("available")),
                        "dot": bool(state.get("allowed") and state.get("available") and state.get("needs_setup")),
                    }
                }
            ),
        },
    )


@router.post("/notifications")
async def notifications_action(request: Request):
    from stonepi_contracts import forget_alerts_status

    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return _back(err="Form expired. Try again.")
    action = str(form.get("action") or "").strip()
    cookies = dict(request.cookies)

    if action == "on":
        status, body = services.notify_request("PUT", "/api/me/subscription", cookies, {"enabled": True})
        done = "Personal alerts are on. Scan the code with your phone."
    elif action == "off":
        status, body = services.notify_request("PUT", "/api/me/subscription", cookies, {"enabled": False})
        done = "Personal alerts are off."
    elif action == "test":
        status, body = services.notify_request("POST", "/api/me/test", cookies)
        done = "Test sent. Check your phone."
    elif action == "rotate":
        status, body = services.notify_request("POST", "/api/me/rotate", cookies)
        done = "New topic created. Subscribe again on your phone with the new code."
    elif action == "save_events":
        offered = [x for x in str(form.get("offered") or "").split(",") if x]
        events = {eid: str(form.get(f"event_{eid}") or "") in ON for eid in offered}
        status, body = services.notify_request("PUT", "/api/me/subscription", cookies, {"events": events})
        done = "Alert choices saved."
    elif action == "save_quiet":
        quiet = {
            "enabled": str(form.get("quiet_enabled") or "") in ON,
            "start": str(form.get("quiet_start") or "").strip(),
            "end": str(form.get("quiet_end") or "").strip(),
        }
        status, body = services.notify_request("PUT", "/api/me/subscription", cookies, {"quiet_hours": quiet})
        done = "Quiet hours saved."
    else:
        return _back(err="Unknown action.")

    forget_alerts_status(user.user_id)
    if status >= 400:
        return _back(err=str(body.get("message") or "Something went wrong.")[:200])
    return _back(msg=done)
