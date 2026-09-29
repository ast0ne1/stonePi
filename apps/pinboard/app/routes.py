from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, alerts, people, store
from app.config import ROOT_DIR, env
from stonepi_auth.brand import fonts_rev
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context, notifications_card_context
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()

SETTINGS_TABS = ("notifications", "about")
SETTINGS_TITLES = {"notifications": "Notifications", "about": "About"}
SETTINGS_LEDES = {
    "notifications": "Phone alerts for due reminders and new notices.",
    "about": "App name, description, GitHub, and the version running here.",
}
SETTINGS_HUB_LEDE = "Phone alerts and app details."
SETTINGS_GROUPS = (
    ("alerts", "Alerts", (
        ("notifications", "Notifications", "Phone alerts and reminder time.", "notifications"),
    )),
    ("app", "App", (
        ("about", "About", "Version and project links.", "about"),
    )),
)


def _session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return env.session_secret.strip()


def _settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    return PlatformSettings(
        enabled=bool(_session_secret()),
        session_secret=_session_secret(),
        app_id="pinboard",
        prefix=env.stonepi_prefix or ("/pinboard" if env.routing == "path" else ""),
        auth_url=browser_auth_url(env.auth_url, routing=env.routing),
        public_origin=env.public_origin,
        hostname=env.hostname,
    )


def _user(request: Request):
    return decode_session(request.cookies.get(COOKIE_NAME), _session_secret())


def _safe_next(raw: str, fallback: str = "/") -> str:
    value = (raw or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def _page_ctx(request: Request, *, active: str, user, csrf: str, extra: dict | None = None) -> dict:
    from stonepi_auth.session import factory_admin_warning

    items = store.list_items()
    home = portal_home_url(request, env.public_origin).rstrip("/")
    ctx = {
        "user": user,
        "items": items,
        "active": active,
        "csrf_token": csrf,
        "hostname": env.hostname,
        "public_origin": home,
        "stonepi_home_url": home,
        "using_factory_admin": factory_admin_warning(user),
        "error": request.query_params.get("err"),
        "message": request.query_params.get("msg"),
        "focus_new": request.query_params.get("new") == "1",
        "alerts_bell_state": bell_context(
            user,
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=home,
            enabled=bool(_session_secret()),
        ),
    }
    if extra:
        ctx.update(extra)
    return ctx


def _assignee(assignee_user: str, assignee: str) -> tuple[str, str]:
    """(display name, Auth user id). A picked household member wins; otherwise free text."""
    from stonepi_auth.alerts import auth_user_id

    uid = auth_user_id(assignee_user) or ""
    if uid:
        name = people.name_for(people.household_people(_session_secret()), uid)
        if name:
            return name, uid
    return assignee.strip(), ""


def _deny_page(request: Request, template: str, *, active: str, user):
    return templates.TemplateResponse(
        request,
        template,
        {
            "user": user,
            "error": "No access to Pinboard.",
            "items": store.list_items(),
            "cards": [],
            "active": active,
            "public_origin": portal_home_url(request, env.public_origin).rstrip("/"),
            "csrf_token": "",
            "focus_new": False,
        },
        status_code=403,
    )


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "pinboard"}


@router.get("/api/display")
def api_display():
    return store.display_payload()


@router.get("/", response_class=HTMLResponse)
def board(request: Request):
    user = _user(request)
    if _session_secret() and user is None:
        return RedirectResponse(login_url(_settings(), "/pinboard/"), status_code=303)
    if user and not user.can_access("pinboard") and not user.is_admin:
        return _deny_page(request, "board.html", active="board", user=user)
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "board.html",
        _page_ctx(request, active="board", user=user, csrf=csrf, extra={"cards": store.board_cards()}),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.get("/notices", response_class=HTMLResponse)
def notices_page(request: Request):
    user = _user(request)
    if _session_secret() and user is None:
        return RedirectResponse(login_url(_settings(), "/pinboard/notices"), status_code=303)
    if user and not user.can_access("pinboard") and not user.is_admin:
        return _deny_page(request, "notices.html", active="notices", user=user)
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "notices.html",
        _page_ctx(request, active="notices", user=user, csrf=csrf),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.get("/reminders", response_class=HTMLResponse)
def reminders_page(request: Request):
    user = _user(request)
    if _session_secret() and user is None:
        return RedirectResponse(login_url(_settings(), "/pinboard/reminders"), status_code=303)
    if user and not user.can_access("pinboard") and not user.is_admin:
        return _deny_page(request, "reminders.html", active="reminders", user=user)
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "reminders.html",
        _page_ctx(
            request,
            active="reminders",
            user=user,
            csrf=csrf,
            extra={"people": people.household_people(_session_secret()), "reminder_time": store.reminder_time()},
        ),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str | None = None):
    user = _user(request)
    if _session_secret() and user is None:
        return RedirectResponse(login_url(_settings(), "/pinboard/settings"), status_code=303)
    if user and not user.can_access("pinboard") and not user.is_admin:
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "user": user,
                "error": "No access to Pinboard.",
                "active": "settings",
                "app_name": "Pinboard",
                "app_version": __version__,
                "app_github_user": __github_user__,
                "app_github": __github__,
                "public_origin": portal_home_url(request, env.public_origin).rstrip("/"),
                "csrf_token": "",
            },
            status_code=403,
        )
    # No tab -> hub; each tab is one stacked panel.
    raw = (tab or "").strip().lower()
    hub = raw in {"", "hub"}
    tab = raw if raw in SETTINGS_TABS else "about"
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "settings.html",
        {
            **_page_ctx(request, active="settings", user=user, csrf=csrf),
            "settings_hub": hub,
            "settings_tab": tab,
            "settings_groups": SETTINGS_GROUPS,
            "settings_title": "Settings" if hub else SETTINGS_TITLES[tab],
            "settings_lede": SETTINGS_HUB_LEDE if hub else SETTINGS_LEDES[tab],
            "notifications_card_state": notifications_card_context(
                "pinboard",
                user if _session_secret() else None,
                home_url=portal_home_url(request, env.public_origin).rstrip("/"),
            ),
            "reminder_time": store.reminder_time(),
            "can_set_reminder_time": not _session_secret() or bool(user and user.is_admin),
            "app_name": "Pinboard",
            "app_version": __version__,
            "app_github_user": __github_user__,
            "app_github": __github__,
        },
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.post("/notice")
async def add_notice(request: Request, text: str = Form(""), csrf_token: str = Form("")):
    user = _user(request)
    if _session_secret() and (user is None or (not user.is_admin and not user.can_access("pinboard"))):
        return RedirectResponse(login_url(_settings(), "/pinboard/notices"), status_code=303)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse("/notices?err=Form+expired", status_code=303)
    if not text.strip():
        return RedirectResponse("/notices?err=Notice+required&new=1", status_code=303)
    notice = store.add_notice(text.strip())
    if _session_secret():
        alerts.emit_notice_posted(notice, by=(user.display_name or user.username) if user else "")
    return RedirectResponse("/notices?msg=Notice+added", status_code=303)


@router.post("/reminder")
async def add_reminder(
    request: Request,
    text: str = Form(""),
    due: str = Form(""),
    assignee: str = Form(""),
    assignee_user: str = Form(""),
    csrf_token: str = Form(""),
):
    user = _user(request)
    if _session_secret() and (user is None or (not user.is_admin and not user.can_access("pinboard"))):
        return RedirectResponse(login_url(_settings(), "/pinboard/reminders"), status_code=303)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse("/reminders?err=Form+expired", status_code=303)
    if not text.strip():
        return RedirectResponse("/reminders?err=Reminder+required&new=1", status_code=303)
    name, uid = _assignee(assignee_user, assignee)
    store.add_reminder(text.strip(), due=due, assignee=name, assignee_user=uid)
    return RedirectResponse("/reminders?msg=Reminder+added", status_code=303)


@router.post("/api/reminder")
async def api_reminder(request: Request):
    """Authenticated JSON create — used by PriceScout (and similar) over loopback."""
    user = _user(request)
    if _session_secret() and user is None:
        return JSONResponse({"ok": False, "message": "Sign in required."}, status_code=401)
    if user and not user.is_admin and not user.can_access("pinboard"):
        return JSONResponse(
            {
                "ok": False,
                "message": "Pinboard access required — ask an admin to enable Pinboard for your account.",
            },
            status_code=403,
        )
    ctype = (request.headers.get("content-type") or "").lower()
    text = ""
    due = ""
    assignee = ""
    assignee_user = ""
    csrf_token = ""
    if "application/json" in ctype:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        text = str(body.get("text") or "")
        due = str(body.get("due") or "")
        assignee = str(body.get("assignee") or "")
        assignee_user = str(body.get("assignee_user") or "")
        csrf_token = str(body.get("csrf_token") or "")
    else:
        form = await request.form()
        text = str(form.get("text") or "")
        due = str(form.get("due") or "")
        assignee = str(form.get("assignee") or "")
        assignee_user = str(form.get("assignee_user") or "")
        csrf_token = str(form.get("csrf_token") or "")
    header_csrf = request.headers.get("X-StonePi-CSRF") or ""
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token or header_csrf):
        return JSONResponse({"ok": False, "message": "Form expired — refresh and try again."}, status_code=403)
    if not text.strip():
        return JSONResponse({"ok": False, "message": "Reminder text required."}, status_code=400)
    # Callers may pass a household member's Auth id; without one the reminder is household.
    name, uid = _assignee(assignee_user, assignee)
    item = store.add_reminder(text.strip(), due=due, assignee=name, assignee_user=uid)
    return JSONResponse({"ok": True, "id": item["id"], "message": "Reminder added."})


@router.post("/settings/reminder-time")
async def settings_reminder_time(request: Request, reminder_time: str = Form(""), csrf_token: str = Form("")):
    user = _user(request)
    if _session_secret() and (user is None or not user.is_admin):
        return RedirectResponse(login_url(_settings(), "/pinboard/settings?tab=notifications"), status_code=303)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse("/settings?tab=notifications&err=Form+expired", status_code=303)
    try:
        saved = store.set_reminder_time(reminder_time)
    except ValueError as exc:
        return RedirectResponse(f"/settings?tab=notifications&err={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/settings?tab=notifications&msg={quote(f'Reminders go out at {saved}')}", status_code=303)


@router.post("/delete/{kind}/{item_id}")
async def delete_item(
    kind: str,
    item_id: str,
    request: Request,
    csrf_token: str = Form(""),
    next: str = Form("/"),
):
    user = _user(request)
    if _session_secret() and (user is None or not user.is_admin):
        return RedirectResponse(login_url(_settings(), "/pinboard/"), status_code=303)
    dest = _safe_next(next, "/")
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        sep = "&" if "?" in dest else "?"
        return RedirectResponse(f"{dest}{sep}err=Form+expired", status_code=303)
    store.delete_item(kind, item_id)
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(f"{dest}{sep}msg=Removed", status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
