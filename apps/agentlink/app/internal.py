"""Signed internal API for Notify → Sidekick (stonepi_auth.internal v2; nginx never routes it).

Admins pair and manage Pebbles in Notify; Notify calls these with the platform secret.
Creating and revoking bindings is never an agent tool (muse.md §5).
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from stonepi_audit import ALLOWED
from stonepi_auth.internal import verify_internal_v2

from app import deps, mood, pebble, registry, settings, speech, voices
from app.config import session_secret

PREFIX = "/agent/api/internal"
router = APIRouter(prefix=PREFIX)


def _not_found() -> JSONResponse:
    return JSONResponse({"ok": False, "message": "Not found"}, status_code=404)


def _gone() -> JSONResponse:
    return JSONResponse({"ok": False, "message": "That Pebble isn't paired any more."}, status_code=404)


def _bad(message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=400)


def _binding_id(data: dict[str, Any]) -> int | None:
    value = data.get("id")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _signed(request: Request) -> dict[str, Any] | None:
    """The JSON body when the request is signed; None (→ 404) otherwise."""
    body = await request.body()
    if not verify_internal_v2(session_secret(), request.method, request.url.path, request.headers, body):
        return None
    if not body:
        return {}
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _ai_status() -> dict[str, Any]:
    try:
        import stonepi_ai
    except ImportError:
        return {"ok": False, "detail": "Platform AI isn't installed"}
    if not stonepi_ai.ready("pebble.reply"):
        return {"ok": False, "detail": "No AI model set up (Dashboard → Settings → AI)"}
    return {"ok": True, "detail": stonepi_ai.describe("pebble.reply")}


def _device_row(b: dict[str, Any]) -> dict[str, Any]:
    settings = b.get("settings") or {}
    return {
        "id": b["id"],
        "device_id": b["agent_ref"],
        "nickname": settings.get("nickname") or "Pebble",
        "owner_id": b["user_id"],
        "owner": b["user_name"],
        "talk": settings.get("talk") or {},
        "away_allowed": bool(settings.get("away_allowed")),
        "rev": b["rev"],
        "created": b["created"],
        "last_seen": b["last_seen"],
        "last_path": b["last_path"],
        "fw": b["fw"],
        "battery": b["battery"],
        "model": b["model"],
        "last_ask": b["last_ask"],
        "last_ask_ok": b["last_ask_ok"],
        "mood": _mood(b),
        "tools": [
            {"name": t.name, "tier": t.tier.value, "description": t.description, "enabled": registry.tool_enabled(b, t)}
            for t in registry.TOOLS
        ],
    }


def _mood(b: dict[str, Any]) -> dict[str, str]:
    try:
        now = mood.compute(b)
    except Exception:  # noqa: BLE001 - the page still shows the device
        return {}
    return {"mood": now["mood"], "reason": now["reason"]}


@router.get("/pebble/overview")
async def overview(request: Request):
    if await _signed(request) is None:
        return _not_found()
    # SQLite, the speech status (first call imports faster-whisper) and AI config: off the loop.
    return await run_in_threadpool(_overview)


def _overview() -> dict[str, Any]:
    store = deps.store()
    devices = store.list(adapter="pebble")
    ids = {f"binding:{b['id']}" for b in devices}
    recent = [r for r in deps.audit().query(limit=300) if r["actor"] in ids][:30]
    return {
        "ok": True,
        "devices": [_device_row(b) for b in devices],
        "speech": speech.status(),
        "ai": _ai_status(),
        "hosts": {"lan": pebble.lan_url(), "away": pebble.away_url() or None},
        "voice": voices.catalog(),
        "quiet_hours": settings.quiet_hours(),
        "recent": [
            {"ts": r["ts"], "binding": r["actor"].split(":", 1)[-1], "action": r["action"],
             "decision": r["decision"], "duration_ms": r["duration_ms"], "params": r["params"],
             "via": r.get("note") or ""}
            for r in recent
        ],
        "now": time.time(),
    }


@router.post("/pebble/claim")
async def claim(request: Request):
    data = await _signed(request)
    if data is None:
        return _not_found()
    user_id = str(data.get("user_id") or "").strip()
    if not user_id:
        return JSONResponse({"ok": False, "message": "Pick who this Pebble belongs to."}, status_code=400)
    ok, message = pebble.approve_claim(
        str(data.get("code") or ""),
        user_id=user_id,
        user_name=str(data.get("user_name") or "").strip()[:60],
        nickname=str(data.get("nickname") or "").strip()[:24],
    )
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 404)


@router.post("/pebble/device")
async def update_device(request: Request):
    data = await _signed(request)
    if data is None:
        return _not_found()
    binding_id = _binding_id(data)
    if binding_id is None:
        return _bad("id must be a number.")
    tools = data.get("tools") or {}
    if not isinstance(tools, dict):
        return _bad("tools must be an object of name: true/false.")
    if "talk" in data and not isinstance(data["talk"], dict):
        return _bad("talk must be an object.")
    return await run_in_threadpool(_update_device, binding_id, data, tools)


def _update_device(binding_id: int, data: dict[str, Any], tools: dict[str, Any]) -> Any:
    store = deps.store()
    binding = store.get(binding_id)
    if binding is None or binding["state"] != "BOUND":
        return _gone()
    patch: dict[str, Any] = {}
    if "nickname" in data:
        patch["nickname"] = str(data["nickname"] or "").strip()[:24] or "Pebble"
    if "away_allowed" in data:
        patch["away_allowed"] = bool(data["away_allowed"])
    if isinstance(data.get("talk"), dict):
        talk = data["talk"]
        patch["talk"] = {
            "hold": bool(talk.get("hold")),
            "toggle": bool(talk.get("toggle")),
            "wake": bool(talk.get("wake")),
            "wake_phrase": str(talk.get("wake_phrase") or "hey stonepi").strip()[:32],
        }
        if not (patch["talk"]["hold"] or patch["talk"]["toggle"] or patch["talk"]["wake"]):
            return _bad("Leave at least one way to talk on.")
    for tool, enabled in tools.items():
        if registry.spec(str(tool)) is not None:
            store.set_policy(binding["id"], str(tool), bool(enabled))
    if patch:
        store.update_settings(binding["id"], patch)
    deps.audit().record(actor=f"binding:{binding['id']}", subject=str(binding["user_id"]), action="settings",
                        decision=ALLOWED, params={k: v for k, v in patch.items() if k != "talk"})
    return {"ok": True, "message": "Saved. Pebble picks it up on its next check-in."}


@router.post("/pebble/revoke")
async def revoke(request: Request):
    data = await _signed(request)
    if data is None:
        return _not_found()
    binding_id = _binding_id(data)
    if binding_id is None:
        return _bad("id must be a number.")
    return await run_in_threadpool(_revoke, binding_id)


def _revoke(binding_id: int) -> Any:
    binding = deps.store().get(binding_id)
    if binding is None or not deps.store().revoke(binding_id):
        return _gone()
    deps.audit().record(actor=f"binding:{binding_id}", subject=str(binding["user_id"]), action="revoke",
                        decision=ALLOWED, params={"by": "admin"})
    return {"ok": True, "message": "Removed. The device goes back to its pairing screen."}


@router.post("/pebble/voice")
async def choose_voice(request: Request):
    """Sidekick → Voice: the household's Pebble voice (downloaded first when needed)."""
    data = await _signed(request)
    if data is None:
        return _not_found()
    ok, message = await run_in_threadpool(voices.select, str(data.get("voice") or ""))
    deps.audit().record(actor="admin", action="voice", decision=ALLOWED if ok else "FAILED",
                        params={"voice": str(data.get("voice") or "")})
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 400)


@router.post("/pebble/settings")
async def household_settings(request: Request):
    """Sidekick → Quiet hours: when every Pebble is sleepy (unless something is wrong)."""
    data = await _signed(request)
    if data is None:
        return _not_found()
    quiet = data.get("quiet_hours")
    if not isinstance(quiet, dict):
        return _bad("quiet_hours must be an object.")
    ok, message = await run_in_threadpool(
        settings.set_quiet_hours, bool(quiet.get("enabled")), str(quiet.get("start") or ""), str(quiet.get("end") or "")
    )
    if ok:
        deps.audit().record(actor="admin", action="quiet_hours", decision=ALLOWED, params=settings.quiet_hours())
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 400)


PREVIEW_TEXT = "Hi, I'm Pebble. StonePi is healthy, and your next event is tomorrow evening."


@router.post("/pebble/voice-sample")
async def voice_sample(request: Request):
    """A short sample in an installed voice, for Sidekick's Preview button."""
    data = await _signed(request)
    if data is None:
        return _not_found()
    voice_id = str(data.get("voice") or "")
    if voice_id not in {v for v, _ in voices.VOICES} or not voices.installed(voice_id):
        return _bad("That voice isn't on the Pi yet.")
    path = str(voices.voices_dir() / f"{voice_id}.onnx")
    try:
        wav = await run_in_threadpool(speech.preview, path, PREVIEW_TEXT)
    except speech.SpeechUnavailable as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)
    return Response(wav, media_type="audio/wav")
