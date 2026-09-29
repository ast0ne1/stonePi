from __future__ import annotations

import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__
from app.collect import collect_overview, widget_payloads_for_display
from app.config import ROOT_DIR, env
from stonepi_auth.brand import fonts_rev
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, notifications_url
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()

APP_ID = "notify"
APP_NAME = "Notify"
APP_BLURB = (
    "Household Displays and phone alerts for StonePi — TRMNL screen layouts, phone-alert "
    "setup and approvals, and delivery history."
)

AUDIENCE_LABELS = {"personal": "Personal", "household": "Household", "admin": "Admin"}


def _approval_view(prefs: dict) -> tuple[list[dict], list[dict], int]:
    """Catalog events grouped by app with the admin's decision, plus unknown
    events that arrived without one ("needs review")."""
    import stonepi_notify
    from stonepi_contracts import EVENT_CATALOG, app_label

    decided = prefs.get("events") or {}
    groups: dict[str, dict] = {}
    count = 0
    for ev in EVENT_CATALOG:
        approval = stonepi_notify.normalize_approval(decided.get(ev.id, False))
        review = ev.id not in decided
        count += int(review)
        group = groups.setdefault(ev.app, {"id": ev.app, "label": app_label(ev.app), "events": []})
        group["events"].append(
            {
                "id": ev.id,
                "label": ev.label,
                "blurb": ev.blurb,
                "audience": ev.audience,
                "audience_label": AUDIENCE_LABELS.get(ev.audience, ev.audience),
                "needs_review": review,
                **approval,
            }
        )
    known = {ev.id for ev in EVENT_CATALOG}
    other = []
    for eid, info in sorted(stonepi_notify.load_needs_review().items()):
        if eid in known or eid in decided:
            continue
        other.append(
            {
                "id": eid,
                "label": str(info.get("title") or eid),
                "source": app_label(str(info.get("source") or "")),
                "count": int(info.get("count") or 0),
                "last_seen": str(info.get("last_seen") or "")[:16].replace("T", " "),
            }
        )
    return list(groups.values()), other, count + len(other)


def _display_count() -> int:
    try:
        import stonepi_display

        return len(stonepi_display.load_displays())
    except Exception:
        return 0


def _session_secret() -> str:
    from app.people import session_secret

    return session_secret()


def _prefix() -> str:
    return (env.stonepi_prefix or ("/notify" if env.routing == "path" else "")).rstrip("/")


def _settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    return PlatformSettings(
        enabled=bool(_session_secret()),
        session_secret=_session_secret(),
        app_id=APP_ID,
        prefix=_prefix(),
        auth_url=browser_auth_url(env.auth_url, routing=env.routing),
        public_origin=env.public_origin,
        hostname=env.hostname,
    )


def _user(request: Request):
    return decode_session(request.cookies.get(COOKIE_NAME), _session_secret())


def _login_next(path: str) -> str:
    pfx = _prefix()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{pfx}{path}" if pfx else path


def _alerts_bell_state(user, home: str) -> dict:
    """Notify's own bell, from local data (no HTTP call to itself)."""
    import stonepi_notify

    from app.people import load_people
    from stonepi_auth.alerts import phone_alerts_allowed

    if user is None or not _session_secret():
        return {"show": False, "dot": False, "url": "", "active": False}
    people = load_people()
    person = (people or {}).get(user.user_id)
    allowed = bool(person.get("phone_alerts")) if person is not None else phone_alerts_allowed(user)
    sub = stonepi_notify.get_subscription(user.user_id) or {}
    ntfy_on = bool((stonepi_notify.load_destinations().get("ntfy") or {}).get("enabled"))
    show = allowed and ntfy_on  # hidden until the household has phone alerts on
    needs_setup = not sub.get("topic")  # never set up; turning alerts off keeps the topic
    return {"show": show, "dot": show and needs_setup, "url": notifications_url(home), "active": False}


def _page_ctx(request: Request, *, active: str, user, csrf: str, extra: dict | None = None) -> dict:
    from stonepi_auth.session import factory_admin_warning

    home = portal_home_url(request, env.public_origin).rstrip("/")
    ctx = {
        "user": user,
        "active": active,
        "csrf_token": csrf,
        "hostname": env.hostname,
        "public_origin": home,
        "stonepi_home_url": home,
        "using_factory_admin": factory_admin_warning(user),
        "app_prefix": _prefix(),
        "stonepi_prefix": _prefix(),
        "app_name": APP_NAME,
        "app_version": __version__,
        "asset_rev": __asset_rev__,
        "app_github_user": __github_user__,
        "app_github": __github__,
        "error": request.query_params.get("err"),
        "message": request.query_params.get("msg"),
        "platform_managed": bool(_session_secret()),
        "display_count": _display_count(),
        "alerts_bell_state": _alerts_bell_state(user, home),
    }
    if extra:
        ctx.update(extra)
    return ctx


def _deny_page(request: Request, template: str, *, active: str, user):
    """Non-admins: Notify is admin-only; point them at their own alerts."""
    home = portal_home_url(request, env.public_origin).rstrip("/")
    return templates.TemplateResponse(
        request,
        "denied.html",
        {
            "user": user,
            "error": "Notify is for administrators. Manage your own alerts from the bell on the Dashboard.",
            "active": active,
            "app_name": APP_NAME,
            "app_version": __version__,
            "asset_rev": __asset_rev__,
            "public_origin": home,
            "stonepi_home_url": home,
            "app_prefix": _prefix(),
            "stonepi_prefix": _prefix(),
            "csrf_token": "",
            "display_count": _display_count(),
            "message": None,
        },
        status_code=403,
    )


def _guard(request: Request, *, path: str, active: str, template: str):
    user = _user(request)
    if _session_secret() and user is None:
        return None, RedirectResponse(login_url(_settings(), _login_next(path)), status_code=303)
    # Notify is admin-only; people manage their own alerts on the Dashboard
    # Notifications page (via /api/me/*).
    if user and not user.is_admin:
        return None, _deny_page(request, template, active=active, user=user)
    return user, None


def _q(msg: str = "", err: str = "", tab: str = "") -> str:
    parts = []
    if tab:
        parts.append(f"tab={quote(tab)}")
    if msg:
        parts.append(f"msg={quote(msg)}")
    if err:
        parts.append(f"err={quote(err)}")
    return ("?" + "&".join(parts)) if parts else ""


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": APP_ID}


@router.get("/api/display")
def api_display():
    import stonepi_display

    displays = stonepi_display.load_displays()
    return {
        "ok": True,
        "displays": len(displays),
        "detail": f"{len(displays)} display(s)",
    }


@router.post("/api/events")
async def api_events(request: Request):
    """Loopback event ingest — used by stonepi_contracts.emit_event."""
    import stonepi_notify

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "invalid json"}, status_code=400)
    result = stonepi_notify.ingest_event(body if isinstance(body, dict) else None)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@router.post("/api/push-trmnl")
def api_push_trmnl():
    """Loopback TRMNL push for Dashboard automations."""
    from app.boot import _push_trmnl

    return _push_trmnl()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return RedirectResponse("/displays", status_code=303)


@router.get("/displays", response_class=HTMLResponse)
def displays_page(request: Request):
    import stonepi_display

    user, denied = _guard(request, path="/displays", active="displays", template="displays.html")
    if denied is not None:
        return denied
    csrf = csrf_from_request(request.cookies)
    displays = stonepi_display.load_displays()
    webhook_ids = {d["id"] for d in displays if stonepi_display.get_webhook(d["id"])}
    response = templates.TemplateResponse(
        request,
        "displays.html",
        _page_ctx(
            request,
            active="displays",
            user=user,
            csrf=csrf,
            extra={"displays": displays, "webhook_ids": webhook_ids},
        ),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


def _builder_context(display: dict) -> dict:
    """Everything the Display builder needs, JSON-ready."""
    import stonepi_display
    from stonepi_contracts import WIDGET_CATALOG, WIDGET_SIZES, app_label

    catalog = [
        {
            "id": w.id,
            "code": w.code,
            "app": "StonePi" if w.app == "stonepi" else app_label(w.app),
            "label": w.label,
            "blurb": w.blurb,
            "sizes": list(stonepi_display.allowed_sizes(w.id)),
        }
        for w in WIDGET_CATALOG
        if w.code in stonepi_display.WIDGET_SNIPPETS
    ]
    screens = {
        device: {**stonepi_display.device_grid(device), "width": screen["width"], "height": screen["height"]}
        for device, screen in stonepi_display.PREVIEW_SCREENS.items()
    }
    return {
        "display": display,
        "catalog": catalog,
        "sizes": {k: list(v) for k, v in WIDGET_SIZES.items()},
        "screens": screens,
    }


DEVICE_CHOICES = (
    {"id": "og", "label": "TRMNL OG (7.5\", 800×480)"},
    {"id": "v2", "label": "TRMNL X (1040×780)"},
)


@router.get("/displays/{display_id}", response_class=HTMLResponse)
def display_detail(request: Request, display_id: str):
    import stonepi_display

    user, denied = _guard(
        request, path=f"/displays/{display_id}", active="displays", template="display_detail.html"
    )
    if denied is not None:
        return denied
    display = stonepi_display.get_display(display_id)
    if not display:
        return RedirectResponse("/displays?err=Display+not+found", status_code=303)
    csrf = csrf_from_request(request.cookies)
    builder = _builder_context(display)
    response = templates.TemplateResponse(
        request,
        "display_detail.html",
        _page_ctx(
            request,
            active="displays",
            user=user,
            csrf=csrf,
            extra={
                "display": display,
                "builder": builder,
                "available_widgets": builder["catalog"],
                "devices": DEVICE_CHOICES,
                "webhook_set": bool(stonepi_display.get_webhook(display_id)),
                "universal_template": stonepi_display.UNIVERSAL_TEMPLATE,
                "date_formats": [(k, label) for k, (label, _) in stonepi_display.DATE_FORMATS.items()],
            },
        ),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


def _api_csrf_ok(request: Request) -> bool:
    return csrf_ok_request(request.cookies, header_token=request.headers.get("x-stonepi-csrf") or "")


@router.post("/api/displays/{display_id}/preview")
async def api_display_preview(request: Request, display_id: str):
    """Render a (possibly unsaved) layout exactly as the TRMNL plugin would."""
    import stonepi_display
    from stonepi_display.displays import normalize_display

    user = _user(request)
    if _session_secret() and (user is None or not user.is_admin):
        return JSONResponse({"ok": False, "message": "Admins only."}, status_code=403)
    if not _api_csrf_ok(request):
        return JSONResponse({"ok": False, "message": "Form expired — reload the page."}, status_code=403)
    existing = stonepi_display.get_display(display_id)
    if not existing:
        return JSONResponse({"ok": False, "message": "Display not found."}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    draft = normalize_display(
        {
            **existing,
            "device": body.get("device") or existing.get("device"),
            "title_bar": body.get("title_bar") or existing.get("title_bar"),
            "date_format": body.get("date_format") or existing.get("date_format"),
            "widgets": body.get("widgets") if isinstance(body.get("widgets"), list) else existing["widgets"],
            "trmnl": {**existing.get("trmnl", {}), "template": "universal"},
        }
    )
    if body.get("data") == "live":
        from app.boot import cached_overview

        variables = cached_overview()
    else:
        variables = dict(stonepi_display.SAMPLE_VARIABLES)
    payload = stonepi_display.display_payload(draft, variables)
    try:
        html = stonepi_display.preview_document(payload, device=draft["device"])
    except Exception as exc:  # noqa: BLE001 - template errors should show in the builder
        return JSONResponse({"ok": False, "message": f"Preview failed: {exc}"[:240]}, status_code=500)
    size = len(json.dumps({"merge_variables": payload}, separators=(",", ":"), default=str).encode("utf-8"))
    return {
        "ok": True,
        "html": html,
        "bytes": size,
        "limit": stonepi_display.PAYLOAD_LIMIT_BYTES,
        "widgets": draft["widgets"],
        "unplaced": [w["id"] for w in draft["widgets"] if not w.get("x")],
    }


@router.post("/displays/create")
async def displays_create(request: Request):
    import stonepi_display

    user, denied = _guard(request, path="/displays", active="displays", template="displays.html")
    if denied is not None:
        return denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse("/displays?err=Form+expired", status_code=303)
    name = str(form.get("name") or "").strip() or "New display"
    display_id = str(uuid.uuid4())[:8]
    stonepi_display.upsert_display(
        {"id": display_id, "name": name, "enabled": True, "builtin": False, "device": "og", "widgets": []}
    )
    return RedirectResponse(f"/displays/{display_id}?msg=Display+created", status_code=303)


@router.post("/displays/{display_id}/save")
async def displays_save(request: Request, display_id: str):
    import stonepi_display

    user, denied = _guard(
        request, path=f"/displays/{display_id}", active="displays", template="display_detail.html"
    )
    if denied is not None:
        return denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse(f"/displays/{display_id}?err=Form+expired", status_code=303)
    existing = stonepi_display.get_display(display_id)
    if not existing:
        return RedirectResponse("/displays?err=Display+not+found", status_code=303)
    name = str(form.get("name") or existing["name"]).strip() or existing["name"]
    enabled = str(form.get("enabled") or "") in {"1", "on", "true"}
    # widgets from hidden JSON
    widgets = existing.get("widgets") or []
    raw = str(form.get("widgets_json") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                widgets = parsed
        except json.JSONDecodeError:
            pass
    add_widget = str(form.get("add_widget") or "").strip()
    if add_widget:
        widgets = list(widgets) + [
            {"id": str(uuid.uuid4())[:8], "widget_id": add_widget, "size": "medium", "config": {}}
        ]
    remove_id = str(form.get("remove_widget") or "").strip()
    if remove_id:
        widgets = [w for w in widgets if str(w.get("id")) != remove_id]
    device = str(form.get("device") or existing.get("device") or "og")
    title_bar = str(form.get("title_bar") or existing.get("title_bar") or "bottom")
    date_format = str(form.get("date_format") or existing.get("date_format") or "")
    # The details form carries the Enabled checkbox; the layout form does not.
    if existing.get("builtin"):
        enabled = True
    elif "details" not in form:
        enabled = existing["enabled"]
    stonepi_display.upsert_display(
        {
            **existing,
            "name": name,
            "enabled": enabled,
            "device": device,
            "title_bar": title_bar,
            "date_format": date_format,
            "widgets": widgets,
        }
    )
    return RedirectResponse(f"/displays/{display_id}?msg=Saved", status_code=303)


@router.post("/displays/{display_id}/trmnl")
async def displays_trmnl(request: Request, display_id: str):
    """Per-Display TRMNL settings: push on/off, webhook (vault), interval, template mode."""
    import stonepi_display

    user, denied = _guard(
        request, path=f"/displays/{display_id}", active="displays", template="display_detail.html"
    )
    if denied is not None:
        return denied
    form = await request.form()
    back = f"/displays/{display_id}"
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse(f"{back}?err=Form+expired", status_code=303)
    display = stonepi_display.get_display(display_id)
    if not display:
        return RedirectResponse("/displays?err=Display+not+found", status_code=303)

    webhook = str(form.get("webhook") or "").strip()
    try:
        if webhook:
            stonepi_display.set_webhook(display_id, webhook)
        elif str(form.get("clear_webhook") or "") in {"1", "on", "true"}:
            stonepi_display.set_webhook(display_id, "")
    except ValueError as exc:
        return RedirectResponse(f"{back}?err={quote(str(exc)[:160])}#trmnl", status_code=303)
    except Exception:
        return RedirectResponse(f"{back}?err=Could+not+save+the+webhook+to+the+vault#trmnl", status_code=303)

    display["trmnl"] = {
        **display.get("trmnl", {}),
        "enabled": str(form.get("enabled") or "") in {"1", "on", "true"},
        "interval_minutes": str(form.get("interval_minutes") or display["trmnl"]["interval_minutes"]),
        "template": "legacy" if str(form.get("template") or "") == "legacy" else "universal",
    }
    stonepi_display.upsert_display(display)

    if str(form.get("action") or "") == "push":
        from app.boot import push_one_display

        result = push_one_display(display_id)
        if result.get("ok"):
            return RedirectResponse(f"{back}?msg=Pushed+to+TRMNL#trmnl", status_code=303)
        err = quote(str(result.get("message") or "Push failed")[:160])
        return RedirectResponse(f"{back}?err={err}#trmnl", status_code=303)
    return RedirectResponse(f"{back}?msg=TRMNL+settings+saved#trmnl", status_code=303)


@router.post("/displays/{display_id}/delete")
async def displays_delete(request: Request, display_id: str):
    import stonepi_display

    user, denied = _guard(request, path="/displays", active="displays", template="displays.html")
    if denied is not None:
        return denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse("/displays?err=Form+expired", status_code=303)
    if not stonepi_display.delete_display(display_id):
        return RedirectResponse("/displays?err=Cannot+delete+builtin+Dashboard", status_code=303)
    return RedirectResponse("/displays?msg=Display+deleted", status_code=303)


SETTINGS_REDIRECTS = {"history": "/history", "about": "/about"}


@router.get("/settings")
def settings_redirect(request: Request):
    """The old Settings page split into Phone alerts, History and About."""
    tab = (request.query_params.get("tab") or "").strip().lower()
    if tab == "prefs":
        return RedirectResponse("/alerts#choose", status_code=303)
    return RedirectResponse(SETTINGS_REDIRECTS.get(tab, "/alerts"), status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
