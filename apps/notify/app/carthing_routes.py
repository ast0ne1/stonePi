"""Displays → Car Thing: editor, device controls, and the signed API the panel service reads.

The panel itself (``apps/carthing``, unit ``stonepi-carthing``) only reads from here:
``/api/internal/carthing/*`` is signed (stonepi_auth.internal) and nginx never routes it.
Admins preview the panel through ``/carthing/panel/*``, an admin-only proxy that signs
each request and makes the panel render the editor's unsaved draft.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request, set_csrf_cookie
from stonepi_auth.http import request_is_https
from stonepi_auth.internal import sign_internal, verify_internal
from stonepi_display import carthing, spotify

logger = logging.getLogger("notify.carthing")
router = APIRouter()

PANEL_URL = (os.environ.get("STONEPI_CARTHING_URL") or "http://127.0.0.1:8013").rstrip("/")
HELPER = "/usr/local/sbin/stonepi-carthing-helper"
PREVIEW_BASE = "/carthing/panel/"
MAX_UPLOAD = 8 * 1024 * 1024
# One client for every call to the panel service. Building an httpx client loads the TLS
# certificate store (~350 ms on the dev PC), and the preview proxy fetches every CSS, JS, icon
# and font file through here: a new client per request made each preview reload take seconds.
_panel_client = httpx.Client(timeout=10.0)

# The editor's unsaved config, shown by the preview. One slot: Notify is admin-only.
_draft: dict[str, Any] = {"config": None}
_draft_lock = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────────────────


def _secret() -> str:
    from app.people import session_secret

    return session_secret()


def _internal_ok(request: Request) -> bool:
    return verify_internal(_secret(), request.method, request.url.path, request.headers)


def _auth_optional() -> bool:
    """No session secret skips sign-in only in dev (Windows run-dev or STONEPI_DEV=1)."""
    return os.name == "nt" or (os.environ.get("STONEPI_DEV") or "").strip() == "1"


def _admin(request: Request):
    from app.routes import _user

    if not _secret():
        # Fail closed on a Pi: a missing/unreadable secret must not open the panel editor.
        if _auth_optional():
            return None, None
        return None, JSONResponse({"ok": False, "message": "Admins only."}, status_code=403)
    user = _user(request)
    if user is None or not user.is_admin:
        return None, JSONResponse({"ok": False, "message": "Admins only."}, status_code=403)
    return user, None


def _api_csrf_ok(request: Request) -> bool:
    return csrf_ok_request(request.cookies, header_token=request.headers.get("x-stonepi-csrf") or "")


def _back(msg: str = "", err: str = "", anchor: str = "", config_id: str = "") -> RedirectResponse:
    parts = []
    if config_id:
        parts.append(f"config={quote(config_id)}")
    if msg:
        parts.append(f"msg={quote(msg)}")
    if err:
        parts.append(f"err={quote(err)}")
    query = ("?" + "&".join(parts)) if parts else ""
    return RedirectResponse(f"/displays/carthing{query}{('#' + anchor) if anchor else ''}", status_code=303)


def run_helper(action: str) -> dict:
    """Root helper (sudoers) that enables/disables stonepi-carthing. Dev: report and carry on."""
    if os.name == "nt" or not shutil.which("sudo") or not Path(HELPER).exists():
        return {"ok": True, "dev": True, "message": "Service control runs on the Pi; skipped here."}
    try:
        result = subprocess.run(
            ["sudo", "-n", HELPER, action], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": str(exc)[:160]}
    last = (result.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        data = json.loads(last[0])
    except json.JSONDecodeError:
        data = {"ok": result.returncode == 0, "message": (result.stderr or result.stdout or "").strip()[:160]}
    return data if isinstance(data, dict) else {"ok": False}


def _panel_signed(method: str, path: str, timeout: float = 3.0, **kwargs) -> httpx.Response | None:
    secret = _secret()
    if not secret:
        return None
    try:
        return _panel_client.request(
            method, f"{PANEL_URL}{path}", headers=sign_internal(secret, method, path), timeout=timeout, **kwargs
        )
    except httpx.HTTPError:
        return None


# ── signed internal API (panel service → Notify) ─────────────────────────────


@router.get("/api/internal/carthing/state")
def internal_state(request: Request, draft: str = ""):
    if not _internal_ok(request):
        return JSONResponse({"ok": False}, status_code=403)
    state = carthing.public_state()
    if draft:
        with _draft_lock:
            cfg = _draft["config"]
        if cfg is not None:
            state["config"] = cfg
            state["rev"] = "d" + carthing.state_rev(cfg, carthing.load_device())
        state["enabled"] = True  # the preview works whether or not the device panel is on
    return state


@router.post("/api/internal/carthing/pair")
def internal_pair(request: Request):
    """The connector asks for a fresh device token when it injects the redirect page."""
    if not _internal_ok(request):
        return JSONResponse({"ok": False}, status_code=403)
    return {"ok": True, "token": carthing.new_pairing_token()}


@router.get("/api/internal/carthing/asset/{asset_id}")
def internal_asset(request: Request, asset_id: str):
    if not _internal_ok(request):
        return JSONResponse({"ok": False}, status_code=403)
    if not re.fullmatch(r"[a-f0-9]{16}", asset_id):
        return Response(status_code=404)
    path = carthing.assets_dir() / f"{asset_id}.jpg"
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


def system_feed(overview: dict) -> dict:
    """System mini-app from Notify's collected overview (+ catalog units for restart)."""
    from stonepi_auth import APP_CATALOG

    units = {item["id"]: item.get("unit") for item in APP_CATALOG}
    system = overview.get("system") or {}
    items = []
    for row in overview.get("apps") or []:
        app_id = str(row.get("id") or "")
        running = bool(row.get("running"))
        detail = [f"Status: {'running' if running else 'not running'}"]
        if row.get("d") and row.get("d") != "—":
            detail.append(str(row["d"]))
        if row.get("m"):
            detail.append(str(row["m"]))
        if units.get(app_id):
            detail.append(f"Service: {units[app_id]}")
        items.append(
            {
                "id": app_id,
                "title": str(row.get("n") or app_id),
                "sub": str(row.get("d") or "") if row.get("d") not in (None, "—") else ("Running" if running else "Stopped"),
                "badge": "Up" if running else "Down",
                "level": "up" if running else "down",
                "unit": units.get(app_id) or "",
                "detail": "\n".join(detail),
            }
        )
    up, total = int(overview.get("apps_up") or 0), int(overview.get("apps_total") or 0)
    head = [f"CPU {overview.get('cpu_disp') or '—'}", f"Disk {overview.get('disk_disp') or '—'}"]
    if system.get("temp"):
        head.insert(0, str(overview.get("temp_disp") or f"{system['temp']}°C"))
    def _pct(key: str) -> int | None:
        value = overview.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    return {
        "ok": True,
        "card": {
            "headline": " · ".join(head),
            "sub": f"{up}/{total} services up · backup {overview.get('backup_when') or '—'}",
            "badge": "" if up == total else "Check",
            "level": "" if up == total else "down",
        },
        # Numbers for the panel's large System widget (None = not readable on this machine).
        "stats": {
            "cpu": _pct("cpu_pct"),
            "mem": _pct("mem_pct"),
            "temp": _pct("temp_c"),
            "disk": _pct("disk_pct"),
            "uptime": str(overview.get("uptime") or ""),
            "hostname": str(overview.get("hostname") or ""),
            "up": up,
            "total": total,
            "backup": str(overview.get("backup_when") or ""),
            "backup_ok": bool(overview.get("backup_ok")),
        },
        "items": items,
        "refresh_s": 30,
    }


@router.get("/api/internal/carthing/system")
def internal_system(request: Request):
    if not _internal_ok(request):
        return JSONResponse({"ok": False}, status_code=403)
    from app.boot import cached_overview

    return system_feed(cached_overview(max_age=30.0))


# ── admin preview proxy (Notify → panel) ─────────────────────────────────────


@router.api_route("/carthing/panel/{path:path}", methods=["GET", "POST"])
async def panel_proxy(request: Request, path: str):
    _, denied = _admin(request)
    if denied is not None:
        return denied
    target = "/" + path
    if request.method == "POST" and target not in {"/restart", "/music/cmd"}:
        return Response(status_code=405)
    secret = _secret()
    if not secret:
        return HTMLResponse("<p style='font:16px sans-serif;padding:20px'>Preview needs StonePi sign-in.</p>")
    headers = {
        **sign_internal(secret, request.method, target),
        "x-carthing-preview": "1",
        "x-carthing-base": PREVIEW_BASE,
    }
    if request.method == "POST":
        headers["content-type"] = "application/json"
    body = await request.body() if request.method == "POST" else None
    try:
        # The shared (sync, thread-safe) client in a worker thread: no per-request client.
        upstream = await run_in_threadpool(
            _panel_client.request,
            request.method,
            f"{PANEL_URL}{target}",
            params=dict(request.query_params),
            headers=headers,
            content=body,
        )
    except httpx.HTTPError:
        return HTMLResponse(
            "<body style='margin:0;background:#0b0d10;color:#f5f1e8;font:20px sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "The Car Thing service isn't running.</body>",
            status_code=502,
        )
    keep = {"content-type", "cache-control"}
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() in keep}
    return Response(upstream.content, status_code=upstream.status_code, headers=out_headers)


# ── editor page ──────────────────────────────────────────────────────────────


def _status() -> dict:
    """Device tab status: service answering? device checking in? adb sees it?"""
    response = _panel_signed("GET", "/internal/status")
    data: dict[str, Any] = {"service": False}
    if response is not None and response.status_code == 200:
        try:
            data = {"service": True, **response.json()}
        except ValueError:
            pass
    return data


@router.get("/displays/carthing", response_class=HTMLResponse)
def carthing_page(request: Request, config: str = ""):
    from app.routes import _guard, _page_ctx, templates

    if not _secret() and not _auth_optional():
        return HTMLResponse("StonePi sign-in isn't configured, so the Car Thing editor is locked.", status_code=403)
    user, denied = _guard(request, path="/displays/carthing", active="displays", template="carthing.html")
    if denied is not None:
        return denied
    rows = carthing.list_configs()
    device = carthing.load_device()
    editing = carthing.get_config(config) if config else None
    if editing is None:
        editing = carthing.active_config()
    with _draft_lock:
        _draft["config"] = editing
    csrf = csrf_from_request(request.cookies)
    response = templates.TemplateResponse(
        request,
        "carthing.html",
        _page_ctx(
            request,
            active="displays",
            user=user,
            csrf=csrf,
            extra={
                "device": device,
                "configs": rows,
                "editing": editing,
                "snapshots": carthing.list_snapshots(editing["id"]),
                "editor": {"config": editing, "catalog": carthing.editor_catalog(), "active": device["active_config"]},
                "pin_length": carthing.PIN_LENGTH,
                "music": music_view(request),
            },
        ),
    )
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


@router.get("/api/carthing/status")
def api_status(request: Request):
    _, denied = _admin(request)
    if denied is not None:
        return denied
    device = carthing.load_device()
    return {"ok": True, "enabled": device["enabled"], "paired_at": device["paired_at"], **_status()}


@router.post("/api/carthing/draft")
async def api_draft(request: Request):
    _, denied = _admin(request)
    if denied is not None:
        return denied
    if not _api_csrf_ok(request):
        return JSONResponse({"ok": False, "message": "Form expired — reload the page."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = None
    cfg = carthing.normalize_config(body)
    with _draft_lock:
        _draft["config"] = cfg
    return {"ok": True, "config": cfg}


@router.post("/api/carthing/config/{config_id}")
async def api_save_config(request: Request, config_id: str):
    _, denied = _admin(request)
    if denied is not None:
        return denied
    if not _api_csrf_ok(request):
        return JSONResponse({"ok": False, "message": "Form expired — reload the page."}, status_code=403)
    if carthing.get_config(config_id) is None:
        return JSONResponse({"ok": False, "message": "That config no longer exists."}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "Couldn't read the config."}, status_code=400)
    body = body if isinstance(body, dict) else {}
    body["id"] = config_id
    try:
        saved = carthing.save_config(body)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    with _draft_lock:
        _draft["config"] = saved
    return {"ok": True, "config": saved, "snapshots": carthing.list_snapshots(config_id)}


@router.post("/api/carthing/background")
async def api_background(request: Request):
    """Upload a background: resized/cropped to 800×480 JPEG."""
    _, denied = _admin(request)
    if denied is not None:
        return denied
    if not _api_csrf_ok(request):
        return JSONResponse({"ok": False, "message": "Form expired — reload the page."}, status_code=403)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"ok": False, "message": "Choose an image."}, status_code=400)
    raw = await upload.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        return JSONResponse({"ok": False, "message": "That image is over 8 MB."}, status_code=400)
    try:
        from io import BytesIO

        from PIL import Image, ImageOps

        with Image.open(BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img = ImageOps.fit(img, (carthing.SCREEN_WIDTH, carthing.SCREEN_HEIGHT), Image.Resampling.LANCZOS)
            out = BytesIO()
            img.save(out, "JPEG", quality=82, optimize=True, progressive=False)
    except Exception:  # noqa: BLE001 - not an image Pillow can read
        return JSONResponse({"ok": False, "message": "That file isn't an image StonePi can read."}, status_code=400)
    asset_id = secrets.token_hex(8)
    folder = carthing.assets_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{asset_id}.jpg").write_bytes(out.getvalue())
    return {"ok": True, "asset": asset_id, "bytes": len(out.getvalue())}


@router.get("/api/carthing/asset/{asset_id}")
def api_asset(request: Request, asset_id: str):
    """Admin thumbnail of an uploaded background (editor)."""
    _, denied = _admin(request)
    if denied is not None:
        return denied
    if not re.fullmatch(r"[a-f0-9]{16}", asset_id):
        return Response(status_code=404)
    path = carthing.assets_dir() / f"{asset_id}.jpg"
    return FileResponse(path, media_type="image/jpeg") if path.is_file() else Response(status_code=404)


@router.get("/api/carthing/geocode")
def api_geocode(request: Request, q: str = ""):
    """Weather location search (Open-Meteo geocoding, no key)."""
    _, denied = _admin(request)
    if denied is not None:
        return denied
    name = q.strip()[:80]
    if len(name) < 2:
        return {"ok": True, "results": []}
    try:
        response = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 6, "format": "json"},
            timeout=6.0,
        )
        rows = (response.json() or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": "Location search isn't reachable."}, status_code=502)
    results = []
    for row in rows:
        label = ", ".join(str(p) for p in (row.get("name"), row.get("admin1"), row.get("country")) if p)
        results.append({"label": label[:60], "lat": row.get("latitude"), "lon": row.get("longitude")})
    return {"ok": True, "results": results}


# ── form posts (device + saved configs) ──────────────────────────────────────


async def _form(request: Request):
    from app.routes import _guard

    user, denied = _guard(request, path="/displays/carthing", active="displays", template="carthing.html")
    if denied is not None:
        return None, denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return None, _back(err="Form expired — try again.")
    return form, None


@router.post("/displays/carthing/device")
async def device_action(request: Request):
    form, denied = await _form(request)
    if denied is not None:
        return denied
    action = str(form.get("action") or "")
    if action == "enable":
        carthing.update_device(enabled=True)
        result = run_helper("enable")
        if not result.get("ok"):
            return _back(err=f"Panel switched on, but the service didn't start: {result.get('message') or 'unknown error'}", anchor="device")
        return _back(msg="Car Thing panel switched on. Plug the Car Thing in; it pairs by itself.", anchor="device")
    if action == "disable":
        carthing.update_device(enabled=False)
        result = run_helper("disable")
        if not result.get("ok"):
            return _back(err=f"Panel switched off, but the service didn't stop: {result.get('message') or 'unknown error'}", anchor="device")
        return _back(msg="Car Thing panel switched off. The device is back on its own app.", anchor="device")
    if action == "repair":
        response = _panel_signed("POST", "/internal/repair")
        if response is None or response.status_code != 200:
            return _back(err="The Car Thing service isn't running, so it can't re-pair.", anchor="device")
        return _back(msg="Re-pairing: the device gets a fresh token within a few seconds.", anchor="device")
    if action == "pin":
        pin = str(form.get("pin") or "").strip()
        enabled = str(form.get("pin_enabled") or "") in {"1", "on", "true"}
        try:
            carthing.set_pin(pin or None, enabled=enabled)
        except ValueError as exc:
            return _back(err=str(exc), anchor="device")
        if pin:
            return _back(msg=f"PIN saved and {'on' if enabled else 'off'}.", anchor="device")
        return _back(msg=f"PIN {'on' if enabled else 'off'}.", anchor="device")
    return _back(err="Unknown action.")


@router.post("/displays/carthing/configs")
async def config_action(request: Request):
    form, denied = await _form(request)
    if denied is not None:
        return denied
    action = str(form.get("action") or "")
    config_id = str(form.get("config_id") or "")
    name = str(form.get("name") or "").strip()
    try:
        if action == "activate":
            cfg = carthing.activate_config(config_id)
            return _back(msg=f"“{cfg['name']}” is now on the Car Thing.", anchor="configs", config_id=cfg["id"])
        if action == "copy":
            cfg = carthing.copy_config(config_id, name)
            return _back(msg=f"Saved a copy as “{cfg['name']}”.", anchor="editor", config_id=cfg["id"])
        if action == "rename":
            cfg = carthing.rename_config(config_id, name)
            return _back(msg="Renamed.", anchor="configs", config_id=cfg["id"])
        if action == "delete":
            carthing.delete_config(config_id)
            return _back(msg="Config deleted.", anchor="configs")
        if action == "restore":
            cfg = carthing.restore_snapshot(config_id, str(form.get("snapshot") or ""))
            return _back(msg="Earlier version restored.", anchor="editor", config_id=cfg["id"])
        if action == "import":
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                return _back(err="Choose a config file to import.", anchor="configs")
            raw = await upload.read(512 * 1024)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _back(err="That file isn't a Car Thing config.", anchor="configs")
            cfg = carthing.import_config(data, name=name)
            return _back(msg=f"Imported as “{cfg['name']}”.", anchor="editor", config_id=cfg["id"])
    except ValueError as exc:
        return _back(err=str(exc), anchor="configs", config_id=config_id if action != "delete" else "")
    return _back(err="Unknown action.")


@router.get("/displays/carthing/configs/{config_id}/export")
def config_export(request: Request, config_id: str):
    from app.routes import _guard

    _, denied = _guard(request, path="/displays/carthing", active="displays", template="carthing.html")
    if denied is not None:
        return denied
    data = carthing.export_config(config_id)
    if data is None:
        return _back(err="That config no longer exists.")
    filename = f"stonepi-carthing-{data['id']}.json"
    return Response(
        json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Music: Spotify sign-in (F7 S1) ───────────────────────────────────────────
#
# Authorization code + PKCE: only the client ID is stored, never a client secret. The refresh
# token goes to the vault; the panel service turns it into access tokens. Two callbacks:
# Notify's own HTTPS address (Tailscale) when the page is on HTTPS, else Spotify's loopback
# exception, where the admin pastes the error page's address back here.

_PENDING_TTL_S = 15 * 60
_pending: dict[str, dict[str, Any]] = {}
_pending_lock = threading.Lock()
_spotify_client = httpx.Client(timeout=10.0)


def _https_redirect(request: Request) -> str:
    """Notify's own callback, only when this page is on HTTPS (Spotify needs HTTPS off loopback)."""
    if not request_is_https(request):
        return ""
    from stonepi_auth.prefix import clean_prefix

    from app.config import env

    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not host:
        return ""
    return f"https://{host}{clean_prefix(env.stonepi_prefix)}{spotify.CALLBACK_PATH}"


def _remember_pending(verifier: str, redirect_uri: str) -> str:
    state = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _pending_lock:
        for key in [k for k, v in _pending.items() if now - v["at"] > _PENDING_TTL_S]:
            _pending.pop(key, None)
        while len(_pending) >= 5:
            _pending.pop(next(iter(_pending)))
        _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "at": now}
    return state


def _take_pending(state: str) -> dict[str, Any] | None:
    with _pending_lock:
        entry = _pending.pop(state or "", None)
    if entry is None or time.monotonic() - entry["at"] > _PENDING_TTL_S:
        return None
    return entry


def _paste_pending() -> bool:
    now = time.monotonic()
    with _pending_lock:
        return any(
            v["redirect_uri"] == spotify.LOOPBACK_REDIRECT and now - v["at"] <= _PENDING_TTL_S for v in _pending.values()
        )


def _vault_secret(key: str) -> str:
    try:
        from stonepi_vault import get_secret

        return (get_secret(key, env_name=key, default="") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _vault_save(key: str, value: str) -> bool:
    try:
        from stonepi_vault import set_secret

        set_secret(key, value)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("spotify: couldn't save %s: %s", key, exc)
        return False


def _tell_panel_music_changed() -> None:
    """The panel drops its in-memory access token and rereads the vault."""
    _panel_signed("POST", "/internal/music/reset")


def music_view(request: Request) -> dict[str, Any]:
    client_id = _vault_secret(spotify.CLIENT_ID_KEY)
    return {
        "client_id": client_id,
        "connected": bool(client_id and _vault_secret(spotify.REFRESH_TOKEN_KEY)),
        "account": carthing.music_account(),
        "https_redirect": _https_redirect(request),
        "loopback_redirect": spotify.LOOPBACK_REDIRECT,
        "awaiting_paste": _paste_pending(),
    }


def _finish_sign_in(code: str, state: str) -> tuple[str, str]:
    """(message, error) after Spotify sends the code back."""
    entry = _take_pending(state)
    if entry is None:
        return "", "That Spotify sign-in expired or didn't start here. Press Connect again."
    client_id = _vault_secret(spotify.CLIENT_ID_KEY)
    if not client_id:
        return "", "Save the Spotify client ID first."
    try:
        tokens = spotify.exchange_code(client_id, code, entry["redirect_uri"], entry["verifier"], client=_spotify_client)
        profile = spotify.fetch_profile(str(tokens["access_token"]), client=_spotify_client)
    except spotify.SpotifyAuthError as exc:
        return "", str(exc)
    refresh = str(tokens.get("refresh_token") or "")
    if not refresh:
        return "", "Spotify didn't return a refresh token. Press Connect again."
    if not _vault_save(spotify.REFRESH_TOKEN_KEY, refresh):
        return "", "Signed in, but the vault couldn't save the token. Check Settings → Vault."
    carthing.save_music_account(profile["name"], profile["id"])
    _tell_panel_music_changed()
    return f"Spotify connected as {profile['name'] or 'your account'}.", ""


@router.get("/api/carthing/music/catalog")
def api_music_catalog(request: Request):
    """Speakers and playlists for the editor's Music tab, from the panel service (it holds the token)."""
    _, denied = _admin(request)
    if denied is not None:
        return denied
    response = _panel_signed("GET", "/internal/music/catalog", timeout=15.0)
    if response is None or response.status_code != 200:
        return {"ok": False, "message": "The Car Thing service isn't running, so speakers and playlists can't be listed.", "devices": [], "playlists": []}
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {"ok": False, "devices": [], "playlists": []}


@router.get("/displays/carthing/spotify/callback")
def spotify_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    from app.routes import _guard

    _, denied = _guard(request, path="/displays/carthing", active="displays", template="carthing.html")
    if denied is not None:
        return denied
    if error:
        _take_pending(state)
        return _back(err="Spotify sign-in was cancelled." if error == "access_denied" else f"Spotify said: {error[:80]}", anchor="music")
    msg, err = _finish_sign_in(code, state)
    return _back(msg=msg, err=err, anchor="music")


@router.post("/displays/carthing/music")
async def music_action(request: Request):
    form, denied = await _form(request)
    if denied is not None:
        return denied
    action = str(form.get("action") or "")
    if action == "client":
        client_id = str(form.get("client_id") or "").strip()
        if not spotify.valid_client_id(client_id):
            return _back(err="A Spotify client ID is 32 letters and digits, from your Spotify app's Settings page.", anchor="music")
        if not _vault_save(spotify.CLIENT_ID_KEY, client_id):
            return _back(err="The vault couldn't save the client ID. Check Settings → Vault.", anchor="music")
        _tell_panel_music_changed()
        return _back(msg="Client ID saved. Now press Connect Spotify.", anchor="music")
    if action == "connect":
        client_id = _vault_secret(spotify.CLIENT_ID_KEY)
        if not client_id:
            return _back(err="Save the Spotify client ID first.", anchor="music")
        redirect_uri = "" if str(form.get("mode") or "") == "paste" else _https_redirect(request)
        redirect_uri = redirect_uri or spotify.LOOPBACK_REDIRECT
        verifier, challenge = spotify.new_pkce()
        state = _remember_pending(verifier, redirect_uri)
        return RedirectResponse(spotify.authorize_url(client_id, redirect_uri, state, challenge), status_code=303)
    if action == "paste":
        parsed = spotify.parse_callback(str(form.get("callback_url") or ""))
        if parsed.get("error"):
            _take_pending(parsed.get("state", ""))
            return _back(err="Spotify sign-in was cancelled.", anchor="music")
        if not parsed.get("code") or not parsed.get("state"):
            return _back(err="Paste the whole address from the error page. It starts with http://127.0.0.1:8888/callback?code=", anchor="music")
        msg, err = _finish_sign_in(parsed["code"], parsed["state"])
        return _back(msg=msg, err=err, anchor="music")
    if action == "test":
        response = _panel_signed("POST", "/internal/music/check", timeout=12.0)
        if response is None or response.status_code != 200:
            return _back(err="The Car Thing service isn't running, so it can't check Spotify.", anchor="music")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if data.get("ok"):
            return _back(msg="The Car Thing service got a Spotify token. Music is ready.", anchor="music")
        return _back(err=str(data.get("message") or "The Car Thing service couldn't reach Spotify."), anchor="music")
    if action == "disconnect":
        try:
            from stonepi_vault import get_vault

            get_vault().delete(spotify.REFRESH_TOKEN_KEY)
        except Exception:  # noqa: BLE001
            return _back(err="The vault couldn't remove the token. Check Settings → Vault.", anchor="music")
        carthing.clear_music_account()
        _tell_panel_music_changed()
        return _back(msg="Spotify disconnected. To revoke it fully, remove StonePi under Manage apps on your Spotify account page.", anchor="music")
    return _back(err="Unknown action.", anchor="music")


def carthing_summary() -> dict:
    """Card on the Displays list."""
    try:
        device = carthing.load_device()
        active = carthing.active_config()
    except Exception:  # noqa: BLE001 - never break the Displays page
        return {"enabled": False, "active": "", "configs": 0}
    return {"enabled": device["enabled"], "active": active["name"], "configs": len(carthing.list_configs())}

