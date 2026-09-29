"""Emit PriceScout leaflet/publication events via StonePi Notify."""

from __future__ import annotations

import json
import logging
from typing import Any

from app import db

logger = logging.getLogger("pricescout.notify")

SEEN_KEY = "seen_catalog_ids"


def _catalog_id_from_row(row: dict[str, Any]) -> str | None:
    raw = row.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            raw = None
    if isinstance(raw, dict):
        cid = str(raw.get("catalog_id") or "").strip()
        if cid:
            return cid
    # Fallback: publication deep-link in catalog_url
    url = str(row.get("catalog_url") or "")
    if "publication=" in url:
        part = url.split("publication=", 1)[1]
        cid = part.split("&", 1)[0].strip()
        if cid:
            return cid
    return None


def catalog_ids_from_rows(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        cid = _catalog_id_from_row(row)
        if cid:
            out.add(cid)
    return out


def _load_seen() -> dict[str, list[str]]:
    raw = db.get_pref("local", SEEN_KEY, "{}")
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(x) for x in (v or [])] if isinstance(v, list) else [] for k, v in data.items()}


def _save_seen(seen: dict[str, list[str]]) -> None:
    db.set_pref("local", SEEN_KEY, json.dumps(seen))


def emit_publication_released(
    *,
    store_name: str,
    source_id: str,
    catalog_id: str,
    url: str | None = None,
    offer_count: int | None = None,
) -> bool:
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        summary = f"New leaflet from {store_name}"
        if offer_count:
            summary = f"{summary} · {offer_count} offers"
        return emit_event(
            EventEnvelope(
                id="pricescout.publication_released",
                source="pricescout",
                title=f"PriceScout: {store_name}",
                summary=summary[:500],
                severity="info",
                audience="household",
                dedupe_key=f"pricescout:publication:{source_id}:{catalog_id}",
                url=url,
                data={"source_id": source_id, "catalog_id": catalog_id, "store": store_name},
            )
        )
    except Exception:
        logger.debug("emit_publication_released failed", exc_info=True)
        return False


def note_source_catalogs(
    source_id: str,
    rows: list[dict[str, Any]],
    *,
    store_name: str | None = None,
) -> dict:
    """Track catalog ids after a refresh; emit for newly seen publications.

    First observation for a source seeds silently (avoids a flood on first install).
    """
    catalogs = catalog_ids_from_rows(rows)
    if not catalogs:
        return {"new": 0, "seeded": False, "notified": 0}

    seen = _load_seen()
    previous = set(seen.get(source_id) or [])
    name = (store_name or source_id).strip() or source_id
    # Prefer dealer catalog_url from any row
    sample_url = None
    for row in rows:
        url = (row.get("catalog_url") or "").strip()
        if url:
            sample_url = url
            break

    if not previous:
        seen[source_id] = sorted(catalogs)
        _save_seen(seen)
        return {"new": 0, "seeded": True, "notified": 0, "catalogs": len(catalogs)}

    fresh = catalogs - previous
    notified = 0
    for cid in sorted(fresh):
        if emit_publication_released(
            store_name=name,
            source_id=source_id,
            catalog_id=cid,
            url=sample_url,
            offer_count=len(rows),
        ):
            notified += 1
    merged = previous | catalogs
    # Cap history so prefs stay small
    seen[source_id] = sorted(merged)[-40:]
    _save_seen(seen)
    return {"new": len(fresh), "seeded": False, "notified": notified, "catalogs": len(catalogs)}
