"""Personal alerts API — the caller's own subscription only.

Used by the Dashboard Notifications page (server-side, forwarding the
person's cookies). Authenticated by the shared StonePi session; writes need
the CSRF header. Nothing here reads or changes another person's record.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from stonepi_auth.csrf import csrf_ok_request
from stonepi_auth.session import COOKIE_NAME, PlatformUser, decode_session

from app.people import load_people, session_secret

router = APIRouter(prefix="/api/me")

AUDIENCE_LABELS = {"personal": "Just for you", "household": "Household", "admin": "Admins"}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status)


def _caller(request: Request) -> tuple[PlatformUser | None, bool, JSONResponse | None]:
    """``(user, phone_alerts_allowed, error)``."""
    secret = session_secret()
    user = decode_session(request.cookies.get(COOKIE_NAME), secret) if secret else None
    if user is None:
        return None, False, _error(401, "Not signed in")
    people = load_people()
    if people is not None:
        person = people.get(user.user_id)
        if person is None:  # disabled or deleted since the cookie was issued
            return None, False, _error(401, "Not signed in")
        return user, bool(person.get("phone_alerts")), None
    # Auth unreachable: fall back to what the signed session says.
    from stonepi_auth.alerts import phone_alerts_allowed

    return user, phone_alerts_allowed(user), None


def _csrf_ok(request: Request) -> bool:
    header = request.headers.get("x-stonepi-csrf") or ""
    return csrf_ok_request(request.cookies, header_token=header)


def _offered(user: PlatformUser, prefs: dict[str, Any]) -> list[dict[str, Any]]:
    """Events this person may tick: approved, and admin-only ones for admins."""
    import stonepi_notify
    from stonepi_contracts import AUDIENCE_ADMIN, EVENT_CATALOG

    out = []
    for ev in EVENT_CATALOG:
        approval = stonepi_notify.event_approval(ev.id, prefs)
        if not approval["approved"]:
            continue
        audience = stonepi_notify.effective_audience({"id": ev.id, "audience": ev.audience}, approval)
        if (audience == AUDIENCE_ADMIN or approval["admin_only"]) and not user.is_admin:
            continue
        out.append({"event": ev, "audience": audience})
    return out


def _subscribe_links(server: str, topic: str) -> dict[str, str]:
    web = f"{server.rstrip('/')}/{quote(topic, safe='')}"
    host = urlsplit(server).netloc or "ntfy.sh"
    return {"web_url": web, "app_url": f"ntfy://{host}/{quote(topic, safe='')}"}


def _payload(user: PlatformUser, allowed: bool) -> dict[str, Any]:
    import stonepi_notify
    from stonepi_contracts import app_label

    ntfy = stonepi_notify.load_destinations().get("ntfy") or {}
    available = bool(ntfy.get("enabled"))
    body: dict[str, Any] = {
        "ok": True,
        "available": available,
        "allowed": allowed,
        "is_admin": user.is_admin,
        "subscription": None,
        "subscribe": None,
        "groups": [],
        "needs_setup": allowed and available,
    }
    if not allowed:
        return body
    sub = stonepi_notify.get_subscription(user.user_id)
    ticks = (sub or {}).get("events") or {}
    groups: dict[str, dict[str, Any]] = {}
    for item in _offered(user, stonepi_notify.load_prefs()):
        ev = item["event"]
        group = groups.setdefault(ev.app, {"id": ev.app, "label": app_label(ev.app), "events": []})
        group["events"].append(
            {
                "id": ev.id,
                "label": ev.label,
                "blurb": ev.blurb,
                "audience": item["audience"],
                "audience_label": AUDIENCE_LABELS.get(item["audience"], item["audience"]),
                "ticked": bool(ticks.get(ev.id)),
            }
        )
    body["groups"] = list(groups.values())
    if sub:
        body["subscription"] = {
            "enabled": sub["enabled"],
            "topic": sub["topic"],
            "quiet_hours": sub["quiet_hours"],
            "updated_at": sub["updated_at"],
        }
        if sub["topic"]:
            body["subscribe"] = _subscribe_links(str(ntfy.get("server") or ""), sub["topic"])
    # Never set up = no personal topic yet. Someone who turned alerts off keeps
    # their topic, so they don't get the "not set up" nudge again.
    body["needs_setup"] = available and not (sub and sub["topic"])
    return body


@router.get("/subscription")
def get_subscription(request: Request):
    user, allowed, err = _caller(request)
    if err is not None:
        return err
    return _payload(user, allowed)


@router.put("/subscription")
async def put_subscription(request: Request):
    import stonepi_notify

    user, allowed, err = _caller(request)
    if err is not None:
        return err
    if not allowed:
        return _error(403, "Phone alerts are not allowed for your account.")
    if not _csrf_ok(request):
        return _error(403, "CSRF check failed")
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid json")
    if not isinstance(body, dict):
        return _error(400, "Expected a JSON object")

    snapshot = {"username": user.username, "is_admin": user.is_admin}
    if body.get("enabled") is True:
        if not (stonepi_notify.load_destinations().get("ntfy") or {}).get("enabled"):
            return _error(409, "Phone alerts are not turned on for the household yet.")
        stonepi_notify.enable_subscription(user.user_id, **snapshot)
    updates: dict[str, Any] = dict(snapshot)
    if body.get("enabled") is False:
        updates["enabled"] = False
    if isinstance(body.get("events"), dict):
        offered = {item["event"].id for item in _offered(user, stonepi_notify.load_prefs())}
        current = (stonepi_notify.get_subscription(user.user_id) or {}).get("events") or {}
        # Keep ticks for events not offered right now (e.g. temporarily unapproved).
        events = {k: v for k, v in current.items() if k not in offered}
        events.update({k: bool(v) for k, v in body["events"].items() if k in offered})
        updates["events"] = events
    if isinstance(body.get("quiet_hours"), dict):
        quiet = body["quiet_hours"]
        updates["quiet_hours"] = {k: quiet[k] for k in ("enabled", "start", "end") if k in quiet}
    stonepi_notify.save_subscription(user.user_id, updates)
    return _payload(user, allowed)


@router.post("/test")
def post_test(request: Request):
    import stonepi_notify

    user, allowed, err = _caller(request)
    if err is not None:
        return err
    if not allowed:
        return _error(403, "Phone alerts are not allowed for your account.")
    if not _csrf_ok(request):
        return _error(403, "CSRF check failed")
    sub = stonepi_notify.get_subscription(user.user_id)
    if not (sub and sub["enabled"] and sub["topic"]):
        return _error(409, "Turn on personal alerts first.")
    result = stonepi_notify.deliver_ntfy(
        title="StonePi",
        body="Personal alerts are working on this phone.",
        tags="white_check_mark",
        source="notify",
        event_id="notify.test_personal",
        topic=sub["topic"],
    )
    if not result.get("ok"):
        return _error(502, str(result.get("message") or "Test failed")[:160])
    return {"ok": True, "message": "Test sent."}


@router.post("/rotate")
def post_rotate(request: Request):
    import stonepi_notify

    user, allowed, err = _caller(request)
    if err is not None:
        return err
    if not allowed:
        return _error(403, "Phone alerts are not allowed for your account.")
    if not _csrf_ok(request):
        return _error(403, "CSRF check failed")
    if not stonepi_notify.get_subscription(user.user_id):
        return _error(409, "Turn on personal alerts first.")
    stonepi_notify.rotate_topic(user.user_id, username=user.username)
    return _payload(user, allowed)
