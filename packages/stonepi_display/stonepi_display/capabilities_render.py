"""Render semantic links/actions according to destination capabilities."""

from __future__ import annotations

from typing import Any

from stonepi_contracts import (
    CAPABILITY_ACTION,
    CAPABILITY_LINK,
    CAPABILITY_QR,
    DESTINATION_NTFY,
    DESTINATION_STONEPI_WEB,
    DESTINATION_TRMNL,
    destination_capabilities,
)


def present_interaction(
    *,
    destination_id: str,
    url: str | None = None,
    label: str = "Open",
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Return how a destination should present a URL/action.

    Keys: mode (link|qr|action|omit), url, label, actions
    """
    caps = destination_capabilities(destination_id)
    href = (url or "").strip() or None
    acts = [a for a in (actions or []) if isinstance(a, dict)]

    if destination_id == DESTINATION_TRMNL:
        if href and CAPABILITY_QR in caps:
            return {"mode": "qr", "url": href, "label": label, "actions": []}
        return {"mode": "omit", "url": None, "label": label, "actions": []}

    if destination_id == DESTINATION_NTFY:
        # ntfy uses Click header; actions are limited
        return {
            "mode": "link" if href and CAPABILITY_LINK in caps else "omit",
            "url": href,
            "label": label,
            "actions": acts if CAPABILITY_ACTION in caps else [],
        }

    # stonepi_web and default
    if href and CAPABILITY_LINK in caps:
        mode = "link"
    elif href and CAPABILITY_QR in caps:
        mode = "qr"
    else:
        mode = "omit"
    return {
        "mode": mode,
        "url": href,
        "label": label,
        "actions": acts if CAPABILITY_ACTION in caps else [],
    }


def enrich_widget_for_destination(
    widget_payload: dict[str, Any],
    *,
    destination_id: str = DESTINATION_STONEPI_WEB,
) -> dict[str, Any]:
    """Attach a `presentation` key based on destination capabilities."""
    payload = dict(widget_payload or {})
    url = payload.get("url")
    if not url and isinstance(payload.get("data"), dict):
        url = payload["data"].get("url")
    label = str(payload.get("action_label") or payload.get("title") or "Open")
    payload["presentation"] = present_interaction(
        destination_id=destination_id,
        url=str(url) if url else None,
        label=label,
        actions=payload.get("actions") if isinstance(payload.get("actions"), list) else [],
    )
    payload["destination_id"] = destination_id
    payload["destination_capabilities"] = sorted(destination_capabilities(destination_id))
    return payload
