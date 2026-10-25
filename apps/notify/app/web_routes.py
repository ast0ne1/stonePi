"""Displays → Web pages (F14): pages any display can show as a captured image.

- ``/displays/web/*``: add, edit and delete pages; preview a capture.
- ``/displays/{id}/web``: a TRMNL "Web page" Display's settings, and its e-ink preview.
- ``/api/internal/web/{id}.png``: signed, for the Car Thing service (colour snapshot).
- ``/api/internal/web/changed``: signed, an app's display page changed (Ripple); see app_pages.
- ``/api/internal/web/app-displays/{app}``: signed, that app's Displays and last pushes.

Captures run in a worker thread (Chromium takes a few seconds) and are shared through
``stonepi_display.webcapture``, so the preview, a push and the Car Thing reuse one run.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote

import stonepi_display
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request, set_csrf_cookie
from stonepi_auth.http import request_is_https
from stonepi_display import carthing, webcapture, webimage
from stonepi_display.webpages import MAX_PAGES, clean_page_id, normalize_page

logger = logging.getLogger("notify.web")
router = APIRouter()

LCD_MAX = (1920, 1440)
# The Car Thing asks at most this often; anything younger is served from the cache.
MIN_MAX_AGE = 30.0
NO_STORE = {"Cache-Control": "no-store"}


def _admin_json(request: Request, *, csrf: bool = False) -> JSONResponse | None:
    from app.carthing_routes import _auth_optional
    from app.routes import _api_csrf_ok, _session_secret, _user

    # Each preview starts Chromium, so fail closed on a Pi with no session secret.
    if not _session_secret():
        if not _auth_optional():
            return JSONResponse({"ok": False, "message": "Admins only."}, status_code=403)
    elif (user := _user(request)) is None or not user.is_admin:
        return JSONResponse({"ok": False, "message": "Admins only."}, status_code=403)
    if csrf and not _api_csrf_ok(request):
        return JSONResponse({"ok": False, "message": "Form expired — reload the page."}, status_code=403)
    return None


def page_usage(page_id: str) -> list[str]:
    """Where a page is shown: TRMNL Displays and Car Thing configs (pages, screensaver)."""
    used: list[str] = []
    for display in stonepi_display.load_displays():
        if display.get("kind") == "web" and (display.get("web") or {}).get("page_id") == page_id:
            used.append(f"TRMNL: {display['name']}")
    widget_id = f"{carthing.WEB_WIDGET_PREFIX}{page_id}"
    for row in carthing.list_configs():
        cfg = carthing.get_config(row["id"]) or {}
        if widget_id in carthing.page_widget_ids(cfg):
            used.append(f"Car Thing: {cfg.get('name') or row['id']} (page)")
        saver = (cfg.get("idle") or {}).get("saver") or {}
        if saver.get("style") == "web" and (saver.get("web") or {}).get("page_id") == page_id:
            used.append(f"Car Thing: {cfg.get('name') or row['id']} (screensaver)")
    return used


def web_pages_summary() -> list[dict[str, Any]]:
    """For the Displays list: each page with where it's used."""
    return [{**page, "used": page_usage(page["id"])} for page in stonepi_display.load_pages()]


def _png(data: bytes, extra: dict[str, str] | None = None) -> Response:
    return Response(content=data, media_type="image/png", headers={**NO_STORE, **(extra or {})})


def _size(value: Any, default: int, hi: int) -> int:
    try:
        return max(16, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# ── Web pages ────────────────────────────────────────────────────────────────


@router.post("/displays/web/create")
async def web_create(request: Request):
    from app.routes import _guard

    user, denied = _guard(request, path="/displays", active="displays", template="displays.html")
    if denied is not None:
        return denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse("/displays?err=Form+expired#web-pages", status_code=303)
    name = str(form.get("name") or "").strip()[:60] or "Web page"
    try:
        page = stonepi_display.create_page({"name": name, "url": str(form.get("url") or "")})
    except ValueError as exc:
        return RedirectResponse(f"/displays?err={quote(str(exc))}#web-pages", status_code=303)
    return RedirectResponse(f"/displays/web/{page['id']}?msg=Web+page+added", status_code=303)


@router.get("/displays/web/{page_id}", response_class=HTMLResponse)
def web_page(request: Request, page_id: str):
    from app.routes import _guard, _page_ctx, templates

    user, denied = _guard(request, path=f"/displays/web/{page_id}", active="displays", template="web_page.html")
    if denied is not None:
        return denied
    page = stonepi_display.get_page(page_id)
    if not page:
        return RedirectResponse("/displays?err=Web+page+not+found#web-pages", status_code=303)
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "web_page.html",
        _page_ctx(
            request,
            active="displays",
            user=user,
            csrf=csrf,
            extra={
                "page": page,
                "presets": stonepi_display.CSS_PRESETS,
                "used": page_usage(page["id"]),
                "last_error": webcapture.last_error(page["id"]),
            },
        ),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.post("/displays/web/{page_id}/save")
async def web_save(request: Request, page_id: str):
    from app.routes import _guard

    back = f"/displays/web/{quote(page_id)}"
    user, denied = _guard(request, path=back, active="displays", template="web_page.html")
    if denied is not None:
        return denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse(f"{back}?err=Form+expired", status_code=303)
    page = stonepi_display.get_page(page_id)
    if not page:
        return RedirectResponse("/displays?err=Web+page+not+found#web-pages", status_code=303)
    try:
        stonepi_display.upsert_page(
            {
                **page,
                "name": str(form.get("name") or page["name"]),
                "url": str(form.get("url") or ""),
                "preset": str(form.get("preset") or page["preset"]),
                "hide": str(form.get("hide") or ""),
                "css": str(form.get("css") or ""),
                "settle_s": form.get("settle_s"),
                "scale": form.get("scale"),
            }
        )
    except ValueError as exc:
        return RedirectResponse(f"{back}?err={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"{back}?msg=Saved", status_code=303)


@router.post("/displays/web/{page_id}/delete")
async def web_delete(request: Request, page_id: str):
    from app.routes import _guard

    user, denied = _guard(request, path="/displays", active="displays", template="displays.html")
    if denied is not None:
        return denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse("/displays?err=Form+expired#web-pages", status_code=303)
    stonepi_display.delete_page(page_id)
    return RedirectResponse("/displays?msg=Web+page+deleted#web-pages", status_code=303)


@router.get("/api/web/{page_id}/preview.png")
async def web_preview(request: Request, page_id: str, w: int = 800, h: int = 480, fresh: str = ""):
    """Colour capture for the page editor (a minute's cache unless ``fresh``)."""
    denied = _admin_json(request)
    if denied is not None:
        return denied
    page = stonepi_display.get_page(page_id)
    if not page or not page.get("url"):
        return JSONResponse({"ok": False, "message": "Save an address first."}, status_code=404)
    width, height = _size(w, 800, LCD_MAX[0]), _size(h, 480, LCD_MAX[1])
    try:
        raw = await run_in_threadpool(webcapture.capture, page, width, height, max_age=0.0 if fresh else 60.0)
        png = await run_in_threadpool(webimage.to_lcd, raw, width, height)
    except Exception as exc:  # noqa: BLE001 - shown under the preview
        return JSONResponse({"ok": False, "message": f"Couldn't capture the page: {exc}"[:240]}, status_code=502)
    return _png(png)


# ── TRMNL "Web page" Display ─────────────────────────────────────────────────


PAGE_FIELDS = ("url", "preset", "hide", "css", "settle_s", "scale")


def _page_draft(page: dict[str, Any], data: Any) -> dict[str, Any]:
    """The page with the form's (unsaved) settings; the address is validated on save."""
    data = data if hasattr(data, "get") else {}
    return {**page, **{k: data.get(k) for k in PAGE_FIELDS if data.get(k) is not None}}


def _display_page(display: dict[str, Any]) -> dict[str, Any] | None:
    return stonepi_display.get_page(str((display.get("web") or {}).get("page_id") or ""))


def _web_draft(display: dict[str, Any], data: Any) -> dict[str, Any]:
    from stonepi_display.displays import normalize_display

    data = data if hasattr(data, "get") else {}
    web = {
        **(display.get("web") or {}),
        **{k: data.get(k) for k in ("page_id", "bits", "invert", "threshold") if data.get(k) is not None},
    }
    if "dither" in data or "web_settings" in data:
        web["dither"] = str(data.get("dither") or "") in {"1", "on", "true", "True"}
    return normalize_display({**display, "device": data.get("device") or display.get("device"), "web": web})


@router.post("/displays/{display_id}/web")
async def display_web_save(request: Request, display_id: str):
    from app.routes import _guard

    back = f"/displays/{quote(display_id)}"
    user, denied = _guard(request, path=back, active="displays", template="display_detail.html")
    if denied is not None:
        return denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return RedirectResponse(f"{back}?err=Form+expired", status_code=303)
    display = stonepi_display.get_display(display_id)
    if not display or display.get("kind") != "web":
        return RedirectResponse("/displays?err=Display+not+found", status_code=303)
    if str(form.get("action") or "") == "switch":
        # "Use another page": only the page changes.
        picked = stonepi_display.get_page(str(form.get("page_id") or ""))
        if not picked:
            return RedirectResponse(f"{back}?err=Pick+a+page#web", status_code=303)
        stonepi_display.upsert_display({**display, "web": {**display["web"], "page_id": picked["id"]}})
        return RedirectResponse(f"{back}?msg={quote('Now showing ' + picked['name'])}#web", status_code=303)
    # The page's own settings (shared with anything else showing it), then the Display's.
    page = _display_page(display)
    try:
        if page:
            stonepi_display.upsert_page(_page_draft(page, form))
            page_id = page["id"]
        else:
            # No page yet (or it was deleted): the address makes one, named after the Display.
            page_id = stonepi_display.create_page(_page_draft({"name": display["name"]}, form))["id"]
    except ValueError as exc:
        return RedirectResponse(f"{back}?err={quote('Web page: ' + str(exc))}#web", status_code=303)
    draft = _web_draft(display, form)
    draft["web"]["page_id"] = page_id
    stonepi_display.upsert_display(draft)
    return RedirectResponse(f"{back}?msg=Saved#web", status_code=303)


@router.post("/api/displays/{display_id}/web-preview")
async def display_web_preview(request: Request, display_id: str):
    """The exact image a push would send, from the form's unsaved settings."""
    denied = _admin_json(request, csrf=True)
    if denied is not None:
        return denied
    display = stonepi_display.get_display(display_id)
    if not display or display.get("kind") != "web":
        return JSONResponse({"ok": False, "message": "Display not found."}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    draft = _web_draft(display, body)
    page = _display_page(display) or {"id": "draft", "name": display["name"]}
    page = normalize_page(_page_draft(page, body))
    if not page or not page.get("url"):
        return JSONResponse({"ok": False, "message": "Add the page's address first."}, status_code=400)
    try:
        png, info = await run_in_threadpool(
            lambda: stonepi_display.web_image(draft, page=page, max_age=0.0 if body.get("fresh") else 60.0)
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": f"Couldn't capture the page: {exc}"[:240]}, status_code=502)
    return _png(
        png,
        {
            "X-Image-Bytes": str(info["bytes"]),
            "X-Image-Bits": str(info["bits"]),
            "X-Image-Inverted": "1" if info["inverted"] else "0",
            "X-Image-Size": f"{info['width']}x{info['height']}",
        },
    )


@router.post("/api/web-pages")
async def api_web_page_create(request: Request):
    """Add a web page from the Car Thing editor ("+ New web page"): {name, url} → the page."""
    denied = _admin_json(request, csrf=True)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    name = str(body.get("name") or "").strip()[:60] or "Web page"
    try:
        page = stonepi_display.create_page({"name": name, "url": body.get("url")})
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return {"ok": True, "page": {"id": page["id"], "name": page["name"]}, "widget": f"{carthing.WEB_WIDGET_PREFIX}{page['id']}"}


# ── Signed: the Car Thing service ────────────────────────────────────────────


@router.get("/api/internal/web/{name}")
async def internal_web(request: Request, name: str, w: int = 800, h: int = 480, max_age: float = 60.0):
    from app.carthing_routes import _internal_ok

    if not _internal_ok(request):
        return JSONResponse({"ok": False}, status_code=403)
    match = re.fullmatch(r"([a-z0-9-]{1,40})\.png", name)
    page = stonepi_display.get_page(clean_page_id(match.group(1))) if match else None
    if not page or not page.get("url"):
        return Response(status_code=404)
    width, height = _size(w, 800, LCD_MAX[0]), _size(h, 480, LCD_MAX[1])
    try:
        raw = await run_in_threadpool(webcapture.capture, page, width, height, max_age=max(MIN_MAX_AGE, float(max_age)))
        png = await run_in_threadpool(webimage.to_lcd, raw, width, height)
    except Exception as exc:  # noqa: BLE001 - the panel keeps its last image
        logger.warning("web page %s capture failed: %s", page["id"], str(exc)[:160])
        return Response(status_code=502)
    return _png(png)


@router.post("/api/internal/web/changed")
async def internal_web_changed(request: Request):
    """An app's display page changed (Ripple, signed v2): push its Displays shortly."""
    from stonepi_auth.catalog import app_by_id
    from stonepi_auth.internal import verify_internal_v2

    from app.app_pages import schedule_refresh
    from app.carthing_routes import _secret

    body = await request.body()
    if not verify_internal_v2(_secret(), "POST", request.url.path, request.headers, body):
        return JSONResponse({"ok": False}, status_code=404)
    try:
        app_id = str((json.loads(body or b"{}") or {}).get("app") or "")
    except (ValueError, AttributeError):
        app_id = ""
    app = app_by_id(app_id)
    if not app or not app.get("capture_key"):
        return JSONResponse({"ok": False, "message": "Not an app with a display page."}, status_code=400)
    schedule_refresh(app_id)
    return {"ok": True}


@router.get("/api/internal/web/app-displays/{app_id}")
async def internal_app_displays(request: Request, app_id: str):
    """An app's Displays and their last push, for the app's settings page (signed)."""
    from app.app_pages import app_display_status, display_app
    from app.carthing_routes import _internal_ok

    if not _internal_ok(request):
        return JSONResponse({"ok": False}, status_code=404)
    if display_app(app_id) is None:
        return JSONResponse({"ok": False}, status_code=404)
    return {"ok": True, "displays": app_display_status(app_id)}


def web_pages_for_editor() -> list[dict[str, str]]:
    """Id and name of each page, for the Car Thing editor and panel."""
    return [{"id": p["id"], "name": p["name"]} for p in stonepi_display.load_pages()[:MAX_PAGES]]
