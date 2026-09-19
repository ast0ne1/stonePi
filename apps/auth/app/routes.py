from __future__ import annotations

import logging
from collections import defaultdict
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import ROOT_DIR, env
from app.db import get_db, init_db
from app import users as users_svc
from stonepi_auth import COOKIE_NAME, clear_cookie, encode_session, set_cookie
from stonepi_auth.csrf import csrf_from_request, csrf_ok, csrf_ok_request, set_csrf_cookie
from stonepi_auth.http import client_ip, portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_MAX_AGE, CSRF_COOKIE, safe_next

logger = logging.getLogger("stonepi.auth")
templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
router = APIRouter()

LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    return client_ip(request)


def _https(request: Request) -> bool:
    return request_is_https(request)


def _theme_ctx(request: Request) -> dict[str, str]:
    """First-paint theme from cookies so auth status/login match the rest of StonePi."""
    pref = (request.cookies.get("stonepi-theme") or "system").strip().lower()
    if pref not in {"light", "dark", "system"}:
        pref = "system"
    palette = (request.cookies.get("stonepi-palette") or "default").strip().lower()
    if palette not in {"default", "ocean", "forest", "slate"}:
        palette = "default"
    if pref in {"light", "dark"}:
        theme = pref
    else:
        # Prefer light for SSR when system; boot script corrects from prefers-color-scheme.
        theme = "light"
    return {"theme_pref": pref, "theme": theme, "palette": palette}


def _request_origin(request: Request) -> str:
    proto = (
        (request.headers.get("x-forwarded-proto") or request.url.scheme or "http")
        .split(",")[0]
        .strip()
    )
    host = (
        (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
        .split(",")[0]
        .strip()
    )
    if not host:
        return ""
    return f"{proto}://{host}".rstrip("/")


def _resolve_next(value: str | None, request: Request) -> str:
    """Keep post-login redirects on the host the browser used (.home / .local / IP).

    Only rewrite to PUBLIC_ORIGIN for local split-port dev (auth :8011 → dashboard :8010).
    """
    nxt = safe_next(value)
    if nxt.startswith("http://") or nxt.startswith("https://"):
        return nxt
    configured = (env.public_origin or "").rstrip("/")
    req_origin = _request_origin(request)
    if (
        configured
        and "127.0.0.1" in configured
        and configured != req_origin
        and nxt.startswith("/")
        and not nxt.startswith("//")
    ):
        return configured + nxt
    return nxt


def _rate_limited(request: Request, username: str) -> bool:
    import time

    now = time.time()
    keys = [f"ip:{_client_ip(request)}", f"user:{(username or '').strip().casefold()}"]
    for key in keys:
        stamps = [t for t in _login_failures[key] if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[key] = stamps
        if len(stamps) >= LOGIN_MAX_FAILURES:
            return True
    return False


def _record_failure(request: Request, username: str) -> None:
    import time

    now = time.time()
    _login_failures[f"ip:{_client_ip(request)}"].append(now)
    _login_failures[f"user:{(username or '').strip().casefold()}"].append(now)


def _clear_failures(request: Request, username: str) -> None:
    _login_failures.pop(f"ip:{_client_ip(request)}", None)
    _login_failures.pop(f"user:{(username or '').strip().casefold()}", None)


def current_user(request: Request, db: Session):
    from stonepi_auth.session import decode_session

    token = request.cookies.get(COOKIE_NAME)
    platform = decode_session(token, users_svc.session_secret())
    if platform is None:
        return None
    session = users_svc.get_session(db, platform.session_id)
    if session is None:
        return None
    user = users_svc.get_user(db, platform.user_id)
    if user is None or not user.enabled:
        return None
    return user


def require_user(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def require_admin(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator only")
    return user


def _login_template(
    request: Request,
    context: dict,
    status_code: int = 200,
    *,
    db: Session | None = None,
):
    token = csrf_from_request(request.cookies)
    payload = {**_theme_ctx(request), **context, "csrf_token": token}
    if db is not None and "using_factory_admin" not in payload:
        payload["using_factory_admin"] = users_svc.using_factory_admin(db)
    response = templates.TemplateResponse(request, "login.html", payload, status_code=status_code)
    set_csrf_cookie(response, token, secure=_https(request))
    return response


def _attach(response, user, session_id: str, request: Request, db: Session):
    enabled = users_svc.enabled_app_ids(db)
    token = encode_session(
        secret=users_svc.session_secret(),
        user_id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        is_admin=user.is_admin,
        apps=users_svc.granted_apps(user, enabled_only=enabled),
        permissions=users_svc.granted_permissions(user, enabled_only=enabled),
        session_id=session_id,
        max_age=COOKIE_MAX_AGE,
    )
    set_cookie(response, token, secure=_https(request), max_age=COOKIE_MAX_AGE)


@router.get("/healthz")
@router.get("/health")
def healthz():
    return {"ok": True, "service": "auth"}


@router.get("/api/display")
def api_display(db: Annotated[Session, Depends(get_db)]):
    """Compact household stats for StonePi → TRMNL overview push."""
    from sqlalchemy import func, select

    from app.models import AuthSession

    sessions = int(db.execute(select(func.count()).select_from(AuthSession)).scalar() or 0)
    return {"ok": True, "sessions": sessions}


@router.get("/")
def root(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    token = csrf_from_request(request.cookies)
    home_url = portal_home_url(request, env.public_origin)
    response = templates.TemplateResponse(
        request,
        "status.html",
        {
            **_theme_ctx(request),
            "user": users_svc.user_payload(user, db),
            "hostname": env.hostname,
            "service": "Authentication",
            "csrf_token": token,
            "home_url": home_url,
        },
    )
    set_csrf_cookie(response, token, secure=_https(request))
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Annotated[Session, Depends(get_db)], next: str = "/"):
    nxt = _resolve_next(next, request)
    user = current_user(request, db)
    if user is not None:
        return RedirectResponse(nxt, status_code=303)
    return _login_template(
        request,
        {"error": None, "username": "", "next": nxt, "hostname": env.hostname},
        db=db,
    )


@router.post("/login")
async def login_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
):
    nxt = _resolve_next(next, request)
    name = username.strip()
    form = await request.form()
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), str(form.get("csrf_token") or "")):
        return _login_template(
            request,
            {
                "error": "That sign-in form expired. Refresh and try again.",
                "username": name,
                "next": nxt,
                "hostname": env.hostname,
            },
            status_code=400,
            db=db,
        )
    if _rate_limited(request, name):
        return _login_template(
            request,
            {
                "error": "Too many failed attempts. Wait a few minutes and try again.",
                "username": name,
                "next": nxt,
                "hostname": env.hostname,
            },
            status_code=429,
            db=db,
        )
    user = users_svc.authenticate(db, name, password)
    if user is None:
        _record_failure(request, name)
        return _login_template(
            request,
            {
                "error": "That username or password is not right.",
                "username": name,
                "next": nxt,
                "hostname": env.hostname,
            },
            status_code=401,
            db=db,
        )
    _clear_failures(request, name)
    session = users_svc.create_session(db, user)
    response = RedirectResponse(nxt, status_code=303)
    _attach(response, user, session.id, request, db)
    return response


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request, db: Annotated[Session, Depends(get_db)], next: str = ""):
    from stonepi_auth.session import decode_session

    platform = decode_session(request.cookies.get(COOKIE_NAME), users_svc.session_secret())
    if platform is not None:
        users_svc.revoke_session(db, platform.session_id)
    target = _resolve_next(next, request) if next else "/login"
    response = RedirectResponse(target, status_code=303)
    clear_cookie(response)
    return response


def _require_api_csrf(request: Request) -> None:
    header = request.headers.get("x-stonepi-csrf") or request.headers.get("X-StonePi-CSRF")
    if not csrf_ok_request(request.cookies, header_token=header):
        raise HTTPException(status_code=403, detail="CSRF check failed")


@router.get("/api/me")
def api_me(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    payload = users_svc.user_payload(user, db)
    if user.is_admin:
        payload["using_factory_admin"] = users_svc.using_factory_admin(db)
    return payload


@router.put("/api/me/launcher-order")
async def api_set_launcher_order(request: Request, db: Annotated[Session, Depends(get_db)]):
    user = current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    _require_api_csrf(request)
    body = await request.json()
    order = body.get("order") if isinstance(body, dict) else None
    if not isinstance(order, list):
        raise HTTPException(status_code=400, detail="order must be a list of app ids")
    result = users_svc.set_launcher_order(db, user, [str(item) for item in order])
    return {"ok": True, "launcher_order": result}


@router.post("/api/me/password")
async def api_change_password(request: Request, db: Annotated[Session, Depends(get_db)]):
    from stonepi_auth.session import decode_session

    user = current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    _require_api_csrf(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    platform = decode_session(request.cookies.get(COOKIE_NAME), users_svc.session_secret())
    keep_sid = platform.session_id if platform else None
    try:
        users_svc.change_own_password(
            db,
            user,
            current_password=str(body.get("current_password") or ""),
            new_password=str(body.get("new_password") or body.get("password") or ""),
            keep_session_id=keep_sid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": "Password updated."}


@router.get("/api/users")
def api_users(request: Request, db: Annotated[Session, Depends(get_db)]):
    require_admin(request, db)
    return {"users": [users_svc.user_payload(user, db) for user in users_svc.list_users(db)]}


@router.post("/api/users")
async def api_create_user(request: Request, db: Annotated[Session, Depends(get_db)]):
    require_admin(request, db)
    _require_api_csrf(request)
    body = await request.json()
    try:
        user = users_svc.create_user(
            db,
            username=str(body.get("username") or ""),
            password=str(body.get("password") or ""),
            display_name=str(body.get("display_name") or ""),
            is_admin=bool(body.get("is_admin")),
            enabled=body.get("enabled", True) is not False,
            apps=body.get("apps"),
            permissions=body.get("permissions"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return users_svc.user_payload(user, db)


@router.patch("/api/users/{user_id}")
async def api_update_user(user_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    require_admin(request, db)
    _require_api_csrf(request)
    user = users_svc.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    body = await request.json()
    try:
        user = users_svc.update_user(
            db,
            user,
            display_name=body.get("display_name"),
            enabled=body.get("enabled"),
            is_admin=body.get("is_admin"),
            apps=body.get("apps"),
            permissions=body.get("permissions"),
            password=body.get("password"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return users_svc.user_payload(user, db)


@router.delete("/api/users/{user_id}")
def api_delete_user(user_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    require_admin(request, db)
    _require_api_csrf(request)
    user = users_svc.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        users_svc.delete_user(db, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/api/apps")
def api_apps(request: Request, db: Annotated[Session, Depends(get_db)]):
    require_admin(request, db)
    return {
        "apps": users_svc.catalog_payload(db),
        "disabled": users_svc.disabled_apps(db),
    }


@router.patch("/api/apps")
async def api_apps_update(request: Request, db: Annotated[Session, Depends(get_db)]):
    require_admin(request, db)
    _require_api_csrf(request)
    body = await request.json()
    disabled = body.get("disabled")
    if disabled is None:
        raise HTTPException(status_code=400, detail="Provide a disabled app id list.")
    if not isinstance(disabled, list):
        raise HTTPException(status_code=400, detail="disabled must be a list of app ids.")
    result = users_svc.set_disabled_apps(db, [str(item) for item in disabled])
    return {"apps": users_svc.catalog_payload(db), "disabled": result}
