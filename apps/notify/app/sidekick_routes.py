"""Sidekick: pair and manage Pebbles (ideation/pebble.md, Pebble protocol v1).

The devices talk to AgentLink (``apps/agentlink``, port 8015). This page is the admin side:
it calls AgentLink's signed internal API (stonepi_auth.internal v2) and never sees a token.
Each Pebble belongs to one person, picked here when it's added.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request
from stonepi_auth.internal import get_internal_json, sign_internal_v2, verify_internal_v2

logger = logging.getLogger("notify.sidekick")
router = APIRouter()

AGENTLINK_URL = (os.environ.get("STONEPI_AGENTLINK_URL") or "http://127.0.0.1:8015").rstrip("/")
INTERNAL = "/agent/api/internal/pebble"
_client = httpx.Client(timeout=5.0)


def _secret() -> str:
    from app.people import session_secret

    return session_secret()


def _agentlink(method: str, path: str, body: dict | None = None) -> tuple[int, dict[str, Any]]:
    """Signed call to AgentLink. (0, {}) when it can't be reached."""
    secret = _secret()
    if not secret:
        return 0, {"message": "No session secret: can't reach AgentLink."}
    raw = json.dumps(body).encode() if body is not None else b""
    headers = sign_internal_v2(secret, method, path, raw)
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        response = _client.request(method, f"{AGENTLINK_URL}{path}", content=raw or None, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("AgentLink %s %s failed: %s", method, path, exc)
        return 0, {}
    try:
        data = response.json()
    except ValueError:
        data = {}
    return response.status_code, data if isinstance(data, dict) else {}


FUNNEL_HELPER = "/usr/local/sbin/stonepi-pebble-funnel"


def _funnel(action: str) -> dict[str, Any]:
    """Root helper (sudoers) for Tailscale Funnel on :8443. Dev / no helper: report and carry on."""
    if os.name == "nt" or not shutil.which("sudo") or not Path(FUNNEL_HELPER).exists():
        return {"ok": True, "dev": True, "on": False, "message": ""}
    try:
        result = subprocess.run(["sudo", "-n", FUNNEL_HELPER, action], capture_output=True, text=True, timeout=75, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "on": False, "message": str(exc)[:160]}
    last = (result.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        data = json.loads(last[0])
    except json.JSONDecodeError:
        data = {"ok": False, "on": False, "message": (result.stderr or result.stdout or "").strip()[:160]}
    return data if isinstance(data, dict) else {"ok": False, "on": False}


SPEECH_HELPER = "/usr/local/sbin/stonepi-speech-setup"


def _speech_setup(action: str) -> dict[str, Any]:
    """stonepi-speech-setup --start / --status through sudoers (exactly those two). The setup
    itself runs as its own systemd unit, so it carries on if Notify restarts. Dev: no helper."""
    if os.name == "nt" or not shutil.which("sudo") or not Path(SPEECH_HELPER).exists():
        return {"ok": False, "dev": True}
    try:
        result = subprocess.run(["sudo", "-n", SPEECH_HELPER, action], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": str(exc)[:160]}
    last = (result.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        data = json.loads(last[0])
    except json.JSONDecodeError:
        data = {"ok": False, "message": (result.stderr or result.stdout or "").strip()[:160]}
    return data if isinstance(data, dict) else {"ok": False}


def _speech_ready(speech: dict[str, Any]) -> bool:
    return bool((speech.get("stt") or {}).get("ok") and (speech.get("tts") or {}).get("ok"))


FUNNEL_STATUS_TTL_S = 60
_FUNNEL_CACHE: dict[str, Any] = {"at": -1e9, "data": None}


def _funnel_status(*, fresh: bool = False) -> dict[str, Any]:
    """`status` through the helper (two tailscale CLI calls as root), cached for a minute."""
    now = time.monotonic()
    if fresh or _FUNNEL_CACHE["data"] is None or now - _FUNNEL_CACHE["at"] > FUNNEL_STATUS_TTL_S:
        _FUNNEL_CACHE.update(at=now, data=_funnel("status"))
    return _FUNNEL_CACHE["data"]


_sync_lock = threading.Lock()
PERIODIC_SYNC_S = 5 * 60
_last_periodic = {"at": -1e9}


def _sync_funnel() -> tuple[str, str]:
    """Funnel on while any Pebble may go away from home, off otherwise. (message, error)."""
    with _sync_lock:  # Sidekick saves, AgentLink's nudges and the timer never overlap
        return _sync_funnel_locked()


def sync_funnel_periodic() -> None:
    """Scheduler tick (boot.push_due_displays): every 5 minutes, put Funnel back in step with
    the devices (a device unpaired itself, Tailscale Serve was reset) and refresh the away URL
    AgentLink hands out. Errors are logged, never raised."""
    now = time.monotonic()
    if now - _last_periodic["at"] < PERIODIC_SYNC_S:
        return
    _last_periodic["at"] = now
    # Own thread: switching Funnel can take ~1 min, which must not hold up display pushes.
    threading.Thread(target=_periodic_sync, name="funnel-sync", daemon=True).start()


def _periodic_sync() -> None:
    try:
        _msg, err = _sync_funnel()
        if err:
            logger.warning("Funnel sync: %s", err)
    except Exception:  # noqa: BLE001 - a background check must never crash Notify
        logger.exception("Funnel sync failed")


def _sync_funnel_locked() -> tuple[str, str]:
    status, data = _agentlink("GET", f"{INTERNAL}/overview")
    if status != 200:
        return "", ""
    wanted = any(d.get("away_allowed") for d in data.get("devices") or [])
    current = _funnel_status(fresh=True)
    if current.get("dev") or bool(current.get("on")) == wanted:
        return "", ""
    result = _funnel("on" if wanted else "off")
    _FUNNEL_CACHE.update(at=-1e9, data=None)
    if result.get("ok"):
        return str(result.get("message") or ""), ""
    err = str(result.get("message") or "Couldn't change Away from home.")
    if result.get("enable_url"):
        err += f" ({result['enable_url']})"
    return "", err


def _people() -> list[dict[str, str]]:
    from app.config import env

    data = get_internal_json(env.auth_url, _secret(), "/api/internal/people/names") or {}
    rows = [r for r in data.get("people") or [] if isinstance(r, dict) and r.get("id")]
    return sorted(({"id": str(r["id"]), "name": str(r.get("name") or r["id"])} for r in rows), key=lambda r: r["name"].lower())


def _ago(ts: float | None, now: float) -> str:
    if not ts:
        return "never"
    secs = max(0, int(now - ts))
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return datetime.fromtimestamp(ts).strftime("%d %b")


ACTION_LABELS = {
    "ask": "Asked a question", "claim": "Paired", "unpair": "Unpaired on the device", "revoke": "Removed",
    "settings": "Settings changed", "get_status": "Read StonePi status", "get_today": "Read today's overview",
    "get_events": "Read upcoming events", "get_reminders": "Read reminders", "get_notices": "Read notices",
    "get_news_briefing": "Read today's news briefing", "get_weather": "Checked the weather",
}
SEARCH_LABELS = {"search_events": "Searched events", "search_news": "Searched news"}


def _activity_label(action: str, params: dict | None) -> str:
    """What happened, in words; searches say what was looked for (the words, not the answer)."""
    query = str((params or {}).get("query") or "").strip()
    if action in SEARCH_LABELS:
        return f"{SEARCH_LABELS[action]} for “{query}”" if query else SEARCH_LABELS[action]
    return ACTION_LABELS.get(action, action)


def _back(msg: str = "", err: str = "", anchor: str = "", pairing: bool = False) -> RedirectResponse:
    parts = [f"msg={quote(msg)}"] if msg else []
    if err:
        parts.append(f"err={quote(err)}")
    if pairing:
        parts.append("pairing=1")
    return RedirectResponse(f"/sidekick{'?' + '&'.join(parts) if parts else ''}{'#' + anchor if anchor else ''}", status_code=303)


@router.get("/sidekick", response_class=HTMLResponse)
@router.get("/sidekick/", response_class=HTMLResponse, include_in_schema=False)  # Health links add a slash
def sidekick_page(request: Request):
    from app.routes import _guard, _page_ctx, templates

    user, denied = _guard(request, path="/sidekick", active="sidekick", template="sidekick.html")
    if denied is not None:
        return denied
    status, data = _agentlink("GET", f"{INTERNAL}/overview")
    now = time.time()
    devices = data.get("devices") or []
    hosts = dict(data.get("hosts") or {})
    if any(d.get("away_allowed") for d in devices) or hosts.get("away"):
        # Funnel can change outside StonePi (Tailscale admin, tailscaled restarts): show what it
        # really serves. The helper also corrects the away URL AgentLink hands to devices.
        funnel = _funnel_status()
        if not funnel.get("dev"):
            hosts["away"] = funnel.get("url") or None
    names = {str(d["id"]): d["nickname"] for d in devices}
    for d in devices:
        d["seen_text"] = _ago(d.get("last_seen"), now)
        d["ask_text"] = _ago(d.get("last_ask_ok"), now)
    recent = []
    for r in data.get("recent") or []:
        recent.append({
            "when": _ago(r.get("ts"), now),
            "device": names.get(str(r.get("binding")), "A removed Pebble"),
            "action": _activity_label(str(r.get("action") or ""), r.get("params")),
            "kind": r.get("action"),
            "in_ask": r.get("via") == "ask",
            "ok": str(r.get("decision") or "").startswith(("ALLOWED", "APPROVED")),
            "decision": r.get("decision"),
            "seconds": (r.get("duration_ms") or 0) / 1000,
        })
    people = _people()
    me = getattr(user, "user_id", "") if user else ""
    speech = data.get("speech") or {}
    # Only when Pebble can't listen or speak: one sudo call to see whether a setup is running.
    setup = _speech_setup("--status") if status == 200 and not _speech_ready(speech) else {}
    return templates.TemplateResponse(
        request,
        "sidekick.html",
        _page_ctx(
            request,
            active="sidekick",
            user=user,
            csrf=csrf_from_request(request.cookies),
            extra={
                "reachable": status == 200,
                "devices": devices,
                "speech": speech,
                "setup": setup,
                "ai": data.get("ai") or {},
                "hosts": hosts,
                "voice": data.get("voice") or {},
                "quiet": data.get("quiet_hours") or {"enabled": True, "start": "22:00", "end": "07:00"},
                "recent": recent,
                "people": people,
                "me": me,
                "pairing": request.query_params.get("pairing") == "1",
            },
        ),
    )


async def _admin_form(request: Request) -> tuple[Any, dict[str, str] | None, Any]:
    """(user, form, early response). Reading the form is async; the rest of each handler is
    blocking (AgentLink, Auth, the Funnel helper's subprocess) and runs in a worker thread,
    so a slow Funnel switch never stalls the rest of Notify."""
    from app.routes import _guard

    user, denied = _guard(request, path="/sidekick", active="sidekick", template="sidekick.html")
    if denied is not None:
        return user, None, denied
    form = await request.form()
    if not csrf_ok_request(request.cookies, str(form.get("csrf_token") or "")):
        return user, None, _back(err="Form expired. Try again.")
    fields = {str(k): str(v) for k, v in form.multi_items() if isinstance(v, str)}
    return user, fields, None


@router.get("/sidekick/devices.json")
def sidekick_devices(request: Request):
    """The paired Pebbles' ids and names: the page polls this while a new Pebble finishes pairing."""
    from app.routes import _guard

    _user, denied = _guard(request, path="/sidekick", active="sidekick", template="sidekick.html")
    if denied is not None:
        return JSONResponse({"ok": False}, status_code=403)
    status, data = _agentlink("GET", f"{INTERNAL}/overview")
    if status != 200:
        return JSONResponse({"ok": False}, status_code=503)
    return {"ok": True, "devices": [{"id": d["id"], "nickname": d.get("nickname") or "Pebble"} for d in data.get("devices") or []]}


@router.post("/sidekick/add")
async def sidekick_add(request: Request):
    user, form, early = await _admin_form(request)
    if early is not None:
        return early
    return await run_in_threadpool(_add, user, form)


def _add(user: Any, form: dict[str, str]) -> RedirectResponse:
    owner_id = form.get("owner", "").strip()
    owner = next((p for p in _people() if p["id"] == owner_id), None)
    if owner is None:
        if not owner_id and user is None:  # dev without Auth: let run-dev pair to a stand-in owner
            owner = {"id": "dev", "name": "Dev"}
        else:
            return _back(err="Pick who this Pebble belongs to.", anchor="add")
    status, data = _agentlink("POST", f"{INTERNAL}/claim", {
        "code": form.get("code", "").strip().upper(),
        "user_id": owner["id"],
        "user_name": owner["name"],
        "nickname": form.get("nickname", "").strip(),
    })
    if status == 200 and data.get("ok"):
        # The device finishes pairing on its next poll (a few seconds): the page waits for it.
        return _back(pairing=True, anchor="pairing")
    return _back(err=str(data.get("message") or "AgentLink isn't answering."), anchor="add")


@router.post("/sidekick/{binding_id}/save")
async def sidekick_save(request: Request, binding_id: int):
    _user, form, early = await _admin_form(request)
    if early is not None:
        return early
    return await run_in_threadpool(_save, binding_id, form)


def _save(binding_id: int, form: dict[str, str]) -> RedirectResponse:
    tools = {k[5:]: True for k in form if k.startswith("tool_")}
    for name in form.get("tools_listed", "").split(","):
        if name and name not in tools:
            tools[name] = False
    status, data = _agentlink("POST", f"{INTERNAL}/device", {
        "id": binding_id,
        "nickname": form.get("nickname", "").strip(),
        "away_allowed": form.get("away") == "1",
        "talk": {
            "hold": form.get("hold") == "1",
            "toggle": form.get("toggle") == "1",
            "wake": form.get("wake") == "1",
            "wake_phrase": form.get("wake_phrase", "").strip(),
        },
        "tools": tools,
    })
    if status == 200 and data.get("ok"):
        funnel_msg, funnel_err = _sync_funnel()
        msg = " ".join(m for m in (str(data.get("message") or "Saved."), funnel_msg) if m)
        return _back(msg=msg, err=funnel_err, anchor=f"pebble-{binding_id}")
    return _back(err=str(data.get("message") or "AgentLink isn't answering."), anchor=f"pebble-{binding_id}")


@router.post("/sidekick/{binding_id}/remove")
async def sidekick_remove(request: Request, binding_id: int):
    _user, form, early = await _admin_form(request)
    if early is not None:
        return early
    return await run_in_threadpool(_remove, binding_id)


def _remove(binding_id: int) -> RedirectResponse:
    status, data = _agentlink("POST", f"{INTERNAL}/revoke", {"id": binding_id})
    if status == 200 and data.get("ok"):
        funnel_msg, funnel_err = _sync_funnel()
        msg = " ".join(m for m in (str(data.get("message") or "Removed."), funnel_msg) if m)
        return _back(msg=msg, err=funnel_err)
    return _back(err=str(data.get("message") or "AgentLink isn't answering."))


@router.post("/api/internal/sidekick/sync-funnel")
async def internal_sync_funnel(request: Request):
    """AgentLink's nudge after a device unpaired itself or was re-claimed (signed v2; nginx
    never routes /notify/api/internal/). The sync runs in the background."""
    body = await request.body()
    if not verify_internal_v2(_secret(), "POST", request.url.path, request.headers, body):
        return JSONResponse({"ok": False}, status_code=404)
    threading.Thread(target=_sync_funnel, name="funnel-sync", daemon=True).start()
    return {"ok": True}


@router.post("/sidekick/speech-setup")
async def sidekick_speech_setup(request: Request):
    """Set up speech: starts the download and install in the background; the page follows it."""
    _user, _form, early = await _admin_form(request)
    if early is not None:
        return early
    data = await run_in_threadpool(_speech_setup, "--start")
    if data.get("dev"):
        return _back(err="Speech is set up on the Pi: run sudo stonepi-speech-setup there.", anchor="ready")
    if data.get("ok"):
        return _back(msg=str(data.get("message") or "Setting up speech."), anchor="ready")
    return _back(err=str(data.get("message") or "Couldn't start the speech setup."), anchor="ready")


@router.get("/sidekick/speech-setup.json")
def sidekick_speech_setup_status(request: Request):
    """The running setup's state and step, polled by the page until it finishes."""
    from app.routes import _guard

    _user, denied = _guard(request, path="/sidekick", active="sidekick", template="sidekick.html")
    if denied is not None:
        return JSONResponse({"ok": False}, status_code=403)
    data = _speech_setup("--status")
    return {"ok": bool(data.get("ok")), "state": data.get("state") or "", "step": data.get("step") or ""}


@router.post("/sidekick/voice")
async def sidekick_voice(request: Request):
    _user, form, early = await _admin_form(request)
    if early is not None:
        return early
    status, data = await run_in_threadpool(_agentlink, "POST", f"{INTERNAL}/voice", {"voice": form.get("voice", "")})
    if status == 200 and data.get("ok"):
        return _back(msg=str(data.get("message") or "Voice changed."), anchor="voice")
    return _back(err=str(data.get("message") or "AgentLink isn't answering."), anchor="voice")


@router.post("/sidekick/quiet")
async def sidekick_quiet(request: Request):
    _user, form, early = await _admin_form(request)
    if early is not None:
        return early
    quiet = {"enabled": form.get("enabled") == "1", "start": form.get("start", ""), "end": form.get("end", "")}
    status, data = await run_in_threadpool(_agentlink, "POST", f"{INTERNAL}/settings", {"quiet_hours": quiet})
    if status == 200 and data.get("ok"):
        return _back(msg=str(data.get("message") or "Quiet hours saved."), anchor="quiet")
    return _back(err=str(data.get("message") or "AgentLink isn't answering."), anchor="quiet")


@router.get("/sidekick/voice-sample")
def sidekick_voice_sample(request: Request, voice: str = ""):
    """Preview: AgentLink speaks a sample in that voice; streamed to the page's <audio>."""
    from app.routes import _guard

    _user, denied = _guard(request, path="/sidekick", active="sidekick", template="sidekick.html")
    if denied is not None:
        return denied
    secret = _secret()
    if not secret:
        return JSONResponse({"ok": False}, status_code=503)
    path = f"{INTERNAL}/voice-sample"
    raw = json.dumps({"voice": voice[:64]}).encode()
    headers = {"Content-Type": "application/json", **sign_internal_v2(secret, "POST", path, raw)}
    try:
        response = _client.post(f"{AGENTLINK_URL}{path}", content=raw, headers=headers, timeout=30.0)
    except httpx.HTTPError:
        return JSONResponse({"ok": False, "message": "AgentLink isn't answering."}, status_code=502)
    if response.status_code != 200:
        return JSONResponse({"ok": False, "message": "No preview for that voice yet."}, status_code=response.status_code)
    return Response(response.content, media_type="audio/wav", headers={"Cache-Control": "no-store"})
