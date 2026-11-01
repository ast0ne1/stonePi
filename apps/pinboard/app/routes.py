from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, alerts, links, people, store
from app.config import ROOT_DIR, env
from stonepi_auth.brand import brand_color, fonts_rev, manifest_icons
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context, notifications_card_context
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()

SETTINGS_TABS = ("board", "notifications", "about")
SETTINGS_TITLES = {"board": "Board", "notifications": "Notifications", "about": "About"}
SETTINGS_LEDES = {
    "board": "Your background, the areas on the board and removing done items.",
    "notifications": "Phone alerts for due reminders and new notices.",
    "about": "App name, description, GitHub, and the version running here.",
}
SETTINGS_HUB_LEDE = "The board, phone alerts and app details."
SETTINGS_GROUPS = (
    ("board", "Board", (
        ("board", "Board", "Background, areas and done items.", "board"),
    )),
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


POST_NOTICES = "can_post_notices"
ASSIGN_OTHERS = "can_assign_others"
NO_POST_NOTICES = "Ask an admin to allow 'Add and pin cards' in StonePi → Users."
NO_ASSIGN_OTHERS = "Ask an admin to allow 'Assign reminders to others' in StonePi → Users."


def _can(user, capability: str) -> bool:
    """Pinboard capability; standalone (no secret) has no users, so everything is allowed."""
    if not _session_secret():
        return True
    return bool(user and (user.is_admin or user.has_capability("pinboard", capability)))


def _own_assignee(user) -> tuple[str, str]:
    name = people.name_for(people.household_people(_session_secret()), user.user_id)
    return name or user.display_name or user.username, user.user_id


def _reminder_assignee(user, assignee_user: str, assignee: str) -> tuple[str, str, str | None]:
    """(name, uid, error). Without "Assign reminders to others" a reminder is the caller's own.

    An unassigned (household) reminder pushes to everyone, so it counts as
    assigning to others: those callers get it as their own reminder instead
    (PriceScout's shopping-list send keeps working), while naming somebody
    else is refused.
    """
    if _can(user, ASSIGN_OTHERS):
        name, uid = _assignee(assignee_user, assignee)
        return name, uid, None
    from stonepi_auth.alerts import auth_user_id

    picked = auth_user_id(assignee_user) or ""
    if (picked and picked != user.user_id) or (not picked and assignee.strip()):
        return "", "", NO_ASSIGN_OTHERS
    name, uid = _own_assignee(user)
    return name, uid, None


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
        "can_post_notices": _can(user, POST_NOTICES),
        "can_assign_others": _can(user, ASSIGN_OTHERS),
        "no_post_notices_hint": NO_POST_NOTICES,
        "no_assign_others_hint": NO_ASSIGN_OTHERS,
        "alerts_bell_state": bell_context(
            user,
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=home,
            enabled=bool(_session_secret()),
            app_id="pinboard",
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
def api_display(items: int = 0):
    payload = store.display_payload()
    if items > 0:  # Car Thing panel lists; the plain call (Dashboard tile, TRMNL) is unchanged
        payload.update(store.panel_feed(items))
    return payload


@router.get("/", response_class=HTMLResponse)
def board(request: Request):
    return _board_page(request, "/pinboard/")


_URL_IN_TEXT = re.compile(r"https?://\S+")


@router.get("/add", response_class=HTMLResponse)
def share_add(request: Request, url: str = "", title: str = "", text: str = ""):
    """Add a link from somewhere else (pinboard-canvas.md §5): the phone's share sheet (the
    manifest's share target, or the iOS Shortcut) and the desktop bookmarklet open
    ``/add?url=…&title=…``, and the Board opens Add link with them filled in.

    Android often sends the address inside ``text``; whatever else ``text`` holds
    becomes the card's note."""
    shared = url.strip()
    note = text.strip()
    if not shared:
        found = _URL_IN_TEXT.search(note)
        shared = found.group(0) if found else ""
    if shared and shared in note:
        note = note.replace(shared, "").strip(" \n-–—:")
    share = {"url": shared[: store.URL_MAX], "title": " ".join(title.split())[: store.TITLE_MAX], "note": note[: store.TEXT_MAX]}
    query = request.url.query
    return _board_page(request, f"/pinboard/add?{query}" if query else "/pinboard/add", share=share)


def _board_page(request: Request, back: str, share: dict | None = None):
    user = _user(request)
    if _session_secret() and user is None:
        return RedirectResponse(login_url(_settings(), back), status_code=303)
    if user and not user.can_access("pinboard") and not user.is_admin:
        return _deny_page(request, "board.html", active="board", user=user)
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "board.html",
        _page_ctx(request, active="board", user=user, csrf=csrf, extra={**_board_ctx(user), "share": share}),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.get("/manifest.webmanifest")
def web_manifest():
    """Home-screen install; on Android it also puts Pinboard in the share sheet. Paths are
    relative to this file, so they work with or without the /pinboard prefix."""
    return JSONResponse(
        {
            "name": "Pinboard",
            "short_name": "Pinboard",
            "id": "./",
            "start_url": "./",
            "scope": "./",
            "display": "standalone",
            "background_color": brand_color("canvas"),
            "theme_color": brand_color("surface"),
            "icons": manifest_icons("pinboard"),
            "share_target": {
                "action": "./add",
                "method": "GET",
                "params": {"title": "title", "text": "text", "url": "url"},
            },
        },
        media_type="application/manifest+json",
    )


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
            "can_set_reminder_time": _is_admin(user),
            "is_admin": _is_admin(user),
            "background": store.user_pref(_uid(user), "background", store.BACKGROUNDS),
            "backgrounds": BACKGROUND_LABELS,
            "areas": store.areas(),
            "household_area": store.HOUSEHOLD_AREA,
            "auto_remove_days": store.auto_remove_days(),
            "auto_remove_choices": store.AUTO_REMOVE_DAYS,
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
    if not _can(user, POST_NOTICES):
        return RedirectResponse(f"/notices?err={quote(NO_POST_NOTICES)}", status_code=303)
    if not csrf_ok_request(request, form_token=csrf_token):
        return RedirectResponse("/notices?err=Form+expired", status_code=303)
    if not text.strip():
        return RedirectResponse("/notices?err=Notice+required&new=1", status_code=303)
    notice = store.add_notice(text.strip(), author=user.user_id if user else "")
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
    if not csrf_ok_request(request, form_token=csrf_token):
        return RedirectResponse("/reminders?err=Form+expired", status_code=303)
    if not text.strip():
        return RedirectResponse("/reminders?err=Reminder+required&new=1", status_code=303)
    name, uid, err = _reminder_assignee(user, assignee_user, assignee)
    if err:
        return RedirectResponse(f"/reminders?err={quote(err)}&new=1", status_code=303)
    store.add_reminder(text.strip(), due=due, assignee=name, assignee_user=uid, author=user.user_id if user else "")
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
    if not csrf_ok_request(request, form_token=csrf_token, header_token=header_csrf):
        return JSONResponse({"ok": False, "message": "Form expired — refresh and try again."}, status_code=403)
    if not text.strip():
        return JSONResponse({"ok": False, "message": "Reminder text required."}, status_code=400)
    # Callers may pass a household member's Auth id; without one the reminder is
    # household — or the caller's own when they can't assign to others.
    name, uid, err = _reminder_assignee(user, assignee_user, assignee)
    if err:
        return JSONResponse({"ok": False, "message": err}, status_code=403)
    item = store.add_reminder(
        text.strip(), due=due, assignee=name, assignee_user=uid, author=user.user_id if user else ""
    )
    return JSONResponse({"ok": True, "id": item["id"], "message": "Reminder added."})


@router.post("/settings/reminder-time")
async def settings_reminder_time(request: Request, reminder_time: str = Form(""), csrf_token: str = Form("")):
    user = _user(request)
    if _session_secret() and (user is None or not user.is_admin):
        return RedirectResponse(login_url(_settings(), "/pinboard/settings?tab=notifications"), status_code=303)
    if not csrf_ok_request(request, form_token=csrf_token):
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
    if not csrf_ok_request(request, form_token=csrf_token):
        sep = "&" if "?" in dest else "?"
        return RedirectResponse(f"{dest}{sep}err=Form+expired", status_code=303)
    store.delete_item(kind, item_id)
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(f"{dest}{sep}msg=Removed", status_code=303)



# --- the board: cards (pinboard-canvas.md) ---------------------------------------------

BACKGROUND_LABELS = {"cork": "Cork board", "plain": "Plain", "dotted": "Dotted grid", "whiteboard": "Whiteboard"}
PIN_STYLE_LABELS = {"push": "Push pin", "tack": "Round tack", "map": "Map pin", "tape": "Tape"}
_PREVIEW_NAME = re.compile(r"^[0-9a-f]{12}-(image|icon)\.(png|jpg|gif|webp|ico)$")


def _uid(user) -> str:
    """Whose card or preference this is; '' when Pinboard runs without sign-in."""
    return user.user_id if user and _session_secret() else ""


def _is_admin(user) -> bool:
    return not _session_secret() or bool(user and user.is_admin)


def _may_edit(user, card: dict) -> bool:
    """Edit the words or remove: the card's author or an admin (§8)."""
    if _is_admin(user):
        return True
    return bool(user and card.get("author") and card["author"] == user.user_id)


def _may_tick(user, card: dict) -> bool:
    """Tick done: a household checklist or reminder is anyone's; an assigned reminder is its
    assignee's, its author's or an admin's."""
    if card["type"] != "reminder" or _may_edit(user, card):
        return True
    owner = card.get("assignee_user") or ""
    return not owner or bool(user and owner == user.user_id)


def _board_ctx(user) -> dict:
    groups = store.board_groups()
    for group in groups:
        for card in group["cards"]:
            card["can_edit"] = _may_edit(user, card)
            card["can_tick"] = _may_tick(user, card)
    uid = _uid(user)
    return {
        "groups": groups,
        "canvas": store.canvas_state(),
        "areas": store.areas(),
        "people": people.household_people(_session_secret()),
        "background": store.user_pref(uid, "background", store.BACKGROUNDS),
        "pin_style": store.user_pref(uid, "pin_style", store.PIN_STYLES),
        "pin_colors": store.PIN_COLORS,
        "pin_styles": PIN_STYLE_LABELS,
        "note_colors": store.NOTE_COLORS,
        "can_pin": _can(user, POST_NOTICES),
        "is_admin": _is_admin(user),
    }


def _wants_json(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


def _reply(request: Request, ok: bool, message: str, *, anchor: str = "", status: int = 400, **data):
    """JSON for the page's own fetches, otherwise back to the board with a banner."""
    if _wants_json(request):
        return JSONResponse({"ok": ok, "message": message, **data}, status_code=200 if ok else status)
    key = "msg" if ok else "err"
    return RedirectResponse(f"/?{key}={quote(message)}{'#card-' + anchor if anchor else ''}", status_code=303)


async def _member_form(request: Request):
    """(user, form, refusal): signed in with Pinboard access and a fresh form, or a reply."""
    user = _user(request)
    if _session_secret() and (user is None or (not user.is_admin and not user.can_access("pinboard"))):
        if _wants_json(request):
            return user, None, JSONResponse({"ok": False, "message": "Sign in to Pinboard."}, status_code=401)
        return user, None, RedirectResponse(login_url(_settings(), "/pinboard/"), status_code=303)
    form = await request.form()
    token = str(form.get("csrf_token") or "") or (request.headers.get("X-StonePi-CSRF") or "")
    if not csrf_ok_request(request, form_token=token):
        return user, None, _reply(request, False, "Form expired — refresh and try again.", status=403)
    return user, form, None


def _pin_from(form, user) -> tuple[str, str] | None:
    """The pin picked in a form, if this person may pin (Add and pin cards); remembers the style."""
    color = str(form.get("pin_color") or "")
    if color not in store.PIN_COLORS or not _can(user, POST_NOTICES):
        return None
    style = str(form.get("pin_style") or "")
    if style in store.PIN_STYLES:
        store.set_user_pref(_uid(user), "pin_style", style, store.PIN_STYLES)
    else:
        style = store.user_pref(_uid(user), "pin_style", store.PIN_STYLES)
    return color, style


def _number(form, key: str) -> float | None:
    try:
        value = float(str(form.get(key) or "").strip())
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def _at(form) -> tuple[float, float] | None:
    """Where a card was put down on the canvas (x and y), if it was."""
    x, y = _number(form, "x"), _number(form, "y")
    return (x, y) if x is not None and y is not None else None


def _card_or_reply(request: Request, card_id: str):
    raw = store.get_card(card_id)
    if raw is None:
        return None, _reply(request, False, "That card is no longer on the board.", status=404)
    return store.card_view(raw), None


@router.post("/cards")
async def card_add(request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    kind = str(form.get("type") or "")
    area_id = str(form.get("area_id", store.HOUSEHOLD_AREA))
    at = _at(form)
    pin = _pin_from(form, user)
    try:
        if kind == "reminder":
            text = str(form.get("text") or "").strip()
            if not text:
                raise ValueError("Reminder text required.")
            name, uid, err = _reminder_assignee(
                user, str(form.get("assignee_user") or ""), str(form.get("assignee") or "")
            )
            if err:
                return _reply(request, False, err, status=403)
            card_id = store.add_reminder(
                text,
                due=str(form.get("due") or ""),
                assignee=name,
                assignee_user=uid,
                author=_uid(user),
                pin=pin,
                area_id=area_id,
                at=at,
            )["id"]
        elif kind in {"note", "link", "checklist"}:
            card_id = store.add_card(kind, dict(form), pin=pin, author=_uid(user), area_id=area_id, at=at)["id"]
        else:
            raise ValueError("Pick what to add.")
    except ValueError as exc:
        return _reply(request, False, str(exc))
    if kind == "link":
        links.fetch_preview_later(card_id)
    if kind == "note" and pin and _session_secret():
        by = (user.display_name or user.username) if user else ""
        alerts.emit_notice_posted({"id": card_id, "text": str(form.get("text") or "")}, by=by)
    return _reply(request, True, "Added to the board", anchor=card_id, id=card_id)


@router.post("/cards/{card_id}/edit")
async def card_edit(card_id: str, request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    card, missing = _card_or_reply(request, card_id)
    if missing:
        return missing
    if not _may_edit(user, card):
        return _reply(request, False, "Only whoever added it, or an admin, can change this card.", status=403)
    try:
        updated = store.update_card(card_id, dict(form))
    except ValueError as exc:
        return _reply(request, False, str(exc))
    except LookupError:
        return _reply(request, False, "That card is no longer on the board.", status=404)
    if updated["type"] == "link" and updated["data"].get("url") != card["url"]:
        links.fetch_preview_later(card_id)
    return _reply(request, True, "Saved", anchor=card_id)


@router.post("/cards/{card_id}/pin")
async def card_pin(card_id: str, request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    if not _can(user, POST_NOTICES):
        return _reply(request, False, NO_POST_NOTICES, status=403)
    color = str(form.get("pin_color") or "")
    style = str(form.get("pin_style") or "")
    if style in store.PIN_STYLES:
        store.set_user_pref(_uid(user), "pin_style", style, store.PIN_STYLES)
    if not store.set_pin(card_id, color if color in store.PIN_COLORS else "", style):
        return _reply(request, False, "That card is no longer on the board.", status=404)
    return _reply(request, True, "Pinned" if color in store.PIN_COLORS else "Unpinned", anchor=card_id)


@router.post("/cards/{card_id}/area")
async def card_area(card_id: str, request: Request):
    _user_, form, refused = await _member_form(request)
    if refused:
        return refused
    if not store.move_card(card_id, str(form.get("area_id") or "")):
        return _reply(request, False, "That card is no longer on the board.", status=404)
    return _reply(request, True, "Moved", anchor=card_id)


@router.post("/cards/{card_id}/place")
async def card_place(card_id: str, request: Request):
    """Canvas: a card dragged or resized. Anyone with access may move cards (§8)."""
    _user_, form, refused = await _member_form(request)
    if refused:
        return refused
    at = _at(form)
    if at is None:
        return _reply(request, False, "Missing position.")
    placed = store.place_card(card_id, at[0], at[1], _number(form, "w"), _number(form, "h"))
    if placed is None:
        return _reply(request, False, "That card is no longer on the board.", status=404)
    return _reply(request, True, "Moved", anchor=card_id, **placed)


@router.post("/areas/{area_id}/place")
async def area_place(area_id: str, request: Request):
    """Canvas: an area dragged or resized; its cards move with it."""
    _user_, form, refused = await _member_form(request)
    if refused:
        return refused
    at = _at(form)
    if at is None:
        return _reply(request, False, "Missing position.")
    if not store.place_area(area_id, at[0], at[1], _number(form, "w"), _number(form, "h")):
        return _reply(request, False, "That area is no longer on the board.", status=404)
    return _reply(request, True, "Moved")


@router.get("/api/cards")
def api_cards(request: Request):
    """Where every card and area is, so an open board can follow other people's changes."""
    user = _user(request)
    if _session_secret() and (user is None or (not user.is_admin and not user.can_access("pinboard"))):
        return JSONResponse({"ok": False, "message": "Sign in to Pinboard."}, status_code=401)
    return {"ok": True, **store.canvas_state()}


@router.post("/cards/{card_id}/done")
async def card_done(card_id: str, request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    card, missing = _card_or_reply(request, card_id)
    if missing:
        return missing
    if card["type"] != "reminder":
        return _reply(request, False, "Only reminders can be marked done.")
    if not _may_tick(user, card):
        return _reply(
            request, False, "Only the person it's for, whoever added it, or an admin can tick this.", status=403
        )
    done = str(form.get("done") or "1") == "1"
    store.set_reminder_done(card_id, done)
    return _reply(request, True, "Done" if done else "Not done", anchor=card_id, done=done)


@router.post("/cards/{card_id}/check/{index}")
async def card_check(card_id: str, index: int, request: Request):
    _user_, _form, refused = await _member_form(request)
    if refused:
        return refused
    state = store.toggle_check(card_id, index)
    if state is None:
        return _reply(request, False, "That item is no longer on the list.", status=404)
    return _reply(request, True, "Ticked" if state else "Unticked", anchor=card_id, done=state)


@router.post("/cards/{card_id}/delete")
async def card_delete(card_id: str, request: Request):
    user, _form, refused = await _member_form(request)
    if refused:
        return refused
    card, missing = _card_or_reply(request, card_id)
    if missing:
        return missing
    if not _may_edit(user, card):
        return _reply(request, False, "Only whoever added it, or an admin, can remove this card.", status=403)
    store.delete_card(card_id)
    return _reply(request, True, "Removed")


@router.post("/cards/{card_id}/preview")
async def card_preview(card_id: str, request: Request):
    _user_, _form, refused = await _member_form(request)
    if refused:
        return refused
    card, missing = _card_or_reply(request, card_id)
    if missing:
        return missing
    if card["type"] != "link":
        return _reply(request, False, "Only links have a preview.")
    store.set_link_preview(card_id, {"preview": "pending"})
    links.fetch_preview_later(card_id)
    return _reply(request, True, "Fetching the preview again", anchor=card_id)


@router.get("/previews/{name}")
def preview_file(name: str, request: Request):
    user = _user(request)
    if _session_secret() and (user is None or (not user.is_admin and not user.can_access("pinboard"))):
        return JSONResponse({"ok": False}, status_code=404)
    if not _PREVIEW_NAME.match(name):
        return JSONResponse({"ok": False}, status_code=404)
    path = store.previews_dir() / name
    if not path.is_file():
        return JSONResponse({"ok": False}, status_code=404)
    return FileResponse(
        path,
        media_type=links.MEDIA_TYPES[name.rsplit(".", 1)[1]],
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
    )


# --- Settings → Board ------------------------------------------------------------------


def _settings_reply(ok: bool, message: str):
    key = "msg" if ok else "err"
    return RedirectResponse(f"/settings?tab=board&{key}={quote(message)}", status_code=303)


@router.post("/settings/background")
async def settings_background(request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    try:
        store.set_user_pref(_uid(user), "background", str(form.get("background") or ""), store.BACKGROUNDS)
    except ValueError as exc:
        return _settings_reply(False, str(exc))
    return _settings_reply(True, "Background saved")


@router.post("/settings/auto-remove")
async def settings_auto_remove(request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    if not _is_admin(user):
        return _settings_reply(False, "Only an admin can change this.")
    try:
        days = store.set_auto_remove_days(int(str(form.get("days") or "0")))
    except ValueError:
        return _settings_reply(False, "Pick one of the listed choices.")
    if not days:
        return _settings_reply(True, "Done items stay until someone removes them")
    return _settings_reply(True, f"Done items are removed after {days} day{'s' if days != 1 else ''}")


@router.post("/settings/areas")
async def settings_area_add(request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    if not _is_admin(user):
        return _settings_reply(False, "Only an admin can add areas.")
    try:
        area = store.add_area(str(form.get("name") or ""))
    except ValueError as exc:
        return _settings_reply(False, str(exc))
    return _settings_reply(True, f"Added {area['name']}")


@router.post("/settings/areas/{area_id}")
async def settings_area_change(area_id: str, request: Request):
    user, form, refused = await _member_form(request)
    if refused:
        return refused
    if not _is_admin(user):
        return _settings_reply(False, "Only an admin can change areas.")
    try:
        if str(form.get("action") or "") == "delete":
            store.delete_area(area_id)
            return _settings_reply(True, "Area removed; its cards are still on the board")
        store.rename_area(area_id, str(form.get("name") or ""))
    except ValueError as exc:
        return _settings_reply(False, str(exc))
    return _settings_reply(True, "Area renamed")


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
