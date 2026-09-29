from __future__ import annotations

from urllib.parse import quote

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, db
from app.config import (
    CONDITION_OPTIONS,
    DEFAULT_CONDITION,
    DEFAULT_SCHEDULE_MINUTES,
    ROOT_DIR,
    SCHEDULE_OPTIONS,
    env,
)
from app.services import schedule as schedule_service
from app.services import strike
from app.sources import get_source

from stonepi_auth.brand import fonts_rev
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context, notifications_card_context
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import CSRF_COOKIE, csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.prefix import strip_prefix
from stonepi_auth.session import COOKIE_NAME, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()

STATUS_LABELS = {
    "watching": "Watching",
    "strike_found": "Strike found",
    "paused": "Paused",
    "error": "Check failed",
    "no_results": "No offers",
}
# Dashboard sections: (group id, label, statuses in that group).
WATCH_GROUPS = (
    ("strikes", "Strike found", ("strike_found",)),
    ("watching", "Watching", ("watching", "error", "no_results")),
    ("paused", "Paused", ("paused",)),
)

SETTINGS_TABS = (
    ("general", "General"),
    ("sources", "Sources"),
    ("notifications", "Notifications"),
    ("about", "About"),
)
SETTINGS_TAB_KEYS = {key for key, _ in SETTINGS_TABS}
SETTINGS_LEDES = {
    "general": "Defaults for new watches, and how long price history is kept.",
    "sources": "Which retailers and price sites PriceWatch checks.",
    "notifications": "Strike alerts on your phone, just for your own watches.",
    "about": "App name, description, GitHub, and the version running here.",
}
SETTINGS_HUB_SUBTEXTS = {
    "general": "Schedule, condition, history.",
    "sources": "Price sites to check.",
    "notifications": "Your phone alerts.",
    "about": "Version and project links.",
}
SETTINGS_HUB_LEDE = "Defaults, sources, and strike alerts."
# Phone/tablet hub groups: (id, label, tab keys in order).
SETTINGS_GROUPS = (
    ("watching", "Watching", ("general", "sources")),
    ("alerts", "Alerts", ("notifications",)),
    ("app", "App", ("about",)),
)


def _settings_groups() -> tuple:
    labels = dict(SETTINGS_TABS)
    return tuple(
        (group_id, label, tuple((key, labels[key], SETTINGS_HUB_SUBTEXTS[key]) for key in keys))
        for group_id, label, keys in SETTINGS_GROUPS
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


def _prefix() -> str:
    return (env.stonepi_prefix or "").rstrip("/")


def _settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    pfx = _prefix()
    return PlatformSettings(
        enabled=bool(_session_secret()),
        session_secret=_session_secret(),
        app_id="pricewatch",
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
    if user and not user.is_admin and not user.can_access("pricewatch"):
        return None, HTMLResponse("No access to PriceWatch.", status_code=403)
    if capability and user and not user.is_admin and not user.has_capability("pricewatch", capability):
        return None, HTMLResponse(f"Missing capability: {capability}", status_code=403)
    return user, None


def _user_key(user) -> str:
    if user is None:
        return "local"
    return str(getattr(user, "user_id", None) or getattr(user, "username", None) or "local")


def _csrf_ok(request: Request, token: str | None) -> bool:
    return csrf_ok(request.cookies.get(CSRF_COOKIE), token)


def _csrf_response(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    ctx["csrf_token"] = csrf
    ctx.setdefault("stonepi_home_url", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", _prefix())
    ctx.setdefault("app_name", "PriceWatch")
    ctx.setdefault("app_version", __version__)
    ctx.setdefault("asset_rev", __asset_rev__)
    ctx.setdefault("app_github", __github__)
    ctx.setdefault("app_github_user", __github_user__)
    ctx.setdefault("sso", bool(_session_secret()))
    ctx.setdefault("format_price", db.format_price)
    ctx.setdefault("schedule_options", SCHEDULE_OPTIONS)
    ctx.setdefault("condition_options", CONDITION_OPTIONS)
    ctx.setdefault("status_labels", STATUS_LABELS)
    ctx.setdefault("condition_labels", {c["id"]: c["label"] for c in CONDITION_OPTIONS})
    ctx.setdefault("schedule_labels", {s["minutes"]: s["label"] for s in SCHEDULE_OPTIONS})
    ctx.setdefault("scheduler", schedule_service.scheduler_status())
    from stonepi_auth.session import factory_admin_warning

    ctx.setdefault("using_factory_admin", factory_admin_warning(ctx.get("user")))
    ctx.setdefault(
        "alerts_bell_state",
        bell_context(
            ctx.get("user"),
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=ctx["stonepi_home_url"],
            enabled=bool(_session_secret()),
        ),
    )
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


def _primary_source_id() -> str:
    if env.mock:
        return "mock"
    for src in db.list_sources():
        if src["enabled"] and src["id"] != "mock":
            return str(src["id"])
    return "pricerunner_dk"


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "pricewatch"}


@router.get("/api/display")
def api_display():
    counts = db.watch_counts()
    strikes = counts.get("strike_found", 0)
    active = counts.get("active", 0)
    detail = f"{strikes} strike" if strikes else f"{active} watching"
    if strikes != 1 and strikes:
        detail = f"{strikes} strikes"
    return JSONResponse(
        {
            "ok": True,
            "watches": counts.get("total", 0),
            "strikes": strikes,
            "detail": detail if counts.get("total") else "—",
        }
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    watches = db.list_watches()
    counts = {status: 0 for status in STATUS_LABELS}
    for w in watches:
        counts[w["status"]] = counts.get(w["status"], 0) + 1
    groups = []
    for group_id, label, statuses in WATCH_GROUPS:
        rows = [w for w in watches if w["status"] in statuses]
        if rows:
            groups.append((group_id, label, rows))
    upcoming = sorted(
        w["next_check_at"] for w in watches if w["status"] != "paused" and w.get("next_check_at")
    )
    return _csrf_response(
        request,
        "dashboard.html",
        {
            "user": user,
            "active": "watches",
            "watches": watches,
            "watch_groups": groups,
            "counts": counts,
            "watching_count": len(watches) - counts["paused"],
            "next_check": upcoming[0][11:16] if upcoming else None,
            "message": msg,
            "error": error,
        },
    )


@router.post("/check-all")
def check_all(request: Request, csrf_token: str = Form(""), next: str = Form("/")):
    user, denied = _require_user(request)
    if denied:
        return denied
    back = strip_prefix(next, _prefix()) if next.startswith("/") and not next.startswith("//") else "/"
    if not _csrf_ok(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    active = [w for w in db.list_watches() if w["status"] != "paused"]
    if not active:
        return _redirect(back, msg="No active watches to check")
    failed = 0
    strikes = 0
    for w in active:
        result = strike.check_watch_now(int(w["id"]))
        if not result.get("ok"):
            failed += 1
        elif result.get("qualifying"):
            strikes += 1
    summary = f"Checked {len(active)} watch{'es' if len(active) != 1 else ''}"
    if strikes:
        summary += f" · {strikes} at target"
    if failed:
        return _redirect(back, error=f"{summary} · {failed} failed")
    return _redirect(back, msg=summary)


@router.get("/watches/new", response_class=HTMLResponse)
def watch_new_get(
    request: Request,
    q: str | None = None,
    product_id: str | None = None,
    source_id: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    user, denied = _require_user(request)
    if denied:
        return denied
    hits = []
    selected = None
    sid = source_id or _primary_source_id()
    if q and not product_id:
        src = get_source(sid) or get_source("mock")
        try:
            hits = [h.as_dict() for h in (src.search(q) if src else [])]
        except Exception as exc:
            error = error or str(exc)
    if product_id:
        src = get_source(sid) or get_source("mock")
        try:
            # Prefer the search hit (has name/image) when the user just picked from results.
            if q and src:
                for hit in src.search(q):
                    if hit.product_id == str(product_id):
                        selected = hit.as_dict()
                        break
            if not selected and src:
                detail = src.get_product(product_id)
                if detail:
                    selected = detail.as_dict()
            if not selected:
                selected = {
                    "product_id": product_id,
                    "name": f"Product {product_id}",
                    "source_id": sid,
                }
        except Exception as exc:
            error = error or str(exc)
    settings = db.list_settings()
    return _csrf_response(
        request,
        "watch_new.html",
        {
            "user": user,
            "active": "add",
            "q": q or "",
            "hits": hits,
            "selected": selected,
            "source_id": sid,
            "default_schedule": int(settings.get("default_schedule_minutes") or DEFAULT_SCHEDULE_MINUTES),
            "default_condition": settings.get("default_condition") or DEFAULT_CONDITION,
            "default_in_stock": settings.get("default_in_stock_required", "1") == "1",
            "message": msg,
            "error": error,
        },
    )


@router.post("/watches/new")
def watch_new_post(
    request: Request,
    csrf_token: str = Form(""),
    source_id: str = Form(...),
    product_id: str = Form(...),
    product_name: str = Form(...),
    variant: str = Form(""),
    manufacturer: str = Form(""),
    image_url: str = Form(""),
    product_url: str = Form(""),
    target_price: str = Form(...),
    condition: str = Form("new"),
    in_stock_required: str = Form("0"),
    schedule_minutes: int = Form(DEFAULT_SCHEDULE_MINUTES),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect("/watches/new", error="Invalid CSRF token")
    try:
        price = float(target_price.replace(",", ".").strip())
    except ValueError:
        return _redirect("/watches/new", error="Enter a valid target price")
    if price <= 0:
        return _redirect("/watches/new", error="Target price must be positive")
    watch_id = db.create_watch(
        {
            "user_key": _user_key(user),
            "source_id": source_id,
            "product_id": product_id,
            "product_name": product_name.strip(),
            "variant": variant.strip() or None,
            "manufacturer": manufacturer.strip() or None,
            "image_url": image_url.strip() or None,
            "product_url": product_url.strip() or None,
            "target_price": price,
            "condition": condition if condition in {"new", "used", "either"} else "new",
            "in_stock_required": in_stock_required in {"1", "on", "true", "yes"},
            "schedule_minutes": int(schedule_minutes),
        }
    )
    try:
        strike.check_watch_now(watch_id)
    except Exception:
        pass
    return _redirect(f"/watches/{watch_id}", msg="Watch created")


@router.get("/watches/{watch_id}", response_class=HTMLResponse)
def watch_detail(request: Request, watch_id: int, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    watch = db.get_watch(watch_id)
    if not watch:
        return _redirect("/", error="Watch not found")
    observations = db.list_observations(watch_id, limit=40)
    history = list(reversed(observations))
    latest_offers = []
    if observations:
        latest_offers = observations[0].get("offers") or []
    return _csrf_response(
        request,
        "watch_detail.html",
        {
            "user": user,
            "active": "watches",
            "watch": watch,
            "observations": observations,
            "history": history,
            "offers": latest_offers,
            "historical_lowest": db.historical_lowest(watch_id),
            "message": msg,
            "error": error,
        },
    )


@router.post("/watches/{watch_id}/pause")
def watch_pause(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id)
    if not watch:
        return _redirect("/", error="Watch not found")
    db.pause_watch(watch_id)
    return _redirect(f"/watches/{watch_id}", msg="Watch paused")


@router.post("/watches/{watch_id}/resume")
def watch_resume(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id)
    if not watch:
        return _redirect("/", error="Watch not found")
    db.resume_watch(watch_id)
    return _redirect(f"/watches/{watch_id}", msg="Watch resumed")


@router.post("/watches/{watch_id}/rearm")
def watch_rearm(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id)
    if not watch:
        return _redirect("/", error="Watch not found")
    db.rearm_watch(watch_id)
    return _redirect(f"/watches/{watch_id}", msg="Watch re-armed")


@router.post("/watches/{watch_id}/check")
def watch_check(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id)
    if not watch:
        return _redirect("/", error="Watch not found")
    result = strike.check_watch_now(watch_id)
    if not result.get("ok"):
        return _redirect(f"/watches/{watch_id}", error=result.get("error") or "Check failed")
    return _redirect(f"/watches/{watch_id}", msg="Checked just now")


@router.post("/watches/{watch_id}/delete")
def watch_delete(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    if not db.get_watch(watch_id):
        return _redirect("/", error="Watch not found")
    db.delete_watch(watch_id)
    return _redirect("/", msg="Watch deleted")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str = "", msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    # No tab → phone hub (desktop JS swaps to the first tab's chip bar).
    hub = tab.strip().lower() in {"", "hub"}
    tab = tab if tab in SETTINGS_TAB_KEYS else "general"
    settings = db.list_settings()
    return _csrf_response(
        request,
        "settings.html",
        {
            "user": user,
            "active": "settings",
            "settings_tab": tab,
            "settings_hub": hub,
            "settings_tabs": SETTINGS_TABS,
            "settings_groups": _settings_groups(),
            "settings_ledes": SETTINGS_LEDES,
            "settings_hub_lede": SETTINGS_HUB_LEDE,
            "settings": settings,
            "sources": db.list_sources(),
            "notifications_card_state": notifications_card_context(
                "pricewatch",
                user if _session_secret() else None,
                home_url=portal_home_url(request, env.public_origin).rstrip("/"),
            ),
            "message": msg,
            "error": error,
            "settings_lede": SETTINGS_HUB_LEDE if hub else SETTINGS_LEDES[tab],
        },
    )


@router.post("/settings/general")
def settings_general(
    request: Request,
    csrf_token: str = Form(""),
    default_schedule_minutes: int = Form(DEFAULT_SCHEDULE_MINUTES),
    default_condition: str = Form(DEFAULT_CONDITION),
    default_in_stock_required: str = Form("0"),
    retention_days: int = Form(90),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect("/settings?tab=general", error="Invalid CSRF token")
    db.set_setting("default_schedule_minutes", str(int(default_schedule_minutes)))
    db.set_setting(
        "default_condition",
        default_condition if default_condition in {"new", "used", "either"} else "new",
    )
    db.set_setting("default_in_stock_required", "1" if default_in_stock_required in {"1", "on", "true"} else "0")
    db.set_setting("retention_days", str(max(7, int(retention_days))))
    return _redirect("/settings?tab=general", msg="Settings saved")


@router.post("/settings/sources")
def settings_sources(
    request: Request,
    csrf_token: str = Form(""),
    enabled: Annotated[list[str], Form()] = [],
):
    user, denied = _require_user(request, capability="can_manage_sources")
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect("/settings?tab=sources", error="Invalid CSRF token")
    enabled_set = set(enabled)
    # Only toggle sources currently shown in Settings (respects mock visibility).
    for src in db.list_sources():
        db.set_source_enabled(src["id"], src["id"] in enabled_set)
    return _redirect("/settings?tab=sources", msg="Sources updated")


@router.post("/settings/sources/{source_id}/remove")
def settings_source_remove(request: Request, source_id: str, csrf_token: str = Form("")):
    user, denied = _require_user(request, capability="can_manage_sources")
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect("/settings?tab=sources", error="Invalid CSRF token")
    ok, err = db.delete_source(source_id)
    if not ok:
        return _redirect("/settings?tab=sources", error=err or "Could not remove source")
    return _redirect("/settings?tab=sources", msg="Source removed")


@router.post("/settings/notifications")
def settings_notifications(request: Request):
    """Legacy form target — ntfy lives in Notifications now."""
    return _redirect(
        "/settings?tab=notifications",
        msg="Configure alerts in StonePi Notify",
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if not _csrf_ok(request, csrf_token):
        return HTMLResponse("Invalid CSRF token", status_code=400)
    response = RedirectResponse(logout_url(_settings()), status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
