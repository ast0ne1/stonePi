"""Calls to the apps' agent read endpoints (``POST /api/internal/agent`` in EventTrakr,
Pinboard and NewsCast), signed with stonepi_auth.internal v2 so the user they answer for is
covered by the signature. Loopback only, on each app's catalog port.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from stonepi_auth.internal import sign_internal_v2

from app.config import session_secret

logger = logging.getLogger("agentlink.apps")

PATH = "/api/internal/agent"
TIMEOUT = httpx.Timeout(6.0, connect=2.0)
_client = httpx.Client(timeout=TIMEOUT)


class AppUnavailable(Exception):
    """The app didn't answer (stopped, starting, or an error): a tool failure, not a crash."""


def _port(app_id: str) -> int:
    from stonepi_auth import app_by_id

    item = app_by_id(app_id)
    if not item:
        raise AppUnavailable(f"Unknown app {app_id}")
    return int(item["port"])


def call(app_id: str, tool: str, user_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    secret = session_secret()
    if not secret:
        raise AppUnavailable("No session secret")
    body = json.dumps({"tool": tool, "user": user_id, "params": params or {}}).encode()
    headers = {"Content-Type": "application/json", **sign_internal_v2(secret, "POST", PATH, body)}
    try:
        response = _client.post(f"http://127.0.0.1:{_port(app_id)}{PATH}", content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise AppUnavailable(f"{app_id} isn't answering") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise AppUnavailable(f"{app_id} gave no answer") from exc
    if response.status_code != 200 or not data.get("ok"):
        logger.warning("%s %s -> %s %s", app_id, tool, response.status_code, data.get("error"))
        raise AppUnavailable(str(data.get("message") or f"{app_id} couldn't answer"))
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else {}
