from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from app.config import ROOT_DIR, env, resolve_cockpit_url
from app import __asset_rev__, jobs, services
from stonepi_auth.brand import fonts_rev
from stonepi_auth import APP_CATALOG, APP_IDS, SYSTEM_APP_IDS, login_url, logout_url
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok, csrf_ok_request, set_csrf_cookie
from stonepi_auth.alerts import add_shared_templates
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
templates.env.globals["notify_url"] = services.notify_page_url
# Catalog ids (plus the platform) that have their own service icon in _icons.html.
templates.env.globals["service_icon_ids"] = frozenset({"platform", *APP_IDS})
router = APIRouter()

_SETTINGS_CACHE_TTL = 30.0
_backup_tab_cache: dict = {"at": 0.0, "data": None}
_acl_status_cache: dict = {"at": 0.0, "data": None}


def _browser_auth_url() -> str:
    """Login/logout URL for the browser (not server-side auth_request)."""
    try:
        from stonepi_auth.http import browser_auth_url

        return browser_auth_url(env.auth_url, routing=getattr(env, "routing", "path"))
    except ImportError:
        # Pi overlay may update dashboard before stonepi_auth — keep working.
        from pathlib import Path
        import os

        raw = (env.auth_url or "").strip() or "/auth"
        if Path("/etc/nginx/sites-enabled/stonepi").exists():
            return "/auth"
        if getattr(env, "routing", "path") == "path" and os.name != "nt":
            return "/auth"
        return raw


def _platform_hostname() -> str:
    try:
        from stonepi_auth import platform_hostname

        return platform_hostname()
    except Exception:  # noqa: BLE001
        return (env.hostname or "stonepi").strip() or "stonepi"


def _settings() -> PlatformSettings:
    return PlatformSettings(
        enabled=True,
        session_secret=services.session_secret(),
        app_id="dashboard",
        prefix="",
        auth_url=_browser_auth_url(),
        public_origin=env.public_origin,
        hostname=_platform_hostname(),
    )


def _login_next(request: Request) -> str:
    path = request.url.path or "/"
    origin = (env.public_origin or "").rstrip("/")
    if origin and "127.0.0.1" in origin:
        return f"{origin}{path}"
    return path


def _user_or_login(request: Request, *, admin: bool = False, require_dashboard: bool = True):
    user = services.current_user(request.cookies)
    if user is None:
        return None, RedirectResponse(login_url(_settings(), _login_next(request)), status_code=303)
    if require_dashboard and not user.can_access("dashboard"):
        return None, templates.TemplateResponse(
            request, "forbidden.html", {"user": user, "hostname": _platform_hostname()}, status_code=403
        )
    if admin and not user.is_admin:
        return None, templates.TemplateResponse(
            request, "forbidden.html", {"user": user, "hostname": _platform_hostname()}, status_code=403
        )
    return user, None


def _request_host(request: Request) -> str:
    return (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    )


def _alerts_bell(request: Request, user) -> dict:
    """Top-bar bell: shown to people with Phone alerts; dot until they set up."""
    from stonepi_auth.alerts import bell_context
    from stonepi_auth.session import COOKIE_NAME

    # Dashboard is the portal home, so the Notifications page is same-origin.
    return bell_context(user, session_cookie=request.cookies.get(COOKIE_NAME), home_url="")


def _ctx(request: Request, user, extra: dict | None = None):
    payload = {
        "request": request,
        "user": user,
        "hostname": _platform_hostname(),
        "cockpit_url": resolve_cockpit_url(_request_host(request)),
        "nav": request.url.path,
        "active": "overview",
        "error": None,
        "message": None,
        "csrf_token": csrf_from_request(request.cookies),
    }
    if extra:
        payload.update(extra)
    if "alerts_bell_state" not in payload:
        payload["alerts_bell_state"] = _alerts_bell(request, user)
    payload["alerts_bell_state"] = {
        "url": "/notifications",
        **payload["alerts_bell_state"],
        "active": payload.get("active") == "notifications",
    }
    if "using_factory_admin" not in payload:
        payload["using_factory_admin"] = (
            _factory_admin(dict(request.cookies), user) if user else False
        )
    return payload


def _html(request: Request, template: str, user, extra: dict | None = None, status_code: int = 200):
    ctx = _ctx(request, user, extra)
    response = templates.TemplateResponse(request, template, ctx, status_code=status_code)
    set_csrf_cookie(response, ctx["csrf_token"])
    return response


def _require_csrf(request: Request, form) -> bool:
    return csrf_ok(request.cookies.get(CSRF_COOKIE), str(form.get("csrf_token") or ""))


def _require_csrf_any(request: Request, form=None) -> bool:
    form_token = str(form.get("csrf_token") or "") if form is not None else None
    header = request.headers.get("x-stonepi-csrf") or request.headers.get("X-StonePi-CSRF")
    return csrf_ok_request(dict(request.cookies), form_token=form_token, header_token=header)


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept or request.headers.get("x-requested-with") == "fetch"


def _login_redirect(request: Request, next_path: str | None = None) -> RedirectResponse:
    """Send the browser to platform sign-in and drop the dead session cookie.

    Auth's login page checks its own session table, so a cookie that still
    verifies here but was revoked there gets the sign-in form (no redirect loop).
    """
    if next_path is None:
        nxt = _login_next(request)
        if request.url.query:
            nxt = f"{nxt}?{request.url.query}"
    else:
        origin = (env.public_origin or "").rstrip("/")
        nxt = f"{origin}{next_path}" if origin and "127.0.0.1" in origin else next_path
    response = RedirectResponse(login_url(_settings(), nxt), status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def _auth_wait_page(request: Request, user, *, retry_url: str | None = None):
    """Auth is restarting: say so and retry on our own instead of showing an error."""
    here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    section = (retry_url or here).split("?", 1)[0].strip("/").split("/", 1)[0]
    active = {"": "home", "users": "users", "overview": "overview", "applications": "applications",
              "settings": "settings"}.get(section, "")
    return _html(
        request,
        "auth_wait.html",
        user,
        {"active": active, "retry_url": retry_url or here},
        status_code=503,
    )


def _auth_error_text(exc: Exception) -> str:
    return str(exc) or "Auth did not answer. Try again."


def _auth_failure(request: Request, user, exc: Exception, *, next_path: str | None = None, wait_page: bool = True):
    """One answer for an Auth call made on the person's behalf that failed.

    - Auth 401 (session revoked: signed out elsewhere, password changed, account
      disabled, restore) -> platform sign-in with ``next`` back here, instead of
      a page showing Auth's "Not signed in" as an error.
    - Auth unreachable / 502-504 (restarting) -> a self-retrying "Auth is
      restarting" page (503 JSON for fetch callers).
    Anything else returns None so the caller keeps its own error handling.
    ``next_path`` is the page to come back to (for POSTs); ``wait_page=False``
    lets a form POST fall through to its own error banner rather than re-posting.
    """
    if not isinstance(exc, services.AuthAPIError):
        return None
    if exc.signed_out:
        if _wants_json(request):
            target = _login_redirect(request, next_path).headers["location"]
            response = JSONResponse(
                {"ok": False, "error": "You were signed out. Sign in again.", "signed_out": True, "login_url": target},
                status_code=401,
            )
            response.delete_cookie(COOKIE_NAME, path="/")
            return response
        return _login_redirect(request, next_path)
    if exc.unavailable:
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "error": services.AUTH_RESTARTING_MESSAGE, "retry": True}, status_code=503
            )
        if wait_page:
            return _auth_wait_page(request, user, retry_url=next_path)
    return None


async def _form_body(request: Request) -> FormData:
    """Parse form body for sync handlers (awaited on the loop; handler runs in threadpool)."""
    return await request.form()


def _job_started_response(
    request: Request,
    *,
    job_id: str,
    redirect_url: str,
    message: str,
):
    if _wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "job_id": job_id,
                "status": "pending",
                "message": message,
            }
        )
    sep = "&" if "?" in redirect_url else "?"
    return RedirectResponse(
        f"{redirect_url}{sep}msg={quote(message, safe='')}&job={job_id}",
        status_code=303,
    )


LOCKED_APP_IDS = frozenset(SYSTEM_APP_IDS)


@router.get("/healthz")
@router.get("/health")
def healthz():
    return {"ok": True, "service": "dashboard"}


@router.get("/system/health")
def system_health(request: Request):
    """Admin platform health rollup (JSON). Liveness remains /healthz."""
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    from app import collector

    snap = collector.get_snapshot()
    payload = snap.get("system_health")
    if not payload:
        payload = collector.refresh_now().get("system_health") or {
            "ok": False,
            "ready": False,
            "summary": "checking…",
        }
    return JSONResponse(payload)


def _factory_admin(cookies: dict[str, str], user) -> bool:
    if not user or not user.is_admin:
        return False
    # Session `fac` / using_factory_admin — avoid /api/me on every render.
    return bool(getattr(user, "using_factory_admin", False))


def _format_backup_stamp(stamp: str) -> str:
    """ISO-ish stamp → 'DD-MM-YYYY HH:MM:SS'; unparseable stamps pass through trimmed."""
    if not stamp:
        return ""
    raw = stamp[:19].replace("T", " ")
    date_part, _, time_part = raw.partition(" ")
    bits = date_part.split("-")
    if len(bits) == 3 and len(bits[0]) == 4 and all(b.isdigit() for b in bits):
        date_part = f"{bits[2]}-{bits[1]}-{bits[0]}"
    return f"{date_part} {time_part}".strip()


def _backup_days_ago(stamp: str) -> str:
    """ISO-ish stamp → 'Today' / 'Yesterday' / 'N days ago' (Pi local calendar days)."""
    from datetime import datetime

    if not stamp:
        return ""
    try:
        dt = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    days = (datetime.now().date() - dt.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    return f"{days} days ago"


def _one_backup_summary(backup: dict, *, kind_label: str) -> dict:
    status = str(backup.get("status") or "").strip().lower()
    stamp = str(backup.get("timestamp") or backup.get("finished_at") or backup.get("time") or "").strip()
    reason = str(backup.get("reason") or backup.get("message") or "").strip()
    stamp_label = _format_backup_stamp(stamp)

    def detail(*parts: str) -> str:
        return " · ".join(part for part in parts if part)

    if status in {"failed", "error"}:
        return {
            "label": "Failed",
            "detail": detail(kind_label, stamp_label, reason or "Last backup failed"),
            "ok": False,
        }
    if status == "skipped":
        return {
            "label": "Skipped",
            "detail": detail(kind_label, stamp_label, reason or "Last backup was skipped"),
            "ok": False,
        }
    ago = _backup_days_ago(stamp) or stamp_label
    if status == "partial":
        # Platform data backed up; the opt-in Library content copy didn't fit or failed.
        return {
            "label": stamp_label or "Partial",
            "ago": ago,
            "detail": detail(kind_label, "Library content not copied — see Library → Settings → Backup"),
            "ok": True,
        }
    if status in {"ok", "complete", "success"} or (stamp and status not in {"none", "unknown", ""}):
        if stamp_label:
            return {"label": stamp_label, "ago": ago, "detail": detail(kind_label, "Last recorded backup"), "ok": True}
    if stamp_label and status in {"none", "", "unknown"}:
        return {"label": stamp_label, "ago": ago, "detail": detail(kind_label, "Last recorded backup"), "ok": True}
    if status in {"none", "", "unknown"}:
        return {"label": "Never", "detail": f"{kind_label} · No backup recorded yet", "ok": False}
    return {
        "label": (status or "unknown").capitalize(),
        "detail": detail(kind_label, stamp_label, reason or "Backup status"),
        "ok": False,
    }


def _backup_summary(backup: dict) -> dict:
    """Overview prefers local when present; falls back to USB / legacy flat stamp."""
    local = backup.get("local") if isinstance(backup.get("local"), dict) else {}
    usb = backup.get("usb") if isinstance(backup.get("usb"), dict) else {}
    local_sum = _one_backup_summary(local or {}, kind_label="Local")
    usb_sum = _one_backup_summary(usb or {}, kind_label="USB")
    primary = local_sum
    if local_sum.get("ok"):
        primary = local_sum
    elif usb_sum.get("ok"):
        primary = usb_sum
    elif not local_sum.get("ok") and backup.get("status"):
        primary = _one_backup_summary(backup, kind_label="Backup")
    return {
        "label": primary.get("label") or "Never",
        "detail": " · ".join(
            p
            for p in (
                f"Local: {local_sum.get('ago') or local_sum.get('label')}",
                f"USB: {usb_sum.get('ago') or usb_sum.get('label')}",
            )
            if p
        ),
        "ok": bool(local_sum.get("ok") or usb_sum.get("ok")),
        "local": local_sum,
        "usb": usb_sum,
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    launcher_status: dict = {}
    try:
        tiles = services.launcher_tiles(user, dict(request.cookies), status=launcher_status)
    except services.AuthAPIError as exc:
        return _auth_failure(request, user, exc) or _login_redirect(request)
    return _html(
        request,
        "home.html",
        user,
        {
            "active": "home",
            "tiles": tiles,
            # Auth restarting: tiles show in catalog order; don't offer to save over the real one.
            "launcher_order_live": bool(launcher_status.get("order_live")),
            "using_factory_admin": _factory_admin(dict(request.cookies), user),
            "message": request.query_params.get("msg"),
            "error": request.query_params.get("err"),
        },
    )


@router.post("/launcher-order")
def launcher_order(request: Request, form: FormData = Depends(_form_body)):
    """Save Home's tile order.

    Home's drag-to-reorder posts with fetch and gets JSON back — failures as
    4xx/5xx ``{"ok": false, "error"}`` — so a failed save can never look saved
    (a 303 to ``/?err=`` was followed silently by fetch). A plain form post
    still gets the redirect + banner.
    """
    wants_json = _wants_json(request)
    if wants_json and services.current_user(request.cookies) is None:
        return JSONResponse(
            {"ok": False, "error": "You were signed out. Sign in again.", "signed_out": True,
             "login_url": _login_redirect(request, "/").headers["location"]},
            status_code=401,
        )
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected

    def fail(message: str, status_code: int = 400):
        if wants_json:
            return JSONResponse({"ok": False, "error": message}, status_code=status_code)
        return RedirectResponse("/?err=" + quote(message), status_code=303)

    if not _require_csrf(request, form):
        return fail("This page expired. Refresh and try again.", 403)
    raw = str(form.get("order") or "").strip()
    if raw.startswith("["):
        try:
            order = json.loads(raw)
        except json.JSONDecodeError:
            return fail("Invalid order")
    else:
        order = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(order, list) or not order:
        return fail("Pick at least one app")
    try:
        saved = services.save_launcher_order(dict(request.cookies), [str(item) for item in order])
    except Exception as exc:
        handled = _auth_failure(request, user, exc, next_path="/", wait_page=False)
        if handled is not None:
            return handled
        status = int(getattr(exc, "status_code", 502) or 502)
        return fail(_auth_error_text(exc), status if 400 <= status < 600 else 502)
    if wants_json:
        return JSONResponse({"ok": True, "order": saved, "message": "App order saved"})
    return RedirectResponse("/?msg=" + quote("App order saved"), status_code=303)


# Household-wide "hidden apps" list from Auth, remembered briefly so the Health
# page poller doesn't add an Auth round trip every few seconds.
_DISABLED_TTL = 30.0
_disabled_cache: dict = {"at": -1e9, "ids": frozenset()}


def _remember_disabled(ids) -> None:
    _disabled_cache.update(at=time.monotonic(), ids=frozenset(str(item) for item in ids))


def _disabled_for_poll(cookies: dict[str, str]) -> set[str]:
    """Hidden apps for the Health poller: cached copy, refreshed from Auth at most every 30 s.

    Raises AuthAPIError when Auth says the session is gone (so the poller can send
    the person to sign in); any other failure keeps the last known list.
    """
    if time.monotonic() - float(_disabled_cache["at"]) < _DISABLED_TTL:
        return set(_disabled_cache["ids"])
    try:
        payload = services.auth_request("GET", "/api/apps", cookies)
        _remember_disabled(payload.get("disabled") or [])
    except services.AuthAPIError as exc:
        if exc.signed_out:
            raise
    except Exception:  # noqa: BLE001
        pass
    return set(_disabled_cache["ids"])


def overview_watch_payload(snap: dict, disabled: set[str]) -> dict:
    """What the Health page needs to repaint its banner and per-app states in place."""
    import stonepi_watch
    from app import collector

    watch = snap.get("watch") or {}
    if disabled and isinstance(watch, dict) and watch:
        watch = services.apply_disabled_to_watch(watch, disabled)
    cards = []
    healthy = 0
    for card in snap.get("cards") or []:
        enabled = card.get("enabled", True) is not False and card["id"] not in disabled
        ok = bool((card.get("health") or {}).get("ok"))
        healthy += 1 if ok else 0
        cards.append({"id": card["id"], "enabled": enabled, "health_ok": ok, "unit_status": card.get("unit_status") or ""})
    level = str(watch.get("level") or stonepi_watch.LEVEL_HEALTHY).lower()
    ready = bool(snap.get("ready"))
    transitional = collector.watch_in_transition(watch)
    return {
        "ok": True,
        "ready": ready,
        "level": level,
        "summary": watch.get("summary") or "",
        "summary_detail": watch.get("summary_detail") or "",
        "checked_at": watch.get("checked_at") or snap.get("checked_at") or "",
        "healthy_count": healthy,
        "total": len(cards),
        "transitional": transitional,
        # Nothing to wait for: the page can slow its polling right down.
        "settled": ready and level == stonepi_watch.LEVEL_HEALTHY and not transitional,
        "cards": cards,
    }


@router.get("/api/overview/watch")
def overview_watch(request: Request):
    """Health page poller: banner summary + per-app state from the collector snapshot.

    Never probes anything itself. While something isn't healthy it nudges the
    collector to re-check early (collector rate-limits that to one pass per
    FAST_CARDS_INTERVAL no matter how many tabs poll).
    """
    if services.current_user(request.cookies) is None:
        return JSONResponse(
            {"ok": False, "signed_out": True, "login_url": _login_redirect(request, "/overview").headers["location"]},
            status_code=401,
        )
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return JSONResponse({"ok": False, "error": "Administrator only"}, status_code=403)
    from app import collector

    try:
        disabled = _disabled_for_poll(dict(request.cookies))
    except services.AuthAPIError:
        return JSONResponse(
            {"ok": False, "signed_out": True, "login_url": _login_redirect(request, "/overview").headers["location"]},
            status_code=401,
        )
    payload = overview_watch_payload(collector.get_snapshot(), disabled)
    if not payload["settled"]:
        collector.request_refresh()
    response = JSONResponse(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/overview", response_class=HTMLResponse)
def overview(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    import stonepi_watch
    from app import collector

    cookies = dict(request.cookies)
    try:
        from stonepi_auth.http import request_public_origin

        access_origin = request_public_origin(request)
    except Exception:
        access_origin = ""

    snap = collector.get_snapshot()
    if not snap.get("ready"):
        # First hit before collector primed — do one sync fill (still cheap vs old path).
        snap = collector.refresh_now()

    cards = list(snap.get("cards") or [])
    for card in cards:
        card["display_url"] = services.app_display_url(card, access_origin=access_origin)

    # Overlay disabled apps from Auth when possible (collector has no cookies).
    disabled: set[str] = set()
    try:
        payload = services.auth_request("GET", "/api/apps", cookies)
        disabled = set(payload.get("disabled") or [])
        _remember_disabled(disabled)
        for card in cards:
            if card["id"] in disabled:
                card["enabled"] = False
    except services.AuthAPIError as exc:
        if exc.signed_out:
            return _login_redirect(request)
    except Exception:
        pass

    watch = snap.get("watch") or {}
    if disabled and isinstance(watch, dict):
        watch = services.apply_disabled_to_watch(watch, disabled)
    backup = snap.get("backup") or services.backup_info()
    network = snap.get("network") or {}
    listening = snap.get("listening") or {}
    hardware = snap.get("hardware") or {}
    ntp = snap.get("ntp") or {}
    journal_errors = snap.get("journal_errors") or {}
    destinations = snap.get("destinations") or {}
    mem_pct = snap.get("mem_pct")
    temp_c = snap.get("temp_c")
    uptime = snap.get("uptime")

    users, error = [], None
    try:
        users = services.auth_request("GET", "/api/users", cookies).get("users", [])
    except Exception as exc:
        if isinstance(exc, services.AuthAPIError) and exc.signed_out:
            return _login_redirect(request)
        # Auth down is itself a health finding (its card shows Down): no error banner.
        if not (isinstance(exc, services.AuthAPIError) and exc.unavailable):
            error = str(exc)

    users_ok = error is None
    enabled_count = sum(1 for card in cards if card.get("enabled", True) is not False)
    healthy_count = sum(1 for card in cards if (card.get("health") or {}).get("ok"))
    disk_pct = watch.get("disk_pct")
    disk_warn = disk_pct is not None and int(disk_pct) >= stonepi_watch.DISK_ATTENTION_PCT
    notifications_card = next((c for c in cards if c.get("id") == "notify"), None)
    checking = not bool(snap.get("ready"))
    return _html(
        request,
        "overview.html",
        user,
        {
            "active": "overview",
            "cards": cards,
            "card_groups": services.application_card_groups(cards=cards),
            "backup": backup,
            "backup_summary": _backup_summary(backup),
            "watch": watch,
            "disk_pct": disk_pct,
            "disk_warn": disk_warn,
            "mem_pct": mem_pct,
            "temp_c": temp_c,
            "uptime": uptime,
            "destinations": destinations,
            "listening": listening,
            "hardware": hardware,
            "ntp": ntp,
            "journal_errors": journal_errors,
            "notifications_ok": bool((notifications_card or {}).get("health", {}).get("ok")),
            "healthy_count": healthy_count,
            "user_count": len(users) if users_ok else None,
            "users_unavailable": not users_ok,
            "enabled_count": enabled_count,
            "error": error,
            "platform_version": services_platform_version(),
            "using_factory_admin": _factory_admin(cookies, user),
            "network": network,
            "health_checking": checking,
        },
    )


def services_platform_version() -> str:
    from app import update_service

    return update_service.platform_version()


def _service_cards(request: Request) -> list[dict]:
    """Service cards from the background collector (refreshed every 20 s) with
    Auth's live enabled/disabled list on top, instead of probing every service
    and unit on each page load."""
    from app import collector

    snap = collector.get_snapshot()
    if not snap.get("ready") or not snap.get("cards"):
        return services.application_cards(dict(request.cookies))
    cards = [dict(card) for card in snap["cards"]]
    try:
        disabled = set(services.auth_request("GET", "/api/apps", dict(request.cookies)).get("disabled") or [])
    except services.AuthAPIError as exc:
        if exc.signed_out:
            raise
        disabled = set()
    except Exception:
        disabled = set()
    for card in cards:
        card["enabled"] = card["id"] not in disabled and card.get("enabled", True) is not False
    return cards


@router.get("/applications", response_class=HTMLResponse)
def applications(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    try:
        cards = _service_cards(request)
    except services.AuthAPIError:
        return _login_redirect(request)
    return _html(
        request,
        "applications.html",
        user,
        {
            "active": "applications",
            "cards": cards,
            "card_groups": services.application_card_groups(cards=cards),
            "locked_apps": LOCKED_APP_IDS,
            "message": request.query_params.get("msg") or None,
        },
    )


@router.get("/applications/{app_id}", response_class=HTMLResponse)
def application_detail(app_id: str, request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    try:
        cards = {item["id"]: item for item in _service_cards(request)}
    except services.AuthAPIError:
        return _login_redirect(request)
    card = cards.get(app_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Unknown service")
    return _html(
        request,
        "application.html",
        user,
        {
            "active": "applications",
            "card": card,
            "logs": services.unit_logs(card["unit"]) if card.get("unit") else "",
            "message": request.query_params.get("msg") or None,
        },
    )


RESTART_PATH = "/api/internal/units/restart"


def _collector_settle(window: float = 30.0) -> None:
    try:
        from app import collector

        collector.request_refresh(window=window)
    except Exception:  # noqa: BLE001
        pass


@router.post(RESTART_PATH)
async def internal_unit_restart(request: Request):
    """Signed service-to-service restart (the Car Thing panel's System screen).

    Dashboard stays the only place that controls services; callers only name a catalog unit.
    """
    import asyncio
    import logging
    import threading

    from stonepi_auth.internal import verify_internal

    if not verify_internal(services.session_secret(), "POST", RESTART_PATH, request.headers):
        return JSONResponse({"ok": False, "message": "Not allowed."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    unit = str(body.get("unit") or "")
    source = str(body.get("source") or "internal")[:24]
    names = {item["unit"]: item["name"] for item in APP_CATALOG if item.get("unit")}
    if unit not in names:
        return JSONResponse({"ok": False, "message": "Unknown service."}, status_code=400)
    logger = logging.getLogger("dashboard.services")
    logger.warning("Restart %s requested via %s", unit, source)
    if unit == "stonepi-dashboard":
        # Restarting ourselves: answer first, then restart.
        threading.Timer(1.0, services.control_unit, args=(unit, "restart")).start()
        return {"ok": True, "message": f"Restarting {names[unit]}…"}
    ok, message = await asyncio.to_thread(services.control_unit, unit, "restart")
    _collector_settle()
    if not ok:
        logger.warning("Restart %s via %s failed: %s", unit, source, message)
        return {"ok": False, "message": message[:160]}
    return {"ok": True, "message": f"Restarted {names[unit]}."}


@router.post("/applications/{app_id}/{action}")
def application_action(app_id: str, action: str, request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    cards = {item["id"]: item for item in services.application_cards(dict(request.cookies))}
    card = cards.get(app_id)
    if card is None or not card.get("unit"):
        raise HTTPException(status_code=404, detail="Unknown service")
    if not _require_csrf(request, form):
        return _html(
            request,
            "application.html",
            user,
            {
                "active": "applications",
                "card": card,
                "logs": services.unit_logs(card["unit"]),
                "error": "That form expired. Refresh and try again.",
            },
            status_code=400,
        )
    ok, message = services.control_unit(card["unit"], action)
    # Health/Services read the collector snapshot: re-check now and keep checking
    # quickly while the unit comes up (or goes down), so they don't lag 20 s behind.
    _collector_settle()
    extra = {"active": "applications", "card": card, "logs": services.unit_logs(card["unit"])}
    if not ok:
        extra["error"] = message
        return _html(request, "application.html", user, extra, status_code=400)
    label = {"start": "Started", "stop": "Stopped", "restart": "Restarted"}.get(action, "Updated")
    msg = f"{label} {card['name']}."
    return RedirectResponse(f"/applications/{app_id}?msg={msg.replace(' ', '+')}", status_code=303)


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    error = request.query_params.get("err") or None
    people = []
    apps = services.catalog_apps(include_auth=False, include_non_grantable=False, cookies=dict(request.cookies))
    try:
        people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
    except Exception as exc:
        handled = _auth_failure(request, user, exc)
        if handled is not None:
            return handled
        error = error or str(exc)
    return _html(
        request,
        "users.html",
        user,
        {
            "active": "users",
            "people": people,
            "apps": apps,
            "users_form_base": "/users",
            "error": error,
            "message": request.query_params.get("msg") or None,
        },
    )


@router.post("/users")
@router.post("/settings/users")
def users_create(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    apps = services.catalog_apps(include_auth=False, include_non_grantable=False, cookies=dict(request.cookies))
    wants_json = _wants_json(request)

    def _fail(message: str, status_code: int = 400):
        if wants_json:
            return JSONResponse({"ok": False, "error": message}, status_code=status_code)
        people = []
        try:
            people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
        except Exception:
            pass
        return _html(
            request,
            "users.html",
            user,
            {
                "active": "users",
                "people": people,
                "apps": apps,
                "users_form_base": "/users",
                "error": message,
            },
            status_code=status_code,
        )

    if not _require_csrf(request, form):
        return _fail("That form expired. Refresh and try again.")
    try:
        permissions = services.parse_permissions_form(form, apps)
        app_ids = services.ensure_fileserve_for_studio_publish(
            [str(value) for value in form.getlist("apps")],
            permissions,
        )
        services.auth_request(
            "POST",
            "/api/users",
            dict(request.cookies),
            {
                "username": str(form.get("username") or ""),
                "password": str(form.get("password") or ""),
                "display_name": str(form.get("display_name") or ""),
                "is_admin": form.get("is_admin") == "1",
                "phone_alerts": form.get("phone_alerts") == "1",
                "apps": app_ids,
                "permissions": permissions,
            },
        )
    except Exception as exc:
        handled = _auth_failure(request, user, exc, next_path="/users", wait_page=False)
        if handled is not None:
            return handled
        return _fail(_auth_error_text(exc))
    if wants_json:
        return JSONResponse({"ok": True, "message": "Account created"})
    return RedirectResponse("/users?msg=Saved", status_code=303)


@router.post("/users/{user_id}")
@router.post("/settings/users/{user_id}")
def users_update(user_id: str, request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    apps = services.catalog_apps(include_auth=False, include_non_grantable=False, cookies=dict(request.cookies))
    wants_json = _wants_json(request)

    def _fail(message: str, status_code: int = 400):
        if wants_json:
            return JSONResponse({"ok": False, "error": message}, status_code=status_code)
        people = []
        try:
            people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
        except Exception:
            pass
        return _html(
            request,
            "users.html",
            user,
            {
                "active": "users",
                "people": people,
                "apps": apps,
                "users_form_base": "/users",
                "error": message,
            },
            status_code=status_code,
        )

    if not _require_csrf(request, form):
        return _fail("That form expired. Refresh and try again.")
    action = str(form.get("action") or "save")
    try:
        if action == "delete":
            services.auth_request("DELETE", f"/api/users/{user_id}", dict(request.cookies))
            message = "Account deleted"
        else:
            permissions = services.parse_permissions_form(form, apps)
            payload = {
                "display_name": str(form.get("display_name") or ""),
                "enabled": form.get("enabled") == "1",
                "is_admin": form.get("is_admin") == "1",
                "phone_alerts": form.get("phone_alerts") == "1",
                "apps": services.ensure_fileserve_for_studio_publish(
                    [str(value) for value in form.getlist("apps")],
                    permissions,
                ),
                "permissions": permissions,
            }
            password = str(form.get("password") or "")
            if password:
                payload["password"] = password
            services.auth_request("PATCH", f"/api/users/{user_id}", dict(request.cookies), payload)
            message = "Password updated" if password else "Saved"
    except Exception as exc:
        handled = _auth_failure(request, user, exc, next_path="/users", wait_page=False)
        if handled is not None:
            return handled
        return _fail(_auth_error_text(exc))
    if wants_json:
        return JSONResponse(
            {
                "ok": True,
                "message": message,
                "action": action,
                "user_id": user_id,
                "enabled": form.get("enabled") == "1" if action != "delete" else None,
                "is_admin": form.get("is_admin") == "1" if action != "delete" else None,
            }
        )
    return RedirectResponse("/users?msg=Saved", status_code=303)


@router.post("/settings/password")
def settings_password_change(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=general&err=Form+expired#account-password", status_code=303)
    current = str(form.get("current_password") or "")
    new = str(form.get("new_password") or "")
    confirm = str(form.get("new_password_confirm") or "")
    if new != confirm:
        return RedirectResponse(
            "/settings?tab=general&err=New+passwords+do+not+match#account-password",
            status_code=303,
        )
    try:
        auth_resp = services.auth_exchange(
            "POST",
            "/api/me/password",
            dict(request.cookies),
            {"current_password": current, "new_password": new},
        )
    except Exception as exc:
        if isinstance(exc, services.AuthAPIError) and exc.signed_out:
            return _login_redirect(request, "/settings?tab=general")
        return RedirectResponse(
            f"/settings?tab=general&err={quote(_auth_error_text(exc), safe='')}#account-password",
            status_code=303,
        )
    redirect = RedirectResponse("/settings?tab=general&msg=Password+updated#account-password", status_code=303)
    # Forward re-issued session cookie so the factory-password banner clears immediately.
    services.forward_auth_cookies(redirect, auth_resp)
    return redirect


@router.post("/settings/app-colours")
def settings_app_colours_save(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse(
            "/settings?tab=general&panel=app-colours&err=Form+expired",
            status_code=303,
        )
    if str(form.get("action") or "").strip().lower() == "reset":
        services.save_app_colors({}, reset=True)
        return RedirectResponse(
            "/settings?tab=general&panel=app-colours&msg=App+colours+reset+to+defaults",
            status_code=303,
        )
    colors = {
        item["id"]: str(form.get(f"color_{item['id']}") or "")
        for item in services.app_color_items()
    }
    services.save_app_colors(colors)
    return RedirectResponse(
        "/settings?tab=general&panel=app-colours&msg=App+colours+saved",
        status_code=303,
    )


@router.post("/applications/availability")
def applications_availability(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    wants_json = _wants_json(request)
    cookies = dict(request.cookies)

    def fail(message: str, status_code: int = 400):
        if wants_json:
            return JSONResponse({"ok": False, "error": message}, status_code=status_code)
        cards = services.application_cards(cookies)
        return _html(
            request,
            "applications.html",
            user,
            {
                "active": "applications",
                "cards": cards,
                "card_groups": services.application_card_groups(cards=cards),
                "locked_apps": LOCKED_APP_IDS,
                "error": message,
            },
            status_code=status_code,
        )

    if not _require_csrf(request, form):
        return fail("That form expired. Refresh and try again.")
    app_id = str(form.get("app_id") or "").strip()
    enabled_raw = str(form.get("enabled") or "").strip().lower()
    enabled = enabled_raw in {"1", "true", "on", "yes"}
    if app_id not in APP_IDS:
        return fail("Unknown service.")
    if app_id in LOCKED_APP_IDS:
        return fail("SYSTEM services always stay available.")
    names = {item["id"]: str(item.get("name") or item["id"]) for item in APP_CATALOG}
    try:
        payload = services.auth_request("GET", "/api/apps", cookies)
        disabled = {str(item) for item in (payload.get("disabled") or [])}
        if enabled:
            disabled.discard(app_id)
        else:
            disabled.add(app_id)
        disabled -= LOCKED_APP_IDS
        services.auth_request("PATCH", "/api/apps", cookies, {"disabled": sorted(disabled)})
        _remember_disabled(disabled)
    except Exception as exc:
        handled = _auth_failure(request, user, exc, next_path="/applications", wait_page=False)
        if handled is not None:
            return handled
        return fail(_auth_error_text(exc))
    name = names.get(app_id, app_id)
    message = f"{name} {'enabled' if enabled else 'hidden'}"
    if wants_json:
        return JSONResponse({"ok": True, "app_id": app_id, "enabled": enabled, "message": message})
    return RedirectResponse("/applications?msg=" + quote(message), status_code=303)


@router.get("/backups", response_class=HTMLResponse)
def backups(request: Request):
    return RedirectResponse("/settings?tab=backup", status_code=303)


@router.get("/updates", response_class=HTMLResponse)
def updates_page(request: Request):
    return RedirectResponse("/settings?tab=update", status_code=303)


@router.post("/updates/repo")
@router.post("/settings/update/repo")
def updates_repo(request: Request, form: FormData = Depends(_form_body)):
    from app import update_service

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=update&err=Form+expired", status_code=303)
    raw = str(form.get("github_repo") or "")
    repo = update_service.set_github_repo(raw)
    if raw.strip() and not repo:
        return RedirectResponse("/settings?tab=update&err=Use+owner/repo+for+the+GitHub+repository.", status_code=303)
    return RedirectResponse("/settings?tab=update&msg=Repository+saved", status_code=303)


@router.post("/updates/{app_id}/check")
@router.post("/settings/update/{app_id}/check")
def updates_check(app_id: str, request: Request, form: FormData = Depends(_form_body)):
    from app import update_service

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=update&err=Form+expired", status_code=303)
    if app_id not in update_service.updatable_ids():
        raise HTTPException(status_code=404, detail="Unknown target")
    result = update_service.check_latest(app_id)
    msg = result.get("message") or "Checked GitHub."
    key = "msg" if result.get("ok", True) else "err"
    return RedirectResponse(f"/settings?tab=update&{key}={quote(msg, safe='')}", status_code=303)


@router.post("/updates/{app_id}/install")
@router.post("/settings/update/{app_id}/install")
def updates_install(app_id: str, request: Request, form: FormData = Depends(_form_body)):
    from app import update_service

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=update&err=Form+expired", status_code=303)
    if app_id not in update_service.updatable_ids():
        raise HTTPException(status_code=404, detail="Unknown target")

    def _run_install() -> str:
        result = update_service.install_latest(app_id)
        msg = result.get("message") or "Installed."
        if not result.get("ok", True):
            raise RuntimeError(msg)
        return msg

    job_id = jobs.start_job(f"update_install:{app_id}", _run_install)
    return _job_started_response(
        request,
        job_id=job_id,
        redirect_url="/settings?tab=update",
        message="Update install started — refresh Updates in a minute.",
    )


SETTINGS_TABS = [
    ("general", "General"),
    ("network", "Network"),
    ("vault", "Vault"),
    ("automations", "Automations"),
    ("update", "Updates"),
    ("backup", "Backup"),
    ("about", "About"),
]
SETTINGS_LEDES = {
    "general": "Appearance, app colours, password, and view options. Accounts stay under Users.",
    "network": "Appliance hostname, home network vs internet-facing posture, and Tailscale remote access.",
    "vault": "Encrypted secrets for apps and platform services.",
    "automations": "When→then jobs: USB backup, Display push on Watch or backup.",
    "update": "GitHub Releases for app packages and the platform pack.",
    "backup": "Local schedule, USB status, restore drill, and snapshot restore.",
    "about": "Name, description, GitHub, and the version running here.",
}
SETTINGS_HUB_LEDE = "Household portal settings — appearance, network, and system."
SETTINGS_HUB_SUBTEXTS = {
    "general": "Palette, app colours, view density, and your password.",
    "network": "Hostname, LAN vs internet-facing, Tailscale.",
    "vault": "Encrypted secrets for apps.",
    "automations": "USB backup and display jobs.",
    "update": "GitHub Releases for packages.",
    "backup": "Local schedule, USB drill, and restore.",
    "about": "Version and project links.",
}
SETTINGS_GROUPS = (
    ("you", "You", ("general",)),
    ("house", "House", ("network", "vault", "automations")),
    ("system", "System", ("update", "backup", "about")),
)
# Multi-card tabs: (panel_id, label, card_ids)
SETTINGS_SECTION_PANELS = {
    "general": (
        ("appearance", "Appearance", ("appearance",)),
        ("app-colours", "App colours", ("app-colours",)),
        ("view", "View options", ("view",)),
        ("password", "Your password", ("password",)),
    ),
    "network": (
        ("hostname", "Hostname", ("hostname",)),
        ("exposure", "Network exposure", ("exposure",)),
        ("remote", "Remote access", ("remote",)),
    ),
}
# L2 list icons — match section-heading icons on each panel card.
SETTINGS_PANEL_ICONS = {
    "appearance": "appearance",
    "app-colours": "appearance",
    "view": "view",
    "password": "auth",
    "hostname": "network",
    "exposure": "network",
    "remote": "remote",
}
SETTINGS_PANEL_SUBTEXTS = {
    "appearance": "Palette for this browser",
    "app-colours": "Tile accents on Home, Health, and Services",
    "view": "Home, Health, and Services density",
    "password": "Change your sign-in password",
    "hostname": "LAN name for NAME.local",
    "exposure": "LAN vs internet-facing",
    "remote": "Tailscale remote access",
}


def _panel_list_entry(panel_id: str, label: str, cards: tuple[str, ...]) -> dict:
    return {
        "id": panel_id,
        "label": label,
        "cards": list(cards),
        "icon": SETTINGS_PANEL_ICONS.get(panel_id, panel_id),
        "subtext": SETTINGS_PANEL_SUBTEXTS.get(panel_id, "Open this section"),
    }


def settings_groups_for(*, appearance_only: bool = False) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    allowed = {"general"} if appearance_only else {key for key, _ in SETTINGS_TABS}
    labels = dict(SETTINGS_TABS)
    groups: list[tuple[str, str, list[tuple[str, str, str]]]] = []
    for group_id, group_label, tab_keys in SETTINGS_GROUPS:
        rows = [
            (key, labels[key], SETTINGS_HUB_SUBTEXTS.get(key, SETTINGS_LEDES.get(key, "")))
            for key in tab_keys
            if key in allowed
        ]
        if rows:
            groups.append((group_id, group_label, rows))
    return groups


def settings_section_panels_for(tab: str) -> list[tuple[str, str, tuple[str, ...]]]:
    panels = SETTINGS_SECTION_PANELS.get(tab, ())
    return list(panels) if len(panels) > 1 else []


def normalize_settings_panel(tab: str, value: str | None) -> str | None:
    panels = settings_section_panels_for(tab)
    if not panels:
        return None
    key = (value or "").strip().lower()
    if not key:
        return None
    if any(panel_id == key for panel_id, _label, _cards in panels):
        return key
    return panels[0][0]

# Friendly catalog for Settings → Vault (dropdown). Values are env/Vault key names.
VAULT_KEY_CATALOG = [
    {
        "group": "Platform / Outputs",
        "items": [
            {"id": "STONEPI_SESSION_SECRET", "label": "Session secret", "blurb": "Shared sign-in cookie secret across apps.", "required_hint": True},
            {"id": "STONEPI_NTFY_TOKEN", "label": "Household ntfy token", "blurb": "Shared ntfy access token — configure topic under Notify → Destinations.", "required_hint": False},
            {"id": "DISPLAY_WEBHOOK_URL", "label": "TRMNL webhook (Dashboard Display)", "blurb": "Webhook for the built-in Dashboard Display. Other Displays use DISPLAY_WEBHOOK_URL_<ID>; set them on each Display in Notify.", "required_hint": False},
            {"id": "STONEPI_RECOVER_PASSWORD", "label": "Recover password", "blurb": "Password for /recover/ console (username stonepi), at least 12 characters. Not your portal login. Saved root-only to /etc/stonepi/recover.passwd, never the Vault.", "required_hint": False},
            {"id": "TAILSCALE_API_KEY", "label": "Tailscale API key", "blurb": "Cloud API key to apply ACL from Settings → Network.", "required_hint": False},
            {"id": "TAILSCALE_TAILNET", "label": "Tailscale tailnet", "blurb": "Tailnet name or id for ACL API (e.g. example.com).", "required_hint": False},
        ],
    },
    {
        "group": "AI / Studio",
        "items": [
            {"id": "OPENAI_API_KEY", "label": "OpenAI API key", "blurb": "NewsCast briefings and Studio (OpenAI models)."},
            {"id": "ANTHROPIC_API_KEY", "label": "Anthropic API key", "blurb": "Studio chat-build when using Claude."},
            {"id": "STUDIO_LLM_BASE_URL", "label": "Studio LLM base URL", "blurb": "Optional OpenAI-compatible endpoint for Studio."},
        ],
    },
    {
        "group": "NewsCast",
        "items": [
            {"id": "X3_SYNC_TOKEN", "label": "Reader sync token", "blurb": "CrossPoint / OPDS catalog token when internet-facing."},
            {"id": "NEWSCAST_READER_SSH_PASSWORD", "label": "Reader SSH password", "blurb": "Optional password for reader device setup."},
        ],
    },
    {
        "group": "Data providers",
        "items": [
            {"id": "BRIGHTDATA_API_KEY", "label": "Bright Data API key (EventTrakr)", "blurb": "Facebook events and social accounts in EventTrakr."},
            {"id": "PRICEWATCH_BRIGHTDATA_API_KEY", "label": "Bright Data API key (PriceWatch)", "blurb": "Trustpilot trust scores for PriceWatch retailers."},
        ],
    },
    {
        "group": "Calendars",
        "items": [
            {"id": "GOOGLE_CLIENT_ID", "label": "Google client ID", "blurb": "Google Calendar OAuth client id."},
            {"id": "GOOGLE_CLIENT_SECRET", "label": "Google client secret", "blurb": "Google Calendar OAuth client secret."},
        ],
    },
    {
        "group": "Legacy (prefer Destinations)",
        "items": [
            {"id": "NEWSCAST_NTFY_TOKEN", "label": "NewsCast ntfy (legacy)", "blurb": "Unused when Notify Destinations owns ntfy — prefer STONEPI_NTFY_TOKEN."},
            {"id": "PRICEWATCH_NTFY_TOKEN", "label": "PriceWatch ntfy (legacy)", "blurb": "Unused when Notify Destinations owns ntfy — prefer STONEPI_NTFY_TOKEN."},
        ],
    },
]


# The Recover console runs as root; its password must not sit in the group-readable Vault.
# Settings writes it straight to recover.passwd through the root backup helper instead.
RECOVER_PASSWORD_KEY = "STONEPI_RECOVER_PASSWORD"
RECOVER_PASSWD_FILE = Path(os.environ.get("STONEPI_RECOVER_PASSWD", "/etc/stonepi/recover.passwd"))
RECOVER_PASSWORD_MIN_LENGTH = 12


def _recover_password_set() -> bool:
    """Existence only — the file is root 0600 and the Dashboard never reads it."""
    try:
        return RECOVER_PASSWD_FILE.is_file()
    except OSError:
        return False


def _vault_label_map() -> dict[str, str]:
    labels: dict[str, str] = {}
    for group in VAULT_KEY_CATALOG:
        for item in group["items"]:
            labels[item["id"]] = item["label"]
    return labels


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str | None = None, panel: str | None = None):
    import app as dashboard_app
    from app import update_service

    # Appearance is for every signed-in household member (product apps link here).
    # Admin-only tabs still load only for admins below.
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    raw = (tab or "").strip().lower()
    settings_hub = raw == "" or raw == "hub"
    settings_tab = "general" if settings_hub else raw
    if settings_tab in {"trmnl", "trmnl-integration", "display"}:
        return RedirectResponse(services.notify_page_url("displays"), status_code=303)
    if settings_tab in {"updates"}:
        settings_tab = "update"
    if settings_tab in {"backups"}:
        settings_tab = "backup"
    if settings_tab in {"exposure"}:
        settings_tab = "network"

    about = {
        "app_name": "StonePi",
        "app_github_user": getattr(dashboard_app, "__github_user__", "ast0ne1"),
        "app_github": getattr(dashboard_app, "__github__", "https://github.com/ast0ne1"),
        "app_version": getattr(dashboard_app, "__version__", "0.0.0"),
    }

    if not user.is_admin:
        # Household members: Appearance + own password on General.
        if not settings_hub:
            settings_tab = "general"
        section_panels = settings_section_panels_for("general")
        settings_panel = None if settings_hub else normalize_settings_panel("general", panel)
        panels_by_tab = {
            "general": [
                _panel_list_entry(panel_id, label, cards)
                for panel_id, label, cards in SETTINGS_SECTION_PANELS.get("general", ())
            ]
        }
        extra = {
            "active": "settings",
            "settings_hub": settings_hub,
            "settings_tab": "general",
            "settings_panel": settings_panel,
            "settings_section_panels": section_panels,
            "settings_panels_by_tab": panels_by_tab,
            "settings_groups": settings_groups_for(appearance_only=True),
            "settings_tabs": [("general", "General")],
            "settings_lede": SETTINGS_HUB_LEDE if settings_hub else SETTINGS_LEDES["general"],
            "settings_hub_lede": SETTINGS_HUB_LEDE,
            "settings_ledes": {"general": SETTINGS_LEDES["general"]},
            "settings_labels": {"general": "General"},
            "message": request.query_params.get("msg") or None,
            "error": request.query_params.get("err") or None,
            "app_color_items": services.app_color_items(),
            **about,
            "settings_appearance_only": True,
        }
        return _html(request, "settings.html", user, extra)

    if settings_tab == "watch":
        return RedirectResponse("/overview", status_code=303)

    known = {key for key, _ in SETTINGS_TABS}
    if not settings_hub and settings_tab not in known:
        settings_tab = "general"
        settings_hub = False

    section_panels = [] if settings_hub else settings_section_panels_for(settings_tab)
    settings_panel = None if settings_hub else normalize_settings_panel(settings_tab, panel)
    panels_by_tab = {
        key: [
            _panel_list_entry(panel_id, label, cards)
            for panel_id, label, cards in SETTINGS_SECTION_PANELS.get(key, ())
        ]
        for key, _ in SETTINGS_TABS
        if key in SETTINGS_SECTION_PANELS
    }
    extra = {
        "active": "settings",
        "settings_hub": settings_hub,
        "settings_tab": settings_tab,
        "settings_panel": settings_panel,
        "settings_section_panels": section_panels,
        "settings_panels_by_tab": panels_by_tab,
        "settings_groups": settings_groups_for(appearance_only=False),
        "settings_tabs": SETTINGS_TABS,
        "settings_lede": SETTINGS_HUB_LEDE if settings_hub else SETTINGS_LEDES[settings_tab],
        "settings_hub_lede": SETTINGS_HUB_LEDE,
        "settings_ledes": SETTINGS_LEDES,
        "settings_labels": dict(SETTINGS_TABS),
        "message": request.query_params.get("msg") or None,
        "error": request.query_params.get("err") or None,
        "app_color_items": services.app_color_items(),
        **about,
        "settings_appearance_only": False,
    }
    if settings_hub:
        pass
    elif settings_tab == "general":
        pass
    elif settings_tab == "network":
        from app import network as network_svc
        from stonepi_auth import exposure_mode

        extra["exposure_mode"] = exposure_mode()
        extra["network"] = network_svc.network_snapshot()
        extra["appliance_hostname"] = _platform_hostname()
        extra["acl_status"] = _tailscale_acl_status()
    elif settings_tab == "vault":
        from stonepi_vault import get_vault

        extra["vault_catalog"] = VAULT_KEY_CATALOG
        labels = _vault_label_map()
        extra["vault_known_ids"] = sorted(labels.keys())
        stored_ids: set[str] = set()
        try:
            vault = get_vault()
            stored = vault.list_keys()
            stored_ids = {str(k) for k in stored}
            extra["vault_keys"] = [
                {"id": key, "label": labels.get(key, key), "known": key in labels}
                for key in stored
                if key != RECOVER_PASSWORD_KEY
            ]
        except Exception as exc:
            extra["vault_keys"] = []
            extra["error"] = extra.get("error") or f"Vault unavailable: {exc}"
        stored_ids.discard(RECOVER_PASSWORD_KEY)
        if _recover_password_set():
            stored_ids.add(RECOVER_PASSWORD_KEY)
            extra["vault_keys"].append(
                {"id": RECOVER_PASSWORD_KEY, "label": labels.get(RECOVER_PASSWORD_KEY, RECOVER_PASSWORD_KEY), "known": True}
            )
        matrix: list[dict] = []
        for group in VAULT_KEY_CATALOG:
            if str(group.get("group") or "").startswith("Legacy"):
                continue
            for item in group["items"]:
                matrix.append(
                    {
                        "id": item["id"],
                        "label": item["label"],
                        "group": group["group"],
                        "configured": item["id"] in stored_ids,
                    }
                )
        extra["vault_matrix"] = matrix
    elif settings_tab == "automations":
        import stonepi_automations as automations

        extra["automation_rules"] = automations.RULE_CATALOG
        extra["automation_state"] = automations.load_state()
    elif settings_tab == "update":
        extra.update(
            {
                "github_repo": update_service.github_repo(),
                "update_groups": update_service.version_card_groups(),
            }
        )
    elif settings_tab == "backup":
        extra.update(_backup_tab_extras())
    extra["notify_status"] = services.notify_admin_status(dict(request.cookies))
    return _html(request, "settings.html", user, extra)


def _backup_tab_extras() -> dict:
    now = time.monotonic()
    cached = _backup_tab_cache.get("data")
    if cached is not None and (now - float(_backup_tab_cache.get("at") or 0)) < _SETTINGS_CACHE_TTL:
        return dict(cached)
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_backup = pool.submit(services.backup_info)
        f_snaps = pool.submit(services.list_usb_backups)
        f_sched = pool.submit(services.local_backup_schedule)
        f_drill = pool.submit(services.restore_drill_status)
        f_fail = pool.submit(services.failover_status)
        data = {
            "backup": f_backup.result(),
            "backup_snapshots": f_snaps.result(),
            "backup_schedule": f_sched.result(),
            "drill": f_drill.result(),
            "failover": f_fail.result(),
        }
    _backup_tab_cache["data"] = data
    _backup_tab_cache["at"] = now
    return dict(data)


def _tailscale_acl_status() -> dict:
    """Whether the Tailscale API key + tailnet are in the Vault.

    Read in-process (Dashboard can read the Vault): the old path ran a root
    helper that started Python twice, ~1-2 s on a Pi, on every Network visit.
    """
    now = time.monotonic()
    cached = _acl_status_cache.get("data")
    if cached is not None and (now - float(_acl_status_cache.get("at") or 0)) < _SETTINGS_CACHE_TTL:
        return dict(cached)
    info: dict = {"configured": False}
    try:
        from stonepi_vault import get_secret

        api_key = get_secret("TAILSCALE_API_KEY", env_name="TAILSCALE_API_KEY", default="")
        tailnet = get_secret("TAILSCALE_TAILNET", env_name="TAILSCALE_TAILNET", default="")
        if api_key and tailnet:
            info = {"configured": True, "tailnet": tailnet}
    except Exception:
        pass
    _acl_status_cache["data"] = info
    _acl_status_cache["at"] = now
    return dict(info)


@router.get("/api/jobs/{job_id}")
def api_job_status(job_id: str, request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return JSONResponse(job)


@router.get("/api/jobs")
def api_jobs_list(request: Request, kind: str | None = None):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if kind:
        latest = jobs.latest_job(kind)
        return JSONResponse({"ok": True, "kind": kind, "latest": latest, "jobs": jobs.list_jobs(kind)})
    return JSONResponse({"ok": True, "jobs": jobs.list_jobs()})


@router.get("/settings/backup/job-status")
def settings_backup_job_status(request: Request, id: str | None = None, kind: str | None = None):
    """JSON status for a backup restore/drill job (poll from Backup tab)."""
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    job = None
    if id:
        job = jobs.get_job(id)
    else:
        want = (kind or "").strip() or "backup_restore"
        job = jobs.latest_job(want)
        if job is None and not kind:
            job = jobs.latest_job("backup_drill")
    if job is None:
        return JSONResponse({"ok": False, "error": "No job found"}, status_code=404)
    return JSONResponse({"ok": True, **job})


@router.post("/settings/backup/drill")
def settings_backup_drill(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=backup&err=Form+expired", status_code=303)

    def _run_drill() -> str:
        ok, out = services.run_restore_drill()
        if not ok:
            raise RuntimeError((out or "Drill failed")[:500])
        return (out or "Restore drill passed")[:500]

    job_id = jobs.start_job("backup_drill", _run_drill)
    return _job_started_response(
        request,
        job_id=job_id,
        redirect_url="/settings?tab=backup",
        message="Restore drill started — refresh Backup in a minute.",
    )


@router.post("/settings/backup/restore")
def settings_backup_restore(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=backup&err=Form+expired", status_code=303)
    path = str(form.get("path") or "").strip()
    local_ok = path in {"/var/backups/stonepi/current", "/var/backups/stonepi/current/"}
    usb_ok = path.startswith("/mnt/stonepi-backup/RaspberryPi-Backup/")
    if not (local_ok or usb_ok):
        return RedirectResponse("/settings?tab=backup&err=Invalid+backup+path", status_code=303)

    def _run_restore() -> str:
        ok, out = services.run_usb_restore(path)
        if not ok:
            raise RuntimeError((out or "Restore failed")[:500])
        return (out or "Restore complete")[:500]

    job_id = jobs.start_job("backup_restore", _run_restore)
    return _job_started_response(
        request,
        job_id=job_id,
        redirect_url="/settings?tab=backup",
        message="Restore started — refresh Backup in a minute.",
    )


@router.post("/settings/backup/schedule")
def settings_backup_schedule(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=backup&err=Form+expired", status_code=303)
    enabled = str(form.get("enabled") or "").strip().lower() in {"1", "on", "true", "yes"}
    cadence = str(form.get("cadence") or "weekly").strip().lower()
    time_of_day = str(form.get("time") or "03:30").strip()
    weekday = str(form.get("weekday") or "Sun").strip()
    ok, out = services.set_local_backup_schedule(enabled, cadence, time_of_day, weekday)
    if ok:
        return RedirectResponse("/settings?tab=backup&msg=Local+backup+schedule+saved", status_code=303)
    return RedirectResponse(
        f"/settings?tab=backup&err={quote((out or 'Schedule failed')[:300], safe='')}",
        status_code=303,
    )


@router.post("/settings/backup/run-local")
def settings_backup_run_local(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=backup&err=Form+expired", status_code=303)
    ok, out = services.run_local_backup_now()
    if ok:
        return RedirectResponse(
            "/settings?tab=backup&msg=Local+backup+started.+Refresh+in+a+minute.",
            status_code=303,
        )
    return RedirectResponse(
        f"/settings?tab=backup&err={quote((out or 'Could not start backup')[:300], safe='')}",
        status_code=303,
    )


@router.post("/settings/network/acl/apply")
def settings_network_acl_apply(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=network&err=Form+expired", status_code=303)
    import subprocess
    from pathlib import Path

    helper = Path("/usr/local/sbin/stonepi-tailscale-acl")
    if not helper.exists():
        return RedirectResponse("/settings?tab=network&err=ACL+helper+missing", status_code=303)
    try:
        result = subprocess.run(
            ["sudo", "-n", str(helper), "apply"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            return RedirectResponse(
                f"/settings?tab=network&err={quote(out[:300] or 'ACL apply failed', safe='')}",
                status_code=303,
            )
        return RedirectResponse("/settings?tab=network&msg=Tailscale+ACL+applied", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            f"/settings?tab=network&err={quote(str(exc)[:300], safe='')}",
            status_code=303,
        )


@router.post("/settings/network/hostname")
def settings_hostname_save(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    from app import network as network_svc

    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=network&err=Form+expired", status_code=303)
    wanted = str(form.get("hostname") or "").strip()
    ok, detail = network_svc.apply_hostname(wanted)
    if not ok:
        return RedirectResponse(
            f"/settings?tab=network&panel=hostname&err={quote(detail, safe='')}",
            status_code=303,
        )
    msg = f"Hostname is {detail}. LAN address is http://{detail}.local — apps pick this up immediately."
    if not network_svc.hostname_helper_available():
        msg += " On this machine the OS hostname was not changed (dev / no helper)."
    return RedirectResponse(
        f"/settings?tab=network&panel=hostname&msg={quote(msg, safe='')}",
        status_code=303,
    )


@router.post("/settings/exposure")
@router.post("/settings/network/exposure")
def settings_exposure_save(request: Request, form: FormData = Depends(_form_body)):
    from stonepi_auth import set_exposure_mode

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=network&err=Form+expired", status_code=303)
    mode = str(form.get("exposure") or "lan").strip().lower()
    if mode not in {"lan", "public"}:
        return RedirectResponse(
            "/settings?tab=network&err=Choose+home+network+or+internet-facing",
            status_code=303,
        )
    try:
        set_exposure_mode(mode)
    except OSError:
        # Installer creates /var/lib/stonepi/exposure; older installs left it root-owned.
        return RedirectResponse(
            "/settings?tab=network&err="
            + quote("Couldn't save the exposure setting (permission denied). Re-run the installer to fix permissions.", safe=""),
            status_code=303,
        )
    if mode == "public":
        msg = "Internet-facing mode on. Apps pick this up immediately — no restart."
    else:
        msg = "Home network mode on. Reader APIs stay LAN-friendly."
    return RedirectResponse(f"/settings?tab=network&msg={quote(msg, safe='')}", status_code=303)


@router.get("/api/network/status")
def api_network_status(request: Request, fresh: int = 0):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    from app import network as network_svc

    # Default uses TTL cache; ?fresh=1 only while polling a pending connect.
    return JSONResponse(network_svc.network_snapshot(fresh=bool(fresh)))


@router.get("/api/network/tailscale/qr")
def api_tailscale_login_qr(request: Request):
    """QR code (inline SVG) for the pending Tailscale login link, to approve from a phone.

    Encodes the Pi's own current link, never one sent by the browser.
    """
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    from app import network as network_svc
    from app.notifications import _qr_svg

    ts = network_svc.network_snapshot(fresh=False).get("tailscale") or {}
    url = str(ts.get("auth_url") or "")
    svg = _qr_svg(url) if url.startswith("https://") and not ts.get("connected") else ""
    if not svg:
        return JSONResponse({"ok": False, "error": "No Tailscale login link right now."}, status_code=404)
    return JSONResponse({"ok": True, "url": url, "svg": svg})


@router.post("/settings/network/tailscale/wanted")
def settings_tailscale_wanted(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    from app import network as network_svc

    if not _require_csrf_any(request, form):
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": "Form expired"}, status_code=403)
        return RedirectResponse("/settings?tab=network&err=Form+expired", status_code=303)
    enabled = str(form.get("wanted") or form.get("enabled") or "").strip().lower() in {
        "on",
        "1",
        "true",
        "yes",
        "enabled",
    }
    try:
        network_svc.set_tailscale_wanted(enabled)
        snap = network_svc.network_snapshot()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or "Could not update remote access."
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": msg}, status_code=500)
        return RedirectResponse(f"/settings?tab=network&err={quote(msg, safe='')}", status_code=303)
    if _wants_json(request):
        return JSONResponse({"ok": True, **snap})
    msg = "Remote access enabled." if enabled else "Remote access disabled."
    return RedirectResponse(f"/settings?tab=network&msg={quote(msg, safe='')}", status_code=303)


@router.post("/settings/network/tailscale/connect")
def settings_tailscale_connect(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    from app import network as network_svc

    if not _require_csrf_any(request, form):
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": "Form expired"}, status_code=403)
        return RedirectResponse("/settings?tab=network&err=Form+expired", status_code=303)
    try:
        ts = network_svc.start_login()
        # Avoid a second long helper round-trip; internet probe alone is enough.
        snap = {
            "ok": True,
            "internet": network_svc.internet_status(),
            "tailscale": ts,
            "helper_available": network_svc.helper_available(),
            "appliance": network_svc.helper_available(),
        }
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or "Could not start Tailscale connect."
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": msg}, status_code=500)
        return RedirectResponse(f"/settings?tab=network&err={quote(msg, safe='')}", status_code=303)
    if _wants_json(request):
        return JSONResponse(snap)
    if ts.get("auth_url"):
        msg = "Open the Tailscale link to finish signing in."
    elif ts.get("connected"):
        msg = "Tailscale connected."
    else:
        msg = "Connect started — waiting for authentication."
    return RedirectResponse(f"/settings?tab=network&msg={quote(msg, safe='')}", status_code=303)


@router.post("/settings/network/tailscale/disconnect")
def settings_tailscale_disconnect(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    from app import network as network_svc

    if not _require_csrf_any(request, form):
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": "Form expired"}, status_code=403)
        return RedirectResponse("/settings?tab=network&err=Form+expired", status_code=303)
    try:
        network_svc.set_tailscale_wanted(False)
        network_svc.disconnect()
        snap = network_svc.network_snapshot()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or "Could not disconnect Tailscale."
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": msg}, status_code=500)
        return RedirectResponse(f"/settings?tab=network&err={quote(msg, safe='')}", status_code=303)
    if _wants_json(request):
        return JSONResponse({"ok": True, **snap})
    return RedirectResponse(
        f"/settings?tab=network&msg={quote('Tailscale disconnected.', safe='')}",
        status_code=303,
    )


@router.get("/settings/display", response_class=HTMLResponse)
def settings_display_redirect():
    return RedirectResponse(services.notify_page_url("displays"), status_code=303)


@router.post("/settings/trmnl")
@router.post("/settings/display")
def settings_display_save(request: Request):
    return RedirectResponse(services.notify_page_url("displays"), status_code=303)


@router.post("/settings/vault")
def settings_vault_save(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=vault&err=Form+expired.+Try+again.", status_code=303)
    from stonepi_vault import get_vault

    action = str(form.get("action") or "set").strip().lower()
    key = str(form.get("key") or "").strip()
    custom = str(form.get("custom_key") or "").strip()
    if key in {"", "__custom__"}:
        key = custom
    value = str(form.get("value") or "")
    if not key:
        return RedirectResponse(
            "/settings?tab=vault&err=Choose+a+setting+or+enter+a+custom+key.",
            status_code=303,
        )
    if any(ch.isspace() for ch in key) or "/" in key:
        return RedirectResponse(
            "/settings?tab=vault&err=Keys+cannot+contain+spaces+or+slashes.",
            status_code=303,
        )
    label = _vault_label_map().get(key, key)
    if key == RECOVER_PASSWORD_KEY:
        return _save_recover_password(action, value, label)
    vault = get_vault()
    if action == "delete":
        try:
            removed = vault.delete(key)
        except OSError as exc:
            return RedirectResponse(
                f"/settings?tab=vault&err={quote(f'Could not remove {label}: {exc}', safe='')}",
                status_code=303,
            )
        if not removed:
            return RedirectResponse(
                f"/settings?tab=vault&err={quote(f'{label} was not in the vault.', safe='')}",
                status_code=303,
            )
        return RedirectResponse(
            f"/settings?tab=vault&msg={quote(f'{label} removed from the vault.', safe='')}",
            status_code=303,
        )
    if not value.strip():
        return RedirectResponse("/settings?tab=vault&err=Enter+a+value+to+save.", status_code=303)
    try:
        vault.set(key, value)
    except OSError as exc:
        return RedirectResponse(
            f"/settings?tab=vault&err={quote(f'Could not save {label}: {exc}', safe='')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/settings?tab=vault&msg={quote(f'{label} saved to the vault.', safe='')}",
        status_code=303,
    )


def _save_recover_password(action: str, value: str, label: str) -> RedirectResponse:
    """Set or clear recover.passwd via the root helper; the value never touches the Vault."""
    if action == "delete":
        code, out = services._helper_run(["recover-passwd-clear"], timeout=15)
        verb = "cleared"
    else:
        if not value.strip() or any(c in value.strip() for c in "\r\n"):
            return RedirectResponse("/settings?tab=vault&err=Enter+a+single-line+value+to+save.", status_code=303)
        if len(value.strip()) < RECOVER_PASSWORD_MIN_LENGTH:
            # A root console on the LAN: the per-IP lockout slows guessing, length stops it.
            msg = f"{label} must be at least {RECOVER_PASSWORD_MIN_LENGTH} characters."
            return RedirectResponse(f"/settings?tab=vault&err={quote(msg, safe='')}", status_code=303)
        code, out = services._helper_run(["recover-passwd-set"], timeout=15, stdin=value.strip() + "\n")
        verb = "saved"
    if code != 0:
        detail = (out or "backup helper failed").strip()[:200]
        return RedirectResponse(
            f"/settings?tab=vault&err={quote(f'Could not update {label}: {detail}', safe='')}",
            status_code=303,
        )
    # Older builds also kept a Vault copy; drop it so app users can't read it.
    try:
        from stonepi_vault import get_vault

        get_vault().delete(RECOVER_PASSWORD_KEY)
    except Exception:
        pass
    return RedirectResponse(
        f"/settings?tab=vault&msg={quote(f'{label} {verb} (root-only file, not the Vault).', safe='')}",
        status_code=303,
    )


@router.post("/settings/automations")
def settings_automations_save(request: Request, form: FormData = Depends(_form_body)):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=automations&err=Form+expired", status_code=303)
    import stonepi_automations as automations

    enabled = {}
    for rule in automations.RULE_CATALOG:
        enabled[rule["id"]] = form.get(f"rule_{rule['id']}") == "1"
    automations.save_state({"enabled": enabled})
    return RedirectResponse("/settings?tab=automations&msg=Automations+saved", status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request, form: FormData = Depends(_form_body)):
    if request.method == "POST":
        if not _require_csrf(request, form):
            return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
    return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
