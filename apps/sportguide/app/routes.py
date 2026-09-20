from __future__ import annotations

from collections import OrderedDict
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, db
from app.config import COMMON_TIMEZONES, FOOTBALL_LEAGUES, ROOT_DIR, SPORTS, env
from app.services import ingest
from app.timeutil import format_local, timezone_choices, window_utc

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


def _prefix() -> str:
    return (env.stonepi_prefix or "").rstrip("/")


def _settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    pfx = _prefix()
    return PlatformSettings(
        enabled=bool(_session_secret()),
        session_secret=_session_secret(),
        app_id="sportguide",
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
    if user and not user.is_admin and not user.can_access("sportguide"):
        return None, HTMLResponse("No access to SportGuide.", status_code=403)
    if capability and user and not user.is_admin and not user.has_capability("sportguide", capability):
        return None, HTMLResponse(f"Missing capability: {capability}", status_code=403)
    return user, None


def _user_key(user) -> str:
    if user is None:
        return "local"
    return str(getattr(user, "user_id", None) or getattr(user, "username", None) or "local")


def _csrf_response(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    ctx["csrf_token"] = csrf
    ctx.setdefault("public_origin", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", _prefix())
    ctx.setdefault("app_name", "SportGuide")
    ctx.setdefault("app_version", __version__)
    ctx.setdefault("asset_rev", __asset_rev__)
    ctx.setdefault("app_github", __github__)
    ctx.setdefault("app_github_user", __github_user__)
    ctx.setdefault("sso", bool(_session_secret()))
    ctx.setdefault("format_local", format_local)
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
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{'&'.join(parts)}"
    return RedirectResponse(url, status_code=303)


def _group_listings(listings: list[dict], *, sport: str, league: str = "all") -> list[dict]:
    """Group for the feed: All → sport then league; football → league; single league → flat."""
    if not listings:
        return []
    if sport == "all":
        by_sport: OrderedDict[str, list] = OrderedDict()
        order = ("afl", "cricket", "rugby", "football")
        for s in order:
            by_sport[s] = []
        for row in listings:
            by_sport.setdefault(row["sport"], []).append(row)
        sections = []
        for sid, rows in by_sport.items():
            if not rows:
                continue
            label = next((x["label"] for x in SPORTS if x["id"] == sid), sid.title())
            if sid == "football":
                subsections = _group_by_league(rows)
                sections.append({"key": sid, "label": label, "subsections": subsections, "rows": []})
            else:
                sections.append({"key": sid, "label": label, "subsections": [], "rows": rows})
        return sections
    if sport == "football":
        if league and league != "all":
            return [{"key": "football", "label": league, "subsections": [], "rows": listings}]
        return [{"key": "football", "label": "Football", "subsections": _group_by_league(listings), "rows": []}]
    label = next((x["label"] for x in SPORTS if x["id"] == sport), sport.title())
    return [{"key": sport, "label": label, "subsections": [], "rows": listings}]


def _group_by_league(rows: list[dict]) -> list[dict]:
    buckets: OrderedDict[str, list] = OrderedDict()
    for name in FOOTBALL_LEAGUES:
        buckets[name] = []
    for row in rows:
        league = row.get("league") or "Other"
        if league not in buckets:
            league = "Other"
        buckets[league].append(row)
    return [{"key": k, "label": k, "rows": v} for k, v in buckets.items() if v]


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "sportguide"}


@router.get("/api/display")
def api_display():
    tz = db.get_pref("local", "timezone", "Australia/Melbourne")
    start, end, _ = window_utc(tz)
    n = db.count_on_now(start, end)
    return JSONResponse(
        {
            "ok": True,
            "on_now": n,
            "detail": f"{n} on now" if n else "—",
        }
    )


@router.get("/", response_class=HTMLResponse)
def now_page(
    request: Request,
    sport: str = "all",
    league: str = "all",
    msg: str | None = None,
    error: str | None = None,
):
    user, denied = _require_user(request)
    if denied:
        return denied
    uk = _user_key(user)
    tz = db.get_pref(uk, "timezone", "Australia/Melbourne")
    city = db.get_pref(uk, "city", "")
    # Daily refresh check using this user's tz
    try:
        ingest.maybe_daily_refresh(tz_name=tz, async_=True)
    except Exception:
        pass
    sport = (sport or "all").lower()
    if sport not in {s["id"] for s in SPORTS}:
        sport = "all"
    league = league or "all"
    start, end, local_now = window_utc(tz)
    listings = db.query_listings(
        sport=None if sport == "all" else sport,
        league=None if sport != "football" or league == "all" else league,
        starts_from=start,
        starts_to=end,
        limit=250,
    )
    for row in listings:
        row["local_time"] = format_local(row["starts_at"], tz)
    groups = _group_listings(listings, sport=sport, league=league)
    can_refresh = bool(
        not _session_secret()
        or (user and (user.is_admin or user.has_capability("sportguide", "can_refresh")))
    )
    return _csrf_response(
        request,
        "now.html",
        {
            "user": user,
            "active": "now",
            "sports": SPORTS,
            "sport": sport,
            "league": league,
            "leagues": ("all",) + FOOTBALL_LEAGUES,
            "groups": groups,
            "listings": listings,
            "timezone": tz,
            "city": city,
            "local_now": local_now.strftime("%a %H:%M"),
            "look_ahead_hours": env.look_ahead_hours,
            "message": msg,
            "error": error,
            "refresh": ingest.refresh_status(),
            "has_data": db.listing_count() > 0,
            "can_refresh": can_refresh,
        },
    )


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    can_refresh = bool(
        not _session_secret()
        or (user and (user.is_admin or user.has_capability("sportguide", "can_refresh")))
    )
    return _csrf_response(
        request,
        "sources.html",
        {
            "user": user,
            "active": "sources",
            "sources": db.list_sources(),
            "refresh": ingest.refresh_status(),
            "message": msg,
            "error": error,
            "can_refresh": can_refresh,
        },
    )


@router.post("/sources/refresh")
async def sources_refresh(request: Request, csrf_token: str = Form(""), next: str = Form("sources")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if _session_secret() and user and not user.is_admin and not user.has_capability("sportguide", "can_refresh"):
        return HTMLResponse("Missing capability: can_refresh", status_code=403)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        dest = "/" if next == "now" else "/sources"
        return _redirect(dest, error="Invalid session token")
    ingest.refresh_async()
    if next == "now":
        return _redirect("/", msg="Refresh started")
    return _redirect("/sources", msg="Refresh started")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    tab: str = "general",
    msg: str | None = None,
    error: str | None = None,
):
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
            "city": db.get_pref(uk, "city", ""),
            "timezone": db.get_pref(uk, "timezone", "Australia/Melbourne"),
            "timezones": timezone_choices(),
            "message": msg,
            "error": error,
            "settings_lede": "City and timezone for SportGuide times.",
        },
    )


@router.post("/settings/general")
async def settings_general(
    request: Request,
    city: str = Form(""),
    timezone: str = Form("Australia/Melbourne"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/settings?tab=general", error="Invalid session token")
    uk = _user_key(user)
    db.set_pref(uk, "city", city.strip())
    tz = timezone.strip()
    if tz not in COMMON_TIMEZONES:
        tz = "Australia/Melbourne"
    db.set_pref(uk, "timezone", tz)
    return _redirect("/settings?tab=general", msg="Saved")


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form("")):
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/", error="Invalid session token")
    response = RedirectResponse(logout_url(_settings()), status_code=303)
    return response
