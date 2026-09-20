from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import llm, store, workspace
from app.config import BUILD_KINDS, ROOT_DIR, env
from stonepi_auth import login_url, logout_url
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
router = APIRouter()

_KIND_LABELS = {item["id"]: item["label"] for item in BUILD_KINDS}

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
        app_id="studio",
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
    if user and not user.is_admin and not user.can_access("studio"):
        return None, HTMLResponse("No access to Studio.", status_code=403)
    if capability and user and not user.is_admin and not user.has_capability("studio", capability):
        return None, HTMLResponse(f"Missing capability: {capability}", status_code=403)
    return user, None


def _csrf_response(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    ctx["csrf_token"] = csrf
    ctx.setdefault("public_origin", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", _prefix())
    response = templates.TemplateResponse(request, name, ctx)
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "studio"}


@router.get("/api/display")
def api_display():
    """Compact household stats for StonePi → TRMNL overview push."""
    projects = store.list_projects()
    built = sum(1 for item in projects if item.get("built"))
    pending = max(0, len(projects) - built)
    if built and not pending:
        detail = "Build passing"
    elif pending and built:
        detail = f"Build passing · {pending} queued"
    elif pending:
        detail = f"{pending} queued"
    else:
        detail = "—"
    return JSONResponse(
        {
            "ok": True,
            "projects": len(projects),
            "built": built,
            "queued": pending,
            "detail": detail,
        }
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user, denied = _require_user(request)
    if denied:
        return denied
    return _csrf_response(
        request,
        "home.html",
        {
            "user": user,
            "active": "create",
            "build_kinds": BUILD_KINDS,
            "error": request.query_params.get("err"),
            "message": request.query_params.get("msg"),
        },
    )


@router.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    user, denied = _require_user(request)
    if denied:
        return denied
    return _csrf_response(
        request,
        "projects.html",
        {
            "user": user,
            "active": "projects",
            "projects": store.list_projects(),
            "kind_labels": _KIND_LABELS,
            "error": request.query_params.get("err"),
            "message": request.query_params.get("msg"),
        },
    )


@router.post("/create")
async def create_project(
    request: Request,
    name: str = Form(""),
    kind: str = Form("spa"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(f"{_prefix()}/?err=Form+expired", status_code=303)
    project = store.create_project(name, kind=kind)
    return RedirectResponse(f"{_prefix()}/p/{project['id']}", status_code=303)


@router.post("/p/{project_id}/delete")
async def delete_project(request: Request, project_id: str, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(f"{_prefix()}/projects?err=Form+expired", status_code=303)
    if not store.delete_project(project_id):
        return RedirectResponse(f"{_prefix()}/projects?err=Project+not+found", status_code=303)
    return RedirectResponse(f"{_prefix()}/projects?msg=Project+deleted", status_code=303)


@router.get("/p/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: str):
    user, denied = _require_user(request)
    if denied:
        return denied
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    files = workspace.list_files(store.workspace_root(project_id))
    can_llm = bool(user and (user.is_admin or user.has_capability("studio", "can_use_llm")))
    can_publish = bool(user and (user.is_admin or user.has_capability("studio", "can_publish")))
    kind = project.get("kind") or "spa"
    messages = project.get("messages") or []
    has_described = any(m.get("role") == "user" for m in messages)
    has_built = bool(project.get("built"))
    return _csrf_response(
        request,
        "project.html",
        {
            "user": user,
            "active": "projects",
            "project": project,
            "files": files,
            "can_llm": can_llm,
            "can_publish": can_publish,
            "kind_label": _KIND_LABELS.get(kind, "Project"),
            "preview_url": f"/p/{project_id}/preview/",
            "has_described": has_described,
            "has_built": has_built,
            "error": request.query_params.get("err"),
            "message": request.query_params.get("msg"),
        },
    )


@router.post("/p/{project_id}/chat")
async def project_chat(
    request: Request,
    project_id: str,
    message: str = Form(""),
    intent: str = Form("clarify"),
    csrf_token: str = Form(""),
):
    user = _user(request)
    if _session_secret() and user is None:
        return JSONResponse({"ok": False, "message": "Not signed in."}, status_code=401)
    if user and not user.is_admin and not user.can_access("studio"):
        return JSONResponse({"ok": False, "message": "No access."}, status_code=403)
    if user and not user.is_admin and not user.has_capability("studio", "can_use_llm"):
        return JSONResponse({"ok": False, "message": "LLM not enabled for your account."}, status_code=403)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return JSONResponse({"ok": False, "message": "Form expired."}, status_code=403)
    project = store.get_project(project_id)
    if project is None:
        return JSONResponse({"ok": False, "message": "Project not found."}, status_code=404)
    mode = (intent or "clarify").strip().lower()
    if mode not in {"clarify", "build"}:
        mode = "clarify"
    text = (message or "").strip()
    if not text and mode == "build":
        text = "Build the site from our conversation so far."
    if not text:
        return JSONResponse({"ok": False, "message": "Enter a message."}, status_code=400)
    root = store.workspace_root(project_id)
    files = workspace.list_files(root)
    history = [{"role": m["role"], "content": m["content"]} for m in project.get("messages") or []]
    history.append({"role": "user", "content": text})
    store.append_message(project_id, "user", text)
    try:
        reply = llm.chat(history, files, kind=str(project.get("kind") or "spa"), intent=mode)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=502)
    mapping = workspace.parse_file_map(reply) if mode == "build" else None
    applied = workspace.apply_file_map(root, mapping) if mapping else []
    if mode == "build" and applied:
        store.mark_built(project_id)
    summary = llm.assistant_summary(reply)
    store.append_message(project_id, "assistant", summary)
    return JSONResponse(
        {
            "ok": True,
            "message": summary,
            "intent": mode,
            "applied": applied,
            "built": bool((store.get_project(project_id) or {}).get("built")),
            "files": workspace.list_files(root),
        }
    )


@router.get("/p/{project_id}/preview/")
@router.get("/p/{project_id}/preview/{asset_path:path}")
def project_preview(request: Request, project_id: str, asset_path: str = ""):
    user, denied = _require_user(request)
    if denied:
        return denied
    if store.get_project(project_id) is None:
        raise HTTPException(404, "Project not found.")
    root = store.workspace_root(project_id)
    rel = (asset_path or "index.html").strip("/") or "index.html"
    safe = workspace.safe_rel(rel)
    if safe is None:
        raise HTTPException(400, "Invalid path.")
    path = root / safe
    if not path.is_file():
        raise HTTPException(404, "Not found.")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media)


@router.post("/p/{project_id}/publish")
async def project_publish(
    request: Request,
    project_id: str,
    title: str = Form(""),
    slug: str = Form(""),
    description: str = Form(""),
    protect: str = Form("0"),
    protected: str = Form("0"),
    page_username: str = Form(""),
    page_password: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    expiry: str = Form("none"),
    expiry_date: str = Form(""),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request, capability="can_publish")
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(f"{_prefix()}/p/{project_id}?err=Form+expired", status_code=303)
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    root = store.workspace_root(project_id)
    try:
        payload = workspace.workspace_zip(root)
    except ValueError as exc:
        return RedirectResponse(f"{_prefix()}/p/{project_id}?err={exc}", status_code=303)
    cookie_header = request.headers.get("cookie", "")
    csrf = csrf_from_request(request.cookies)
    protect_on = protect in {"1", "true", "on"} or protected in {"1", "true", "on"}
    data = {
        "title": (title or project.get("name") or "Site").strip(),
        "slug": slug.strip(),
        "description": description.strip(),
        "protect": "1" if protect_on else "0",
        "protected": "1" if protect_on else "0",
        "page_username": (page_username or username).strip(),
        "page_password": page_password or password,
        "username": (page_username or username).strip(),
        "password": page_password or password,
        "expiry": expiry.strip() or "none",
        "expiry_date": expiry_date.strip(),
        "csrf_token": csrf,
    }
    page_id = project.get("fileserve_page_id")
    if page_id:
        data["page_id"] = str(page_id)
    url = f"{env.fileserve_url.rstrip('/')}/api/studio/publish"
    headers = {"Accept": "application/json", "X-StonePi-CSRF": csrf}
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                url,
                data=data,
                files={"file": ("site.zip", payload, "application/zip")},
                headers=headers,
            )
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        return RedirectResponse(f"{_prefix()}/p/{project_id}?err=Publish+failed", status_code=303)
    if resp.status_code >= 400 or not body.get("ok", resp.status_code < 300):
        msg = body.get("message") or "Publish failed"
        return RedirectResponse(f"{_prefix()}/p/{project_id}?err={msg}", status_code=303)
    new_id = body.get("page_id")
    if new_id is not None:
        store.set_fileserve_page_id(project_id, int(new_id))
    return RedirectResponse(f"{_prefix()}/p/{project_id}?msg=Published", status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    return RedirectResponse(logout_url(_settings(), f"{_prefix()}/"), status_code=303)
