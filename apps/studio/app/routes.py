from __future__ import annotations

import json
import mimetypes
import random
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, llm, model_settings, prompt_files, store, workspace
from app.config import BUILD_KINDS, ROOT_DIR, env


from stonepi_auth.brand import fonts_rev
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context, notifications_card_context
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()

_KIND_LABELS = {item["id"]: item["label"] for item in BUILD_KINDS}
_KINDS = {item["id"]: item for item in BUILD_KINDS}

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
    _sync_ownership(user)
    return user, None


def _user_label(user) -> str:
    return ((user.display_name or user.username) if user else "").strip()


def _sync_ownership(user) -> None:
    """Projects from before ownership belong to the admin; keep owner labels current."""
    if not user or not user.user_id:
        return
    if user.is_admin:
        store.claim_unowned(user.user_id, _user_label(user))
    store.refresh_owner_name(user.user_id, _user_label(user))


def _owns(user, project: dict) -> bool:
    if not _session_secret():  # solo run: one person, no accounts
        return True
    return bool(user and user.user_id and store.owner_id(project) == user.user_id)


def _can_act(user, project: dict) -> bool:
    """Owners act on their own projects; household admins can act on every project."""
    if _owns(user, project):
        return True
    return bool(user and user.is_admin)


def _project_for(user, project_id: str) -> dict | None:
    """The project if this user may act on it. Someone else's project reads as missing."""
    project = store.get_project(project_id)
    if project is None or not _can_act(user, project):
        return None
    return project


def _projects_home(user, project: dict) -> str:
    """Where to land after acting from the list: admins on another's project go back to All."""
    return f"{_prefix()}/projects" + ("" if _owns(user, project) else "?view=all")


def _ago(stamp: str | None) -> str:
    """ISO timestamp → "just now" / "3 hours ago" / "yesterday" / "12 Sep"."""
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - when).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = int(seconds // 86400)
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    label = f"{when.day} {when.strftime('%b')}"
    return label if when.year == datetime.now(timezone.utc).year else f"{label} {when.year}"


def _fileserve_public_base(request: Request) -> str:
    if env.fileserve_public_url.strip():
        return env.fileserve_public_url.strip().rstrip("/")
    if _prefix():  # mounted under the portal, e.g. /studio → FileServe lives at /files
        return portal_home_url(request, env.public_origin).rstrip("/") + "/files"
    return env.fileserve_url.rstrip("/")


def _greeting_name(user) -> str:
    """First word of the user's label ("Adam Stone" → "Adam") for friendly greetings."""
    label = ((user.display_name or user.username) if user else "").strip()
    return label.split()[0] if label else ""


def _csrf_response(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    ctx["csrf_token"] = csrf
    ctx.setdefault("public_origin", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("stonepi_home_url", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", _prefix())
    user = ctx.get("user")
    from stonepi_auth.session import factory_admin_warning

    ctx.setdefault("using_factory_admin", factory_admin_warning(user))
    ctx.setdefault(
        "alerts_bell_state",
        bell_context(
            user,
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=ctx["stonepi_home_url"],
            enabled=bool(_session_secret()),
        ),
    )
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


def _is_admin(user) -> bool:
    """Solo (no SSO secret) counts as admin; otherwise require household admin."""
    if not _session_secret():
        return True
    return bool(user and user.is_admin)


def _studio_settings_groups(is_admin: bool) -> tuple:
    build = (
        ("model", "Model", "Which AI service Studio uses.", "model"),
        ("prompts", "Prompts", "Guidelines injected into each turn.", "prompts"),
    )
    app_rows = (
        ("notifications", "Notifications", "Alerts when a build is published.", "bell"),
        ("about", "About", "Version and project links.", "about"),
    )
    groups = []
    if is_admin:
        groups.append(("build", "Build", build))
    groups.append(("app", "App", app_rows))
    return tuple(groups)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user, denied = _require_user(request)
    if denied:
        return denied
    is_admin = _is_admin(user)
    raw_tab = (request.query_params.get("tab") or "").strip().lower()
    settings_hub = raw_tab == ""
    tab = "about" if settings_hub else raw_tab
    if tab not in {"about", "model", "prompts", "notifications"}:
        tab = "about"
        settings_hub = False
    if tab in {"model", "prompts"} and not is_admin:
        return RedirectResponse(f"{_prefix()}/settings?tab=about", status_code=303)

    prompt_id = prompt_files.normalize_prompt_id(request.query_params.get("prompt"))
    prompt_rows = prompt_files.list_prompts() if is_admin else []
    prompt_body = prompt_files.read_prompt(prompt_id) if tab == "prompts" and is_admin else ""
    prompt_def = prompt_files.get_prompt_def(prompt_id) if tab == "prompts" and is_admin else None

    home = portal_home_url(request, env.public_origin).rstrip("/")
    ledes = {
        "about": "App name, description, GitHub, and the version running here.",
        "prompts": "Edit the markdown guidelines Studio injects into every LLM turn.",
        "model": "Choose which AI service and model Studio uses for every chat and build.",
        "notifications": "Alerts when a game or app is published to FileServe.",
    }
    model_ctx = {}
    if tab == "model" and is_admin:
        active = llm.active_provider()
        anthropic_model = model_settings.anthropic_model()
        model_ctx = {
            "provider_pref": model_settings.provider_pref(),
            "key_status": llm.key_status(),
            "active_provider": {"id": active["id"], "label": active["label"], "model": active["model"]},
            "model_choices": model_settings.MODEL_CHOICES,
            "anthropic_model": anthropic_model,
            "anthropic_custom": model_settings.is_custom(anthropic_model),
            "openai_model": model_settings.openai_model(),
            "openai_custom": model_settings.is_custom_openai(model_settings.openai_model()),
            "openai_choices": model_settings.OPENAI_CHOICES,
        }
    return _csrf_response(
        request,
        "settings.html",
        {
            "user": user,
            "active": "settings",
            "settings_tab": tab,
            "settings_hub": settings_hub,
            "settings_groups": _studio_settings_groups(is_admin),
            "is_admin": is_admin,
            "settings_lede": "Chat-builds small sites and games for FileServe." if settings_hub else ledes.get(tab, ledes["about"]),
            "app_name": "Studio",
            "app_version": __version__,
            "app_github_user": __github_user__,
            "app_github": __github__,
            "prompt_id": prompt_id,
            "prompt_rows": prompt_rows,
            "prompt_body": prompt_body,
            "prompt_def": prompt_def,
            "prompt_customized": prompt_files.is_customized(prompt_id) if tab == "prompts" and is_admin else False,
            "notifications_card_state": notifications_card_context(
                "studio",
                user if _session_secret() else None,
                home_url=home,
            ),
            "stonepi_home_url": home,
            **model_ctx,
            "error": request.query_params.get("err"),
            "message": request.query_params.get("msg"),
        },
    )


@router.post("/settings/model")
def settings_model_save(
    request: Request,
    provider: str = Form("auto"),
    anthropic_model: str = Form(""),
    anthropic_custom: str = Form(""),
    openai_model: str = Form(""),
    openai_custom: str = Form(""),
    action: str = Form("save"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _is_admin(user):
        return HTMLResponse("Admins only.", status_code=403)
    back = f"{_prefix()}/settings?tab=model"
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(f"{back}&err={quote('Form expired')}", status_code=303)
    try:
        if (action or "save").strip().lower() == "reset":
            model_settings.reset()
            msg = "Back to the defaults"
        else:
            chosen = anthropic_custom.strip() if anthropic_model == "custom" else anthropic_model
            chosen_openai = openai_custom.strip() if openai_model == "custom" else openai_model
            model_settings.save(provider=provider.strip(), anthropic=chosen, openai=chosen_openai)
            msg = "Model saved. The next message uses it."
    except ValueError as exc:
        return RedirectResponse(f"{back}&err={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"{back}&msg={quote(msg)}", status_code=303)


@router.post("/settings/prompts")
def settings_prompts_save(
    request: Request,
    prompt: str = Form(""),
    body: str = Form(""),
    action: str = Form("save"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _is_admin(user):
        return HTMLResponse("Admins only.", status_code=403)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(
            f"{_prefix()}/settings?tab=prompts&prompt={prompt_files.normalize_prompt_id(prompt)}&err={quote('Form expired')}",
            status_code=303,
        )
    prompt_id = prompt_files.normalize_prompt_id(prompt)
    try:
        if (action or "save").strip().lower() == "reset":
            prompt_files.reset_prompt(prompt_id)
            msg = "Restored packaged default"
        else:
            prompt_files.write_prompt(prompt_id, body)
            msg = "Prompt saved"
    except ValueError as exc:
        return RedirectResponse(
            f"{_prefix()}/settings?tab=prompts&prompt={prompt_id}&err={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        f"{_prefix()}/settings?tab=prompts&prompt={prompt_id}&msg={quote(msg)}",
        status_code=303,
    )


@router.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    user, denied = _require_user(request)
    if denied:
        return denied
    everything = store.list_projects()
    is_admin = bool(user and user.is_admin)
    view = "all" if is_admin and request.query_params.get("view") == "all" else "mine"
    owner_filter = (request.query_params.get("owner") or "").strip() if view == "all" else ""
    if view == "all":
        shown = [p for p in everything if not owner_filter or store.owner_id(p) == owner_filter]
    else:
        shown = [p for p in everything if _owns(user, p)]
    owners: dict[str, str] = {}
    for p in everything:
        if store.owner_id(p):
            owners.setdefault(store.owner_id(p), str(p.get("owner_name") or "Someone"))
    return _csrf_response(
        request,
        "projects.html",
        {
            "user": user,
            "active": "projects",
            "projects": [
                {
                    **p,
                    "ago": _ago(p.get("updated_at")),
                    "chats": sum(1 for m in p.get("messages") or [] if m.get("role") == "user"),
                    "is_mine": _owns(user, p),
                }
                for p in shown
            ],
            "view": view,
            "is_admin": is_admin,
            "owner_filter": owner_filter,
            "owner_options": sorted(owners.items(), key=lambda kv: kv[1].lower()),
            "kind_labels": _KIND_LABELS,
            "error": request.query_params.get("err"),
            "message": request.query_params.get("msg"),
        },
    )


@router.post("/create")
def create_project(
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
    project = store.create_project(
        name, kind=kind, owner=(user.user_id if user else ""), owner_name=_user_label(user)
    )
    return RedirectResponse(f"{_prefix()}/p/{project['id']}", status_code=303)


def _with(url: str, query: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{query}"


@router.post("/p/{project_id}/delete")
def delete_project(request: Request, project_id: str, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(f"{_prefix()}/projects?err=Form+expired", status_code=303)
    project = _project_for(user, project_id)
    if project is None:
        return RedirectResponse(f"{_prefix()}/projects?err=Project+not+found", status_code=303)
    home = _projects_home(user, project)
    store.delete_project(project_id)
    return RedirectResponse(_with(home, "msg=Project+deleted"), status_code=303)


@router.post("/p/{project_id}/rename")
def rename_project(
    request: Request,
    project_id: str,
    name: str = Form(""),
    back: str = Form(""),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    project = _project_for(user, project_id)
    if project is None:
        return RedirectResponse(f"{_prefix()}/projects?err=Project+not+found", status_code=303)
    # Only two places rename from; never redirect to a caller-supplied URL.
    dest = _projects_home(user, project) if back == "projects" else f"{_prefix()}/p/{project_id}"
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(_with(dest, "err=Form+expired"), status_code=303)
    if not store.clean_name(name):
        return RedirectResponse(_with(dest, f"err={quote('Give your project a name first.')}"), status_code=303)
    store.rename_project(project_id, name)
    return RedirectResponse(_with(dest, "msg=Renamed"), status_code=303)


@router.get("/p/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: str):
    user, denied = _require_user(request)
    if denied:
        return denied
    project = _project_for(user, project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    files = workspace.list_files(store.workspace_root(project_id))
    can_llm = bool(user and (user.is_admin or user.has_capability("studio", "can_use_llm")))
    can_publish = bool(user and (user.is_admin or user.has_capability("studio", "can_publish")))
    kind = project.get("kind") or "spa"
    messages = project.get("messages") or []
    has_described = any(m.get("role") == "user" for m in messages)
    has_built = bool(project.get("built"))
    built_at = str(project.get("built_at") or "")
    # Chat turns since the last build = changes waiting to be built (drives the Rebuild prompt).
    pending_changes = sum(
        1 for m in messages if m.get("role") == "user" and (not has_built or str(m.get("at") or "") > built_at)
    )
    return _csrf_response(
        request,
        "project.html",
        {
            "user": user,
            "active": "projects",
            "project": project,
            "is_mine": _owns(user, project),
            "files": files,
            "can_llm": can_llm,
            "can_publish": can_publish,
            "kind_label": _KIND_LABELS.get(kind, "Project"),
            "kind_info": _KINDS.get(kind, _KINDS["spa"]),
            "greeting_name": _greeting_name(user),
            # A fresh handful each visit keeps the starting screen inspiring rather than static.
            "ideas": random.sample(_KINDS.get(kind, _KINDS["spa"])["ideas"], 3),
            "preview_url": f"/p/{project_id}/preview/",
            "has_described": has_described,
            "has_built": has_built,
            "pending_changes": pending_changes,
            "published_url": (
                _fileserve_public_base(request) + project["public_path"] if project.get("public_path") else ""
            ),
            # Rebuilt after the last share → offer "Share the update".
            "changed_since_publish": bool(
                project.get("published_at") and built_at and built_at > str(project.get("published_at"))
            ),
            "just_published": request.query_params.get("msg") == "Published",
            "kind_noun": {"game": "game", "guide": "story"}.get(kind, "app"),
            "error": request.query_params.get("err"),
            "message": None if request.query_params.get("msg") == "Published" else request.query_params.get("msg"),
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
    project = _project_for(user, project_id)
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
    kind = str(project.get("kind") or "spa")
    return StreamingResponse(
        _chat_events(
            project_id, root, history, files, kind=kind, mode=mode, needs_name=bool(project.get("auto_name"))
        ),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _event(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _chat_events(
    project_id: str,
    root: Path,
    history: list[dict],
    files: list[str],
    *,
    kind: str,
    mode: str,
    needs_name: bool = False,
):
    """NDJSON progress stream: status → prose/file progress → done (or error)."""
    yield _event({"type": "status", "stage": "received"})
    sources = workspace.read_text_files(root)
    try:
        chunks = llm.stream_chat(history, files, kind=kind, intent=mode, sources=sources, needs_name=needs_name)
        yield _event({"type": "status", "stage": "thinking"})
        parts: list[str] = []
        stop = "end_turn"
        last_scan = 0.0
        sent_prose = ""
        for what, chunk in chunks:
            if what == "stop":
                stop = chunk
                continue
            parts.append(chunk)
            now = time.monotonic()
            if now - last_scan < 0.3:
                continue
            last_scan = now
            progress = _progress(parts)
            if progress["prose"] != sent_prose:
                sent_prose = progress["prose"]
                yield _event({"type": "prose", "text": sent_prose})
            if progress.get("writing") or progress["done"]:
                yield _event({"type": "files", **{k: v for k, v in progress.items() if k != "prose"}})
    except Exception as exc:  # network, auth, rate limits — surface them in the chat
        store.append_message(project_id, "assistant", f"Something went wrong talking to the model: {exc}", note="error")
        yield _event({"type": "error", "message": str(exc)})
        return

    reply = "".join(parts)
    summary = workspace.strip_file_blocks(reply)
    applied: list[str] = []
    issues: list[str] = []
    note = ""
    renamed = None
    if mode == "build":
        yield _event({"type": "status", "stage": "checking"})
        mapping, unclosed = workspace.parse_file_blocks(reply)
        if not mapping and not unclosed:
            mapping = workspace.parse_file_map(reply)  # older replies / custom prompts
        if stop == "refusal":
            issues.append("The AI said no to that one. Try describing it a different way.")
            mapping = {}
        elif unclosed or stop == "max_tokens":
            cut = unclosed[0] if unclosed else "the reply"
            issues.append(
                f"The model ran out of room while writing {cut}, so nothing was changed. "
                "Try Build again, or ask for a smaller change."
            )
            mapping = {}
        elif not mapping:
            issues.append("The model answered without writing any files, so the preview did not change.")
        if mapping:
            applied = workspace.apply_file_map(root, mapping)
            store.mark_built(project_id)
            issues.extend(workspace.check_site(root))
            if needs_name and "index.html" in applied:
                renamed = store.adopt_name(project_id, workspace.page_title(root))
        note = ("Updated " + ", ".join(applied)) if applied else "No files changed"
    if stop == "refusal" and not summary:
        summary = "Hmm, I can't help with that one. Could we try a different idea? 🙂"
    if not summary:
        summary = "Build finished." if applied else ("Done." if mode == "clarify" else "Build did not change any files.")
    store.append_message(project_id, "assistant", summary, note=note)
    yield _event(
        {
            "type": "done",
            "message": summary,
            "note": note,
            "intent": mode,
            "applied": applied,
            "issues": issues,
            "built": bool((store.get_project(project_id) or {}).get("built")),
            "files": workspace.list_files(root),
            "renamed": renamed,
        }
    )


def _progress(parts: list[str]) -> dict:
    text = "".join(parts)
    prose = workspace.strip_file_blocks(text)
    tail = prose.rfind("<")
    if tail >= 0 and len(prose) - tail < 40 and ">" not in prose[tail:]:
        prose = prose[:tail].rstrip()  # hold back a half-streamed <studio-file tag
    opened = [m for m in workspace.FILE_OPEN_RE.finditer(text)]
    closed = text.lower().count(workspace.FILE_CLOSE)
    done = [m.group(1) for m in opened[:closed]]
    out: dict = {"prose": prose, "done": done}
    if len(opened) > closed:
        last = opened[-1]
        out["writing"] = last.group(1)
        out["chars"] = len(text) - last.end()
    return out


@router.get("/p/{project_id}/preview/")
@router.get("/p/{project_id}/preview/{asset_path:path}")
def project_preview(request: Request, project_id: str, asset_path: str = ""):
    user, denied = _require_user(request)
    if denied:
        return denied
    if _project_for(user, project_id) is None:
        raise HTTPException(404, "Project not found.")
    root = store.workspace_root(project_id)
    rel =(asset_path or "index.html").strip("/") or "index.html"
    safe = workspace.safe_rel(rel)
    if safe is None:
        raise HTTPException(400, "Invalid path.")
    path = root / safe
    if not path.is_file():
        raise HTTPException(404, "Not found.")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # Rebuilds rewrite app.js / style.css in place; a cached copy made the preview
    # show stale or mismatched files.
    headers = {"Cache-Control": "no-store"}
    if safe.lower().endswith((".html", ".htm")):
        html = path.read_text(encoding="utf-8", errors="replace")
        return HTMLResponse(_inject_preview_probe(html), headers=headers)
    return FileResponse(path, media_type=media, headers=headers)


# Preview-only (never published): reports load/runtime errors to the Studio page so a
# broken build is visible before Publish instead of a silent blank frame.
_PREVIEW_PROBE = """<script>(function(){
var P=window.parent;if(!P||P===window)return;
function send(t,d){try{P.postMessage(Object.assign({source:"studio-preview",type:t},d||{}),"*")}catch(e){}}
function short(u){return String(u||"").split("?")[0].split("/").pop()}
window.addEventListener("error",function(e){
 var t=e.target;
 if(t&&t!==window&&(t.src||t.href)){send("error",{message:"Could not load "+short(t.src||t.href)});return}
 send("error",{message:String(e.message||"Script error"),file:short(e.filename),line:e.lineno||0,col:e.colno||0})
},true);
window.addEventListener("unhandledrejection",function(e){var r=e.reason;send("error",{message:"Unhandled promise rejection: "+(r&&r.message||r)})});
window.addEventListener("load",function(){setTimeout(function(){
 var b=document.body,c=document.querySelector("canvas");
 send("ready",{empty:!b||(!b.innerText.trim()&&!c&&!document.querySelector("img,svg,video")),w:innerWidth,h:innerHeight})
},600)});
})();</script>"""


def _inject_preview_probe(html: str) -> str:
    lower = html.lower()
    head = lower.find("<head")
    if head >= 0:
        close = lower.find(">", head)
        if close >= 0:
            return html[: close + 1] + _PREVIEW_PROBE + html[close + 1 :]
    return _PREVIEW_PROBE + html


@router.post("/p/{project_id}/publish")
def project_publish(
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
    project = _project_for(user, project_id)
    if project is None:
        raise HTTPException(404, "Project not found.")
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return RedirectResponse(f"{_prefix()}/p/{project_id}?err=Form+expired", status_code=303)
    root = store.workspace_root(project_id)
    try:
        payload = workspace.workspace_zip(root)
    except ValueError as exc:
        return RedirectResponse(f"{_prefix()}/p/{project_id}?err={quote(str(exc))}", status_code=303)
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
        content_type = resp.headers.get("content-type", "")
        body = resp.json() if content_type.startswith("application/json") else {}
    except Exception:
        return RedirectResponse(
            f"{_prefix()}/p/{project_id}?err={quote('Publish failed — FileServe did not respond.')}",
            status_code=303,
        )
    if resp.status_code >= 400 or not body.get("ok", resp.status_code < 300):
        msg = (body.get("message") or "").strip()
        if not msg:
            if resp.status_code in {401, 403}:
                msg = (
                    "FileServe access required — ask an admin to enable FileServe "
                    "for your account (Studio Publish needs both)."
                )
            else:
                msg = "Publish failed"
        return RedirectResponse(f"{_prefix()}/p/{project_id}?err={quote(msg)}", status_code=303)
    new_id = body.get("page_id")
    was_update = bool(page_id)
    if new_id is not None:
        store.set_fileserve_page_id(
            project_id, int(new_id), public_path=str(body.get("public_path") or ""), description=description.strip()
        )
    try:
        from app.services import notify as notify_service

        public_path = str(body.get("public_path") or project.get("public_path") or "")
        public_url = None
        if public_path:
            base = _fileserve_public_base(request)
            public_url = f"{base}{public_path}" if public_path.startswith("/") else f"{base}/{public_path}"
        notify_service.emit_site_published(
            title=str(data.get("title") or project.get("name") or "Site"),
            kind=str(project.get("kind") or "spa"),
            project_id=project_id,
            public_url=public_url,
            is_update=was_update,
        )
    except Exception:
        pass
    return RedirectResponse(f"{_prefix()}/p/{project_id}?msg=Published", status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    return RedirectResponse(logout_url(_settings(), f"{_prefix()}/"), status_code=303)
