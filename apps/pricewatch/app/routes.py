from __future__ import annotations

import os
from urllib.parse import quote, urlencode, urlsplit

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, db
from app.config import (
    CONDITION_OPTIONS,
    DEFAULT_CONDITION,
    DEFAULT_SCHEDULE_MINUTES,
    MIN_SCORE_OPTIONS,
    ROOT_DIR,
    SCHEDULE_OPTIONS,
    env,
)
from app.services import schedule as schedule_service
from app.services import access, strike, trust
from app.sources import get_source
from app.sources.base import normalise_domain
from app.sources.brightdata_trustpilot import profile_url as trustpilot_url

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
    ("trust", "Trust scores"),
    ("notifications", "Notifications"),
    ("about", "About"),
)
SETTINGS_TAB_KEYS = {key for key, _ in SETTINGS_TABS}
SETTINGS_LEDES = {
    "general": "Defaults for new watches, and how long price history is kept.",
    "sources": "Which retailers and price sites PriceWatch checks.",
    "trust": "Retailer ratings from Trustpilot, and the shops PriceWatch has seen.",
    "notifications": "Strike alerts on your phone, just for your own watches.",
    "about": "App name, description, GitHub, and the version running here.",
}
SETTINGS_HUB_SUBTEXTS = {
    "general": "Schedule, condition, history.",
    "sources": "Price sites to check.",
    "trust": "Retailer ratings and shops.",
    "notifications": "Your phone alerts.",
    "about": "Version and project links.",
}
SETTINGS_HUB_LEDE = "Defaults, sources, trust scores, and strike alerts."
# Phone/tablet hub groups: (id, label, tab keys in order).
SETTINGS_GROUPS = (
    ("watching", "Watching", ("general", "sources", "trust")),
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


def _auth_optional() -> bool:
    """No session secret skips sign-in only in dev (Windows run-dev or STONEPI_DEV=1)."""
    return os.name == "nt" or (os.environ.get("STONEPI_DEV") or "").strip() == "1"


def _require_user(request: Request, *, capability: str | None = None):
    user = _user(request)
    secret = _session_secret()
    if not secret and not _auth_optional():
        # Fail closed on a Pi: a missing/unreadable secret must not open PriceWatch to everyone.
        return None, HTMLResponse("StonePi sign-in isn't configured, so PriceWatch is locked.", status_code=503)
    if secret and user is None:
        return None, RedirectResponse(login_url(_settings(), f"{_prefix()}/"), status_code=303)
    if user and not user.is_admin and not user.can_access("pricewatch"):
        return None, HTMLResponse("No access to PriceWatch.", status_code=403)
    if capability and user and not user.is_admin and not user.has_capability("pricewatch", capability):
        return None, HTMLResponse(f"Missing capability: {capability}", status_code=403)
    return user, None


def _is_admin(user) -> bool:
    """Platform admin, or the single local user of a dev run without SSO."""
    if user is None:
        return not _session_secret() and _auth_optional()
    return bool(user.is_admin)


ADMIN_ONLY_BRIGHTDATA = "Only a StonePi admin can change the shared Bright Data key or limit"
ADMIN_ONLY_LOOKUPS = "Only a StonePi admin can turn on, schedule or start Bright Data lookups"
ADMIN_ONLY_GENERAL = "Only a StonePi admin can change household defaults and history retention"
MANAGE_WATCHES = access.CAN_MANAGE_WATCHES


def _can(user, capability: str) -> bool:
    """Session capability for templates, matching _require_user (no user = dev without SSO)."""
    if user is None:
        return True
    return bool(user.is_admin or user.has_capability("pricewatch", capability))


def _user_key(user) -> str:
    if user is None:
        return "local"
    return str(getattr(user, "user_id", None) or getattr(user, "username", None) or "local")


def _schedule(minutes: int) -> int:
    """A check interval from the form, limited to the offered options."""
    allowed = {int(s["minutes"]) for s in SCHEDULE_OPTIONS}
    return int(minutes) if int(minutes) in allowed else DEFAULT_SCHEDULE_MINUTES


def _scope(user) -> str | None:
    """Owner filter for watch queries: admins (and dev without SSO) see every
    watch, everyone else only the watches they created."""
    if user is None or user.is_admin:
        return None
    return _user_key(user)


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
    ctx.setdefault("min_score_options", MIN_SCORE_OPTIONS)
    ctx.setdefault("scheduler", schedule_service.scheduler_status())
    ctx.setdefault("can_manage_watches", _can(ctx.get("user"), access.CAN_MANAGE_WATCHES))
    ctx.setdefault("can_manage_sources", _can(ctx.get("user"), access.CAN_MANAGE_SOURCES))
    ctx.setdefault("can_use_alerts", _can(ctx.get("user"), access.CAN_USE_ALERTS))
    ctx.setdefault("is_admin", _is_admin(ctx.get("user")))
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


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "on", "true", "yes"}


def _parse_min_score(raw: str | None) -> float | None:
    allowed = {str(o["id"]): o["value"] for o in MIN_SCORE_OPTIONS}
    return allowed.get((raw or "").strip())


def _trust_fields(min_trust_score: str, require_rating: str, low_rated_mode: str) -> dict:
    minimum = _parse_min_score(min_trust_score)
    return {
        "min_trust_score": minimum,
        # Both only mean something once a minimum is set.
        "require_rating": minimum is not None and _truthy(require_rating),
        "low_rated_mode": low_rated_mode if minimum is not None and low_rated_mode in db.LOW_RATED_MODES else "ignore",
    }


PRODUCT_HINT_KEYS = ("name", "manufacturer", "variant", "image_url", "product_url")
URL_HINT_KEYS = ("image_url", "product_url")


def _safe_url(value: str | None) -> str | None:
    """Only absolute http(s) links: these arrive in query strings and form fields
    and end up in href/src, so ``javascript:`` and friends are dropped."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    return raw if parts.scheme in {"http", "https"} and parts.netloc else None


def _product_summary(sid: str, product_id: str, hint: dict, offers: list[dict]) -> dict:
    """Name/image for a product without re-running the search.

    Uses what the search result passed along, then the offers, and only then
    asks the source (one more request).
    """
    summary = {"product_id": str(product_id), "source_id": sid}
    for key, value in hint.items():
        if key in URL_HINT_KEYS:
            value = _safe_url(value)
        if value:
            summary[key] = value
    if not summary.get("name") and offers:
        summary["name"] = offers[0].get("product_name")
        if not summary.get("image_url") and _safe_url(offers[0].get("image_url")):
            summary["image_url"] = offers[0]["image_url"]
    if not summary.get("name") or not summary.get("product_url"):
        src = get_source(sid) or get_source("mock")
        detail = None
        try:
            detail = src.get_product(product_id) if src else None
        except Exception:
            detail = None
        if detail:
            for key, value in detail.as_dict().items():
                if key in URL_HINT_KEYS:
                    value = _safe_url(value)
                if key in PRODUCT_HINT_KEYS and value and not summary.get(key):
                    summary[key] = value
    summary.setdefault("name", f"Product {product_id}")
    return summary


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
    watches = db.list_watches(_scope(user))
    counts = {status: 0 for status in STATUS_LABELS}
    for w in watches:
        counts[w["status"]] = counts.get(w["status"], 0) + 1
    _attach_row_retailers(watches)
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


def _attach_row_retailers(watches: list[dict]) -> None:
    """Dashboard rows show "at {retailer} ★x.x": the strike's shop, else the cheapest
    matching offer from the last check. Scores are current, not as-of-check."""
    latest = db.latest_offers_by_watch()
    picks: list[dict] = []
    for w in watches:
        pick = w.get("strike")
        if not pick:
            offers = [
                o for o in latest.get(int(w["id"]), [])
                if strike.offer_matches(o, w) and strike.watch_price(o, w) is not None
            ]
            pick = min(offers, key=lambda o: strike.watch_price(o, w)) if offers else None
        picks.append(pick or {})
    annotated = trust.annotate([p for p in picks if p])
    it = iter(annotated)
    for w, pick in zip(watches, picks):
        w["row_offer"] = next(it) if pick else None
        # The row's price belongs to the shop it names (a cheaper low-rated shop
        # is explained on the watch page, not here).
        row_price = strike.watch_price(w["row_offer"], w) if w["row_offer"] else None
        w["row_price"] = row_price if row_price is not None else w.get("current_lowest")


@router.post("/check-all")
def check_all(request: Request, csrf_token: str = Form(""), next: str = Form("/")):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    back = strip_prefix(next, _prefix()) if next.startswith("/") and not next.startswith("//") else "/"
    if not _csrf_ok(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    # Admins check everyone's watches: skip owners Auth's roster has revoked, as the scheduler does.
    active = strike.checkable_watches([w for w in db.list_watches(_scope(user)) if w["status"] != "paused"])
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
    # Searching and adding fetch PriceRunner, and every new shop seen is queued
    # for a paid Bright Data lookup: "Manage watches" only.
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
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
        # Arrives from Compare → "Watch this", carrying the search hit's details;
        # no second search.
        hint = {k: request.query_params.get(k) for k in PRODUCT_HINT_KEYS}
        cached = db.get_scan_cache(sid, product_id) or []
        selected = _product_summary(sid, product_id, hint, cached)
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
            "trust_settings": trust.trust_settings(settings),
            "message": msg,
            "error": error,
        },
    )


COMPARE_SORTS = {"total": "Total", "price": "Price", "trust": "Trust"}


@router.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    product_id: str,
    source_id: str | None = None,
    q: str | None = None,
    sort: str = "total",
):
    """Every retailer's offer for one product, with trust scores; "Watch this" from here."""
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    sid = source_id or _primary_source_id()
    error = None
    offers: list[dict] = []
    try:
        offers = strike.fetch_offers(sid, product_id)
    except Exception as exc:
        error = str(exc)
    hint = {k: request.query_params.get(k) for k in PRODUCT_HINT_KEYS}
    product = _product_summary(sid, product_id, hint, offers)
    rows = trust.annotate(offers)
    sort = sort if sort in COMPARE_SORTS else "total"

    def total(o):
        value = o.get("total_price") if o.get("total_price") is not None else o.get("product_price")
        return float(value) if value is not None else 1e18

    if sort == "price":
        rows.sort(key=lambda o: float(o.get("product_price") or 1e18))
    elif sort == "trust":
        rows.sort(key=lambda o: (-(o["trust"].get("score") or -1), total(o)))
    else:
        rows.sort(key=total)
    watch_query = {"product_id": product["product_id"], "source_id": sid, "q": q or ""}
    watch_query.update({k: product.get(k) for k in PRODUCT_HINT_KEYS if product.get(k)})
    return _csrf_response(
        request,
        "compare.html",
        {
            "user": user,
            "active": "add",
            "q": q or "",
            "product": product,
            "offers": rows,
            "sort": sort,
            "compare_sorts": COMPARE_SORTS,
            "source_id": sid,
            "watch_url": "/watches/new?" + urlencode(watch_query),
            "compare_base": "/compare?" + urlencode(watch_query) + "&sort=",
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
    include_delivery: str = Form("0"),
    min_trust_score: str = Form(""),
    require_rating: str = Form("0"),
    low_rated_mode: str = Form("ignore"),
    schedule_minutes: int = Form(DEFAULT_SCHEDULE_MINUTES),
):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
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
            "image_url": _safe_url(image_url),
            "product_url": _safe_url(product_url),
            "target_price": price,
            "condition": condition if condition in {"new", "used", "either"} else "new",
            "in_stock_required": in_stock_required in {"1", "on", "true", "yes"},
            "include_delivery": _truthy(include_delivery),
            "schedule_minutes": _schedule(schedule_minutes),
            **_trust_fields(min_trust_score, require_rating, low_rated_mode),
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
    watch = db.get_watch(watch_id, _scope(user))
    if not watch:
        return _redirect("/", error="Watch not found")
    observations = db.list_observations(watch_id, limit=40)
    history = list(reversed(observations))
    latest_offers = []
    if observations:
        latest_offers = observations[0].get("offers") or []
    tsettings = trust.trust_settings()
    # Badges use today's scores; the strike/low-rated snapshots keep their own prices.
    offers = []
    for o in trust.annotate(latest_offers, settings=tsettings):
        price = strike.watch_price(o, watch)
        o["is_hit"] = price is not None and price <= float(watch["target_price"])
        o["below_minimum"] = o["is_hit"] and not trust.is_trusted(o.get("trust"), watch)
        offers.append(o)
    strike_offer = trust.annotate([watch["strike"]], settings=tsettings)[0] if watch.get("strike") else None
    low_rated = trust.annotate([watch["low_rated"]], settings=tsettings)[0] if watch.get("low_rated") else None
    return _csrf_response(
        request,
        "watch_detail.html",
        {
            "user": user,
            "active": "watches",
            "watch": watch,
            "observations": observations,
            "history": history,
            "offers": offers,
            "strike_offer": strike_offer,
            "low_rated": low_rated,
            "trust_settings": tsettings,
            "historical_lowest": db.historical_lowest(watch_id),
            "message": msg,
            "error": error,
        },
    )


@router.post("/watches/{watch_id}/edit")
def watch_edit(
    request: Request,
    watch_id: int,
    csrf_token: str = Form(""),
    target_price: str = Form(...),
    condition: str = Form("new"),
    in_stock_required: str = Form("0"),
    include_delivery: str = Form("0"),
    min_trust_score: str = Form(""),
    require_rating: str = Form("0"),
    low_rated_mode: str = Form("ignore"),
    schedule_minutes: int = Form(DEFAULT_SCHEDULE_MINUTES),
):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id, _scope(user))
    if not watch:
        return _redirect("/", error="Watch not found")
    try:
        price = float(target_price.replace(",", ".").strip())
    except ValueError:
        return _redirect(f"/watches/{watch_id}", error="Enter a valid target price")
    if price <= 0:
        return _redirect(f"/watches/{watch_id}", error="Target price must be positive")
    trust_fields = _trust_fields(min_trust_score, require_rating, low_rated_mode)
    fields = dict(
        target_price=price,
        condition=condition if condition in {"new", "used", "either"} else "new",
        in_stock_required=_truthy(in_stock_required),
        include_delivery=_truthy(include_delivery),
        schedule_minutes=_schedule(schedule_minutes),
        **trust_fields,
    )
    # Changed rating rules: forget the remembered low-rated offer so the re-check
    # below judges it afresh (e.g. switching Ignore → Warn warns about it now).
    if any(watch.get(k) != v for k, v in trust_fields.items()):
        fields.update(low_rated_snapshot_json=None, low_rated_fingerprint=None, low_rated_at=None)
    db.update_watch_fields(watch_id, **fields)
    if watch["status"] != "paused":
        try:
            strike.check_watch_now(watch_id)
        except Exception:
            pass
    return _redirect(f"/watches/{watch_id}", msg="Watch updated")


@router.post("/watches/{watch_id}/pause")
def watch_pause(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id, _scope(user))
    if not watch:
        return _redirect("/", error="Watch not found")
    db.pause_watch(watch_id)
    return _redirect(f"/watches/{watch_id}", msg="Watch paused")


@router.post("/watches/{watch_id}/resume")
def watch_resume(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id, _scope(user))
    if not watch:
        return _redirect("/", error="Watch not found")
    db.resume_watch(watch_id)
    return _redirect(f"/watches/{watch_id}", msg="Watch resumed")


@router.post("/watches/{watch_id}/rearm")
def watch_rearm(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id, _scope(user))
    if not watch:
        return _redirect("/", error="Watch not found")
    db.rearm_watch(watch_id)
    return _redirect(f"/watches/{watch_id}", msg="Watch re-armed")


@router.post("/watches/{watch_id}/check")
def watch_check(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    watch = db.get_watch(watch_id, _scope(user))
    if not watch:
        return _redirect("/", error="Watch not found")
    result = strike.check_watch_now(watch_id)
    if not result.get("ok"):
        return _redirect(f"/watches/{watch_id}", error=result.get("error") or "Check failed")
    return _redirect(f"/watches/{watch_id}", msg="Checked just now")


@router.post("/watches/{watch_id}/delete")
def watch_delete(request: Request, watch_id: int, csrf_token: str = Form("")):
    user, denied = _require_user(request, capability=MANAGE_WATCHES)
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(f"/watches/{watch_id}", error="Invalid CSRF token")
    if not db.get_watch(watch_id, _scope(user)):
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
            **_trust_context(settings),
            # The Bright Data key and quota are shared with EventTrakr: admins only.
            "can_admin_brightdata": _is_admin(user),
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


def _trust_context(settings: dict) -> dict:
    tsettings = trust.trust_settings(settings)
    merchants = db.list_merchants()
    for m in merchants:
        m["trust"] = trust.effective_score(m, tsettings)
        m["website_mismatch"] = trust.website_mismatch(m)
        m["trustpilot_link"] = m.get("tp_url") or (
            trustpilot_url(m["effective_domain"]) if m.get("effective_domain") else None
        )
    return {
        "trust_settings": tsettings,
        "trust_key_set": bool(trust.get_api_key()),
        "trust_eventtrakr_key": trust.eventtrakr_key_available(),
        "trust_usage": trust.usage(),
        "trust_status": trust.status(),
        "trust_merchants": merchants,
        "trust_mock": env.mock,
    }


TRUST_BACK = "/settings?tab=trust"


@router.post("/settings/trust")
def settings_trust(
    request: Request,
    csrf_token: str = Form(""),
    brightdata_enabled: str | None = Form(None),
    pricerunner_fallback: str = Form("0"),
    min_reviews: int = Form(50),
    refresh_days: int | None = Form(None),
    brightdata_limit: str = Form(""),
    brightdata_reset_day: str = Form(""),
):
    """Trust display options for "Manage sources"; anything that spends Bright Data
    records (turning lookups on, how often they refresh, the shared limit) is admin-only."""
    user, denied = _require_user(request, capability="can_manage_sources")
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(TRUST_BACK, error="Invalid CSRF token")
    admin = _is_admin(user)
    if (brightdata_limit.strip() or brightdata_reset_day.strip()) and not admin:
        return _redirect(TRUST_BACK, error=ADMIN_ONLY_BRIGHTDATA)
    current = trust.trust_settings()
    was_enabled = current["brightdata_enabled"]
    # Members' form leaves these out; an unchecked admin checkbox is absent too.
    enabled = _truthy(brightdata_enabled) if (admin or brightdata_enabled is not None) else was_enabled
    days = min(90, max(1, int(refresh_days))) if refresh_days is not None else int(current["refresh_days"])
    if not admin and (enabled != was_enabled or days != int(current["refresh_days"])):
        return _redirect(TRUST_BACK, error=ADMIN_ONLY_LOOKUPS)
    db.set_setting("trust_brightdata_enabled", "1" if enabled else "0")
    db.set_setting("trust_pricerunner_fallback", "1" if _truthy(pricerunner_fallback) else "0")
    db.set_setting("trust_min_reviews", str(max(0, int(min_reviews))))
    db.set_setting("trust_refresh_days", str(days))
    if brightdata_limit.strip() or brightdata_reset_day.strip():
        try:
            if brightdata_limit.strip():
                trust.set_limit(min(10_000_000, max(0, int(brightdata_limit))))
            if brightdata_reset_day.strip():
                trust.set_reset_day(min(31, max(1, int(brightdata_reset_day))))
        except ValueError:
            return _redirect(TRUST_BACK, error="Enter whole numbers for the limit and reset day")
        except Exception:
            return _redirect(TRUST_BACK, error="Couldn't save the Bright Data limit: the Vault folder isn't writable")
    if enabled and not was_enabled:
        trust.refresh_in_background(force=True)
        return _redirect(TRUST_BACK, msg="Saved · looking up scores in the background")
    return _redirect(TRUST_BACK, msg="Trust settings saved")


@router.post("/settings/trust/key")
def settings_trust_key(
    request: Request,
    csrf_token: str = Form(""),
    action: str = Form("save"),
    api_key: str = Form(""),
):
    user, denied = _require_user(request, capability="can_manage_sources")
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(TRUST_BACK, error="Invalid CSRF token")
    if not _is_admin(user):
        return _redirect(TRUST_BACK, error=ADMIN_ONLY_BRIGHTDATA)
    if action == "copy":
        if not trust.copy_key_from_eventtrakr():
            return _redirect(TRUST_BACK, error="EventTrakr has no Bright Data key in the vault")
        return _redirect(TRUST_BACK, msg="Copied EventTrakr's Bright Data key")
    if action == "remove":
        trust.set_api_key("")
        return _redirect(TRUST_BACK, msg="PriceWatch's Bright Data key removed")
    if not api_key.strip():
        return _redirect(TRUST_BACK, msg="Key unchanged")
    trust.set_api_key(api_key)
    return _redirect(TRUST_BACK, msg="Bright Data key saved")


@router.post("/settings/trust/refresh")
def settings_trust_refresh(request: Request, csrf_token: str = Form("")):
    user, denied = _require_user(request, capability="can_manage_sources")
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(TRUST_BACK, error="Invalid CSRF token")
    if not _is_admin(user):
        return _redirect(TRUST_BACK, error=ADMIN_ONLY_LOOKUPS)
    if not trust.trust_settings()["brightdata_enabled"]:
        return _redirect(TRUST_BACK, error="Turn on trust scores via Bright Data first")
    if not trust.get_api_key() and not env.mock:
        return _redirect(TRUST_BACK, error="Add a Bright Data key first")
    if not trust.refresh_in_background(force=True):
        return _redirect(TRUST_BACK, msg="A lookup is already running")
    return _redirect(TRUST_BACK, msg="Looking up scores in the background — reload in a minute")


@router.post("/settings/trust/retailers/{source_id}/{merchant_id}")
def settings_trust_retailer(
    request: Request,
    source_id: str,
    merchant_id: str,
    csrf_token: str = Form(""),
    action: str = Form("save"),
    domain: str = Form(""),
):
    user, denied = _require_user(request, capability="can_manage_sources")
    if denied:
        return denied
    if not _csrf_ok(request, csrf_token):
        return _redirect(TRUST_BACK, error="Invalid CSRF token")
    # Both actions re-queue the shop for a paid Bright Data lookup.
    if not _is_admin(user):
        return _redirect(TRUST_BACK, error=ADMIN_ONLY_LOOKUPS)
    merchant = db.get_merchant(source_id, merchant_id)
    if not merchant:
        return _redirect(TRUST_BACK, error="Retailer not found")
    if action == "refetch":
        db.requeue_merchant(source_id, merchant_id)
        return _redirect(TRUST_BACK, msg=f"{merchant['name']} queued for the next lookup")
    raw = domain.strip()
    override = normalise_domain(raw) if raw else None
    if raw and not override:
        return _redirect(TRUST_BACK, error="That doesn't look like a web address")
    if override == merchant.get("domain"):
        override = None  # same as discovered — no override needed
    db.set_merchant_domain_override(source_id, merchant_id, override)
    return _redirect(TRUST_BACK, msg=f"{merchant['name']} address saved · score re-queued")


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
    # Household-wide: retention prunes everyone's price history.
    if not _is_admin(user):
        return _redirect("/settings?tab=general", error=ADMIN_ONLY_GENERAL)
    db.set_setting("default_schedule_minutes", str(_schedule(default_schedule_minutes)))
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
