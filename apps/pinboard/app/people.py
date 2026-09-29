"""Household members for the reminder assignee picker (signed Auth call).

Uses Auth ``/api/internal/people/names`` (id + display name only). Cached for a
few minutes; empty when Pinboard runs standalone or Auth can't be reached, in
which case the form falls back to a free-text assignee.
"""

from __future__ import annotations

import threading
import time

from app.config import env

NAMES_PATH = "/api/internal/people/names"
TTL_SECONDS = 5 * 60

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "people": []}


def household_people(secret: str) -> list[dict[str, str]]:
    """``[{"id", "name"}]`` sorted by name; [] when unavailable."""
    from stonepi_auth.internal import get_internal_json

    if not secret:
        return []
    now = time.monotonic()
    with _lock:
        if _cache["people"] and now - _cache["at"] < TTL_SECONDS:
            return list(_cache["people"])
    body = get_internal_json(env.auth_url, secret, NAMES_PATH) or {}
    people = [
        {"id": str(row["id"]), "name": str(row.get("name") or "").strip() or "Someone"}
        for row in body.get("people") or []
        if isinstance(row, dict) and row.get("id")
    ]
    people.sort(key=lambda p: p["name"].casefold())
    if people:
        with _lock:
            _cache.update({"at": now, "people": people})
    return people


def name_for(people: list[dict[str, str]], user_id: str) -> str:
    return next((p["name"] for p in people if p["id"] == user_id), "")
