from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import PurePath
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app import __asset_rev__, __github__, __github_user__, __version__, db, display, local, notify, photos
from app.config import ROOT_DIR, env
from stonepi_auth.brand import fonts_rev
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.header import app_page_url
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.internal import verify_internal_v2
from stonepi_auth.prefix import clean_prefix, prefix_path
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, decode_session

logger = logging.getLogger("ripple")

APP_ID = "ripple"
PER_PAGE = 20
# Standalone (no session secret) has no users: everything is allowed and posts are "You".
LOCAL_AUTHOR = ("local", "You")
NO_ACCESS = "No access to Ripple. Ask an admin to add Ripple for you in StonePi → People."
# Photos one person may upload in an hour (each is decoded and re-saved on the Pi).
UPLOADS_PER_HOUR = 60
# The post form's script sends this header and gets JSON back, so an error keeps the form as it is.
AJAX_HEADER = "x-ripple-ajax"

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()

SETTINGS_TABS = ("board", "display", "photos", "about")
SETTINGS_TITLES = {"board": "Board", "display": "Family display", "photos": "Photos", "about": "About"}
SETTINGS_LEDES = {
    "board": "The board's name, shown on the feed and the family display.",
    "display": "What the family's TRMNL shows, and how to set it up.",
    "photos": "How many photos a post can have.",
    "about": "App name, description, GitHub, and the version running here.",
}
SETTINGS_HUB_LEDE = "The board, the family display, photos and app details."
SETTINGS_GROUPS = (
    ("board", "Board", (
        ("board", "Board", "Name of the family board.", "board"),
        ("display", "Family display", "Layout, posts shown and TRMNL setup.", "display"),
        ("photos", "Photos", "Photos per post.", "photo"),
    )),
    ("app", "App", (
        ("about", "About", "Version and project links.", "about"),
    )),
)

_uploads: dict[str, deque] = defaultdict(deque)
_uploads_lock = threading.Lock()


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
        app_id=APP_ID,
        prefix=env.stonepi_prefix or ("/ripple" if env.routing == "path" else ""),
        auth_url=browser_auth_url(env.auth_url, routing=env.routing),
        public_origin=env.public_origin,
        hostname=env.hostname,
    )


def _user(request: Request):
    return decode_session(request.cookies.get(COOKIE_NAME), _session_secret())


def _allowed(user) -> bool:
    """Read and post: anyone granted Ripple (checked on every request), admins, or standalone."""
    if not _session_secret():
        return True
    return bool(user and (user.is_admin or user.can_access(APP_ID)))


def _is_admin(user) -> bool:
    return not _session_secret() or bool(user and user.is_admin)


def _author(user) -> tuple[str, str]:
    if not _session_secret() or user is None:
        return LOCAL_AUTHOR
    return user.user_id, user.display_name or user.username


def _can_change(user, post: dict) -> bool:
    """Authors edit and delete their own posts; admins any post."""
    return _is_admin(user) or (user is not None and post["author_id"] == user.user_id)


def _gate(request: Request, back: str):
    """(user, response). A response means stop: sign in first, or no access."""
    user = _user(request)
    if _session_secret() and user is None:
        return None, RedirectResponse(login_url(_settings(), f"/ripple{back}"), status_code=303)
    if not _allowed(user):
        return user, _render(request, "denied.html", user=user, active="", status_code=403)
    return user, None


def _when(stamp: str) -> str:
    """'Today 14:05', 'Yesterday 19:40', 'Wed 22 Oct', '3 Mar 2025' in the Pi's local time."""
    try:
        moment = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return ""
    today = datetime.now(timezone.utc).astimezone().date()
    days = (today - moment.date()).days
    if days == 0:
        return f"Today {moment:%H:%M}"
    if days == 1:
        return f"Yesterday {moment:%H:%M}"
    if 1 < days < 7:
        return f"{moment:%a} {moment.day} {moment:%b}"
    if moment.year == today.year:
        return f"{moment.day} {moment:%b}"
    return f"{moment.day} {moment:%b} {moment.year}"


def _display_when(stamp: str) -> str:
    """'Sun 25/10 8:31 AM' in the Pi's local time: the family display is read in another time
    zone, where 'Today' could be wrong."""
    try:
        moment = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return ""
    hour = moment.hour % 12 or 12
    return f"{moment:%a} {moment.day}/{moment.month} {hour}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'}"


templates.env.filters["when"] = _when
templates.env.filters["display_when"] = _display_when


def _render(request: Request, template: str, *, user, active: str, status_code: int = 200, **extra):
    home = portal_home_url(request, env.public_origin).rstrip("/")
    csrf = csrf_from_request(request.cookies)
    ctx = {
        "user": user,
        "active": active,
        "csrf_token": csrf,
        "board_name": db.board_name(),
        "public_origin": home,
        "stonepi_home_url": home,
        "error": request.query_params.get("err"),
        "message": request.query_params.get("msg"),
        "is_admin": _is_admin(user),
        "alerts_bell_state": bell_context(
            user,
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=home,
            enabled=bool(_session_secret()),
            app_id=APP_ID,
        ),
        **extra,
    }
    response = templates.TemplateResponse(request, template, ctx, status_code=status_code)
    if status_code == 200:
        set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


def _form_error(back: str, message: str) -> RedirectResponse:
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}err={quote(message)}", status_code=303)


def _ajax(request: Request) -> bool:
    return request.headers.get(AJAX_HEADER) == "1"


def _app_url(path: str) -> str:
    """``path`` as the browser sees it (with the nginx prefix); JSON isn't rewritten like HTML."""
    prefix = clean_prefix(env.stonepi_prefix)
    return prefix_path(path, prefix) if prefix else path


def _done(request: Request, url: str):
    if _ajax(request):
        return JSONResponse({"ok": True, "redirect": _app_url(url)})
    return RedirectResponse(url, status_code=303)


def _fail(request: Request, back: str, message: str, status_code: int = 400):
    if _ajax(request):
        return JSONResponse({"ok": False, "error": message}, status_code=status_code)
    return _form_error(back, message)


def _clean_body(body: str, *, has_photos: bool) -> tuple[str, str | None]:
    text = body.replace("\r\n", "\n").strip()
    if not text and not has_photos:
        return "", "Write something or add a photo."
    if len(text) > db.BODY_MAX:
        return "", f"Posts can be up to {db.BODY_MAX} characters."
    return text, None


def _upload_room(who: str, count: int) -> bool:
    """Whether ``who`` may upload ``count`` more photos this hour (nothing is used up)."""
    now = time.monotonic()
    with _uploads_lock:
        recent = _uploads[who]
        while recent and now - recent[0] > 3600:
            recent.popleft()
        return len(recent) + count <= UPLOADS_PER_HOUR


def _count_uploads(who: str, count: int) -> None:
    """Saved photos count against the hour; refused or failed ones don't."""
    with _uploads_lock:
        _uploads[who].extend([time.monotonic()] * count)


def _uploads_in(form) -> list[UploadFile]:
    """Chosen files only: an empty file input still sends one nameless part."""
    return [f for f in form.getlist("photos") if isinstance(f, UploadFile) and (f.filename or "").strip()]


async def _store_photos(files: list[UploadFile], captions: list) -> tuple[list[photos.Photo], str | None]:
    """Check and store every upload, or none of them: (photos, error)."""
    stored: list[photos.Photo] = []
    for n, upload in enumerate(files):
        caption = str(captions[n]) if n < len(captions) else ""
        data = await upload.read(photos.MAX_BYTES + 1)
        name = PurePath(upload.filename or "").name[:60]
        label = f"Photo {n + 1} ({name})" if name else f"Photo {n + 1}"
        try:
            stored.append(await run_in_threadpool(photos.store, data, caption))
        except photos.PhotoError as exc:
            photos.discard(stored)
            return [], f"{label} {exc}."
        except Exception:  # noqa: BLE001 - never leave the photos already saved behind
            logger.exception("Couldn't save an uploaded photo")
            photos.discard(stored)
            return [], f"{label} couldn't be saved. Try again, or try a different photo."
    return stored, None


def _decorate(post: dict) -> dict:
    for media in post.get("media") or []:
        media["thumb_w"], media["thumb_h"] = photos.thumb_size(media["width"], media["height"])
        media["available"] = photos.available(media["storage_key"])
    return post


def _changed() -> None:
    """Something the family display shows changed: ask Notify to push it soon. Only for
    changes the display can show (a feed-only post would cost a capture for nothing)."""
    notify.display_changed(_session_secret())


def _form_ctx() -> dict:
    return {
        "max_photos": photos.max_per_post(),
        "max_photo_mb": photos.MAX_BYTES // (1024 * 1024),
        "caption_max": photos.CAPTION_MAX,
    }


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": APP_ID}


@router.get("/", response_class=HTMLResponse)
def feed(request: Request, page: int = 1):
    user, stop = _gate(request, "/")
    if stop:
        return stop
    page = max(1, page)
    posts, has_more = db.list_posts(page, PER_PAGE)
    for post in posts:
        _decorate(post)
        post["can_change"] = _can_change(user, post)
    return _render(request, "feed.html", user=user, active="feed", posts=posts, page=page, has_more=has_more)


@router.get("/new", response_class=HTMLResponse)
def new_post(request: Request):
    user, stop = _gate(request, "/new")
    if stop:
        return stop
    return _render(request, "post_form.html", user=user, active="new", post=None, **_form_ctx())


@router.post("/posts")
async def create_post(request: Request):
    back = "/new"
    user, stop = _gate(request, back)
    if stop:
        return stop
    form = await request.form()
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), str(form.get("csrf_token") or "")):
        return _fail(request, back, "Form expired. Refresh the page and try again.", 403)
    files = _uploads_in(form)
    limit = photos.max_per_post()
    if len(files) > limit:
        return _fail(request, back, f"A post can have up to {limit} photos.")
    text, err = _clean_body(str(form.get("body") or ""), has_photos=bool(files))
    if err:
        return _fail(request, back, err)
    author_id, author_name = _author(user)
    if files and not _upload_room(author_id, len(files)):
        return _fail(request, back, "That's a lot of photos in one hour. Try again a little later.", 429)
    stored, err = await _store_photos(files, form.getlist("captions"))
    if err:
        return _fail(request, back, err)
    shown = bool(form.get("show_on_display"))
    try:
        db.create_post(author_id=author_id, author_name=author_name, body=text, show_on_display=shown, photos=stored)
    except Exception:
        photos.discard(stored)
        raise
    _count_uploads(author_id, len(stored))
    if shown:
        _changed()
    return _done(request, "/?msg=Posted")


@router.get("/posts/{post_id}/edit", response_class=HTMLResponse)
def edit_post(request: Request, post_id: int):
    user, stop = _gate(request, f"/posts/{post_id}/edit")
    if stop:
        return stop
    post = db.get_post(post_id)
    if post is None:
        return _form_error("/", "That post has been deleted.")
    if not _can_change(user, post):
        return _form_error("/", "You can only edit your own posts.")
    return _render(request, "post_form.html", user=user, active="feed", post=_decorate(post), **_form_ctx())


@router.post("/posts/{post_id}")
async def update_post(request: Request, post_id: int):
    back = f"/posts/{post_id}/edit"
    user, stop = _gate(request, back)
    if stop:
        return stop
    form = await request.form()
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), str(form.get("csrf_token") or "")):
        return _fail(request, back, "Form expired. Refresh the page and try again.", 403)
    post = db.get_post(post_id)
    if post is None:
        return _fail(request, "/", "That post has been deleted.", 404)
    if not _can_change(user, post):
        return _fail(request, "/", "You can only edit your own posts.", 403)
    existing = {m["id"]: m for m in post["media"]}
    remove = {int(v) for v in form.getlist("remove") if str(v).isdigit()} & set(existing)
    captions: dict[int, str] = {}
    for media_id, media_row in existing.items():
        if media_id in remove:
            continue
        raw = form.get(f"caption_{media_id}")
        captions[media_id] = photos.clean_caption(str(raw)) if raw is not None else media_row["caption"]
    files = _uploads_in(form)
    limit = photos.max_per_post()
    if len(existing) - len(remove) + len(files) > limit:
        return _fail(request, back, f"A post can have up to {limit} photos.")
    text, err = _clean_body(str(form.get("body") or ""), has_photos=len(existing) - len(remove) + len(files) > 0)
    if err:
        return _fail(request, back, err)
    uploader = _author(user)[0]
    if files and not _upload_room(uploader, len(files)):
        return _fail(request, back, "That's a lot of photos in one hour. Try again a little later.", 429)
    stored, err = await _store_photos(files, form.getlist("captions"))
    if err:
        return _fail(request, back, err)
    shown = bool(form.get("show_on_display"))
    try:
        removed = db.update_post(
            post_id, body=text, show_on_display=shown, captions=captions, remove=remove, add=stored
        )
    except Exception:
        photos.discard(stored)
        raise
    if removed is None:
        photos.discard(stored)
        return _fail(request, "/", "That post has been deleted.", 404)
    _count_uploads(uploader, len(stored))
    for key, ext in removed:
        photos.delete_files(key, ext)
    if shown or post["show_on_display"]:  # on the display before or after the edit
        _changed()
    return _done(request, "/?msg=Saved")


@router.post("/posts/{post_id}/delete")
async def delete_post(request: Request, post_id: int, csrf_token: str = Form("")):
    user, stop = _gate(request, "/")
    if stop:
        return stop
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _form_error("/", "Form expired. Try again.")
    post = db.get_post(post_id)
    if post is None:
        return RedirectResponse("/?msg=Deleted", status_code=303)
    if not _can_change(user, post):
        return _form_error("/", "You can only delete your own posts.")
    for key, ext in db.delete_post(post_id) or []:
        photos.delete_files(key, ext)
    if post["show_on_display"]:
        _changed()
    return RedirectResponse("/?msg=Deleted", status_code=303)


@router.get("/media/{key}")
@router.get("/media/{key}/{variant}")
def media(request: Request, key: str, variant: str = "full"):
    """A photo (or its thumbnail), only for people who can read the board."""
    user, stop = _gate(request, "/")
    if stop:
        return stop
    if variant not in {"full", "thumb"} or not photos.KEY_RE.match(key):
        return Response(status_code=404)
    row = db.media_by_key(key)
    if row is None:
        return Response(status_code=404)
    thumb = variant == "thumb"
    path = photos.thumb_path(key) if thumb else photos.photo_path(key, row["ext"])
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(
        path,
        media_type="image/jpeg" if thumb else row["mime_type"],
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str | None = None):
    user, stop = _gate(request, "/settings")
    if stop:
        return stop
    raw = (tab or "").strip().lower()
    hub = raw in {"", "hub"}
    tab = raw if raw in SETTINGS_TABS else "about"
    family = {}
    # Notify's Display details (names, push errors) are admin matters, as in Notify itself.
    if tab == "display" and not hub and _is_admin(user):
        home = portal_home_url(request, env.public_origin).rstrip("/")
        displays = notify.display_status(_session_secret())
        for d in displays or []:
            d["url"] = app_page_url(home, "notify", f"displays/{quote(str(d.get('id') or ''))}")
        family = {
            "family_displays": displays,
            "family_setup_url": app_page_url(home, "notify", "displays?add=ripple#add-display"),
        }
    return _render(
        request,
        "settings.html",
        user=user,
        active="settings",
        settings_hub=hub,
        settings_tab=tab,
        settings_groups=SETTINGS_GROUPS,
        settings_title="Settings" if hub else SETTINGS_TITLES[tab],
        settings_lede=SETTINGS_HUB_LEDE if hub else SETTINGS_LEDES[tab],
        board_name_max=db.BOARD_NAME_MAX,
        display_layout=display.layout(),
        display_posts=display.post_count(),
        display_layouts=(("hero", "Newest photo large, posts beside it"), ("list", "Posts in rows with small photos")),
        display_posts_max=display.MAX_POSTS,
        here_show_time=local.show_time(),
        here_show_weather=local.show_weather(),
        place_here=local.get_place("here"),
        place_family=local.get_place("family"),
        here_preview=local.strip() if tab == "display" else None,
        **family,
        max_photos=photos.max_per_post(),
        max_photos_limit=photos.MAX_PER_POST_LIMIT,
        max_photo_mb=photos.MAX_BYTES // (1024 * 1024),
        app_name="Ripple",
        app_version=__version__,
        app_github_user=__github_user__,
        app_github=__github__,
    )


@router.post("/settings/board")
async def settings_board(request: Request, board_name: str = Form(""), csrf_token: str = Form("")):
    back = "/settings?tab=board"
    user, stop = _gate(request, back)
    if stop:
        return stop
    if not _is_admin(user):
        return _form_error(back, "Only an admin can rename the board.")
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _form_error(back, "Form expired. Try again.")
    name = " ".join(board_name.split())
    if not name:
        return _form_error(back, "Give the board a name.")
    if len(name) > db.BOARD_NAME_MAX:
        return _form_error(back, f"Board names can be up to {db.BOARD_NAME_MAX} characters.")
    db.set_setting("board_name", name)
    _changed()
    return RedirectResponse(f"{back}&msg=Saved", status_code=303)


@router.post("/settings/display")
async def settings_display(
    request: Request, layout: str = Form(""), posts: str = Form(""), csrf_token: str = Form("")
):
    back = "/settings?tab=display"
    user, stop = _gate(request, back)
    if stop:
        return stop
    if not _is_admin(user):
        return _form_error(back, "Only an admin can change the family display.")
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _form_error(back, "Form expired. Try again.")
    try:
        count = int(posts)
    except ValueError:
        count = 0
    if layout not in display.LAYOUTS or not 1 <= count <= display.MAX_POSTS:
        return _form_error(back, f"Pick a layout and between 1 and {display.MAX_POSTS} posts.")
    db.set_setting("display_layout", layout)
    db.set_setting("display_posts", str(count))
    _changed()
    return RedirectResponse(f"{back}&msg=Saved", status_code=303)


@router.post("/settings/here")
async def settings_here(request: Request):
    """The "here" strip on the family display: what it shows and the two places."""
    back = "/settings?tab=display"
    user, stop = _gate(request, back)
    if stop:
        return stop
    if not _is_admin(user):
        return _form_error(back, "Only an admin can change the family display.")
    form = await request.form()
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), str(form.get("csrf_token") or "")):
        return _form_error(back, "Form expired. Try again.")
    show_time, show_weather = bool(form.get("show_time")), bool(form.get("show_weather"))
    here = local.clean_place(str(form.get("place_here") or ""))
    family = local.clean_place(str(form.get("place_family") or ""))
    if (show_time or show_weather) and here is None:
        return _form_error(back, "Choose your city first.")
    db.set_setting("display_show_time", "1" if show_time else "")
    db.set_setting("display_show_weather", "1" if show_weather else "")
    local.set_place("here", here)
    local.set_place("family", family)
    _changed()
    return RedirectResponse(f"{back}&msg=Saved#here", status_code=303)


@router.get("/api/places")
def api_places(request: Request, q: str = ""):
    """City search for the "here" strip (admins; Open-Meteo, no key)."""
    user, stop = _gate(request, "/settings?tab=display")
    if stop:
        return stop
    if not _is_admin(user):
        return JSONResponse({"ok": False, "message": "Admins only."}, status_code=403)
    query = " ".join(q.split())[:80]
    if len(query) < 2:
        return {"ok": True, "places": []}
    try:
        return {"ok": True, "places": local.search_places(query)}
    except Exception:  # noqa: BLE001 - shown next to the search box
        logger.exception("Place search failed")
        return JSONResponse({"ok": False, "message": "The place search isn't reachable right now."}, status_code=502)


@router.get("/display", response_class=HTMLResponse)
def display_page(request: Request, k: str = ""):
    """The family display. Notify's capture brings a one-off key; anyone with Ripple can preview it."""
    if k:
        client = request.client.host if request.client else ""
        if not (display.direct_from_pi(client, request.headers) and display.redeem_key(k)):
            return Response("This capture key isn't valid.", status_code=403, media_type="text/plain")
        panel = display.screen(request.query_params)
    else:
        _, stop = _gate(request, "/display")
        if stop:
            return stop
        # A preview is one of the real screens; it can't ask for an arbitrary size or depth.
        panel = display.preview_screen(request.query_params.get("device"))
    now = datetime.now(timezone.utc).astimezone()
    response = templates.TemplateResponse(
        request,
        "display.html",
        {
            **display.page_data(panel),
            "board_name": db.board_name(),
            "today": f"{now:%a} {now.day} {now:%b}",  # the date only: a clock would change every capture
            "here": local.strip(),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@router.post("/api/internal/capture-key")
async def capture_key(request: Request):
    """Notify, just before it captures /display (signed; nginx never routes /api/internal/)."""
    body = await request.body()
    if not verify_internal_v2(_session_secret(), "POST", request.url.path, request.headers, body):
        return JSONResponse({"ok": False}, status_code=404)
    return {"ok": True, "key": display.issue_key(), "expires_in": display.KEY_TTL_S}


@router.post("/settings/photos")
async def settings_photos(request: Request, max_photos: str = Form(""), csrf_token: str = Form("")):
    back = "/settings?tab=photos"
    user, stop = _gate(request, back)
    if stop:
        return stop
    if not _is_admin(user):
        return _form_error(back, "Only an admin can change this.")
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _form_error(back, "Form expired. Try again.")
    try:
        value = int(max_photos)
    except ValueError:
        value = 0
    if not 1 <= value <= photos.MAX_PER_POST_LIMIT:
        return _form_error(back, f"Choose between 1 and {photos.MAX_PER_POST_LIMIT} photos.")
    db.set_setting("max_photos", str(value))
    return RedirectResponse(f"{back}&msg=Saved", status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    return RedirectResponse(logout_url(_settings(), "/"), status_code=303)
