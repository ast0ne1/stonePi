from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import store
from app.config import ROOT_DIR, env
from stonepi_auth import login_url, logout_url
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
router = APIRouter()


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


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "pinboard"}


@router.get("/api/display")
def api_display():
    return store.display_payload()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = _user(request)
    if _session_secret() and user is None:
        return RedirectResponse(login_url(_settings(), "/pinboard/"), status_code=303)
    if user and not user.can_access("pinboard") and not user.is_admin:
        return templates.TemplateResponse(
            request,
            "home.html",
            {
                "user": user,
                "error": "No access to Pinboard.",
                "items": store.list_items(),
                "public_origin": portal_home_url(request, env.public_origin).rstrip("/"),
                "csrf_token": "",
            },
            status_code=403,
        )
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "items": store.list_items(),
            "csrf_token": csrf,
            "hostname": env.hostname,
            "public_origin": portal_home_url(request, env.public_origin).rstrip("/"),
            "error": request.query_params.get("err"),
            "message": request.query_params.get("msg"),
        },
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.post("/notice")
async def add_notice(request: Request, text: str = Form(""), csrf_token: str = Form("")):
    user = _user(request)
    if _session_secret() and (user is None or (not user.is_admin and not user.can_access("pinboard"))):
        return RedirectResponse(login_url(_settings(), "/pinboard/"), status_code=303)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse("/?err=Form+expired", status_code=303)
    if not text.strip():
        return RedirectResponse("/?err=Notice+required", status_code=303)
    store.add_notice(text.strip())
    return RedirectResponse("/?msg=Notice+added", status_code=303)


@router.post("/reminder")
async def add_reminder(
    request: Request,
    text: str = Form(""),
    due: str = Form(""),
    assignee: str = Form(""),
    csrf_token: str = Form(""),
):
    user = _user(request)
    if _session_secret() and (user is None or (not user.is_admin and not user.can_access("pinboard"))):
        return RedirectResponse(login_url(_settings(), "/pinboard/"), status_code=303)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse("/?err=Form+expired", status_code=303)
    if not text.strip():
        return RedirectResponse("/?err=Reminder+required", status_code=303)
    store.add_reminder(text.strip(), due=due, assignee=assignee)
    return RedirectResponse("/?msg=Reminder+added", status_code=303)


@router.post("/delete/{kind}/{item_id}")
async def delete_item(kind: str, item_id: str, request: Request, csrf_token: str = Form("")):
    user = _user(request)
    if _session_secret() and (user is None or not user.is_admin):
        return RedirectResponse(login_url(_settings(), "/pinboard/"), status_code=303)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse("/?err=Form+expired", status_code=303)
    store.delete_item(kind, item_id)
    return RedirectResponse("/?msg=Removed", status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
