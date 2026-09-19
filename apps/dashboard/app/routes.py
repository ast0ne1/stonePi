from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import ROOT_DIR, env, resolve_cockpit_url
from app import services
from stonepi_auth import APP_CATALOG, login_url, logout_url
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.session import CSRF_COOKIE

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
router = APIRouter()


def _settings() -> PlatformSettings:
    auth_url = env.auth_url
    # Path routing on the Pi: /auth regardless of .local / .home / IP hostname.
    if env.routing == "path" and "127.0.0.1" not in (env.public_origin or ""):
        auth_url = "/auth"
    return PlatformSettings(
        enabled=True,
        session_secret=services.session_secret(),
        app_id="dashboard",
        prefix="",
        auth_url=auth_url,
        public_origin=env.public_origin,
        hostname=env.hostname,
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
            request, "forbidden.html", {"user": user, "hostname": env.hostname}, status_code=403
        )
    if admin and not user.is_admin:
        return None, templates.TemplateResponse(
            request, "forbidden.html", {"user": user, "hostname": env.hostname}, status_code=403
        )
    return user, None


def _request_host(request: Request) -> str:
    return (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    )


def _ctx(request: Request, user, extra: dict | None = None):
    payload = {
        "request": request,
        "user": user,
        "hostname": env.hostname,
        "cockpit_url": resolve_cockpit_url(_request_host(request)),
        "nav": request.url.path,
        "active": "overview",
        "error": None,
        "message": None,
        "csrf_token": csrf_from_request(request.cookies),
    }
    if extra:
        payload.update(extra)
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


@router.get("/healthz")
@router.get("/health")
def healthz():
    return {"ok": True, "service": "dashboard"}


def _factory_admin(cookies: dict[str, str], user) -> bool:
    if not user or not user.is_admin:
        return False
    try:
        me = services.auth_request("GET", "/api/me", cookies)
    except Exception:
        return False
    return bool(me.get("using_factory_admin"))


def _backup_summary(backup: dict) -> dict:
    status = str(backup.get("status") or "").strip().lower()
    stamp = str(backup.get("timestamp") or backup.get("finished_at") or backup.get("time") or "").strip()
    if stamp:
        label = stamp[:19].replace("T", " ")
        return {"label": label, "detail": "Last recorded backup", "ok": True}
    if status in {"none", "", "unknown"}:
        return {"label": "Never", "detail": "No backup recorded yet", "ok": False}
    return {"label": status or "Unknown", "detail": backup.get("message") or "Backup status", "ok": False}


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    tiles = services.launcher_tiles(user, dict(request.cookies))
    return _html(
        request,
        "home.html",
        user,
        {
            "active": "home",
            "tiles": tiles,
            "using_factory_admin": _factory_admin(dict(request.cookies), user),
            "message": request.query_params.get("msg"),
            "error": request.query_params.get("err"),
        },
    )


@router.post("/launcher-order")
async def launcher_order(request: Request):
    user, redirected = _user_or_login(request, require_dashboard=False)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return RedirectResponse("/?err=" + quote("Form expired"), status_code=303)
    raw = str(form.get("order") or "").strip()
    if raw.startswith("["):
        try:
            order = json.loads(raw)
        except json.JSONDecodeError:
            return RedirectResponse("/?err=" + quote("Invalid order"), status_code=303)
    else:
        order = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(order, list) or not order:
        return RedirectResponse("/?err=" + quote("Pick at least one app"), status_code=303)
    try:
        services.save_launcher_order(dict(request.cookies), [str(item) for item in order])
    except Exception as exc:
        return RedirectResponse("/?err=" + quote(str(exc)), status_code=303)
    wants_json = "application/json" in (request.headers.get("accept") or "")
    if wants_json or request.headers.get("x-requested-with") == "fetch":
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True, "order": order})
    return RedirectResponse("/?msg=" + quote("App order saved"), status_code=303)


@router.get("/overview", response_class=HTMLResponse)
def overview(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    cards = services.application_cards(dict(request.cookies))
    backup = services.backup_info()
    users = []
    error = None
    users_ok = False
    try:
        users = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
        users_ok = True
    except Exception as exc:
        error = str(exc)
    enabled_count = sum(1 for card in cards if card.get("enabled", True) is not False)
    return _html(
        request,
        "overview.html",
        user,
        {
            "active": "overview",
            "cards": cards,
            "backup": backup,
            "backup_summary": _backup_summary(backup),
            "user_count": len(users) if users_ok else None,
            "users_unavailable": not users_ok,
            "enabled_count": enabled_count,
            "error": error,
            "platform_version": services_platform_version(),
            "using_factory_admin": _factory_admin(dict(request.cookies), user),
        },
    )


def services_platform_version() -> str:
    from app import update_service

    return update_service.platform_version()


@router.get("/applications", response_class=HTMLResponse)
def applications(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    return _html(
        request,
        "applications.html",
        user,
        {
            "active": "applications",
            "cards": services.application_cards(dict(request.cookies)),
            "manage_apps": [app for app in services.catalog_apps(include_auth=False, cookies=dict(request.cookies))],
            "message": request.query_params.get("msg") or None,
        },
    )


@router.get("/applications/{app_id}", response_class=HTMLResponse)
def application_detail(app_id: str, request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    cards = {item["id"]: item for item in services.application_cards(dict(request.cookies))}
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


@router.post("/applications/{app_id}/{action}")
async def application_action(app_id: str, action: str, request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
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
    error = None
    people = []
    apps = services.catalog_apps(include_auth=False, cookies=dict(request.cookies))
    try:
        people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
    except Exception as exc:
        error = str(exc)
    return _html(
        request,
        "users.html",
        user,
        {
            "active": "users",
            "people": people,
            "apps": apps,
            "error": error,
            "message": request.query_params.get("msg") or None,
        },
    )


@router.post("/users")
async def users_create(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    apps = services.catalog_apps(include_auth=False, cookies=dict(request.cookies))
    if not _require_csrf(request, form):
        people = []
        try:
            people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
        except Exception:
            pass
        return _html(
            request,
            "users.html",
            user,
            {"active": "users", "people": people, "apps": apps, "error": "That form expired. Refresh and try again."},
            status_code=400,
        )
    try:
        services.auth_request(
            "POST",
            "/api/users",
            dict(request.cookies),
            {
                "username": str(form.get("username") or ""),
                "password": str(form.get("password") or ""),
                "display_name": str(form.get("display_name") or ""),
                "is_admin": form.get("is_admin") == "1",
                "apps": [str(value) for value in form.getlist("apps")],
                "permissions": services.parse_permissions_form(form, apps),
            },
        )
    except Exception as exc:
        people = []
        try:
            people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
        except Exception:
            pass
        return _html(
            request,
            "users.html",
            user,
            {"active": "users", "people": people, "apps": apps, "error": str(exc)},
            status_code=400,
        )
    return RedirectResponse("/users?msg=Saved", status_code=303)


@router.post("/users/{user_id}")
async def users_update(user_id: str, request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    apps = services.catalog_apps(include_auth=False, cookies=dict(request.cookies))
    if not _require_csrf(request, form):
        people = []
        try:
            people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
        except Exception:
            pass
        return _html(
            request,
            "users.html",
            user,
            {"active": "users", "people": people, "apps": apps, "error": "That form expired. Refresh and try again."},
            status_code=400,
        )
    action = str(form.get("action") or "save")
    try:
        if action == "delete":
            services.auth_request("DELETE", f"/api/users/{user_id}", dict(request.cookies))
        else:
            payload = {
                "display_name": str(form.get("display_name") or ""),
                "enabled": form.get("enabled") == "1",
                "is_admin": form.get("is_admin") == "1",
                "apps": [str(value) for value in form.getlist("apps")],
                "permissions": services.parse_permissions_form(form, apps),
            }
            password = str(form.get("password") or "")
            if password:
                payload["password"] = password
            services.auth_request("PATCH", f"/api/users/{user_id}", dict(request.cookies), payload)
    except Exception as exc:
        people = []
        try:
            people = services.auth_request("GET", "/api/users", dict(request.cookies)).get("users", [])
        except Exception:
            pass
        return _html(
            request,
            "users.html",
            user,
            {"active": "users", "people": people, "apps": apps, "error": str(exc)},
            status_code=400,
        )
    return RedirectResponse("/users?msg=Saved", status_code=303)


@router.post("/applications/availability")
async def applications_availability(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return _html(
            request,
            "applications.html",
            user,
            {
                "active": "applications",
                "cards": services.application_cards(dict(request.cookies)),
                "manage_apps": services.catalog_apps(include_auth=False, cookies=dict(request.cookies)),
                "error": "That form expired. Refresh and try again.",
            },
            status_code=400,
        )
    enabled = {str(value) for value in form.getlist("enabled_apps")}
    enabled.add("dashboard")
    all_ids = {app["id"] for app in APP_CATALOG}
    disabled = sorted(all_ids - enabled)
    try:
        services.auth_request("PATCH", "/api/apps", dict(request.cookies), {"disabled": disabled})
    except Exception as exc:
        return _html(
            request,
            "applications.html",
            user,
            {
                "active": "applications",
                "cards": services.application_cards(dict(request.cookies)),
                "manage_apps": services.catalog_apps(include_auth=False, cookies=dict(request.cookies)),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse("/applications?msg=Availability+saved", status_code=303)


@router.get("/backups", response_class=HTMLResponse)
def backups(request: Request):
    return RedirectResponse("/settings?tab=backup", status_code=303)


@router.get("/updates", response_class=HTMLResponse)
def updates_page(request: Request):
    return RedirectResponse("/settings?tab=update", status_code=303)


@router.post("/updates/repo")
@router.post("/settings/update/repo")
async def updates_repo(request: Request):
    from app import update_service

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=update&err=Form+expired", status_code=303)
    raw = str(form.get("github_repo") or "")
    repo = update_service.set_github_repo(raw)
    if raw.strip() and not repo:
        return RedirectResponse("/settings?tab=update&err=Use+owner/repo+for+the+GitHub+repository.", status_code=303)
    return RedirectResponse("/settings?tab=update&msg=Repository+saved", status_code=303)


@router.post("/updates/{app_id}/check")
@router.post("/settings/update/{app_id}/check")
async def updates_check(app_id: str, request: Request):
    from app import update_service

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=update&err=Form+expired", status_code=303)
    known = {item[0] for item in update_service.APP_TARGETS}
    if app_id not in known:
        raise HTTPException(status_code=404, detail="Unknown target")
    result = update_service.check_latest(app_id)
    msg = result.get("message") or "Checked GitHub."
    key = "msg" if result.get("ok", True) else "err"
    return RedirectResponse(f"/settings?tab=update&{key}={quote(msg, safe='')}", status_code=303)


@router.post("/updates/{app_id}/install")
@router.post("/settings/update/{app_id}/install")
async def updates_install(app_id: str, request: Request):
    from app import update_service

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=update&err=Form+expired", status_code=303)
    known = {item[0] for item in update_service.APP_TARGETS}
    if app_id not in known:
        raise HTTPException(status_code=404, detail="Unknown target")
    try:
        result = update_service.install_latest(app_id)
        msg = result.get("message") or "Installed."
        key = "msg" if result.get("ok", True) else "err"
        return RedirectResponse(f"/settings?tab=update&{key}={quote(msg, safe='')}", status_code=303)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or "Install failed."
        return RedirectResponse(f"/settings?tab=update&err={quote(msg, safe='')}", status_code=303)


SETTINGS_TABS = [
    ("general", "General"),
    ("display", "Display"),
    ("watch", "Watch"),
    ("vault", "Vault"),
    ("automations", "Automations"),
    ("update", "Updates"),
    ("backup", "Backup"),
    ("about", "About"),
]
SETTINGS_LEDES = {
    "general": "Appearance for this browser, plus whether StonePi is on the home network or internet-facing.",
    "display": "TRMNL layout and webhook push of household display data.",
    "watch": "Status of every StonePi app plus disk and backup — healthy, attention, or critical.",
    "vault": "Encrypted secrets and config for StonePi apps and platform services.",
    "automations": "Thin when→then jobs: USB backup, Display push on Watch or backup.",
    "update": "Check GitHub Releases for per-app packages and the platform pack.",
    "backup": "Full SD-card recovery is started from Cockpit so the backup can see the USB disk.",
    "about": "What StonePi is, who wrote it, and the version running here.",
}

# Friendly catalog for Settings → Vault (dropdown). Values are env/Vault key names.
VAULT_KEY_CATALOG = [
    {
        "group": "Platform",
        "items": [
            {"id": "STONEPI_SESSION_SECRET", "label": "Session secret", "blurb": "Shared sign-in cookie secret across apps."},
            {"id": "DISPLAY_WEBHOOK_URL", "label": "Display webhook", "blurb": "TRMNL Private Plugin webhook URL."},
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
            {"id": "NEWSCAST_NTFY_TOKEN", "label": "ntfy token", "blurb": "Push notifications via ntfy."},
            {"id": "BRIGHTDATA_API_KEY", "label": "Bright Data API key", "blurb": "Optional fetch proxy for stubborn sites."},
        ],
    },
    {
        "group": "Calendars",
        "items": [
            {"id": "GOOGLE_CLIENT_ID", "label": "Google client ID", "blurb": "Google Calendar OAuth client id."},
            {"id": "GOOGLE_CLIENT_SECRET", "label": "Google client secret", "blurb": "Google Calendar OAuth client secret."},
        ],
    },
]


def _vault_label_map() -> dict[str, str]:
    labels: dict[str, str] = {}
    for group in VAULT_KEY_CATALOG:
        for item in group["items"]:
            labels[item["id"]] = item["label"]
    return labels


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str = "general"):
    from app import __author__, __github__, __version__, display, update_service

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    settings_tab = (tab or "general").strip().lower()
    if settings_tab in {"trmnl", "trmnl-integration"}:
        settings_tab = "display"
    if settings_tab in {"updates"}:
        settings_tab = "update"
    if settings_tab in {"backups"}:
        settings_tab = "backup"
    known = {key for key, _ in SETTINGS_TABS}
    if settings_tab not in known:
        settings_tab = "general"
    extra = {
        "active": "settings",
        "settings_tab": settings_tab,
        "settings_tabs": SETTINGS_TABS,
        "settings_lede": SETTINGS_LEDES[settings_tab],
        "message": request.query_params.get("msg") or None,
        "error": request.query_params.get("err") or None,
        "app_author": __author__,
        "app_github": __github__,
        "app_version": __version__,
    }
    if settings_tab == "general":
        from stonepi_auth import exposure_mode

        extra["exposure_mode"] = exposure_mode()
    elif settings_tab == "display":
        cfg = display.load_config()
        device = display.normalize_device(cfg.get("device"))
        design = display.normalize_design(cfg.get("design"))
        layout = display.normalize_layout(cfg.get("layout"), device=device, design=design)
        preview = display.collect_overview(dict(request.cookies))
        vault_webhook = ""
        try:
            from stonepi_vault import get_secret

            vault_webhook = (get_secret("DISPLAY_WEBHOOK_URL", env_name="STONEPI_DISPLAY_WEBHOOK", default="") or "").strip()
        except Exception:
            vault_webhook = ""
        snippets = display.snippets_for(device)
        design_info = display.design_profile(design)
        designer = {
            "layout": layout,
            "catalog": list(display.BLOCK_CATALOG),
            "snippets": snippets,
            "snippets_by_device": {
                item["id"]: display.snippets_for(item["id"]) for item in display.DEVICE_PROFILES
            },
            "defaults_by_device": {
                item["id"]: display.default_layout(item["id"], design) for item in display.DEVICE_PROFILES
            },
            "defaults_by_design": {
                item["id"]: display.default_layout(device, item["id"]) for item in display.DESIGN_PRESETS
            },
            "devices": list(display.DEVICE_PROFILES),
            "designs": list(display.DESIGN_PRESETS),
            "device": device,
            "design": design,
            "fixed_design": bool(design_info.get("fixed")),
            "title_bar": display.TITLE_BAR_MARKUP,
            "preview": preview,
            "markup": display.build_markup(layout, device=device, design=design),
            "markup_by_device_design": {
                device_item["id"]: {
                    design_item["id"]: display.build_markup(
                        display.default_layout(device_item["id"], design_item["id"]),
                        device=device_item["id"],
                        design=design_item["id"],
                    )
                    for design_item in display.DESIGN_PRESETS
                }
                for device_item in display.DEVICE_PROFILES
            },
        }
        extra.update(
            {
                "display": cfg,
                "layout": layout,
                "layout_json": json.dumps(layout, separators=(",", ":")),
                "preview": preview,
                "preview_json": json.dumps(preview, indent=2),
                "generated_markup": display.build_markup(layout, device=device, design=design),
                "designer": designer,
                "vault_has_webhook": bool(vault_webhook),
                "display_devices": list(display.DEVICE_PROFILES),
                "display_device": device,
                "display_device_profile": display.device_profile(device),
                "display_designs": list(display.DESIGN_PRESETS),
                "display_design": design,
                "display_design_profile": design_info,
            }
        )
    elif settings_tab == "watch":
        from stonepi_auth import APP_CATALOG

        watch = display.watch_snapshot(dict(request.cookies))
        colors = {str(item["id"]): str(item.get("color") or "") for item in APP_CATALOG}
        for app in watch.get("apps") or []:
            app["color"] = colors.get(str(app.get("id") or ""), "")
        extra["watch"] = watch
    elif settings_tab == "vault":
        from stonepi_vault import get_vault

        extra["vault_catalog"] = VAULT_KEY_CATALOG
        labels = _vault_label_map()
        extra["vault_known_ids"] = sorted(labels.keys())
        try:
            vault = get_vault()
            stored = vault.list_keys()
            extra["vault_keys"] = [
                {"id": key, "label": labels.get(key, key), "known": key in labels} for key in stored
            ]
        except Exception as exc:
            extra["vault_keys"] = []
            extra["error"] = extra.get("error") or f"Vault unavailable: {exc}"
    elif settings_tab == "automations":
        import stonepi_automations as automations

        extra["automation_rules"] = automations.RULE_CATALOG
        extra["automation_state"] = automations.load_state()
    elif settings_tab == "update":
        extra.update(
            {
                "github_repo": update_service.github_repo(),
                "cards": update_service.version_cards(),
            }
        )
    elif settings_tab == "backup":
        extra["backup"] = services.backup_info()
    return _html(request, "settings.html", user, extra)


@router.post("/settings/exposure")
async def settings_exposure_save(request: Request):
    from stonepi_auth import set_exposure_mode

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=general&err=Form+expired", status_code=303)
    mode = str(form.get("exposure") or "lan").strip().lower()
    if mode not in {"lan", "public"}:
        return RedirectResponse("/settings?tab=general&err=Choose+home+network+or+internet-facing", status_code=303)
    set_exposure_mode(mode)
    if mode == "public":
        msg = "Internet-facing mode on. Apps pick this up immediately — no restart."
    else:
        msg = "Home network mode on. Reader APIs stay LAN-friendly."
    return RedirectResponse(f"/settings?tab=general&msg={quote(msg, safe='')}", status_code=303)


@router.get("/settings/display", response_class=HTMLResponse)
def settings_display_redirect():
    return RedirectResponse("/settings?tab=display", status_code=303)


@router.post("/settings/trmnl")
@router.post("/settings/display")
async def settings_display_save(request: Request):
    from app import display

    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=display&err=Form+expired", status_code=303)

    layout_raw = str(form.get("layout_json") or "").strip()
    layout = None
    if layout_raw:
        try:
            layout = json.loads(layout_raw)
        except json.JSONDecodeError:
            return RedirectResponse("/settings?tab=display&err=Layout+was+invalid", status_code=303)

    updates = {
        "enabled": form.get("enabled") == "1",
        "webhook_url": str(form.get("webhook_url") or ""),
        "interval_minutes": form.get("interval_minutes"),
        "device": str(form.get("device") or display.DEFAULT_DEVICE),
        "design": str(form.get("design") or display.DEFAULT_DESIGN),
    }
    device = display.normalize_device(updates["device"])
    design = display.normalize_design(updates["design"])
    updates["device"] = device
    updates["design"] = design
    if layout is not None:
        updates["layout"] = display.normalize_layout(layout, device=device, design=design)
    if form.get("reset_layout") == "1" or design in {"household", "status"}:
        updates["layout"] = display.default_layout(device, design)

    webhook = str(form.get("webhook_url") or "").strip()
    try:
        from stonepi_vault import set_secret

        # Blank field means “use Vault” — do not wipe DISPLAY_WEBHOOK_URL.
        if webhook:
            set_secret("DISPLAY_WEBHOOK_URL", webhook)
    except Exception:
        pass

    action = str(form.get("action") or "save")
    if action == "push":
        # Persist form URL only when provided; empty keeps display.json blank so Vault is used.
        display.save_config(updates)
        result = display.push_overview(dict(request.cookies))
        if result.get("ok"):
            return RedirectResponse("/settings?tab=display&msg=Pushed+to+TRMNL", status_code=303)
        err = quote(str(result.get("message") or "Push failed"), safe="")
        return RedirectResponse(f"/settings?tab=display&err={err}", status_code=303)
    display.save_config(updates)
    return RedirectResponse("/settings?tab=display&msg=Display+settings+saved", status_code=303)


@router.post("/settings/vault")
async def settings_vault_save(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
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
    vault = get_vault()
    label = _vault_label_map().get(key, key)
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


@router.post("/settings/automations")
async def settings_automations_save(request: Request):
    user, redirected = _user_or_login(request, admin=True)
    if redirected:
        return redirected
    form = await request.form()
    if not _require_csrf(request, form):
        return RedirectResponse("/settings?tab=automations&err=Form+expired", status_code=303)
    import stonepi_automations as automations

    enabled = {}
    for rule in automations.RULE_CATALOG:
        enabled[rule["id"]] = form.get(f"rule_{rule['id']}") == "1"
    automations.save_state({"enabled": enabled})
    return RedirectResponse("/settings?tab=automations&msg=Automations+saved", status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    if request.method == "POST":
        form = await request.form()
        if not _require_csrf(request, form):
            return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
    return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
