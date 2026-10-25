"""Nudge Notify to re-check Tailscale Funnel after a change that can end the need for it.

Only Notify may switch Funnel (through its sudoers'd root helper); AgentLink has no sudo by
design. A device unpairing itself, or a re-claim (which turns Away from home off again), can
leave nothing that needs Funnel, so AgentLink asks Notify to look. Fire and forget: Notify
also re-checks on its own timer.
"""

from __future__ import annotations

import logging
import threading

import httpx
from stonepi_auth.internal import sign_internal_v2

from app.config import env, session_secret

logger = logging.getLogger("agentlink.notify")

SYNC_PATH = "/api/internal/sidekick/sync-funnel"


def _post() -> None:
    secret = session_secret()
    if not secret:
        return
    try:
        httpx.post(f"{env.notify_url.rstrip('/')}{SYNC_PATH}", headers=sign_internal_v2(secret, "POST", SYNC_PATH, b""), timeout=3.0)
    except httpx.HTTPError as exc:
        logger.info("Couldn't ask Notify to re-check Funnel (it re-checks on its own): %s", exc)


def request_funnel_sync() -> None:
    threading.Thread(target=_post, name="funnel-sync", daemon=True).start()
