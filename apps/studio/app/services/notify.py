"""Emit Studio publish events via StonePi Notify."""

from __future__ import annotations

import logging

logger = logging.getLogger("studio.notify")

_KIND_LABEL = {"game": "game", "guide": "guide", "spa": "app"}


def emit_site_published(
    *,
    title: str,
    kind: str = "spa",
    project_id: str,
    public_url: str | None = None,
    is_update: bool = False,
) -> bool:
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        kind_id = (kind or "spa").strip().lower()
        noun = _KIND_LABEL.get(kind_id, "site")
        verb = "updated" if is_update else "published"
        return emit_event(
            EventEnvelope(
                id="studio.site_published",
                source="studio",
                audience="household",
                title=f"Studio: {title}"[:200],
                summary=f"A {noun} was {verb}: {title}"[:500],
                severity="success",
                dedupe_key=f"studio:published:{project_id}:{verb}",
                url=public_url,
                data={"project_id": project_id, "kind": kind_id, "is_update": is_update},
            )
        )
    except Exception:
        logger.debug("emit_site_published failed", exc_info=True)
        return False
