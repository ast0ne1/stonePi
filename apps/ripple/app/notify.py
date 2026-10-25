"""Tell Notify the family display page changed, so it pushes soon rather than at its next interval.

Fire and forget: Notify waits a few seconds to batch quick edits, then pushes the Displays
showing Ripple, within their hourly limit and only if the image changed. If Notify can't be
reached the change still shows at the Display's next scheduled push.
"""

from __future__ import annotations

import json
import logging
import threading

import httpx
from stonepi_auth.internal import sign_internal_v2

from app.config import env

logger = logging.getLogger("ripple.notify")

CHANGED_PATH = "/api/internal/web/changed"


def _post(secret: str) -> None:
    body = json.dumps({"app": "ripple"}).encode()
    headers = {"Content-Type": "application/json", **sign_internal_v2(secret, "POST", CHANGED_PATH, body)}
    try:
        httpx.post(f"{env.notify_url.rstrip('/')}{CHANGED_PATH}", content=body, headers=headers, timeout=3.0)
    except httpx.HTTPError as exc:
        logger.info("Couldn't tell Notify the display changed (it updates on its own schedule): %s", exc)


def display_changed(secret: str) -> None:
    if not secret:
        return  # standalone: no Notify
    threading.Thread(target=_post, args=(secret,), name="ripple-display-changed", daemon=True).start()


STATUS_PATH = "/api/internal/web/app-displays/ripple"


def display_status(secret: str) -> list[dict] | None:
    """Ripple's Displays in Notify and their last push; None when Notify can't be asked."""
    from stonepi_auth.internal import get_internal_json

    if not secret:
        return None
    body = get_internal_json(env.notify_url, secret, STATUS_PATH, timeout=1.5)
    if not body or not isinstance(body.get("displays"), list):
        return None
    return [d for d in body["displays"] if isinstance(d, dict)]
