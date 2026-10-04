from __future__ import annotations

import re
import threading
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, db
from app.config import (
    DEFAULT_CONTENT_DIR,
    DRIVE_SUBDIR,
    FEATURED,
    GIB,
    LANGUAGES,
    ROOT_DIR,
    env,
)
from app.platform import APP_ID, auth_optional, prefix, session_secret, settings as platform_settings
from app.services import backup, catalog, content, downloads, helper, kiwix, storage

from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context, notifications_card_context
from stonepi_auth.brand import fonts_rev
from stonepi_auth.csrf import CSRF_COOKIE, csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()

MANAGE = "can_manage_content"

SETTINGS_TABS = (
    ("storage", "Storage"),
    ("backup", "Backup"),
    ("content", "Content"),
    ("reader", "Reader"),
    ("notifications", "Notifications"),
    ("about", "About"),
)
SETTINGS_TAB_KEYS = {key for key, _ in SETTINGS_TABS}
SETTINGS_LEDES = {
    "storage": "Where library files live: this Pi’s microSD, a USB stick or an SSD.",
    "backup": "Whether library files go into USB backups, and whether they fit.",
    "content": "Catalogue language, download speed and catalogue updates.",
    "reader": "The Kiwix reader that serves your library.",
    "notifications": "Alerts when content is ready or needs attention.",
    "about": "App name, description, GitHub, and the version running here.",
}
SETTINGS_HUB_SUBTEXTS = {
    "storage": "microSD, USB or SSD.",
    "backup": "Include library files.",
    "content": "Language and downloads.",
    "reader": "Kiwix status and restart.",
    "notifications": "Your phone alerts.",
    "about": "Version and project links.",
}
SETTINGS_HUB_LEDE = "Storage, backups, downloads and the reader."
SETTINGS_GROUPS = (
    ("library", "Library", ("storage", "backup", "content")),
    ("system", "System", ("reader", "notifications")),
    ("app", "App", ("about",)),
)
JOB_LABELS = {
    "queued": "Waiting",
    "downloading": "Downloading",
    "verifying": "Verifying",
    "installing": "Installing",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "done": "Done",
}

_setup_lock = threading.Lock()
SAFE_PATH = re.compile(r"^/(mnt|media|srv)/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+$")


def gb(n: int | float | None) -> str:
    value = float(n or 0) / GIB
    if value >= 100:
        return f"{value:.0f} GB"
    if value >= 1:
        return f"{value:.1f} GB"
    return f"{value * 1024:.0f} MB"


templates.env.filters["gb"] = gb


def _settings_groups() -> tuple:
    labels = dict(SETTINGS_TABS)
    return tuple(
        (group_id, label, tuple((key, labels[key], SETTINGS_HUB_SUBTEXTS[key]) for key in keys))
        for group_id, label, keys in SETTINGS_GROUPS
    )


def _user(request: Request):
    return decode_session(request.cookies.get(COOKIE_NAME), session_secret())


def _can_manage(user) -> bool:
    if not session_secret():
        return auth_optional()
    return bool(user and (user.is_admin or user.has_capability(APP_ID, MANAGE)))


def _is_admin(user) -> bool:
    """Admin-only Library settings: storage, setup, backup, the speed cap.

    Without a session secret only run-dev gets this far (_require_user locks
    the Pi), and there everyone is the admin, as in the templates.
    """
    if not session_secret():
        return auth_optional()
    return bool(user and user.is_admin)


def _require_user(request: Request, *, manage: bool = False, admin: bool = False):
    user = _user(request)
    secret = session_secret()
    if not secret and not auth_optional():
        return None, HTMLResponse("StonePi sign-in isn't configured, so the Library is locked.", status_code=503)
    if secret and user is None:
        return None, RedirectResponse(login_url(platform_settings(), f"{prefix()}{request.url.path}"), status_code=303)
    if user and not user.is_admin and not user.can_access(APP_ID):
        return None, HTMLResponse("No access to the Library.", status_code=403)
    if secret and admin and not user.is_admin:
        return None, HTMLResponse("Only a StonePi admin can do that.", status_code=403)
    if manage and not _can_manage(user):
        return None, HTMLResponse("Missing capability: Manage content", status_code=403)
    return user, None


def _csrf(request: Request, token: str | None) -> bool:
    return csrf_ok(request.cookies.get(CSRF_COOKIE), token)


def _redirect(path: str, *, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    url = f"{prefix()}{path}"
    parts = []
    if msg:
        parts.append(f"msg={quote(msg)}")
    if error:
        parts.append(f"error={quote(error)}")
    if parts:
        url += ("&" if "?" in url else "?") + "&".join(parts)
    return RedirectResponse(url, status_code=303)


def _language() -> str:
    lang = db.get_setting("language", "en")
    return lang if lang in dict(LANGUAGES) else "en"


def _page(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    user = ctx.get("user")
    ctx["csrf_token"] = csrf
    ctx.setdefault("stonepi_home_url", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", prefix())
    ctx.setdefault("app_name", "Library")
    ctx.setdefault("app_version", __version__)
    ctx.setdefault("app_github", __github__)
    ctx.setdefault("app_github_user", __github_user__)
    ctx.setdefault("sso", bool(session_secret()))
    ctx.setdefault("can_manage", _can_manage(user))
    ctx.setdefault("is_admin", bool(user and user.is_admin) or not session_secret())
    ctx.setdefault("reader_url", kiwix.reader_url())
    ctx.setdefault("backup_warning", db.get_setting("backup_warning"))
    ctx.setdefault("job_labels", JOB_LABELS)
    from stonepi_auth.session import factory_admin_warning

    ctx.setdefault("using_factory_admin", factory_admin_warning(user))
    ctx.setdefault(
        "alerts_bell_state",
        bell_context(
            user,
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=ctx["stonepi_home_url"],
            enabled=bool(session_secret()),
        ),
    )
    response = templates.TemplateResponse(request, name, ctx)
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


def _job_view(job: dict) -> dict:
    total = int(job["total"] or 0)
    done = int(job["done"] or 0)
    pct = int(done * 100 / total) if total else 0
    return {**job, "pct": min(100, pct), "label": JOB_LABELS.get(job["status"], job["status"])}


def _overview() -> dict:
    """Shared by the home page, /api/status and /api/display."""
    content.reconcile()
    status = kiwix.status()
    rows = db.list_content()
    jobs = [_job_view(j) for j in db.active_jobs()]
    installed = [r for r in rows if r["status"] == "installed"]
    missing = [r for r in rows if r["status"] == "missing"]
    if not status["installed"]:
        state = "not_installed"
    elif missing and not installed:
        state = "storage_missing"
    elif jobs and not installed:
        state = "downloading"
    elif installed:
        state = "ready"
    else:
        state = "empty"
    return {
        "state": state,
        "kiwix": status,
        "rows": rows,
        "installed": installed,
        "missing": missing,
        "jobs": jobs,
        "used": sum(int(r["size"]) for r in installed),
    }


# ---------- platform endpoints ----------

@router.get("/healthz")
def healthz():
    return {"ok": True, "service": APP_ID}


@router.get("/api/display")
def api_display():
    ov = _overview()
    installed = ov["installed"]
    if ov["state"] == "not_installed":
        detail = "Not set up"
    elif ov["state"] == "storage_missing":
        detail = "Storage drive not connected"
    elif ov["jobs"]:
        j = ov["jobs"][0]
        detail = f"Installing {j['title']} · {j['pct']}%"
    elif installed:
        detail = installed[0]["title"] if len(installed) == 1 else f"{len(installed)} collections"
    else:
        detail = "No content yet"
    return JSONResponse(
        {
            "ok": True,
            "state": ov["state"],
            "collections": len(installed),
            "downloading": len(ov["jobs"]),
            "detail": detail,
        }
    )


@router.get("/api/status")
def api_status(request: Request):
    user, denied = _require_user(request)
    if denied:
        return JSONResponse({"ok": False}, status_code=401)
    ov = _overview()
    return JSONResponse(
        {
            "ok": True,
            "state": ov["state"],
            "setup": db.get_setting("setup_state"),
            "jobs": [
                {"id": j["id"], "title": j["title"], "status": j["status"], "label": j["label"], "pct": j["pct"],
                 "done": gb(j["done"]), "total": gb(j["total"]), "error": j["error"]}
                for j in ov["jobs"]
            ],
        }
    )


@router.get("/_auth")
async def nginx_auth(request: Request):
    """nginx auth_request for the Kiwix reader at /library/read/.

    Runs for every page, image and script the reader serves, and only checks
    the signed cookie (no I/O), so it stays on the event loop instead of a
    thread-pool hop per asset.
    """
    if not session_secret():
        # Fail closed on a Pi: no secret means no way to check who is asking.
        return Response(status_code=204 if auth_optional() else 403)
    user = _user(request)
    if user is None:
        return Response(status_code=401)
    if not user.is_admin and not user.can_access(APP_ID):
        return Response(status_code=403)
    return Response(status_code=204)


@router.get("/login")
def login(request: Request):
    # nginx sends ?next=$request_uri unencoded, so a reader URL's own query
    # (search?pattern=x&books.name=y) arrives as extra params: rebuild it whole.
    raw = request.url.query
    target = raw[5:] if raw.startswith("next=") else f"{prefix()}/"
    # An app redirect encodes the whole path (no literal "?"); nginx's raw
    # $request_uri must stay as sent, or %26 in a search turns into "&".
    if "?" not in target:
        target = unquote(target)
    # Browsers drop tabs/newlines and read \ as /, so "/\t/x" or "/\x" would
    # become "//x" — another site.
    if not target.startswith("/") or target.startswith("//") or "\\" in target or any(ord(c) < 32 or ord(c) == 127 for c in target):
        target = f"{prefix()}/"
    return RedirectResponse(login_url(platform_settings(), target), status_code=303)


@router.get(env.reader_path.rstrip("/") + "/{rest:path}")
def reader_dev(rest: str = ""):
    """On the Pi nginx sends the reader path to kiwix-serve and it never gets here.
    In run-dev there's no nginx or Kiwix, so explain instead of a bare 404."""
    if not helper.simulated():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return _redirect("/", msg="The reader (Kiwix) only runs on the Pi. Open the Library there to read.")


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if not _csrf(request, csrf_token):
        return _redirect("/", error="Invalid CSRF token")
    response = RedirectResponse(logout_url(platform_settings()), status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


# ---------- pages ----------

@router.get("/", response_class=HTMLResponse)
def home(request: Request, msg: str | None = None, error: str | None = None, kiwix_down: str | None = None, denied: str | None = None):
    user, deny = _require_user(request)
    if deny:
        return deny
    ov = _overview()
    if request.query_params.get("kiwix") == "down" and not error:
        error = "The reader isn’t responding right now. Restart it from Settings → Reader."
    if denied and not error:
        error = "You don’t have access to the reader."
    updates = content.updates_available() if ov["installed"] else {}
    return _page(
        request,
        "home.html",
        {
            "user": user,
            "active": "library",
            "ov": ov,
            "updates": updates,
            "recent_failed": [_job_view(j) for j in db.recent_jobs(5) if j["status"] == "failed"],
            "setup_state": db.get_setting("setup_state"),
            "setup_error": db.get_setting("setup_error"),
            "drives": storage.list_drives() if ov["state"] == "not_installed" else [],
            "storage_dir": str(storage.content_dir()),
            "storage_kind": storage.kind(),
            "usage": storage.usage(),
            "kiwix_reader": kiwix.reader_url,
            "message": msg,
            "error": error,
        },
    )


@router.get("/browse", response_class=HTMLResponse)
def browse(request: Request, q: str = "", msg: str | None = None, error: str | None = None):
    user, deny = _require_user(request)
    if deny:
        return deny
    lang = _language()
    if catalog.cache_is_stale():
        content.refresh_catalog_in_background(lang)
    use = storage.usage()
    featured = []
    for f in FEATURED:
        name = f["name"].format(lang=lang)
        featured.append({**f, "catalog_name": name, "variants": _annotate(catalog.variants(catalog.entries_for(name)), use)})
    lookup_rows: list[dict] = []
    lookup_error = None
    search_results: list[dict] = []
    if q.strip() and not catalog.looks_like_name(q):
        # Words: search the whole Kiwix catalogue (what browse.library.kiwix.org shows).
        search_results, lookup_error = catalog.search(q, lang)
        for r in search_results:
            r["variants"] = _annotate(catalog.variants(r["entries"]), use)
        if not search_results and not lookup_error:
            lookup_error = f"Nothing in the Kiwix catalogue matches “{q.strip()}”."
    elif q.strip():
        name, flavour = catalog.parse_user_reference(q)
        entries, lookup_error = catalog.lookup(name)
        lookup_rows = _annotate(catalog.variants(entries), use)
        if flavour is not None:
            lookup_rows = [r for r in lookup_rows if (r["flavour"] or "") == flavour] or lookup_rows
        if not lookup_rows and not lookup_error:
            lookup_error = f"Nothing called “{name}” in the Kiwix catalogue."
    return _page(
        request,
        "browse.html",
        {
            "user": user,
            "active": "browse",
            "featured": featured,
            "language": lang,
            "language_label": dict(LANGUAGES)[lang],
            "fetched_at": catalog.fetched_at()[:16].replace("T", " "),
            "q": q,
            "lookup_rows": lookup_rows,
            "search_results": search_results,
            "lookup_error": lookup_error,
            "usage": use,
            "kiwix_installed": kiwix.status()["installed"],
            "message": msg,
            "error": error,
        },
    )


def _annotate(variants: list[dict], use: dict) -> list[dict]:
    """Add fits / installed / downloading flags for the Browse rows."""
    for v in variants:
        need = backup.install_need(int(v["size"] or 0))
        v["fits"] = need["ok"]
        v["doubled"] = need["doubled"]
        v["installed_row"] = content.find_installed(v["name"], v.get("flavour") or "")
        v["job"] = db.job_for_name(v["name"], v.get("flavour") or "")
    return variants


@router.post("/install")
def install(request: Request, csrf_token: str = Form(""), name: str = Form(...), flavour: str = Form(""), back: str = Form("/browse")):
    user, deny = _require_user(request, manage=True)
    if deny:
        return deny
    back = back if back.startswith("/") and not back.startswith("//") else "/browse"
    if not _csrf(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    if not kiwix.status()["installed"]:
        return _redirect("/", error="Set up the Library first")
    entry = next((e for e in catalog.variants(catalog.entries_for(name)) if (e["flavour"] or "") == flavour), None)
    if not entry:
        return _redirect(back, error="That title isn’t in the catalogue any more — refresh and try again")
    if content.find_installed(name, flavour):
        return _redirect(back, msg="Already installed")
    job_id, message = content.enqueue(entry)
    if job_id is None:
        return _redirect(back, error=message)
    return _redirect("/", msg=f"Installing {entry['title']} ({catalog.flavour_label(flavour)})")


@router.post("/content/{content_id}/update")
def update(request: Request, content_id: int, csrf_token: str = Form(""), in_place: str = Form("0")):
    user, deny = _require_user(request, manage=True)
    if deny:
        return deny
    if not _csrf(request, csrf_token):
        return _redirect("/", error="Invalid CSRF token")
    row = db.get_content(content_id)
    latest = content.updates_available().get(content_id)
    if not row or not latest:
        return _redirect("/", msg="Already up to date")
    job_id, message = content.enqueue(latest, replaces=row, in_place=in_place == "1")
    if message == "needs_in_place":
        return _redirect(f"/?replace={content_id}", error="Not enough room for both copies during the update.")
    if job_id is None:
        return _redirect("/", error=message)
    return _redirect("/", msg=f"Updating {row['title']}")


@router.post("/content/{content_id}/remove")
def remove(request: Request, content_id: int, csrf_token: str = Form("")):
    user, deny = _require_user(request, manage=True)
    if deny:
        return deny
    if not _csrf(request, csrf_token):
        return _redirect("/", error="Invalid CSRF token")
    ok, message = content.remove(content_id)
    return _redirect("/", msg=message) if ok else _redirect("/", error=message)


@router.post("/jobs/{job_id}/{action}")
def job_action(request: Request, job_id: int, action: str, csrf_token: str = Form("")):
    user, deny = _require_user(request, manage=True)
    if deny:
        return deny
    if not _csrf(request, csrf_token):
        return _redirect("/", error="Invalid CSRF token")
    job = db.get_job(job_id)
    if not job:
        return _redirect("/", error="Download not found")
    if action == "cancel" and job["status"] in db.JOB_ACTIVE:
        # Flag first: the worker may be picking this job up right now.
        db.update_job(job_id, cancel=1)
        if job["status"] == "queued":
            db.update_job(job_id, status="cancelled")
        return _redirect("/", msg="Cancelling")
    if action == "retry" and job["status"] in {"failed", "cancelled"}:
        db.update_job(job_id, status="queued", cancel=0, error="")
        downloads.wake()
        return _redirect("/", msg="Retrying")
    if action == "dismiss" and job["status"] in {"failed", "cancelled"}:
        db.update_job(job_id, status="dismissed")
        return _redirect("/")
    return _redirect("/")


# ---------- setup ----------

def _storage_choice(choice: str, custom_path: str, *, admin: bool) -> tuple[Path | None, str, str, str | None]:
    """(path, kind, uuid, error) for a storage form value: sd | drive:<uuid> | custom.

    "custom" runs the root helper on a typed path, so it's admin-only here and
    not just hidden in the form.
    """
    if choice == "sd":
        return DEFAULT_CONTENT_DIR, "sd", "", None
    if choice.startswith("drive:"):
        uuid = choice.split(":", 1)[1]
        drive = next((d for d in storage.list_drives() if d["uuid"] == uuid), None)
        if not drive:
            return None, "", "", "That drive isn’t connected any more"
        if drive["problem"]:
            return None, "", "", drive["problem"]
        ok, data = helper.run("mount-drive", uuid, timeout=60)
        if not ok:
            return None, "", "", data.get("error", "Couldn’t mount that drive")
        if not data.get("mountpoint"):
            return None, "", "", "The drive was mounted but didn’t report where"
        return Path(data["mountpoint"]) / DRIVE_SUBDIR, "drive", uuid, None
    if choice == "custom":
        if not admin:
            return None, "", "", "Only a StonePi admin can choose another folder"
        raw = custom_path.strip().rstrip("/")
        if not helper.simulated():
            # Same rule as the root helper: plain characters, a real folder below
            # /mnt/<drive>/, /media/<user>/…, /srv/… — never a bare mountpoint.
            if not SAFE_PATH.match(raw) or "/.." in raw or "//" in raw:
                return None, "", "", "Use a full path with letters, numbers, - _ . and / only"
            # Its parents are root's: the helper creates and hands it over first.
            ok, data = helper.run("prepare-folder", raw)
            if not ok:
                return None, "", "", data.get("error") or "Couldn’t prepare that folder"
        elif not raw.startswith("/") and not (len(raw) > 2 and raw[1] == ":"):
            return None, "", "", "Enter a full folder path"
        return Path(raw), "custom", "", None
    return None, "", "", "Choose where to keep library files"


def _run_setup(path: Path) -> None:
    with _setup_lock:
        db.set_setting("setup_state", "running")
        db.set_setting("setup_error", "")
        ok, data = helper.run("install-kiwix", timeout=900)
        if ok:
            ok, data = helper.run("set-storage", str(path))
        kiwix.forget_status()
        if ok:
            content.sync()
            db.set_setting("setup_state", "done")
        else:
            db.set_setting("setup_state", "failed")
            db.set_setting("setup_error", (data.get("error") or "Setup failed")[:400])


@router.post("/setup")
def setup(request: Request, csrf_token: str = Form(""), storage_choice: str = Form("sd"), custom_path: str = Form("")):
    user, deny = _require_user(request, admin=True)
    if deny:
        return deny
    if not _csrf(request, csrf_token):
        return _redirect("/", error="Invalid CSRF token")
    if db.get_setting("setup_state") == "running":
        return _redirect("/", msg="Setup is already running")
    path, kind, uuid, err = _storage_choice(storage_choice, custom_path, admin=_is_admin(user))
    if err:
        return _redirect("/", error=err)
    problems = storage.validate(path)
    if problems:
        return _redirect("/", error=problems[0])
    db.set_setting("storage_path", str(path))
    db.set_setting("storage_kind", kind)
    db.set_setting("storage_uuid", uuid)
    threading.Thread(target=_run_setup, args=(path,), daemon=True, name="library-setup").start()
    return _redirect("/", msg="Installing the Kiwix reader — this takes a minute or two")


# ---------- settings ----------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str = "", msg: str | None = None, error: str | None = None):
    user, deny = _require_user(request)
    if deny:
        return deny
    hub = tab.strip().lower() in {"", "hub"}
    tab = tab if tab in SETTINGS_TAB_KEYS else "storage"
    rows = db.list_content()
    installed = [r for r in rows if r["status"] == "installed"]
    status = kiwix.status(fresh=tab == "reader")
    return _page(
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
            "kiwix": status,
            "kiwix_reachable": kiwix.reachable() if status["active"] else False,
            "storage_dir": str(storage.content_dir()),
            "storage_kind": storage.kind(),
            "storage_kind_label": storage.KIND_LABELS.get(storage.kind(), ""),
            "storage_mount": storage.mount_info(),
            "usage": storage.usage(),
            "used": sum(int(r["size"]) for r in installed),
            "installed_count": len(installed),
            "drives": storage.list_drives(),
            "move_job": next((j for j in db.active_jobs() if j["kind"] == "move"), None),
            "busy": bool(db.active_jobs()),
            "capacity": backup.check(),
            "languages": LANGUAGES,
            "language": _language(),
            "max_mbps": db.get_setting("max_mbps"),
            "fetched_at": catalog.fetched_at()[:16].replace("T", " "),
            "simulated": helper.simulated(),
            "notifications_card_state": notifications_card_context(
                APP_ID,
                user if session_secret() else None,
                home_url=portal_home_url(request, env.public_origin).rstrip("/"),
            ),
            "message": msg,
            "error": error,
        },
    )


@router.post("/settings/storage")
def settings_storage(request: Request, csrf_token: str = Form(""), storage_choice: str = Form(""), custom_path: str = Form("")):
    user, deny = _require_user(request, admin=True)
    if deny:
        return deny
    back = "/settings?tab=storage"
    if not _csrf(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    if db.active_jobs():
        return _redirect(back, error="Wait for downloads to finish (or cancel them) before moving the library")
    path, kind, uuid, err = _storage_choice(storage_choice, custom_path, admin=_is_admin(user))
    if err:
        return _redirect(back, error=err)
    if path == storage.content_dir():
        return _redirect(back, msg="Library files are already there")
    installed = [r for r in db.list_content() if r["status"] == "installed"]
    total = sum(int(r["size"]) for r in installed)
    problems = storage.validate(path, need_bytes=total + (storage.margin_bytes(storage.usage(path)["total"]) if total else 0))
    if problems:
        return _redirect(back, error=problems[0])
    if not installed:
        ok, data = helper.run("set-storage", str(path))
        if not ok:
            return _redirect(back, error=data.get("error") or "Couldn’t switch the reader to that folder")
        db.set_setting("storage_path", str(path))
        db.set_setting("storage_kind", kind)
        db.set_setting("storage_uuid", uuid)
        content.sync()
        backup.recheck("changing storage")
        return _redirect(back, msg="Storage changed")
    db.set_setting("move_kind", kind)
    db.set_setting("move_uuid", uuid)
    db.add_job({"kind": "move", "name": "_move", "title": "Moving the library", "url": str(path), "file_name": ""})
    downloads.wake()
    return _redirect(back, msg=f"Moving {gb(total)} — the library stays readable until the switch")


@router.post("/settings/backup")
def settings_backup(request: Request, csrf_token: str = Form(""), include: str = Form("0")):
    user, deny = _require_user(request, admin=True)
    if deny:
        return deny
    back = "/settings?tab=backup"
    if not _csrf(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    ok, message = backup.set_enabled(include == "1")
    return _redirect(back, msg=message) if ok else _redirect(back, error=message)


@router.post("/settings/content")
def settings_content(request: Request, csrf_token: str = Form(""), language: str = Form("en"), max_mbps: str | None = Form(None)):
    user, deny = _require_user(request, manage=True)
    if deny:
        return deny
    back = "/settings?tab=content"
    if not _csrf(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    # The speed cap throttles the whole house's internet: admins only. Members
    # with Manage content still pick the catalogue language (their form has no
    # speed field; one posted anyway is refused, not silently dropped).
    if max_mbps is not None and not _is_admin(user):
        return HTMLResponse("Only a StonePi admin can change the download speed limit.", status_code=403)
    cap = None
    if max_mbps is not None:
        try:
            cap = max(0.0, float(max_mbps)) if max_mbps.strip() else 0.0
        except ValueError:
            return _redirect(back, error="Download speed must be a number")
    lang = language if language in dict(LANGUAGES) else "en"
    changed = lang != _language()
    db.set_setting("language", lang)
    if cap is not None:
        db.set_setting("max_mbps", f"{cap:g}" if cap else "")
    if changed:
        content.refresh_catalog_in_background(lang)
    return _redirect(back, msg="Saved")


@router.post("/catalog/refresh")
def catalog_refresh(request: Request, csrf_token: str = Form(""), back: str = Form("/browse")):
    user, deny = _require_user(request, manage=True)
    if deny:
        return deny
    back = back if back.startswith("/") and not back.startswith("//") else "/browse"
    if not _csrf(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    ok, message = content.refresh_catalog(_language())
    return _redirect(back, msg=message) if ok else _redirect(back, error=message)


@router.post("/settings/reader/{action}")
def settings_reader(request: Request, action: str, csrf_token: str = Form("")):
    user, deny = _require_user(request, manage=True, admin=action == "remove")
    if deny:
        return deny
    back = "/settings?tab=reader"
    if not _csrf(request, csrf_token):
        return _redirect(back, error="Invalid CSRF token")
    if action == "restart":
        ok, msg = content.sync()
        return _redirect(back, msg="Reader restarted") if ok else _redirect(back, error=msg)
    if action == "remove":
        if db.active_jobs():
            return _redirect(back, error="Cancel running downloads first")
        for row in db.list_content():
            content.remove(int(row["id"]), reason="removing the Library")
        if backup.include_enabled():
            helper.run("backup-content", "off")
        ok, data = helper.run("remove-kiwix", timeout=600)
        if storage.kind() == "drive":
            helper.run("unmount-drive", db.get_setting("storage_uuid"))
        for key in ("storage_path", "storage_kind", "storage_uuid", "setup_state", "setup_error", "backup_warning"):
            db.set_setting(key, "")
        kiwix.forget_status()
        if not ok:
            return _redirect(back, error=data.get("error", "Couldn’t remove Kiwix"))
        return _redirect("/", msg="Library content and the Kiwix reader were removed")
    return _redirect(back)
