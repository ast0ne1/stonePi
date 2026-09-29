from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from .destinations import DEFAULT_NTFY_SERVER, load_destinations, vault_ntfy_token
from .history import append_history

logger = logging.getLogger("stonepi.notify.ntfy")


def publish_ntfy(
    *,
    topic: str,
    title: str,
    body: str,
    priority: int | None = None,
    tags: str = "",
    click_url: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST one message to one topic on the configured server. No history."""
    cfg = cfg if cfg is not None else (load_destinations().get("ntfy") or {})
    if not cfg.get("enabled"):
        return {"ok": False, "message": "ntfy disabled", "skipped": True}
    topic = str(topic or "").strip()
    if not topic:
        return {"ok": False, "message": "ntfy topic not set", "skipped": True}
    server = str(cfg.get("server") or DEFAULT_NTFY_SERVER).strip().rstrip("/") or DEFAULT_NTFY_SERVER
    try:
        prio = int(priority if priority is not None else cfg.get("default_priority") or 3)
    except (TypeError, ValueError):
        prio = 3
    headers: dict[str, str] = {
        "Title": (title or "StonePi")[:200],
        "Priority": str(max(1, min(5, prio))),
    }
    if tags:
        headers["Tags"] = tags[:120]
    token = vault_ntfy_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if click_url:
        headers["Click"] = click_url
    url = f"{server}/{quote(topic, safe='')}"
    try:
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=True) as client:
            response = client.post(url, content=(body or title or "").encode("utf-8"), headers=headers)
            ok = response.status_code < 400
            message = "Sent." if ok else (response.text or f"HTTP {response.status_code}")[:240]
            return {"ok": ok, "message": message, "status": response.status_code}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ntfy deliver failed: %s", exc)
        return {"ok": False, "message": str(exc)[:240], "status": None}


def deliver_ntfy(
    *,
    title: str,
    body: str,
    priority: int | None = None,
    tags: str = "",
    click_url: str | None = None,
    source: str = "",
    event_id: str = "",
    topic: str | None = None,
) -> dict[str, Any]:
    """Send one message and record it in history.

    ``topic`` defaults to the household channel.
    """
    cfg = load_destinations().get("ntfy") or {}
    target = topic if topic is not None else cfg.get("household_topic")
    result = publish_ntfy(
        topic=str(target or ""),
        title=title,
        body=body,
        priority=priority,
        tags=tags,
        click_url=click_url,
        cfg=cfg,
    )
    if result.get("skipped"):
        return result
    append_history(
        channel="ntfy",
        ok=bool(result.get("ok")),
        title=title,
        message=result.get("message") or "",
        source=source,
        event_id=event_id,
    )
    return result


def send_test_notification(topic: str | None = None) -> dict[str, Any]:
    return deliver_ntfy(
        title="StonePi Notify",
        body="Test notification from StonePi.",
        tags="white_check_mark",
        source="notify",
        event_id="notify.test",
        topic=topic,
    )
