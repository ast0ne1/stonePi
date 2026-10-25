"""Pebble adapter: the device API, Pebble protocol v1.

Base path ``/agent/pebble/v1``. nginx forwards it from port 80 (``X-StonePi-Path: lan``) and,
for devices allowed away from home, from Tailscale Funnel (``X-StonePi-Path: away``).
Claim is LAN-only. Everything a device does after claiming goes through its binding.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
import zlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from stonepi_audit import ALLOWED
from stonepi_auth.http import client_ip

from app import ask, deps, mood, people
from app.notify import request_funnel_sync
from app.config import DATA_DIR, env, session_secret
from app.errors import NOT_FOUND, UNAUTHORIZED, ApiError

BASE = "/agent/pebble/v1"
router = APIRouter(prefix=BASE)

CODE_ALPHABET = "ACDEFHJKMNPRTUVWXY34679"
CODE_TTL_S = 15 * 60
CLAIM_MIN_INTERVAL_S = 1.0
# Pairing is LAN-only and needs an admin to type the code, but don't let one client hold
# every code (forcing 409s on real devices) or grow memory without bound.
MAX_PENDING = 64
MAX_PENDING_PER_IP = 4
MOOD_MIN_INTERVAL_S = 10
MOOD_POLL_S = 30
DRAIN_MAX_BYTES = 4 * 1024 * 1024
_DEVICE_ID = re.compile(r"^pbl-[0-9a-f]{12}$")

_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}  # claim code → {device_id, created, fw, model, approved}
_used: dict[str, float] = {}  # codes handed out or expired → when
_last_claim_poll: dict[str, float] = {}
_last_mood_poll: dict[int, float] = {}


def reset_runtime() -> None:
    with _lock:
        _pending.clear()
        _used.clear()
        _last_claim_poll.clear()
        _last_mood_poll.clear()


# -- hosts and config ----------------------------------------------------------------


_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale's own addresses


def _home_ip(candidate: str) -> bool:
    """A private LAN IPv4 a Pebble at home can reach (not loopback, not Tailscale)."""
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not ip.is_loopback and ip not in _CGNAT


def _lan_ip() -> str:
    """The Pi's home address, or "" when it can't tell (no route, only a Tailscale exit node)."""
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; picks the outbound interface
            candidates.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        candidates += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    return next((c for c in candidates if _home_ip(c)), "")


def lan_url() -> str:
    """AGENTLINK_LAN_URL, else http://<LAN IP>, else http://<hostname>.local (mDNS)."""
    if env.lan_url.strip():
        return env.lan_url.strip().rstrip("/")
    port = "" if env.lan_port == 80 else f":{env.lan_port}"
    host = _lan_ip()
    if not host:
        name = (os.environ.get("STONEPI_HOSTNAME") or socket.gethostname() or "stonepi").split(".")[0]
        host = f"{name}.local"
    return f"http://{host}{port}"


def away_url() -> str:
    """AGENTLINK_AWAY_URL, else what stonepi-pebble-funnel wrote while Funnel is on."""
    if env.away_url.strip():
        return env.away_url.strip().rstrip("/")
    try:
        return (DATA_DIR / "away_url").read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        return ""


def device_hosts(binding: dict[str, Any]) -> dict[str, str | None]:
    away_allowed = bool((binding.get("settings") or {}).get("away_allowed"))
    return {"lan": lan_url(), "away": (away_url() or None) if away_allowed else None}


def config_rev(binding: dict[str, Any], hosts: dict[str, str | None] | None = None) -> int:
    """The rev devices compare: the settings revision plus a fingerprint of the hosts.

    Hosts change outside the settings (Funnel switched on after a save, the Pi's LAN IP or
    Tailscale name), so the settings revision alone could leave a device on stale hosts.
    """
    hosts = hosts if hosts is not None else device_hosts(binding)
    fingerprint = zlib.crc32(json.dumps(hosts, sort_keys=True).encode()) % 100_000
    return int(binding.get("rev") or 1) * 100_000 + fingerprint


def device_config(binding: dict[str, Any]) -> dict[str, Any]:
    settings = binding.get("settings") or {}
    hosts = device_hosts(binding)
    return {
        "rev": config_rev(binding, hosts),
        "nickname": str(settings.get("nickname") or "Pebble"),
        "character": "Pebble",
        "owner": str(binding.get("user_name") or ""),
        "hosts": hosts,
        "away_allowed": bool(settings.get("away_allowed")),
        "talk": settings.get("talk") or {},
        "mood_poll_s": MOOD_POLL_S,
        "max_ask_s": ask.MAX_ASK_S,
    }


# -- request helpers -----------------------------------------------------------------


def from_proxy(request: Request) -> bool:
    """Only nginx and Tailscale Funnel (both loopback) may tell us the path and client IP.

    On the Pi AgentLink binds 127.0.0.1 anyway; in run-dev it listens on the LAN, where a
    client could otherwise send X-StonePi-Path or X-Real-IP itself.
    """
    host = getattr(request.client, "host", "") or ""
    return host in ("127.0.0.1", "::1") or host.startswith("127.")


def request_path(request: Request) -> str:
    value = (request.headers.get("x-stonepi-path") or "").strip().lower()
    if value in ("lan", "away") and from_proxy(request):
        return value
    return "lan" if env.default_path == "lan" else "away"


def peer_ip(request: Request) -> str:
    return client_ip(request, trusted_proxy=from_proxy(request))


def _device(request: Request) -> dict[str, Any]:
    """The live binding behind this request's token, after the path and owner checks."""
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    binding = deps.store().by_token(token)
    if binding is None:
        raise UNAUTHORIZED
    path = request_path(request)
    if path == "away" and not (binding.get("settings") or {}).get("away_allowed"):
        raise ApiError(403, "away_disabled", "Away use is off for this Pebble")
    enabled = people.user_enabled(str(binding["user_id"]))
    if enabled is False:
        raise ApiError(403, "owner_disabled", "Owner no longer has access")
    if enabled is None and session_secret():
        # Auth hasn't answered since AgentLink started: can't confirm the owner, so wait.
        raise ApiError(503, "unavailable", "StonePi is starting up", 30)
    deps.store().touch(
        binding["id"],
        path=path,
        fw=(request.headers.get("x-pebble-fw") or "").strip(),
        battery=(request.headers.get("x-pebble-battery") or "").strip(),
    )
    return binding


async def _read_body(request: Request, limit: int) -> bytes:
    """Body up to ``limit``. Bigger uploads are read (to a cap) then refused, so the 413 arrives."""
    out = bytearray()
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total <= limit:
            out += chunk
        elif total > DRAIN_MAX_BYTES:
            break
    if total > limit:
        raise ApiError(413, "too_large", "That was too long")
    return bytes(out)


# -- claim ---------------------------------------------------------------------------


def _prune(now: float) -> None:
    for code in [c for c, p in _pending.items() if now - p["created"] > CODE_TTL_S]:
        _pending.pop(code, None)
        _used[code] = now
    for code in [c for c, t in _used.items() if now - t > 24 * 3600]:
        _used.pop(code, None)
    for ip in [i for i, t in _last_claim_poll.items() if now - t > 60]:
        _last_claim_poll.pop(ip, None)


def approve_claim(code: str, *, user_id: str, user_name: str, nickname: str = "") -> tuple[bool, str]:
    """Sidekick → Add Pebble. The device must be polling with this code right now."""
    code = (code or "").strip().upper()
    now = time.time()
    with _lock:
        _prune(now)
        entry = _pending.get(code)
        if entry is None:
            return False, "No Pebble is showing that code. Check Settings → Pairing on the device."
        entry["approved"] = {"user_id": user_id, "user_name": user_name, "nickname": nickname}
    return True, "Claimed. Pebble picks it up within a few seconds."


@router.post("/claim/poll")
async def claim_poll(request: Request):
    if request_path(request) == "away":
        raise ApiError(404, "not_found", "Pair Pebble at home")
    ip = peer_ip(request)
    now = time.time()
    with _lock:
        if now - _last_claim_poll.get(ip, 0) < CLAIM_MIN_INTERVAL_S:
            raise ApiError(429, "rate_limited", "Slow down a little", 1)
        _last_claim_poll[ip] = now
    try:
        data = await request.json()
    except ValueError as exc:
        raise ApiError(400, "bad_request", "Body must be JSON") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "Body must be a JSON object")
    code = str(data.get("code") or "").upper()
    device_id = str(data.get("device_id") or "").lower()
    if len(code) != 4 or any(c not in CODE_ALPHABET for c in code):
        raise ApiError(400, "bad_request", "Claim code must be 4 letters/digits")
    if not _DEVICE_ID.match(device_id):
        raise ApiError(400, "bad_request", "device_id must be pbl- + 12 hex")
    with _lock:
        _prune(now)
        if code in _used:
            raise ApiError(410, "code_expired", "Code expired, making a new one")
        entry = _pending.get(code)
        if entry is None:
            mine = sum(1 for p in _pending.values() if p["ip"] == ip)
            if mine >= MAX_PENDING_PER_IP or len(_pending) >= MAX_PENDING:
                raise ApiError(429, "rate_limited", "Too many pairing codes; wait a few minutes", 60)
            entry = {"device_id": device_id, "created": now, "approved": None, "ip": ip,
                     "fw": str(data.get("fw") or "")[:32], "model": str(data.get("model") or "")[:48]}
            _pending[code] = entry
        if entry["device_id"] != device_id:
            raise ApiError(409, "code_in_use", "Code in use, making a new one")
        approved = entry["approved"]
        if not approved:
            return JSONResponse(
                {"status": "waiting", "poll_after_s": 3, "expires_in_s": int(CODE_TTL_S - (now - entry["created"]))},
                status_code=202,
            )
        _pending.pop(code, None)
        _used[code] = now
    return await run_in_threadpool(_finish_claim, device_id, approved, entry)


def _finish_claim(device_id: str, approved: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    binding, token = deps.store().create_pebble(
        device_id=device_id, user_id=approved["user_id"], user_name=approved["user_name"],
        nickname=approved.get("nickname") or "", fw=entry["fw"], model=entry["model"],
    )
    deps.audit().record(actor=f"binding:{binding['id']}", subject=str(binding["user_id"]), action="claim",
                        decision=ALLOWED, params={"device": device_id})
    request_funnel_sync()  # a re-claim turns Away from home off: Funnel may no longer be needed
    return {"token": token, "device_id": device_id, "owner": binding["user_name"], "config": device_config(binding)}


# -- paired device calls -------------------------------------------------------------


@router.get("/config")
def get_config(request: Request):
    return device_config(_device(request))


@router.get("/mood")
def get_mood(request: Request):
    binding = _device(request)
    now = time.time()
    with _lock:
        last = _last_mood_poll.get(binding["id"], 0)
        if now - last < MOOD_MIN_INTERVAL_S:
            raise ApiError(429, "rate_limited", "Slow down a little", math.ceil(MOOD_MIN_INTERVAL_S - (now - last)))
        _last_mood_poll[binding["id"]] = now
    return {**mood.compute(binding), "config_rev": config_rev(binding)}


@router.post("/ask")
async def post_ask(request: Request):
    # _device does SQLite and may ask Auth: keep it (and the ask itself) off the event loop.
    binding = await run_in_threadpool(_device, request)
    body = await _read_body(request, ask.MAX_BYTES)
    wav, headers = await run_in_threadpool(ask.run, binding, body, request.headers.get("content-type") or "")
    return Response(wav, media_type="audio/wav", headers=headers)


@router.post("/unpair")
def unpair(request: Request):
    binding = _device(request)
    deps.store().revoke(binding["id"])
    deps.audit().record(actor=f"binding:{binding['id']}", subject=str(binding["user_id"]), action="unpair",
                        decision=ALLOWED, params={"by": "device"})
    request_funnel_sync()
    return Response(status_code=204)


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def unknown(rest: str):
    del rest
    raise NOT_FOUND
