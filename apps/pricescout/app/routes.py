from __future__ import annotations

import threading
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __github__, __github_user__, __version__, db
from app.config import CATEGORIES, CURRENCIES, ROOT_DIR, env
from app.money import format_price
from app.services import favicon, ingest

from stonepi_auth import login_url, logout_url
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import CSRF_COOKIE, csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, decode_session

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


def _salling_token() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("SALLING_API_TOKEN", env_name="SALLING_API_TOKEN", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return (env.salling_api_token or "").strip()


def _prefix() -> str:
    return (env.stonepi_prefix or "").rstrip("/")


def _settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    pfx = _prefix()
    return PlatformSettings(
        enabled=bool(_session_secret()),
        session_secret=_session_secret(),
        app_id="pricescout",
        prefix=pfx,
        auth_url=browser_auth_url(env.auth_url, routing=env.routing),
        public_origin=env.public_origin,
        hostname=env.hostname,
    )


def _user(request: Request):
    return decode_session(request.cookies.get(COOKIE_NAME), _session_secret())


def _require_user(request: Request, *, capability: str | None = None):
    user = _user(request)
    secret = _session_secret()
    if secret and user is None:
        return None, RedirectResponse(login_url(_settings(), f"{_prefix()}/"), status_code=303)
    if user and not user.is_admin and not user.can_access("pricescout"):
        return None, HTMLResponse("No access to PriceScout.", status_code=403)
    if capability and user and not user.is_admin and not user.has_capability("pricescout", capability):
        return None, HTMLResponse(f"Missing capability: {capability}", status_code=403)
    return user, None


def _user_key(user) -> str:
    if user is None:
        return "local"
    return str(getattr(user, "user_id", None) or getattr(user, "username", None) or "local")


def _currency(user) -> str:
    return (db.get_pref(_user_key(user), "currency", "DKK") or "DKK").upper()


def _csrf_response(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    ctx["csrf_token"] = csrf
    ctx.setdefault("public_origin", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", _prefix())
    ctx.setdefault("app_name", "PriceScout")
    ctx.setdefault("app_version", __version__)
    ctx.setdefault("app_github", __github__)
    ctx.setdefault("app_github_user", __github_user__)
    ctx.setdefault("sso", bool(_session_secret()))
    ctx.setdefault("format_price", format_price)
    response = templates.TemplateResponse(request, name, ctx)
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


def _redirect(path: str, *, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    pfx = _prefix()
    url = f"{pfx}{path}" if path.startswith("/") else f"{pfx}/{path}"
    parts = []
    if msg:
        parts.append(f"msg={quote(msg)}")
    if error:
        parts.append(f"error={quote(error)}")
    if parts:
        url = f"{url}?{'&'.join(parts)}"
    return RedirectResponse(url, status_code=303)


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "pricescout"}


@router.get("/api/display")
def api_display():
    sources = [s for s in db.list_sources(include_foodwaste=False) if s.get("enabled")]
    offers = db.query_offers(limit=1)
    return JSONResponse(
        {
            "ok": True,
            "sources": len(sources),
            "has_offers": bool(offers),
            "detail": f"{len(sources)} stores" if sources else "—",
        }
    )


@router.get("/", response_class=HTMLResponse)
def offers_page(
    request: Request,
    store: str = "all",
    category: str = "All",
    msg: str | None = None,
    error: str | None = None,
):
    user, denied = _require_user(request)
    if denied:
        return denied
    source_ids = None
    if store and store != "all":
        source_ids = [store]
    offers = db.query_offers(source_ids=source_ids, category=None if category == "All" else category)
    sources = [s for s in db.list_sources(include_foodwaste=False) if s.get("enabled")]
    icons = favicon.map_for_sources([s["id"] for s in sources])
    return _csrf_response(
        request,
        "offers.html",
        {
            "user": user,
            "active": "offers",
            "offers": offers,
            "sources": sources,
            "favicons": icons,
            "categories": ("All",) + CATEGORIES,
            "store": store or "all",
            "category": category or "All",
            "message": msg,
            "error": error,
            "refresh": ingest.refresh_status(),
            "currency": _currency(user),
        },
    )


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", category: str = ""):
    user, denied = _require_user(request)
    if denied:
        return denied
    uk = _user_key(user)
    results = []
    q = (q or "").strip()
    category = (category or "").strip()
    if category and category in CATEGORIES:
        results = db.query_offers(category=category, limit=60)
        # Present like search hits
        results = [
            {
                "title": r["title"],
                "price_dkk": r["price_dkk"],
                "unit_text": r.get("unit_text"),
                "source_id": r["source_id"],
                "source_name": r["source_name"],
                "image_url": r.get("image_url"),
                "valid_to": r.get("valid_to"),
                "category": r.get("category"),
            }
            for r in results
        ]
    elif q:
        db.push_search(uk, q)
        results = db.compare_search(q)
    icons = favicon.map_for_sources()
    return _csrf_response(
        request,
        "search.html",
        {
            "user": user,
            "active": "search",
            "q": q,
            "category": category,
            "results": results,
            "recent": db.recent_searches(uk),
            "categories": CATEGORIES,
            "favicons": icons,
            "searched": bool(q or category),
            "currency": _currency(user),
        },
    )


@router.get("/list", response_class=HTMLResponse)
def list_page(request: Request, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    uk = _user_key(user)
    return _csrf_response(
        request,
        "list.html",
        {
            "user": user,
            "active": "list",
            "items": db.list_items(uk),
            "summary": db.best_prices_for_list(uk),
            "message": msg,
            "error": error,
            "can_pinboard": bool(
                not _session_secret()
                or (user and (user.is_admin or user.can_access("pinboard")))
            ),
            "currency": _currency(user),
        },
    )


@router.post("/list/add")
async def list_add(request: Request, label: str = Form(""), csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/list", error="Invalid session token")
    db.add_list_item(_user_key(user), label)
    return _redirect("/list", msg="Added")


@router.post("/list/toggle")
async def list_toggle(
    request: Request,
    item_id: int = Form(...),
    checked: str = Form("0"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/list", error="Invalid session token")
    db.toggle_list_item(_user_key(user), item_id, checked in ("1", "on", "true"))
    return _redirect("/list")


@router.post("/list/delete")
async def list_delete(request: Request, item_id: int = Form(...), csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/list", error="Invalid session token")
    db.delete_list_item(_user_key(user), item_id)
    return _redirect("/list", msg="Removed")


@router.post("/list/to-pinboard")
async def list_to_pinboard(
    request: Request,
    due: str = Form(""),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/list", error="Invalid session token")
    if _session_secret() and user and not user.is_admin and not user.can_access("pinboard"):
        return _redirect(
            "/list",
            error="Pinboard access required — ask an admin to enable Pinboard for your account.",
        )
    uk = _user_key(user)
    open_items = [i for i in db.list_items(uk) if not i.get("checked")]
    if not open_items:
        return _redirect("/list", error="Add unchecked items before sending to Pinboard.")
    lines = ["Shopping list"] + [f"- {i['label']}" for i in open_items]
    text = "\n".join(lines)
    cookie_header = request.headers.get("cookie", "")
    url = f"{env.pinboard_url.rstrip('/')}/api/reminder"
    headers = {"Accept": "application/json", "Content-Type": "application/json", "X-StonePi-CSRF": csrf_token}
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        import httpx

        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                json={"text": text, "due": due.strip(), "csrf_token": csrf_token},
                headers=headers,
            )
        body = resp.json() if "application/json" in (resp.headers.get("content-type") or "") else {}
    except Exception:
        return _redirect("/list", error="Pinboard did not respond.")
    if resp.status_code >= 400 or not body.get("ok", resp.status_code < 300):
        msg = (body.get("message") or "").strip() or "Could not send to Pinboard"
        return _redirect("/list", error=msg)
    return _redirect("/list", msg="Sent to Pinboard as a reminder")


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    can_manage = bool(user and (user.is_admin or user.has_capability("pricescout", "can_manage_sources")))
    if not user:
        can_manage = True  # solo mode
    sources = db.list_sources()
    return _csrf_response(
        request,
        "sources.html",
        {
            "user": user,
            "active": "sources",
            "sources": sources,
            "favicons": favicon.map_for_sources([s["id"] for s in sources]),
            "can_manage": can_manage,
            "salling_configured": bool(_salling_token()),
            "message": msg,
            "error": error,
            "refresh": ingest.refresh_status(),
        },
    )


@router.post("/sources/toggle")
async def sources_toggle(
    request: Request,
    source_id: str = Form(...),
    enabled: str = Form("0"),
    csrf_token: str = Form(""),
):
    _user, denied = _require_user(request, capability="can_manage_sources")
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/sources", error="Invalid session token")
    db.set_source_enabled(source_id, enabled in ("1", "on", "true"))
    return _redirect("/sources", msg="Saved")


@router.post("/sources/refresh")
async def sources_refresh(request: Request, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/sources", error="Invalid session token")
    # Temporarily inject vault token into env for this process
    token = _salling_token()
    if token:
        env.salling_api_token = token
    uk = _user_key(user)
    zip_code = db.get_pref(uk, "zip", "") or db.get_pref("local", "zip", "")

    def _run() -> None:
        ingest.refresh_all(zip_code=zip_code)

    threading.Thread(target=_run, daemon=True).start()
    return _redirect("/sources", msg="Refresh started")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str = "general", msg: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    uk = _user_key(user)
    tab = tab if tab in {"general", "about"} else "general"
    return _csrf_response(
        request,
        "settings.html",
        {
            "user": user,
            "active": "settings",
            "settings_tab": tab,
            "is_admin": bool(user and user.is_admin) or not _session_secret(),
            "zip": db.get_pref(uk, "zip", ""),
            "currency": _currency(user),
            "currencies": CURRENCIES,
            "message": msg,
            "salling_configured": bool(_salling_token()),
            "settings_lede": "Household shopping preferences for PriceScout.",
        },
    )


@router.post("/settings/general")
async def settings_general(
    request: Request,
    zip: str = Form(""),
    currency: str = Form("DKK"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/settings?tab=general", error="Invalid session token")
    uk = _user_key(user)
    db.set_pref(uk, "zip", zip.strip())
    code = (currency or "DKK").strip().upper()
    if code not in {c["id"] for c in CURRENCIES}:
        code = "DKK"
    db.set_pref(uk, "currency", code)
    return _redirect("/settings?tab=general", msg="Saved")


@router.post("/settings/clear-history")
async def settings_clear_history(request: Request, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/settings?tab=general", error="Invalid session token")
    db.clear_search_history(_user_key(user))
    return _redirect("/settings?tab=general", msg="Search history cleared")


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form("")):
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/", error="Invalid session token")
    return RedirectResponse(logout_url(_settings()), status_code=303)
